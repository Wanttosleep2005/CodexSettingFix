"""Checks that produce evidence, not assurances.

Every finding names the file and the line it came from. The point of ``doctor``
is that a user can look at the same bytes and reach the same conclusion -- so a
result is either reproducible from the printed path, or it is not reported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import identity, surface
from .home import Home, check_home
from .toml_doc import ConfigDoc, TomlError, validate

OK, WARN, FAIL = "ok", "warn", "fail"

_SYMBOL = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[fail]"}

#: Files left inside a Codex home by account switchers, by vendor.
FOOTPRINTS: dict[str, tuple[str, ...]] = {
    "Cockpit": (".cockpit*",),
    "CC Switch": ("cc-switch*",),
}

#: Environment variables that override a home's credential or endpoint.
SHADOWING_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "CODEX_API_KEY",
    "CODEX_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
)


@dataclass(frozen=True)
class Finding:
    level: str
    title: str
    detail: str

    def render(self) -> str:
        return f"{_SYMBOL[self.level]} {self.title}\n        {self.detail}"


def _size_of(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            pass
    return total


def footprints(home: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for vendor, patterns in FOOTPRINTS.items():
        hits: list[str] = []
        for pattern in patterns:
            hits.extend(sorted(child.name for child in home.glob(pattern)))
        if hits:
            found[vendor] = hits
    return found


def run(home_path: Path, env: dict[str, str] | None = None) -> list[Finding]:
    env = os.environ if env is None else env
    findings: list[Finding] = []

    try:
        check_home(home_path)
    except (FileNotFoundError, NotADirectoryError) as exc:
        return [Finding(FAIL, "Codex home", str(exc))]

    home = Home(home_path)
    findings.append(Finding(OK, "Codex home", str(home_path)))

    # -- config.toml -----------------------------------------------------
    config = home.read_config()
    if not config:
        findings.append(Finding(WARN, "config.toml",
                                "absent; a session would start with defaults and no surface."))
    else:
        # Checked before parsing, because a duplicated key is *why* parsing fails,
        # and naming the offender is far more useful than the parser's complaint.
        dupes = ConfigDoc(config).duplicate_root_keys()
        if dupes:
            findings.append(Finding(
                FAIL, "duplicate top-level keys",
                ", ".join(sorted(set(dupes))) + f" is defined twice in {home.config_path}",
            ))
        try:
            validate(config, str(home.config_path))
            findings.append(Finding(OK, "config.toml parses", f"{len(config.splitlines())} lines"))
        except TomlError as exc:
            findings.append(Finding(FAIL, "config.toml does not parse", str(exc)))

        provider = surface.provider_of(config)
        declared = surface.declared_providers(config)
        findings.append(Finding(
            OK, "endpoint",
            f"model_provider = {provider or '(unset, defaults to openai)'}"
            + (f"; [model_providers.*] declares {', '.join(declared)}" if declared else
               "; no [model_providers.*] tables"),
        ))
        try:
            surface.assert_consistent(config)
        except TomlError as exc:
            findings.append(Finding(FAIL, "surface is self-inconsistent", str(exc)))

    # -- credential ------------------------------------------------------
    auth = home.read_auth()
    try:
        findings.append(Finding(OK, "credential", identity.describe(auth)))
        if identity.is_mixed(auth):
            findings.append(Finding(
                WARN, "credential is mixed",
                "auth.json holds a ChatGPT login *and* an API key; a switcher blended them. "
                "Capture an official profile to get a clean copy.",
            ))
    except identity.AuthError as exc:
        findings.append(Finding(FAIL, "credential unreadable", str(exc)))

    # -- evidence of past damage ----------------------------------------
    damaged = sorted(
        child.name for child in home_path.glob("config.toml.invalid-toml*")
    )
    if damaged:
        findings.append(Finding(
            FAIL, "a previous write produced invalid TOML",
            f"{', '.join(damaged)} -- a switcher appended its block without parsing the file.",
        ))
    others = sorted(
        child.name for child in home_path.glob("auth.json.*")
        if child.name != "auth.json"
    )
    if others:
        findings.append(Finding(
            WARN, "credential copies left in the home",
            f"{', '.join(others)} -- these can hold a live relay key in clear text.",
        ))

    # -- who else writes here -------------------------------------------
    found = footprints(home_path)
    if found:
        for vendor, names in found.items():
            findings.append(Finding(
                WARN, f"{vendor} writes into this home",
                f"{len(names)} file(s), e.g. {', '.join(names[:4])}"
                + (" ..." if len(names) > 4 else ""),
            ))
    else:
        findings.append(Finding(OK, "no third-party switcher files found", "home looks clean"))

    # -- environment -----------------------------------------------------
    shadow = [name for name in SHADOWING_ENV if env.get(name)]
    if shadow:
        findings.append(Finding(
            WARN, "environment overrides the home",
            f"{', '.join(shadow)} set in this shell; they take precedence over auth.json/config.toml.",
        ))
    else:
        findings.append(Finding(OK, "no shadowing environment variables", "checked " + ", ".join(SHADOWING_ENV[:4]) + " ..."))

    export = env.get("CODEX_HOME")
    findings.append(Finding(
        OK if export else WARN, "CODEX_HOME",
        export or "unset, so Codex uses ~/.codex for everything.",
    ))

    # -- why the home cannot simply be copied ----------------------------
    heavy = sorted(Path(home_path).glob("*.sqlite"))
    if heavy:
        findings.append(Finding(
            WARN, "this home holds live session state",
            f"{len(heavy)} SQLite database(s) totalling {_size_of(home_path) / 1e6:.0f} MB. "
            "Copying it per profile would duplicate every session log, so this tool "
            "swaps only auth.json and the authentication surface instead.",
        ))

    return findings


def exit_code(findings: list[Finding]) -> int:
    if any(f.level == FAIL for f in findings):
        return 2
    if any(f.level == WARN for f in findings):
        return 1
    return 0
