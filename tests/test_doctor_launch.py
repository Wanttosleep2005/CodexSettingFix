import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from settingfix import doctor, launch

from helpers import BASE_CONFIG, OFFICIAL_CONFIG, official_blob, relay_blob, write_home


def levels(findings, needle):
    return [f.level for f in findings if needle.lower() in f.title.lower()]


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "codex"

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_home_reports_no_failures(self):
        write_home(self.home, OFFICIAL_CONFIG, official_blob())
        findings = doctor.run(self.home, env={"CODEX_HOME": str(self.home)})
        self.assertEqual([f for f in findings if f.level == doctor.FAIL], [])
        self.assertEqual(doctor.exit_code(findings), 0)

    def test_a_failure_forces_exit_code_two(self):
        write_home(self.home, 'model = "a"\nmodel = "b"\n', relay_blob())
        self.assertEqual(doctor.exit_code(doctor.run(self.home, env={})), 2)

    def test_missing_home_fails(self):
        findings = doctor.run(self.home / "nope", env={})
        self.assertEqual(findings[0].level, doctor.FAIL)

    def test_duplicate_keys_are_a_failure(self):
        write_home(self.home, 'model = "a"\nmodel = "b"\n', relay_blob())
        findings = doctor.run(self.home, env={})
        self.assertIn(doctor.FAIL, levels(findings, "duplicate"))

    def test_invalid_toml_leftover_is_reported_with_its_filename(self):
        write_home(self.home, OFFICIAL_CONFIG, official_blob())
        (self.home / "config.toml.invalid-toml.123456").write_text("x", encoding="utf-8")
        findings = doctor.run(self.home, env={})
        hit = [f for f in findings if "invalid TOML" in f.title]
        self.assertTrue(hit)
        self.assertIn("config.toml.invalid-toml.123456", hit[0].detail)

    def test_mixed_credential_is_a_warning(self):
        write_home(self.home, BASE_CONFIG, official_blob(api_key="sk-blended"))
        findings = doctor.run(self.home, env={})
        self.assertIn(doctor.WARN, levels(findings, "mixed"))

    def test_credential_copies_left_behind_are_reported(self):
        write_home(self.home, BASE_CONFIG, relay_blob())
        (self.home / "auth.json.bak").write_text("{}", encoding="utf-8")
        findings = doctor.run(self.home, env={})
        self.assertIn(doctor.WARN, levels(findings, "credential copies"))

    def test_switcher_footprints_name_the_vendor(self):
        write_home(self.home, BASE_CONFIG, relay_blob())
        for name in (".cockpit_codex_auth.json", "cc-switch-model-catalog.json"):
            (self.home / name).write_text("{}", encoding="utf-8")
        findings = doctor.run(self.home, env={})
        titles = " ".join(f.title for f in findings)
        self.assertIn("Cockpit", titles)
        self.assertIn("CC Switch", titles)

    def test_shadowing_environment_is_reported(self):
        write_home(self.home, BASE_CONFIG, relay_blob())
        findings = doctor.run(self.home, env={"OPENAI_BASE_URL": "https://evil.invalid"})
        hit = [f for f in findings if "environment" in f.title]
        self.assertTrue(hit)
        self.assertIn("OPENAI_BASE_URL", hit[0].detail)

    def test_relay_without_declaration_is_a_failure(self):
        write_home(self.home, 'model_provider = "ghost"\n', relay_blob())
        findings = doctor.run(self.home, env={})
        self.assertIn(doctor.FAIL, levels(findings, "self-inconsistent"))

    def test_exit_code_reflects_worst_finding(self):
        write_home(self.home, BASE_CONFIG, relay_blob())
        findings = doctor.run(self.home, env={})
        self.assertIn(doctor.exit_code(findings), (1, 2))


class LaunchEnvTests(unittest.TestCase):
    RELAY_SURFACE = '[model_providers.relay]\nenv_key = "RELAY_API_KEY"\n'

    def test_credential_variables_are_stripped(self):
        env = launch.clean_env({
            "OPENAI_API_KEY": "sk-inherited",
            "OPENAI_BASE_URL": "https://evil.invalid",
            "CODEX_API_KEY": "sk-inherited",
            "PATH": "/bin",
        }, self.RELAY_SURFACE)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("OPENAI_BASE_URL", env)
        self.assertNotIn("CODEX_API_KEY", env)
        self.assertEqual(env["PATH"], "/bin")

    def test_declared_env_key_survives_with_its_original_spelling(self):
        env = launch.clean_env({"relay_api_key": "keep-me"}, self.RELAY_SURFACE)
        self.assertEqual(env.get("RELAY_API_KEY"), "keep-me")

    def test_official_surface_declares_nothing(self):
        self.assertEqual(launch.declared_env_keys('model_provider = "openai"\n'), set())

    def test_env_without_a_relay_key_is_simply_absent(self):
        env = launch.clean_env({}, self.RELAY_SURFACE)
        self.assertNotIn("RELAY_API_KEY", env)

    def test_resolve_program_skips_the_shim_directory(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        first = Path(tmp.name) / "first"
        second = Path(tmp.name) / "second"
        first.mkdir()
        second.mkdir()
        target = second / "codex"
        target.write_text("#!/bin/sh\n", encoding="utf-8")
        with unittest.mock.patch.dict(os.environ, {"PATH": f"{first}{os.pathsep}{second}"}):
            self.assertEqual(launch.resolve_program("codex"), str(target))
            self.assertIsNone(launch.resolve_program("codex", exclude=second))


if __name__ == "__main__":
    unittest.main()
