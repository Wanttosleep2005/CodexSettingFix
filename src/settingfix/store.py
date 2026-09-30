"""Named profiles: a credential plus its matching authentication surface.

A profile is deliberately *not* a copy of a Codex home. It holds two files --
``auth.json`` and ``surface.toml`` -- and nothing else, because everything else
in a real home (session databases, plugins, marketplaces, project trust) is
identity-independent and would be wrong to duplicate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import identity, surface
from .home import Home, Pair, private_dir, write_atomic
from .toml_doc import TomlError, validate

NAME_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
KINDS = ("official", "relay")


def check_name(name: str) -> str:
    if not NAME_RE.fullmatch(name) or name.upper().split(".")[0] in _RESERVED:
        raise ValueError(
            "Profile name must be 1-48 characters of letters, digits, dot, dash or "
            "underscore, must start with a letter or digit, and must not be a "
            "reserved device name."
        )
    return name


@dataclass(frozen=True)
class Profile:
    name: str
    kind: str
    note: str
    created: str
    label: str
    root: Path

    @property
    def path(self) -> Path:
        return self.root / "profiles" / self.name


class Store:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(
            root or os.environ.get("SETTINGFIX_HOME") or Path.home() / ".settingfix"
        ).expanduser().absolute()

    # -- paths -----------------------------------------------------------

    def profile_dir(self, name: str) -> Path:
        return self.root / "profiles" / check_name(name)

    def backup_root(self) -> Path:
        return private_dir(self.root / "backups")

    # -- read ------------------------------------------------------------

    def has(self, name: str) -> bool:
        return (self.profile_dir(name) / "meta.json").is_file()

    def get(self, name: str) -> Profile:
        folder = self.profile_dir(name)
        meta_path = folder / "meta.json"
        if not meta_path.is_file():
            raise ValueError(f"No profile named {name!r}.")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return Profile(
            name=name,
            kind=meta.get("kind", "relay"),
            note=meta.get("note", ""),
            created=meta.get("created", ""),
            label=meta.get("label", ""),
            root=self.root,
        )

    def names(self) -> list[str]:
        folder = self.root / "profiles"
        if not folder.is_dir():
            return []
        return sorted(
            child.name
            for child in folder.iterdir()
            if child.is_dir() and (child / "meta.json").is_file()
        )

    def pair(self, name: str) -> Pair:
        if not self.has(name):
            raise ValueError(f"No profile named {name!r}.")
        folder = self.profile_dir(name)
        auth_path = folder / "auth.json"
        auth = auth_path.read_bytes() if auth_path.is_file() else None
        surface_text = (folder / "surface.toml").read_text(encoding="utf-8")
        return Pair(auth, surface_text)

    # -- write -----------------------------------------------------------

    def capture(self, name: str, home: Home, kind: str, note: str = "",
                official_only: bool = False, overwrite: bool = False) -> Profile:
        check_name(name)
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}.")
        folder = self.profile_dir(name)
        if folder.exists() and not overwrite:
            raise ValueError(
                f"Profile {name!r} already exists. Pick another name, or pass --overwrite."
            )

        auth = home.read_auth()
        surface_text = surface.capture(home.read_config())

        if kind == "official":
            if not identity.has_chatgpt_login(auth):
                raise ValueError(
                    "An official profile needs a ChatGPT login in auth.json. Log in "
                    "with Codex first, or capture this as a relay profile instead."
                )
            # Drop any relay key, and give the session an official-only surface so
            # no relay endpoint can survive into the official login.
            auth = identity.strip_api_key(auth)
            surface_text = surface.OFFICIAL_SURFACE
        else:
            if auth is not None:
                identity.parse(auth)
            merged = surface.overlay("", surface_text)
            surface.assert_consistent('cli_auth_credentials_store = "file"\n' + merged)
            if "cli_auth_credentials_store" not in surface_text:
                surface_text = 'cli_auth_credentials_store = "file"\n' + surface_text

        stage = private_dir(folder.parent / f".capture-{uuid.uuid4().hex}")
        write_atomic(stage / "auth.json", auth if auth is not None else b"")
        write_atomic(stage / "auth.present", b"1" if auth is not None else b"0")
        write_atomic(stage / "surface.toml", surface_text.encode())
        write_atomic(stage / "meta.json", json.dumps({
            "kind": kind,
            "note": note,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "label": identity.describe(auth),
        }, indent=2, ensure_ascii=False).encode())

        if folder.exists():
            shutil.rmtree(folder)
        os.replace(stage, folder)
        return self.get(name)

    def use(self, name: str, home: Home, *, keep_api_key: bool = False) -> str:
        """Make ``home`` present exactly as ``name`` describes. Returns a backup id."""
        pair = self.pair(name)
        profile = self.get(name)
        if profile.kind == "relay":
            surface.assert_consistent(
                'cli_auth_credentials_store = "file"\n' + surface.overlay("", pair.surface)
            )
        backup_id = home.snapshot(self.backup_root())
        home.apply(pair, keep_api_key=keep_api_key)
        return backup_id

    def backup_now(self, home: Home, label: str = "") -> str:
        backup_id = home.snapshot(self.backup_root())
        if label:
            write_atomic(self.backup_root() / backup_id / "label.txt", label.encode())
        return backup_id

    def backups(self) -> list[dict]:
        root = self.root / "backups"
        if not root.is_dir():
            return []
        out = []
        for child in sorted(root.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            label_file = child / "label.txt"
            out.append({
                "id": child.name,
                "label": label_file.read_text(encoding="utf-8") if label_file.is_file() else "",
                "auth": (child / "auth.present").read_text(encoding="utf-8").strip()
                if (child / "auth.present").is_file() else "?",
            })
        return out

    def restore(self, backup_id: str, home: Home) -> str:
        if not re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", backup_id):
            raise ValueError("Backup id must look like 20260101T000000Z-1a2b3c4d.")
        folder = self.root / "backups" / backup_id
        if not folder.is_dir():
            raise ValueError(f"No backup with id {backup_id}.")
        pre = home.snapshot(self.backup_root())
        auth_path = folder / "auth.json"
        present = (folder / "auth.present")
        config = (folder / "config.toml").read_text(encoding="utf-8")
        validate(config, "the backup's config.toml")
        if present.is_file() and present.read_text().strip() == "1":
            write_atomic(home.auth_path, auth_path.read_bytes())
        elif home.auth_path.exists():
            home.auth_path.unlink()
        write_atomic(home.config_path, config.encode())
        return pre

    def remove(self, name: str) -> None:
        folder = self.profile_dir(name)
        if not folder.is_dir():
            raise ValueError(f"No profile named {name!r}.")
        shutil.rmtree(folder)

    def diff(self, name: str, home: Home) -> str:
        """Human-readable difference between a profile and the live home."""
        pair = self.pair(name)
        lines: list[str] = []
        live_auth = home.read_auth()
        try:
            live_fp = identity.fingerprint(live_auth)
            want_fp = identity.fingerprint(pair.auth)
        except identity.AuthError as exc:
            return f"cannot compare credentials: {exc}"
        lines.append(
            "credential: " + ("same" if live_fp == want_fp else "different")
            + f" (live {identity.describe(live_auth)} / profile {identity.describe(pair.auth)})"
        )
        live_surface = surface.capture(home.read_config())
        lines.append("authentication surface: " + ("same" if live_surface == pair.surface else "different"))
        return "\n".join(lines)
