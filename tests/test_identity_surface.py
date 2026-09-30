import base64
import json
import unittest

from settingfix import identity, surface


def jwt(subject):
    encode = lambda payload: base64.urlsafe_b64encode(  # noqa: E731
        json.dumps(payload).encode()
    ).decode().rstrip("=")
    return f"{encode({'alg': 'none'})}.{encode({'sub': subject})}.sig"


def official(account="acct-1", subject="user-1", api_key=None):
    return json.dumps({
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": api_key,
        "last_refresh": "2026-01-01T00:00:00Z",
        "tokens": {
            "access_token": jwt(subject),
            "account_id": account,
            "id_token": jwt(subject),
            "refresh_token": "r-1",
        },
    }).encode()


class IdentityTests(unittest.TestCase):
    def test_chatgpt_login(self):
        self.assertEqual(identity.fingerprint(official())[0], "chatgpt")

    def test_api_key(self):
        parts = identity.fingerprint(b'{"OPENAI_API_KEY":"sk-a"}')
        self.assertEqual(parts[0], "api")
        self.assertEqual(len(parts[1]), 16)

    def test_empty(self):
        self.assertEqual(identity.fingerprint(b"{}"), ("empty",))
        self.assertEqual(identity.fingerprint(None), ("empty",))

    def test_same_account_survives_token_refresh(self):
        first = identity.fingerprint(official(subject="user-1"))
        second = identity.fingerprint(official(subject="user-1"))
        self.assertEqual(first, second)

    def test_different_account_is_detected(self):
        self.assertNotEqual(
            identity.fingerprint(official(account="a")),
            identity.fingerprint(official(account="b")),
        )

    def test_mixed_credential_is_flagged(self):
        self.assertTrue(identity.is_mixed(official(api_key="sk-blended")))
        self.assertFalse(identity.is_mixed(official()))

    def test_strip_api_key_keeps_every_token_field(self):
        cleaned = json.loads(identity.strip_api_key(official(api_key="sk-blended")))
        self.assertIsNone(cleaned["OPENAI_API_KEY"])
        self.assertEqual(cleaned["tokens"]["refresh_token"], "r-1")
        self.assertEqual(cleaned["auth_mode"], "chatgpt")

    def test_strip_api_key_refuses_when_there_is_no_login(self):
        with self.assertRaises(identity.AuthError):
            identity.strip_api_key(b'{"OPENAI_API_KEY":"sk-only"}')

    def test_invalid_json_is_rejected(self):
        with self.assertRaises(identity.AuthError):
            identity.fingerprint(b"not json")

    def test_describe_never_prints_a_full_account_id(self):
        self.assertNotIn("acct-1", identity.describe(official()))


BASE = """service_tier = "default"
notify = ["node.exe", "notify.cjs"]
model = "gpt-6-luna"
model_provider = "relay"
cli_auth_credentials_store = "file"

[desktop]
followUpQueueMode = "queue"

[model_providers.relay]
base_url = "https://relay.invalid/v1"
env_key = "RELAY_API_KEY"

[projects.'g:\\\\d']
trust_level = "trusted"
"""

OFFICIAL = 'model_provider = "openai"\ncli_auth_credentials_store = "file"\n'


class SurfaceTests(unittest.TestCase):
    def test_capture_round_trips(self):
        fragment = surface.capture(BASE)
        self.assertIn("model_provider", fragment)
        self.assertIn("model_providers.relay", fragment)
        self.assertNotIn("service_tier", fragment)
        self.assertNotIn("[desktop]", fragment)

    def test_overlay_to_official_removes_the_relay(self):
        out = surface.overlay(BASE, OFFICIAL)
        self.assertNotIn("model_providers", out)
        self.assertNotIn("relay.invalid", out)
        self.assertIn('model_provider = "openai"', out)

    def test_overlay_preserves_everything_outside_the_surface(self):
        out = surface.overlay(BASE, OFFICIAL)
        for line in ("service_tier = \"default\"", 'model = "gpt-6-luna"',
                     "[desktop]", "followUpQueueMode = \"queue\"",
                     "trust_level = \"trusted\"", 'notify = ["node.exe", "notify.cjs"]'):
            self.assertIn(line, out)

    def test_overlay_back_to_relay_restores_the_block(self):
        round_trip = surface.overlay(surface.overlay(BASE, OFFICIAL), surface.capture(BASE))
        self.assertIn("[model_providers.relay]", round_trip)
        self.assertIn('base_url = "https://relay.invalid/v1"', round_trip)

    def test_provider_and_declared(self):
        self.assertEqual(surface.provider_of(BASE), "relay")
        self.assertEqual(surface.declared_providers(BASE), ["relay"])
        self.assertEqual(surface.provider_of("model = \"x\"\n"), None)

    def test_consistency_rejects_relay_without_a_declaration(self):
        with self.assertRaises(Exception):
            surface.assert_consistent('model_provider = "ghost"\n')

    def test_consistency_rejects_non_file_credential_store(self):
        with self.assertRaises(Exception):
            surface.assert_consistent(
                'model_provider = "openai"\ncli_auth_credentials_store = "keyring"\n'
            )

    def test_consistency_accepts_the_official_surface(self):
        surface.assert_consistent(OFFICIAL)


if __name__ == "__main__":
    unittest.main()
