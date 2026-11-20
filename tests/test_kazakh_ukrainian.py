"""Kazakh and Ukrainian language selection and grammar, without live Windows APIs."""
import ctypes
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from halo import i18n
from halo.locales import kk, uk


class KazakhUkrainianTests(unittest.TestCase):
    def test_native_names_and_explicit_catalogs(self):
        for code, name, catalog in (("kk", "Қазақша", kk.MESSAGES),
                                    ("uk", "Українська", uk.MESSAGES)):
            with self.subTest(language=code):
                self.assertEqual(i18n.LANGUAGES[code].name, name)
                self.assertIs(i18n.LANGUAGES[code].catalog, catalog)
                self.assertEqual(set(catalog), set(i18n.LANGUAGES["en"].catalog))
        self.assertEqual(i18n.translate("preferences.language", language="kk"), "Тіл")
        self.assertEqual(i18n.translate("preferences.language", language="uk"), "Мова")

    def test_windows_ui_language_detection(self):
        for langid, code in ((0x043F, "kk"), (0x0422, "uk")):
            with self.subTest(langid=langid):
                api = Mock(return_value=langid)
                win = SimpleNamespace(kernel32=SimpleNamespace(GetUserDefaultUILanguage=api))
                with patch.object(i18n.sys, "platform", "win32"), \
                     patch.object(ctypes, "windll", win, create=True):
                    self.assertEqual(i18n.detect_language(), code)

    def test_ukrainian_plural_boundaries(self):
        expected = {0: "many", 1: "one", 2: "few", 4: "few", 5: "many", 11: "many",
                    12: "many", 14: "many", 20: "many", 21: "one", 22: "few", 25: "many",
                    101: "one", 111: "many", 112: "many", 121: "one", 122: "few"}
        rule = i18n.LANGUAGES["uk"].plural_rule
        for count, category in expected.items():
            with self.subTest(count=count):
                self.assertEqual(rule(count), category)
                for key in ("duration.hours", "duration.days", "menu.none_shown"):
                    self.assertEqual(i18n.translate(key, language="uk", count=count),
                                     uk.MESSAGES[key][category].format(count=count))
        for count in (1.5, True, None):
            self.assertEqual(rule(count), "other")
        self.assertEqual(i18n.format_left(2 * 3600, language="uk"),
                         "залишилося приблизно 2 години роботи")
        self.assertEqual(i18n.format_left(5 * 86400, language="uk"),
                         "залишилося приблизно 5 днів роботи")

    def test_kazakh_numerals_keep_units_uninflected(self):
        rule = i18n.LANGUAGES["kk"].plural_rule
        for count in (0, 1, 2, 5, 11, 21, 100):
            with self.subTest(count=count):
                self.assertEqual(rule(count), "one" if count == 1 else "other")
                self.assertEqual(i18n.translate("duration.hours", language="kk", count=count),
                                 f"шамамен {count} сағат жұмыс уақыты қалды")
                self.assertEqual(i18n.translate("duration.days", language="kk", count=count),
                                 f"шамамен {count} күн жұмыс уақыты қалды")
