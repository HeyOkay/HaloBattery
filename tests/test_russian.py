"""Russian is a selectable Windows-aware catalog with integer plural rules."""
import ctypes,types
import unittest
from unittest.mock import Mock,patch
from halo import i18n
from halo.locales import en,ru


class RussianTests(unittest.TestCase):
    def test_complete_catalog_and_display_name(self):
        self.assertEqual(set(ru.MESSAGES),set(en.MESSAGES))
        self.assertIs(i18n.LANGUAGES['ru'].catalog,ru.MESSAGES)
        self.assertEqual(i18n.LANGUAGES['ru'].name,'Русский')
        self.assertEqual(i18n.translate('preferences.language',language='ru'),'Язык')

    def test_integer_plural_forms(self):
        expected={0:'many',1:'one',2:'few',4:'few',5:'many',11:'many',12:'many',14:'many',20:'many',21:'one',22:'few',25:'many',101:'one',111:'many',112:'many',121:'one'}
        rule=i18n.LANGUAGES['ru'].plural_rule
        for count,form in expected.items():
            with self.subTest(count=count):
                self.assertEqual(rule(count),form)
                self.assertIn(str(count),i18n.translate('duration.hours',language='ru',count=count))
        self.assertEqual(rule(1.5),'other')
        self.assertEqual(i18n.format_left(2*3600,language='ru'),'осталось примерно 2 часа работы')
        self.assertEqual(i18n.format_left(5*24*3600,language='ru'),'осталось примерно 5 дней работы')

    def test_windows_russian_regional_ui_languages(self):
        for langid in (0x0419,0x0819):
            api=Mock(return_value=langid)
            win=types.SimpleNamespace(kernel32=types.SimpleNamespace(GetUserDefaultUILanguage=api))
            with patch.object(i18n.sys,'platform','win32'),patch.object(ctypes,'windll',win,create=True):
                self.assertEqual(i18n.detect_language(),'ru')

    def test_russian_tooltips_keep_unicode_limit(self):
        text=i18n.translate('device.description',language='ru',name='Наушники 🎧'*30,state='Заряжается')
        self.assertLessEqual(len(i18n.tooltip(text).encode('utf-16-le')),254)
        self.assertNotIn('\ufffd',i18n.tooltip(text))
