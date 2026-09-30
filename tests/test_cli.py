import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from settingfix import cli

from helpers import BASE_CONFIG, official_blob, relay_blob, write_home


def run(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "codex"
        self.store = self.root / "store"
        write_home(self.home, BASE_CONFIG, relay_blob())
        self.base = ["--home", str(self.home), "--store", str(self.store)]

    def tearDown(self):
        self.tmp.cleanup()

    def test_status_reports_the_relay_endpoint(self):
        code, out = run([*self.base, "status"])
        self.assertEqual(code, 0)
        self.assertIn("relay", out)

    def test_status_never_prints_a_credential(self):
        code, out = run([*self.base, "status"])
        self.assertNotIn("sk-relay-not-a-real-key", out)

    def test_doctor_flags_a_corrupted_home(self):
        (self.home / "config.toml.invalid-toml.99").write_text("x", encoding="utf-8")
        code, out = run([*self.base, "doctor"])
        self.assertEqual(code, 2)
        self.assertIn("invalid-toml", out)

    def test_capture_list_use_round_trip(self):
        self.assertEqual(run([*self.base, "capture", "relay", "--kind", "relay"])[0], 0)
        write_home(self.home, 'model_provider = "openai"\n', official_blob())
        self.assertEqual(run([*self.base, "capture", "official", "--kind", "official"])[0], 0)

        _, out = run([*self.base, "list"])
        self.assertIn("official", out)
        self.assertIn("relay", out)

        self.assertEqual(run([*self.base, "use", "official"])[0], 0)
        self.assertNotIn("model_providers.relay", (self.home / "config.toml").read_text(encoding="utf-8"))

        self.assertEqual(run([*self.base, "use", "relay"])[0], 0)
        self.assertIn("model_providers.relay", (self.home / "config.toml").read_text(encoding="utf-8"))

    def test_list_marks_the_active_profile(self):
        run([*self.base, "capture", "relay", "--kind", "relay"])
        run([*self.base, "use", "relay"])
        _, out = run([*self.base, "list"])
        self.assertIn("* relay", out)

    def test_using_an_unknown_profile_fails_cleanly(self):
        code, out = run([*self.base, "use", "nope"])
        self.assertEqual(code, 1)
        self.assertIn("nope", out)

    def test_capture_refuses_an_unsafe_name(self):
        code, out = run([*self.base, "capture", "../escape", "--kind", "relay"])
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "escape").exists())

    def test_backup_and_restore_through_the_cli(self):
        original = (self.home / "config.toml").read_text(encoding="utf-8")
        _, backup = run([*self.base, "backup", "--label", "before"])
        backup_id = backup.strip()
        run([*self.base, "capture", "official", "--kind", "official"])
        write_home(self.home, 'model_provider = "openai"\n', official_blob())
        run([*self.base, "use", "official"])
        self.assertEqual(run([*self.base, "restore", backup_id])[0], 0)
        self.assertEqual((self.home / "config.toml").read_text(encoding="utf-8"), original)

    def test_shim_install_and_uninstall(self):
        code, out = run([*self.base, "shim", "install"])
        self.assertEqual(code, 0)
        created = list((self.store / "shim").iterdir())
        self.assertEqual(len(created), 1)
        run([*self.base, "shim", "uninstall"])
        self.assertFalse((self.store / "shim").exists())

    def test_env_reports_what_gets_stripped(self):
        code, out = run([*self.base, "env"])
        self.assertEqual(code, 0)
        self.assertIn("CODEX_HOME", out)

    def test_version(self):
        with self.assertRaises(SystemExit):
            run(["--version"])


if __name__ == "__main__":
    unittest.main()
