"""Settings labels stay complete and format safely in every shipped language."""
from string import Formatter
import unittest

from halo import i18n
from halo.locales import en, es, fr, ja, zh_CN, zh_TW


class SettingsCatalogTests(unittest.TestCase):
    def test_settings_catalogs_have_identical_nonempty_labels(self):
        expected = {key for key in en.MESSAGES if key.startswith("settings.")}
        self.assertIn("settings.title", expected)
        self.assertIn("settings.no_devices", expected)
        self.assertIn("settings.quiet_hint", expected)
        self.assertIn("settings.close", expected)
        for code, module in (("en", en), ("es", es), ("fr", fr), ("ja", ja),
                             ("zh-CN", zh_CN), ("zh-TW", zh_TW)):
            with self.subTest(language=code):
                actual = {key for key in module.MESSAGES if key.startswith("settings.")}
                self.assertEqual(expected, actual)
                self.assertIs(i18n.LANGUAGES[code].catalog, module.MESSAGES)
                for key in expected:
                    with self.subTest(key=key):
                        value = module.MESSAGES[key]
                        self.assertIsInstance(value, str)
                        self.assertTrue(value.strip())
                        self.assertEqual(value, value.strip())

    def test_settings_placeholders_render_in_every_language(self):
        values = {"level": "25%", "error": "Example failure", "version": "1.2.3"}
        expected_fields = {
            "settings.default_low": {"level"},
            "settings.error": {"error"},
            "settings.version": {"version"},
        }
        for code, module in (("en", en), ("es", es), ("fr", fr), ("ja", ja),
                             ("zh-CN", zh_CN), ("zh-TW", zh_TW)):
            for key, value in module.MESSAGES.items():
                if not key.startswith("settings."):
                    continue
                with self.subTest(language=code, key=key):
                    fields = {field for _, field, _, _ in Formatter().parse(value)
                              if field is not None}
                    self.assertEqual(fields, expected_fields.get(key, set()))
                    rendered = i18n.translate(key, language=code, **values)
                    self.assertEqual(rendered, value.format(**values))
                    for field in fields:
                        self.assertIn(values[field], rendered)


if __name__ == "__main__":
    unittest.main()
