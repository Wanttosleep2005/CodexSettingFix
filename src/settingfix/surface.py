"""The "authentication surface": the only part of ``config.toml`` we own.

A Codex home is shared property. The desktop app and the user own model
preferences, plugins, marketplaces, MCP servers and project trust. Account
switchers own a much smaller set of keys -- the ones that decide *which
endpoint and which credential* a session uses.

This module names that small set exactly. Everything outside it is left byte
for byte alone. Getting this boundary right is the whole point: an earlier
design treated the entire home directory as swappable, which is impossible
when the home holds a 666 MB session database.
"""

from __future__ import annotations

from .toml_doc import ConfigDoc, TomlError

#: Top-level keys that decide which endpoint and credential a session uses.
ROOT_KEYS: tuple[str, ...] = (
    "model_provider",
    "model_providers",
    "cli_auth_credentials_store",
    "forced_login_method",
    "forced_chatgpt_workspace_id",
)

#: Table groups that describe relay endpoints.
TABLE_GROUPS: tuple[str, ...] = ("model_providers",)

SURFACE_KEYS = ROOT_KEYS + TABLE_GROUPS

#: Keys that turn an official session into a relay session if left behind.
RELAY_MARKERS: tuple[str, ...] = ("model_provider", "model_providers")

OFFICIAL_ROOT = 'model_provider = "openai"\n'

#: A complete, minimal official surface: official endpoint, file-backed credential.
OFFICIAL_SURFACE = 'model_provider = "openai"\ncli_auth_credentials_store = "file"\n'


def capture(text: str) -> str:
    """Extract the surface as a standalone TOML fragment (raw text, unparsed)."""
    doc = ConfigDoc(text)
    chunks: list[str] = []
    for key in ROOT_KEYS:
        raw = doc.root_raw(key)
        if raw:
            chunks.append(raw.rstrip("\n") + "\n")
    for group in TABLE_GROUPS:
        raw = doc.table_raw(group)
        if raw:
            chunks.append("\n" + raw.rstrip("\n") + "\n")
    return "".join(chunks)


def overlay(text: str, surface: str) -> str:
    """Return ``text`` with its authentication surface replaced by ``surface``.

    The identity-independent remainder of ``text`` is preserved exactly. This is
    what stops one switcher's endpoint from surviving into another's session.
    """
    doc = ConfigDoc(text)
    incoming = ConfigDoc(surface)

    for key in ROOT_KEYS:
        raw = incoming.root_raw(key)
        doc.drop_root_key(key)
        if raw:
            doc.set_root_raw(key, raw.rstrip("\n") + "\n")

    for group in TABLE_GROUPS:
        raw = incoming.table_raw(group)
        doc.drop_tables(group)
        if raw:
            doc.append_raw(raw)

    return doc.text()


def provider_of(text: str) -> str | None:
    doc = ConfigDoc(text)
    raw = doc.root_raw("model_provider")
    if not raw:
        return None
    _, _, value = raw.partition("=")
    return value.strip().strip("\"'") or None


def declared_providers(text: str) -> list[str]:
    """Ids declared under ``[model_providers.*]``."""
    from .toml_doc import bare, header_name

    found: list[str] = []
    for line in text.splitlines():
        name = header_name(line)
        if not name:
            continue
        parts = [bare(part.strip()) for part in name.split(".")]
        if len(parts) >= 2 and parts[0] == "model_providers" and parts[1] not in found:
            found.append(parts[1])
    return found


def assert_consistent(text: str) -> None:
    """Raise if the surface would make Codex fail to start.

    Catches exactly the observed failure: a session told to use a relay that has
    no matching ``[model_providers.<id>]`` block, and the non-file credential
    stores that silently move tokens out of the home we are managing.
    """
    provider = provider_of(text)
    if provider and provider != "openai":
        if provider not in declared_providers(text):
            raise TomlError(
                f'model_provider = "{provider}" but no [model_providers.{provider}] block exists.'
            )
    doc = ConfigDoc(text)
    raw = doc.root_raw("cli_auth_credentials_store")
    if raw:
        value = raw.partition("=")[2].strip().strip("\"'")
        if value != "file":
            raise TomlError(
                f'cli_auth_credentials_store = "{value}" moves credentials outside the Codex home; '
                'set it to "file" so the login stays where this tool can manage it.'
            )
