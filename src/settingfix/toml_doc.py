"""Byte-preserving, line-oriented editor for a Codex ``config.toml``.

Why this is not a normal TOML round-trip
---------------------------------------
``$CODEX_HOME/config.toml`` is co-owned. The Codex desktop app owns ``[desktop]``,
``[plugins.*]``, ``[marketplaces.*]``, ``[mcp_servers.*]``, ``[projects.*]`` and
about twenty other keys; three different account switchers own the
identity-dependent keys. Re-serialising the document would drop comments,
reorder tables and silently rewrite keys this tool does not understand.

So we never re-emit the document. We locate the exact line spans of the keys we
are responsible for, and splice raw text in and out. Every other byte of the
file stays identical, and the edit is checkable against the original.
"""

from __future__ import annotations

import re
import tomllib

HEADER_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*(?:#.*)?$")
KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_\-]+|\"[^\"]*\"|'[^']*')\s*=")


class TomlError(ValueError):
    """Raised when a document is not valid TOML, or a key is ambiguous."""


def validate(text: str, where: str = "config.toml") -> None:
    """Raise :class:`TomlError` if ``text`` is not loadable TOML.

    Duplicate keys -- the failure mode this whole project exists to prevent --
    surface here, because the TOML spec forbids redefining a key.
    """
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise TomlError(f"{where} is not valid TOML: {exc}") from None


def header_name(line: str) -> str | None:
    match = HEADER_RE.match(line)
    return match.group("name").strip() if match else None


def bare(name: str) -> str:
    """``"relay"`` -> ``relay``; leaves bare names untouched."""
    if len(name) >= 2 and name[0] == name[-1] and name[0] in "\"'":
        return name[1:-1]
    return name


def _value_end(lines: list[str], start: int) -> int:
    """Index just past the value that begins on ``lines[start]``.

    Tracks bracket depth outside of strings so a multi-line array value such as
    ``notify = [ ... ]`` is captured whole. Depth is carried across lines, so a
    closing bracket on a later line ends the span even though that line opened
    nothing itself.
    """
    depth = 0
    for index in range(start, len(lines)):
        quote: str | None = None
        escaped = False
        for char in lines[index]:
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\" and quote == '"':
                    escaped = True
                elif char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
            elif char == "#":
                break
            elif char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
        if depth <= 0:
            return index + 1
    return len(lines)


class ConfigDoc:
    """A ``config.toml`` held as lines, with span-based surgery."""

    def __init__(self, text: str) -> None:
        self._lines = text.splitlines(keepends=True)
        self._reindex()

    def _reindex(self) -> None:
        """Recompute header positions. Every mutation must call this.

        Cached line numbers go stale the moment a line is removed, and a stale
        ``root_end`` silently inserts top-level keys *inside* the last table --
        which turns ``model_provider`` into ``desktop.model_provider`` and makes
        Codex ignore it. That is the exact class of corruption this module
        exists to prevent, so the index is rebuilt after every edit.
        """
        self._headers = [
            (index, name)
            for index, line in enumerate(self._lines)
            if (name := header_name(line)) is not None
        ]

    # -- inspection ------------------------------------------------------

    @property
    def root_end(self) -> int:
        """Index one past the last top-level key before the first ``[table]``."""
        return self._headers[0][0] if self._headers else len(self._lines)

    def root_key_span(self, key: str) -> tuple[int, int] | None:
        """Span of the *first* definition of ``key`` in the root region.

        Later duplicates are deliberately left in place: callers report them via
        :meth:`duplicate_root_keys` rather than silently choosing a winner.
        """
        index = 0
        while index < self.root_end:
            match = KEY_RE.match(self._lines[index])
            if match and bare(match.group("key")) == key:
                return (index, _value_end(self._lines, index))
            index += 1
        return None

    def root_raw(self, key: str) -> str | None:
        span = self.root_key_span(key)
        if span is None:
            return None
        return "".join(self._lines[span[0] : span[1]])

    def table_group_span(self, *names: str) -> tuple[int, int] | None:
        """Span covering every table whose name is ``names`` or starts with one."""
        prefixes = tuple(name + "." for name in names)

        def belongs(name: str) -> bool:
            stripped = bare(name.split(".")[0])
            return stripped in names or name.startswith(prefixes)

        inside = False
        start = end = 0
        for index, name in self._headers:
            if belongs(name):
                if not inside:
                    start, inside = index, True
                end = len(self._lines)
                continue
            if inside:
                end = index
                break
        if not inside:
            return None
        return (start, end)

    def table_raw(self, *names: str) -> str:
        span = self.table_group_span(*names)
        return "" if span is None else "".join(self._lines[span[0] : span[1]])

    def defined_root_keys(self) -> list[str]:
        """Every top-level key name, in order, including duplicates."""
        found = []
        index = 0
        while index < self.root_end:
            match = KEY_RE.match(self._lines[index])
            if match:
                found.append(bare(match.group("key")))
                index = _value_end(self._lines, index)
            else:
                index += 1
        return found

    def duplicate_root_keys(self) -> list[str]:
        seen: set[str] = set()
        dupes: list[str] = []
        for key in self.defined_root_keys():
            if key in seen and key not in dupes:
                dupes.append(key)
            seen.add(key)
        return dupes

    # -- surgery ---------------------------------------------------------

    def drop_root_key(self, key: str) -> None:
        span = self.root_key_span(key)
        if span is None:
            return
        start, end = span
        if end < len(self._lines) and self._lines[end].strip() == "" and start > 0:
            end += 1
        del self._lines[start:end]
        self._reindex()

    def set_root_raw(self, key: str, raw: str) -> None:
        """Replace ``key`` in place, or insert it at the end of the root region."""
        raw = raw.rstrip("\n") + "\n"
        span = self.root_key_span(key)
        if span is not None:
            self._lines[span[0] : span[1]] = raw.splitlines(keepends=True)
        else:
            at = self.root_end
            block = raw.splitlines(keepends=True)
            if at > 0 and self._lines[at - 1].strip() != "":
                block = ["\n", *block]
            if at < len(self._lines) and self._lines[at].strip() != "":
                block = [*block, "\n"]
            self._lines[at:at] = block
        self._reindex()

    def drop_tables(self, *names: str) -> None:
        span = self.table_group_span(*names)
        if span is not None:
            del self._lines[span[0] : span[1]]
            self._reindex()

    def append_raw(self, raw: str) -> None:
        raw = raw.strip("\n")
        if not raw:
            return
        if self._lines and self._lines[-1].strip() != "":
            self._lines.append("\n")
        self._lines.append(raw + "\n")
        self._reindex()

    def text(self) -> str:
        return "".join(self._lines)


def only_defined_once(text: str, keys: tuple[str, ...]) -> None:
    """Raise if any of ``keys`` is defined more than once at top level."""
    dupes = [key for key in ConfigDoc(text).duplicate_root_keys() if key in keys]
    if dupes:
        raise TomlError(
            "config.toml defines " + ", ".join(sorted(set(dupes))) + " more than once; "
            "a switcher appended its block without parsing the file."
        )
