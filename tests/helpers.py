"""Synthetic fixtures. No real credential ever appears in this repository."""

import base64
import json
from pathlib import Path

BASE_CONFIG = """service_tier = "default"

notify = ["node.exe", "notify.cjs"]
model = "gpt-6-luna"
model_provider = "relay"
model_reasoning_effort = "high"
cli_auth_credentials_store = "file"

[desktop]
followUpQueueMode = "queue"
conversationDetailMode = "STEPS_COMMANDS"

[plugins."pdf@openai-primary-runtime"]
enabled = true

[model_providers.relay]
name = "Example Relay"
base_url = "https://relay.invalid/v1"
wire_api = "responses"
env_key = "RELAY_API_KEY"

[projects.'g:\\\\code\\\\demo']
trust_level = "trusted"
"""

OFFICIAL_CONFIG = """service_tier = "default"

notify = ["node.exe", "notify.cjs"]
model = "gpt-6-luna"
model_provider = "openai"
model_reasoning_effort = "high"
cli_auth_credentials_store = "file"

[desktop]
followUpQueueMode = "queue"
conversationDetailMode = "STEPS_COMMANDS"

[plugins."pdf@openai-primary-runtime"]
enabled = true

[projects.'g:\\\\code\\\\demo']
trust_level = "trusted"
"""


def _jwt(subject: str) -> str:
    encode = lambda payload: base64.urlsafe_b64encode(  # noqa: E731
        json.dumps(payload).encode()
    ).decode().rstrip("=")
    return f"{encode({'alg': 'none'})}.{encode({'sub': subject})}.sig"


def relay_blob(key: str = "sk-relay-not-a-real-key") -> bytes:
    return json.dumps({"OPENAI_API_KEY": key}).encode()


def official_blob(account: str = "acct-fake-0001", subject: str = "user-fake-1",
                  api_key=None) -> bytes:
    return json.dumps({
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": api_key,
        "last_refresh": "2026-01-01T00:00:00Z",
        "tokens": {
            "access_token": _jwt(subject),
            "account_id": account,
            "id_token": _jwt(subject),
            "refresh_token": "refresh-not-a-real-token",
        },
    }, indent=2).encode() + b"\n"


def write_home(path: Path, config: str, auth: bytes | None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.toml").write_text(config, encoding="utf-8")
    if auth is None:
        (path / "auth.json").unlink(missing_ok=True)
    else:
        (path / "auth.json").write_bytes(auth)
    return path
