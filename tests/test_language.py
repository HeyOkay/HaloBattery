"""Tests for the interface language (English / Spanish). No tray, no hardware.

Only the app's own chrome is translated: device names, provider labels and the
Diagnostics report stay in English on purpose.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from test_hide_rename import (FakeTrayIcon, HideRenameTestCase, dev,  # noqa: E402
                              hb, make_app)

import lang  # noqa: E402
from providers.base import DeviceStatus  # noqa: E402

H = 3600.0
KEY = "logitech:C15E09CD"


def charging(key="k", name="V9 Pro", level=85, online=True):
    return DeviceStatus(key, name, level, True, online, "mchose_v9", kind="headset")


class GetTests(unittest.TestCase):
    def test_spanish_text(self):
        self.assertEqual(lang.get("es", "menu_refresh"), "Actualizar ahora")

    def test_unknown_language_falls_back_to_english(self):
        self.assertEqual(lang.get("fr", "menu_refresh"), "Refresh now")
        self.assertEqual(lang.get(None, "menu_refresh"), "Refresh now")
        self.assertEqual(lang.get(5, "menu_refresh"), "Refresh now")

    def test_unknown_key_falls_back_to_the_key(self):
        self.assertEqual(lang.get("es", "no_such_key"), "no_such_key")
        self.assertEqual(lang.get("en", "no_such_key"), "no_such_key")

    def test_get_never_returns_empty_for_a_real_key(self):
        for code in ("en", "es", "de", None, 5):
            for key in lang.STRINGS["en"]:
                self.assertNotEqual(lang.get(code, key), "", f"{code}/{key}")

    def test_both_languages_have_the_same_keys(self):
        self.assertEqual(set(lang.STRINGS["en"]), set(lang.STRINGS["es"]))


class DefaultLanguageTests(unittest.TestCase):
    def test_default_is_english(self):
        self.assertEqual(hb.DEFAULTS["language"], "en")

    def test_config_without_language_is_english(self):
        app = make_app()
        app.cfg.pop("language", None)              # a settings file from before the setting
        self.assertEqual(app.lang_code(), "en")
        self.assertEqual(app.build_menu(None).items[0].text, "No devices found")

    def test_unknown_language_in_config_is_english(self):
        app = make_app({"language": "zz"})
        self.assertEqual(app.lang_code(), "en")
        self.assertEqual(app.build_menu(None).items[0].text, "No devices found")


class DeviceStateTests(unittest.TestCase):
    def test_english_by_default(self):
        st = charging()
        self.assertEqual(hb.device_state(st), "85%, charging")
        self.assertEqual(hb.describe(st, "V9 Pro"), "V9 Pro: 85%, charging")

    def test_spanish_tooltip_says_cargando_and_not_charging(self):
        text = hb.describe(charging(), "V9 Pro", "", "es")
        self.assertIn("cargando", text)
        self.assertNotIn("charging", text)

    def test_spanish_asleep_and_no_link(self):
        asleep = DeviceStatus("k", "V9 Pro", 85, False, False, "mchose_v9")
        self.assertEqual(hb.device_state(asleep, "", "es"),
                         "85% (último valor conocido, dispositivo suspendido)")
        off = DeviceStatus("k", "V9 Pro", None, False, False, "mchose_v9")
        self.assertEqual(hb.device_state(off, "", "es"), "sin conexión (apagado o suspendido)")

    def test_spanish_keeps_the_time_left_fragment(self):
        st = DeviceStatus("k", "V9 Pro", 85, False, True, "mchose_v9", kind="headset")
        text = hb.describe(st, "V9 Pro", "unas 5 h de uso restantes", "es")
        self.assertTrue(text.endswith("85%, unas 5 h de uso restantes"))


class NotificationTests(unittest.TestCase):
    def test_low_battery_spanish(self):
        self.assertEqual(hb.low_battery_text("G502", 15, False, "es"),
                         "G502: 15% restante. Es hora de cargar.")
        self.assertEqual(hb.low_battery_text("Mando", None, True, "es"),
                         "Mando: batería baja. Es hora de cargar.")

    def test_fully_charged_spanish(self):
        self.assertEqual(hb.fully_charged_text("G502", "es"), "G502: carga completa.")

    def test_update_spanish(self):
        text = hb.update_text("1.2.3", "es")
        self.assertIn("La versión 1.2.3 está disponible", text)
        self.assertIn("Descargar v1.2.3…", text)

    def test_temp_autostart_spanish(self):
        self.assertIn("carpeta temporal", hb.temp_autostart_text("es"))

    def test_english_texts_are_unchanged(self):
        self.assertEqual(hb.low_battery_text("G502", 15, False), "G502: 15% left. Time to charge.")
        self.assertEqual(hb.fully_charged_text("G502"), "G502 is fully charged.")
        self.assertIn("Download v1.2.3…", hb.update_text("1.2.3"))


class TimeLeftTests(unittest.TestCase):
    def test_english_format_unchanged(self):
        self.assertEqual(hb.history.format_left(1800), "less than 1 h of use left")
        self.assertEqual(hb.history.format_left(5.4 * H), "about 5 h of use left")
        self.assertEqual(hb.history.format_left(72 * H), "about 3 days of use left")

    def test_spanish_format(self):
        self.assertEqual(hb.history.format_left_in(1800, "es"), "menos de 1 h de uso restante")
        self.assertEqual(hb.history.format_left_in(5.4 * H, "es"), "unas 5 h de uso restantes")
        self.assertEqual(hb.history.format_left_in(72 * H, "es"), "unos 3 días de uso restantes")
        self.assertEqual(hb.history.format_left_in(1.2 * H, "es"), "alrededor de 1 h de uso restante")


class MenuLanguageTests(HideRenameTestCase):
    @staticmethod
    def find(menu, text):
        return next(i for i in menu.items if i.text == text)

    def prefs(self, app):
        """The Preferences submenu, whatever the app language is."""
        top = lang.get(app.lang_code(), "menu_preferences")
        return self.find(app.build_menu(None), top).submenu

    def pick_language(self, app, label):
        """Click a language radio, as pystray does."""
        selector = self.find(self.prefs(app), "Language / Idioma").submenu
        next(i for i in selector.items if i.text == label)(FakeTrayIcon())

    def test_english_menu_by_default(self):
        app = make_app()
        menu = app.build_menu(None)
        self.assertEqual(menu.items[0].text, "No devices found")
        self.find(menu, "Refresh now")
        self.find(menu, "Preferences")

    def test_hidden_header_translated(self):
        app = make_app({"language": "es", "hidden": {"a": "Xbox Controller"}})
        self.assertEqual(app.build_menu(None).items[0].text,
                         "No se muestran dispositivos (1 oculto)")
        hidden = self.find(app.build_menu(None), "Dispositivos ocultos").submenu
        self.assertEqual([i.text for i in hidden.items], ["Mostrar Xbox Controller"])

    def test_changing_language_rebuilds_menus_without_restart(self):
        app = make_app()
        app.apply([dev()])
        ic = app.icons[KEY]
        self.find(app.build_menu(None), "Refresh now")
        self.pick_language(app, "Español")
        self.assertEqual(app.cfg["language"], "es")
        self.assertEqual(app.lang_code(), "es")
        self.assertEqual(self.saved[-1]["language"], "es")
        menu = app.build_menu(None)
        self.assertEqual(menu.items[0].text, "No se encontraron dispositivos")
        self.assertFalse([i for i in menu.items if i.text == "Refresh now"])
        self.find(menu, "Actualizar ahora")
        # the icon's own menu object was replaced too (the flyout reads it on each open)
        ic_texts = [i.text for i in ic.icon.menu.items if i.visible]
        self.assertIn("Actualizar ahora", ic_texts)
        self.assertNotIn("Refresh now", ic_texts)

    def test_switch_back_to_english(self):
        app = make_app({"language": "es"})
        self.find(app.build_menu(None), "Actualizar ahora")
        self.pick_language(app, "English")
        self.assertEqual(app.cfg["language"], "en")
        self.assertEqual(self.find(app.build_menu(None), "Refresh now").text, "Refresh now")

    def test_preferences_translated(self):
        app = make_app({"language": "es"})
        texts = [i.text for i in self.prefs(app).items if i is not hb.Menu.SEPARATOR and i.text]
        for expected in ("Intervalo de sondeo", "Aviso de batería baja", "Tiempo restante estimado",
                         "Silencio durante el juego", "Tipos de dispositivo", "Color del icono",
                         "Iniciar con Windows", "Buscar actualizaciones", "Language / Idioma"):
            self.assertIn(expected, texts)
        intervals = self.find(self.prefs(app), "Intervalo de sondeo")
        self.assertEqual([label for _value, label in intervals.choices],
                         ["15 s", "30 s", "1 min", "2 min", "5 min"])

    def test_device_menu_translated(self):
        app = make_app({"language": "es", "names": {KEY: "Ratón de trabajo"}})
        app.apply([dev()])
        menu = app.build_menu(app.icons[KEY])
        texts = [i.text for i in menu.items if i.visible]
        for expected in ("Cambiar nombre…", "Restablecer nombre", "Icono",
                         "Aviso de batería baja a", "Ocultar este dispositivo"):
            self.assertIn(expected, texts)
        labels = [i.text for i in self.find(menu, "Icono").submenu.items]
        for expected in ("Automático", "Ratón", "Teclado", "Auriculares", "Mando"):
            self.assertIn(expected, labels)


class NotTranslatedTests(unittest.TestCase):
    def test_provider_labels_stay_english(self):
        self.assertEqual(hb.PROVIDER_LABELS["logitech"], "Logitech")
        self.assertIn("Razer", hb.PROVIDER_LABELS["razer"])
        self.assertIn("MCHOSE", hb.PROVIDER_LABELS["mchose"])

    def test_diagnostics_style_describe_stays_english(self):
        # write_diag() and probe() call describe()/device_state() without a code
        st = charging()
        self.assertEqual(hb.describe(st, "V9 Pro"), "V9 Pro: 85%, charging")
        self.assertEqual(hb.device_state(st), "85%, charging")


if __name__ == "__main__":
    unittest.main()
