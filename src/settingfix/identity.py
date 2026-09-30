"""Which credential a ``$CODEX_HOME`` is currently logged in as.

The fingerprint is used to tell "the same login, token refreshed" apart from
"a different login". It is a comparison aid and a label only. It is never
treated as proof of identity, and the account id is never printed in full.
"""

from __future__ import annotations

import base64
import hashlib
import json

MASK = "\u2022" * 4


class AuthError(ValueError):
    pass


def parse(payload: bytes | None) -> dict:
    if payload is None:
        return {}
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeError):
        raise AuthError("auth.json is not valid JSON.") from None
    if not isinstance(data, dict):
        raise AuthError("auth.json is not a JSON object.")
    return data


def _jwt_subject(token: str) -> str:
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return str(claims.get("sub", ""))
    except (ValueError, IndexError, AttributeError, UnicodeError):
        return ""


def fingerprint(payload: bytes | None) -> tuple[str, ...]:
    """``("chatgpt", account_id, subject)`` / ``("api", digest)`` / ``("empty",)``."""
    auth = parse(payload)
    tokens = auth.get("tokens")
    if isinstance(tokens, dict) and isinstance(tokens.get("access_token"), str) and tokens["access_token"]:
        account = str(tokens.get("account_id", ""))
        subject = _jwt_subject(tokens.get("id_token", "")) or _jwt_subject(tokens["access_token"])
        if account or subject:
            return ("chatgpt", account, subject)
    key = auth.get("OPENAI_API_KEY")
    if isinstance(key, str) and key:
        return ("api", hashlib.sha256(key.encode()).hexdigest()[:16])
    return ("empty",)


def describe(payload: bytes | None) -> str:
    """A short, non-secret label: ``ChatGPT \u00b7 acct 8f2c\u2022\u2022\u2022\u2022``."""
    try:
        parts = fingerprint(payload)
    except AuthError:
        return "unreadable auth.json"
    if parts[0] == "chatgpt":
        account = parts[1] if len(parts) > 1 else ""
        short = (account[:4] + MASK) if account else "account id absent"
        return f"ChatGPT login \u00b7 {short}"
    if parts[0] == "api":
        return f"API key \u00b7 sha256 {parts[1][:8]}{MASK}"
    return "no credentials"


def has_chatgpt_login(payload: bytes | None) -> bool:
    return fingerprint(payload)[0] == "chatgpt"


def has_api_key(payload: bytes | None) -> bool:
    key = parse(payload).get("OPENAI_API_KEY")
    return isinstance(key, str) and bool(key)


def is_mixed(payload: bytes | None) -> bool:
    """A ChatGPT login that also carries an API key -- the classic relay blend."""
    return has_chatgpt_login(payload) and has_api_key(payload)


def strip_api_key(payload: bytes) -> bytes:
    """Return an official-only credential blob, preserving every token field."""
    auth = parse(payload)
    if not has_chatgpt_login(payload):
        raise AuthError("This credential has no ChatGPT login to keep.")
    clean = {key: value for key, value in auth.items() if key != "OPENAI_API_KEY"}
    clean["OPENAI_API_KEY"] = None
    clean.setdefault("auth_mode", "chatgpt")
    return json.dumps(clean, indent=2).encode() + b"\n"
