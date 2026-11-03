"""Edge cases for halo/i18n: lookups that miss, unusual counts, tooltip truncation.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from halo import history, i18n  # noqa: E402
from halo.locales import en  # noqa: E402

LIMIT = 127                      # UTF-16 code units a tray tooltip may hold (128 with the NUL)
EMOJI = "\U0001F3A7"             # one character, two UTF-16 code units
CJK = "電"                   # one character, one code unit


def units(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


class TranslateTests(unittest.TestCase):
    def test_unknown_language_falls_back_to_english(self):
        for language in ("xx", "", "EN", "fr-FR", "zh", None):
            with self.subTest(language=language):
                self.assertEqual(i18n.translate("state.level", language=language, level=5), "5%")
                self.assertEqual(i18n.translate("menu.preferences", language=language),
                                 en.MESSAGES["menu.preferences"])

    def test_missing_key_raises_key_error_in_every_language(self):
        for code in list(i18n.LANGUAGES) + ["xx"]:
            with self.subTest(language=code), self.assertRaises(KeyError):
                i18n.translate("no.such.key", language=code)

    def test_missing_parameter_raises_key_error(self):
        for code in i18n.LANGUAGES:
            with self.subTest(language=code), self.assertRaises(KeyError):
                i18n.translate("device.description", language=code, name="x")   # no {state}

    def test_extra_parameters_are_ignored(self):
        self.assertEqual(i18n.translate("state.level", level=5, unused="x", other=1), "5%")

    def test_plural_message_needs_a_count(self):
        for code in i18n.LANGUAGES:
            with self.subTest(language=code), self.assertRaises(ValueError):
                i18n.translate("duration.hours", language=code)

    def test_plural_counts_render_in_every_language(self):
        for code in i18n.LANGUAGES:
            for count in (0, 1, 2, 5, 11, 100, 1000000, -1):
                with self.subTest(language=code, count=count):
                    text = i18n.translate("duration.days", language=code, count=count)
                    self.assertTrue(text.strip())
                    self.assertNotRegex(text, r"[{}]")
                    self.assertIn(str(count), text)

    def test_english_singular_and_plural(self):
        self.assertEqual(i18n.translate("duration.days", count=1), "about 1 day of use left")
        self.assertEqual(i18n.translate("duration.days", count=0), "about 0 days of use left")
        self.assertEqual(i18n.translate("duration.days", count=2), "about 2 days of use left")

    def test_braces_and_percent_in_parameter_values_are_data(self):
        for value in ("{state}", "{0}", "%s", "100%", "}{", "{{x}}"):
            with self.subTest(value=value):
                self.assertEqual(i18n.translate("device.description", name=value, state="ok"),
                                 f"{value}: ok")


class FormatLeftTests(unittest.TestCase):
    CASES = (0, -1, -3600.0, 0.5, 59.9, 3599.9, 3600, 3601, 5400, 47 * 3600, 47.6 * 3600, 48 * 3600,
             100 * 3600, 10 ** 9, 5e9)

    def test_every_language_renders_every_duration(self):
        for code in i18n.LANGUAGES:
            for seconds in self.CASES:
                with self.subTest(language=code, seconds=seconds):
                    text = i18n.format_left(seconds, language=code)
                    self.assertTrue(text.strip())
                    self.assertNotRegex(text, r"[{}]")

    def test_english_thresholds(self):
        for seconds, expected in ((0, "no use left"), (-5, "no use left"),
                                  (1, "less than 1 h of use left"), (3599, "less than 1 h of use left"),
                                  (3600, "about 1 h of use left"), (47 * 3600, "about 47 h of use left"),
                                  (47.6 * 3600, "about 48 h of use left"),
                                  (48 * 3600, "about 2 days of use left"),
                                  (24 * 3600 * 40, "about 40 days of use left")):
            with self.subTest(seconds=seconds):
                self.assertEqual(i18n.format_left(seconds), expected)

    def test_history_helper_agrees_with_the_english_catalog(self):
        for seconds in self.CASES:
            with self.subTest(seconds=seconds):
                self.assertEqual(history.format_left(seconds), i18n.format_left(seconds))


class TooltipTests(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        for text in ("", "a", "G502: 80%", "日本語", EMOJI, "x" * LIMIT):
            with self.subTest(text=text[:10]):
                self.assertEqual(i18n.tooltip(text), text)

    def test_truncates_to_the_buffer_whatever_the_script(self):
        for name, unit in (("ascii", "a"), ("cjk", CJK), ("accents", "é"), ("emoji", EMOJI),
                           ("mixed", "a" + CJK + EMOJI)):
            for repeat in (60, 63, 64, 65, 200):
                with self.subTest(script=name, repeat=repeat):
                    text = unit * repeat
                    out = i18n.tooltip(text)
                    self.assertLessEqual(units(out), LIMIT)
                    self.assertTrue(text.startswith(out))
                    # it keeps as much as fits: at most one character's worth is lost
                    self.assertGreaterEqual(units(out), min(units(text), LIMIT) - 1)
                    out.encode("utf-16-le")                   # no lone surrogate left over

    def test_surrogate_pair_is_not_split_at_the_boundary(self):
        self.assertEqual(i18n.tooltip("a" * 126 + EMOJI), "a" * 126)   # would need units 127-128
        self.assertEqual(i18n.tooltip("a" * 125 + EMOJI), "a" * 125 + EMOJI)   # exactly 127 units
        self.assertEqual(i18n.tooltip(EMOJI * 100), EMOJI * 63)

    def test_cjk_counts_one_unit_per_character(self):
        self.assertEqual(i18n.tooltip(CJK * 200), CJK * LIMIT)

    def test_a_lone_surrogate_in_a_device_name_does_not_raise(self):
        # hidapi hands back what Windows has; a broken pair must not kill the icon update
        for text in ("a\ud800b", "\udc00", "x" * 200 + "\ud83c"):
            with self.subTest(text=text[:5]):
                out = i18n.tooltip(text)
                self.assertLessEqual(units(out), LIMIT)


class CatalogPlaceholderTests(unittest.TestCase):
    """Each translation keeps exactly the {placeholders} of the English message."""

    FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)[^{}]*\}")

    def fields(self, message):
        texts = message.values() if isinstance(message, dict) else [message]
        found = [frozenset(self.FIELD.findall(t)) for t in texts]
        self.assertEqual(len(set(found)), 1, message)       # one set for all plural forms
        return found[0]

    def test_same_keys_and_placeholders_as_english(self):
        for code, language in i18n.LANGUAGES.items():
            with self.subTest(language=code):
                self.assertEqual(set(language.catalog), set(en.MESSAGES))
                for key, reference in en.MESSAGES.items():
                    self.assertEqual(self.fields(language.catalog[key]), self.fields(reference),
                                     f"{code}:{key}")

    def test_no_translation_is_blank(self):
        for code, language in i18n.LANGUAGES.items():
            for key, message in language.catalog.items():
                texts = message.values() if isinstance(message, dict) else [message]
                for text in texts:
                    self.assertTrue(text.strip(), f"{code}:{key}")


if __name__ == "__main__":
    unittest.main()
