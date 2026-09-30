"""Starting a program against a chosen Codex home, with a clean environment.

Two different leaks have to be closed at launch time:

* inherited ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` style variables, which
  outrank ``auth.json`` and quietly redirect a session; and
* the variables a relay legitimately declares via ``env_key``, which must
  survive so the relay keeps working.

The rule is: strip the known credential/routing variables, then put back only
the ones the surface in effect actually names.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .doctor import SHADOWING_ENV
from .home import Home

DEFAULT_ENV_KEY = "OPENAI_API_KEY"


def declared_env_keys(surface_text: str) -> set[str]:
    """Every ``env_key`` value named by the surface's ``model_providers`` tables."""
    found: set[str] = set()
    for line in surface_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("env_key"):
            continue
        _, _, value = stripped.partition("=")
        name = value.strip().strip("\"'")
        if name:
            found.add(name)
    return found


def clean_env(base: dict[str, str] | None, surface_text: str) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for name in SHADOWING_ENV:
        env.pop(name, None)
    for name in declared_env_keys(surface_text):
        for source in (base or os.environ).keys():
            if source.lower() == name.lower():
                env[name] = (base or os.environ)[source]
    return env


def resolve_program(program: str, *, exclude: Path | None = None) -> str | None:
    """Find ``program`` on PATH, skipping ``exclude`` (a shim must not recurse)."""
    if os.path.dirname(program) or os.sep in program or (os.altsep and os.altsep in program):
        return program if Path(program).exists() else None
    entries = [Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if exclude is not None:
        skip = exclude.resolve()
        entries = [entry for entry in entries if _safe_resolve(entry) != skip]
    names = [program]
    if sys.platform.startswith("win"):
        for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";"):
            names.append(program + ext.lower())
            names.append(program + ext.upper())
    for entry in entries:
        for name in names:
            candidate = entry / name
            if candidate.is_file():
                return str(candidate)
    return None


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def launch(argv: list[str], home: Home, *, exclude: Path | None = None,
           base_env: dict[str, str] | None = None) -> int:
    if not argv:
        raise ValueError("No program given.")
    program, args = argv[0], argv[1:]
    target = resolve_program(program, exclude=exclude)
    if target is None:
        raise FileNotFoundError(f"Cannot find {program!r} on PATH.")

    env = clean_env(base_env, home.read_config())
    env["CODEX_HOME"] = str(home.path)

    if sys.platform.startswith("win") and Path(target).suffix.lower() in (".bat", ".cmd"):
        # A batch file must go through cmd.exe; never build a command string.
        completed = subprocess.run(["cmd.exe", "/d", "/s", "/c", target, *args], env=env)
    else:
        completed = subprocess.run([target, *args], env=env)
    return completed.returncode


def find_own_dir() -> Path | None:
    exe = shutil.which(sys.argv[0]) if sys.argv and sys.argv[0] else None
    return Path(exe).parent if exe else None
