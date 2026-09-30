import json
import tempfile
import unittest
from pathlib import Path

from settingfix import store as store_module
from settingfix.home import Home, Pair, resolve_home
from settingfix.toml_doc import TomlError

from helpers import BASE_CONFIG, OFFICIAL_CONFIG, official_blob, relay_blob, write_home


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home_path = self.root / "codex"
        write_home(self.home_path, BASE_CONFIG, relay_blob())
        self.home = Home(self.home_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_apply_validates_before_writing_anything(self):
        before = self.home.config_path.read_bytes()
        broken = Pair(relay_blob(), 'model_provider = "ghost"\n')
        with self.assertRaises(TomlError):
            self.home.apply(broken)
        self.assertEqual(self.home.config_path.read_bytes(), before)

    def test_apply_refuses_a_config_that_already_has_duplicate_keys(self):
        self.home.config_path.write_text('model = "a"\nmodel = "b"\n', encoding="utf-8")
        with self.assertRaises(TomlError):
            self.home.apply(Pair(relay_blob(), 'model_provider = "openai"\n'))

    def test_apply_rejects_a_non_file_credential_store(self):
        with self.assertRaises(TomlError):
            self.home.apply(Pair(relay_blob(), 'cli_auth_credentials_store = "keyring"\n'))

    def test_apply_writes_both_files(self):
        self.home.apply(Pair(official_blob(), store_module.surface.OFFICIAL_SURFACE))
        self.assertIn('model_provider = "openai"', self.home.config_path.read_text())
        self.assertNotIn("model_providers", self.home.config_path.read_text())

    def test_snapshot_and_restore_round_trip(self):
        store_root = self.root / "store"
        original_config = self.home.config_path.read_text()
        original_auth = self.home.auth_path.read_bytes()
        home = Home(self.home_path)
        store = store_module.Store(store_root)
        backup = home.snapshot(store.backup_root())
        home.apply(Pair(official_blob(), store_module.surface.OFFICIAL_SURFACE))
        self.assertNotEqual(self.home.config_path.read_text(), original_config)
        store.restore(backup, home)
        self.assertEqual(self.home.config_path.read_text(), original_config)
        self.assertEqual(self.home.auth_path.read_bytes(), original_auth)

    def test_restore_rejects_a_malformed_id(self):
        store = store_module.Store(self.root / "store")
        with self.assertRaises(ValueError):
            store.restore("../../etc/passwd", self.home)

    def test_resolve_home_precedence(self):
        self.assertEqual(resolve_home(str(self.root / "x")), (self.root / "x"))
        import os

        previous = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.root / "env")
        try:
            self.assertEqual(resolve_home(None), self.root / "env")
            self.assertEqual(resolve_home(str(self.root / "win")), self.root / "win")
        finally:
            if previous is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = previous


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home_path = self.root / "codex"
        write_home(self.home_path, BASE_CONFIG, relay_blob())
        self.home = Home(self.home_path)
        self.store = store_module.Store(self.root / "store")

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_relay_keeps_the_relay_surface(self):
        profile = self.store.capture("relay", self.home, "relay")
        self.assertEqual(profile.kind, "relay")
        fragment = (profile.path / "surface.toml").read_text(encoding="utf-8")
        self.assertIn("model_providers.relay", fragment)
        self.assertIn("model_provider", fragment)

    def test_capture_official_strips_the_api_key_and_the_relay(self):
        write_home(self.home_path, BASE_CONFIG, official_blob(api_key="sk-blended"))
        profile = self.store.capture("official", self.home, "official")
        blob = json.loads((profile.path / "auth.json").read_bytes())
        self.assertIsNone(blob["OPENAI_API_KEY"])
        fragment = (profile.path / "surface.toml").read_text(encoding="utf-8")
        self.assertIn('model_provider = "openai"', fragment)
        self.assertNotIn("relay.invalid", fragment)

    def test_capture_official_refuses_without_a_login(self):
        with self.assertRaises(ValueError):
            self.store.capture("official", self.home, "official")

    def test_capture_refuses_to_overwrite_silently(self):
        self.store.capture("relay", self.home, "relay")
        with self.assertRaises(ValueError):
            self.store.capture("relay", self.home, "relay")
        self.store.capture("relay", self.home, "relay", overwrite=True)

    def test_name_validation(self):
        for bad in ("../escape", "a" * 60, "-leading", "CON", "a/b", ""):
            with self.assertRaises(ValueError, msg=bad):
                store_module.check_name(bad)

    def test_capture_rejects_traversal_before_touching_disk(self):
        with self.assertRaises(ValueError):
            self.store.capture("../escape", self.home, "relay")
        self.assertFalse((self.root / "escape").exists())

    def test_use_switches_to_official_and_drops_the_relay(self):
        self.store.capture("relay", self.home, "relay")
        write_home(self.home_path, OFFICIAL_CONFIG, official_blob())
        self.store.capture("official", self.home, "official")
        self.store.use("official", self.home)
        text = self.home.config_path.read_text(encoding="utf-8")
        self.assertNotIn("model_providers.relay", text)
        self.assertIn('model_provider = "openai"', text)
        self.assertIn("[desktop]", text)

    def test_use_relay_restores_the_block_after_being_official(self):
        self.store.capture("relay", self.home, "relay")
        write_home(self.home_path, OFFICIAL_CONFIG, official_blob())
        self.store.capture("official", self.home, "official")
        self.store.use("official", self.home)
        self.store.use("relay", self.home)
        text = self.home.config_path.read_text(encoding="utf-8")
        self.assertIn("[model_providers.relay]", text)
        self.assertIn("relay.invalid", text)

    def test_use_returns_a_restorable_backup_id(self):
        self.store.capture("relay", self.home, "relay")
        write_home(self.home_path, OFFICIAL_CONFIG, official_blob())
        self.store.capture("official", self.home, "official")
        write_home(self.home_path, BASE_CONFIG, relay_blob())
        original = self.home.config_path.read_text(encoding="utf-8")
        backup = self.store.use("official", self.home)
        self.store.restore(backup, self.home)
        self.assertEqual(self.home.config_path.read_text(encoding="utf-8"), original)

    def test_diff_reports_a_difference(self):
        self.store.capture("relay", self.home, "relay")
        write_home(self.home_path, OFFICIAL_CONFIG, official_blob())
        self.store.capture("official", self.home, "official")
        write_home(self.home_path, BASE_CONFIG, relay_blob())
        report = self.store.diff("official", self.home)
        self.assertIn("different", report)
        self.assertIn("API key", report)

    def test_using_an_unknown_profile_raises(self):
        with self.assertRaises(ValueError):
            self.store.use("ghost", self.home)

    def test_remove(self):
        self.store.capture("relay", self.home, "relay")
        self.store.remove("relay")
        self.assertEqual(self.store.names(), [])


if __name__ == "__main__":
    unittest.main()
