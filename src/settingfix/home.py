"""Locating a Codex home and reading/writing the two files that carry identity."""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import surface
from .toml_doc import ConfigDoc, TomlError, validate

AUTH_NAME = "auth.json"
CONFIG_NAME = "config.toml"


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            path.chmod(0o700)
        except OSError:
            pass
    return path


def write_atomic(path: Path, payload: bytes) -> None:
    private_dir(path.parent)
    scratch = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(scratch, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(scratch, path)
    finally:
        if scratch.exists():
            scratch.unlink()


def resolve_home(explicit: str | None = None) -> Path:
    """``--home`` beats ``$CODEX_HOME`` beats ``~/.codex``, matching Codex."""
    raw = explicit or os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    home = Path(raw).expanduser()
    if not home.is_absolute():
        home = (Path.cwd() / home).resolve()
    return home


def check_home(home: Path) -> None:
    """Codex requires an explicit ``CODEX_HOME`` to already exist and be a directory."""
    if not home.exists():
        raise FileNotFoundError(f"Codex home does not exist: {home}")
    if not home.is_dir():
        raise NotADirectoryError(f"Codex home is not a directory: {home}")


@dataclass(frozen=True)
class Pair:
    """The credential and the surface that belong together."""

    auth: bytes | None
    surface: str

    def fingerprint(self) -> tuple[str, ...]:
        from . import identity

        return identity.fingerprint(self.auth)


class Home:
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def auth_path(self) -> Path:
        return self.path / AUTH_NAME

    @property
    def config_path(self) -> Path:
        return self.path / CONFIG_NAME

    def read_auth(self) -> bytes | None:
        try:
            return self.auth_path.read_bytes()
        except FileNotFoundError:
            return None

    def read_config(self) -> str:
        try:
            return self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def read(self) -> Pair:
        return Pair(self.read_auth(), surface.capture(self.read_config()))

    # -- writes ----------------------------------------------------------

    def apply(self, pair: Pair, *, keep_api_key: bool = False) -> None:
        """Write credential and surface as one validated unit.

        The config is assembled and validated *before* either file is touched,
        so a bad surface can never leave the home in a half-written state.
        """
        current = self.read_config()
        if current:
            validate(current, str(self.config_path))
            duplicates = ConfigDoc(current).duplicate_root_keys()
            if duplicates:
                raise TomlError(
                    "config.toml already defines "
                    + ", ".join(sorted(set(duplicates)))
                    + " more than once; repair it before switching."
                )

        merged = surface.overlay(current, pair.surface)
        validate(merged, "the merged config.toml")
        surface.assert_consistent(merged)

        if pair.auth is None:
            auth = None
        elif keep_api_key and (existing := self.read_auth()) is not None:
            from . import identity

            incoming = identity.parse(pair.auth)
            previous = identity.parse(existing)
            if incoming.get("OPENAI_API_KEY") is None and previous.get("OPENAI_API_KEY"):
                incoming["OPENAI_API_KEY"] = previous["OPENAI_API_KEY"]
            import json

            auth = json.dumps(incoming, indent=2).encode() + b"\n"
        else:
            auth = pair.auth

        if auth is not None:
            write_atomic(self.auth_path, auth)
        write_atomic(self.config_path, merged.encode())

    def snapshot(self, backups_root: Path) -> str:
        """Copy the current credential + full config aside; return the backup id.

        ``backups_root`` is the backups collection itself, not the store root --
        the caller owns that decision so the two never disagree.
        """
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
        folder = private_dir(backups_root / backup_id)
        auth = self.read_auth()
        write_atomic(folder / "auth.json", auth if auth is not None else b"")
        write_atomic(folder / "config.toml", self.read_config().encode())
        write_atomic(folder / "auth.present", b"1" if auth is not None else b"0")
        return backup_id


def is_windows() -> bool:
    return sys.platform.startswith("win")
