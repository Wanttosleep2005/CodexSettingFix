import unittest

from settingfix.toml_doc import ConfigDoc, TomlError, validate

DESKTOP = """service_tier = "default"

notify = ["node.exe", "notify.cjs"]
model = "gpt-6-luna"

[desktop]
followUpQueueMode = "queue"

[projects.'g:\\\\code\\\\demo']
trust_level = "trusted"
"""


class ValidateTests(unittest.TestCase):
    def test_accepts_valid(self):
        validate(DESKTOP)

    def test_rejects_duplicate_top_level_key(self):
        with self.assertRaises(TomlError):
            validate('model = "a"\nmodel = "b"\n')

    def test_rejects_unclosed_table(self):
        with self.assertRaises(TomlError):
            validate("[desktop\nx = 1\n")


class RootKeyTests(unittest.TestCase):
    def test_reports_duplicates(self):
        doc = ConfigDoc('model = "a"\nmodel = "b"\n')
        self.assertEqual(doc.duplicate_root_keys(), ["model"])

    def test_no_duplicates_reported_for_distinct_keys(self):
        doc = ConfigDoc('model = "a"\nnotify = []\n[desktop]\nmodel = "b"\n')
        self.assertEqual(doc.duplicate_root_keys(), [])

    def test_reads_multiline_array_value(self):
        doc = ConfigDoc(DESKTOP)
        self.assertEqual(doc.root_raw("notify"), 'notify = ["node.exe", "notify.cjs"]\n')

    def test_array_value_span_ignores_brackets_inside_strings(self):
        text = 'notify = [\n  "node.exe",\n  "a]b",\n]\nmodel = "x"\n'
        doc = ConfigDoc(text)
        self.assertEqual(doc.root_raw("notify"), text.split("model")[0])

    def test_root_region_stops_at_first_header(self):
        doc = ConfigDoc(DESKTOP)
        self.assertIsNone(doc.root_raw("followUpQueueMode"))


class InsertionTests(unittest.TestCase):
    def test_new_root_key_lands_before_first_table(self):
        """Regression: a stale header index used to insert keys *inside* the table.

        ``[desktop]`` then swallowed ``model_provider``, making it
        ``desktop.model_provider``, which Codex silently ignores.
        """
        doc = ConfigDoc(DESKTOP)
        doc.drop_root_key("model")
        doc.set_root_raw("model_provider", 'model_provider = "relay"\n')
        out = doc.text()
        self.assertLess(out.index("model_provider"), out.index("[desktop]"))
        validate(out)
        self.assertEqual(ConfigDoc(out).root_raw("model_provider"), 'model_provider = "relay"\n')

    def test_replacing_keeps_file_position(self):
        doc = ConfigDoc(DESKTOP)
        doc.set_root_raw("model", 'model = "other"\n')
        out = doc.text()
        self.assertIn('model = "other"\n', out)
        self.assertEqual(out.count("model ="), 1)
        validate(out)

    def test_drop_removes_only_the_named_key(self):
        doc = ConfigDoc(DESKTOP)
        doc.drop_root_key("model")
        self.assertNotIn("gpt-6-luna", doc.text())
        self.assertIn("service_tier", doc.text())
        validate(doc.text())


class TableTests(unittest.TestCase):
    TEXT = (
        'model_provider = "relay"\n'
        "\n"
        "[model_providers.relay]\n"
        'base_url = "https://x.invalid"\n'
        "\n"
        "[model_providers.second]\n"
        'base_url = "https://y.invalid"\n'
        "\n"
        "[projects.'g:\\\\d']\n"
        'trust_level = "trusted"\n'
    )

    def test_group_span_covers_every_matching_table(self):
        raw = ConfigDoc(self.TEXT).table_raw("model_providers")
        self.assertIn("model_providers.relay", raw)
        self.assertIn("model_providers.second", raw)
        self.assertNotIn("projects", raw)

    def test_drop_tables_leaves_later_tables_intact(self):
        doc = ConfigDoc(self.TEXT)
        doc.drop_tables("model_providers")
        out = doc.text()
        self.assertNotIn("model_providers", out)
        self.assertIn("trust_level", out)
        validate(out)

    def test_append_keeps_document_valid(self):
        doc = ConfigDoc(DESKTOP)
        doc.append_raw('[model_providers.relay]\nbase_url = "https://x.invalid"\n')
        validate(doc.text())


if __name__ == "__main__":
    unittest.main()
