"""Spanish integration with the shared renderer; no tray or hardware access."""
import ctypes
import json
import os
import tempfile
import types
import unittest
from unittest import mock

from halo import i18n
from halo.locales import es
from test_hide_rename import dev, hb, make_app
from test_i18n import find


class SpanishTests(unittest.TestCase):
    def test_windows_spanish_regional_variants(self):
        # Spain (both sorts), Mexico, Argentina, Chile and the US, among others.
        api = mock.Mock()
        win = types.SimpleNamespace(kernel32=types.SimpleNamespace(GetUserDefaultUILanguage=api))
        with mock.patch.object(i18n.sys, "platform", "win32"), \
                mock.patch.object(ctypes, "windll", win, create=True):
            for langid in (0x000A, 0x040A, 0x080A, 0x0C0A, 0x280A, 0x2C0A, 0x340A, 0x540A):
                with self.subTest(langid=langid):
                    api.return_value = langid
                    self.assertEqual(i18n.detect_language(), "es")

    def test_initial_detection_and_saved_manual_choice(self):
        for old_config in (None, {"interval": 30, "names": {"k": "Ratón 🎧"}, "future": True}):
            with self.subTest(old_config=old_config), tempfile.TemporaryDirectory() as folder:
                path = os.path.join(folder, "config.json")
                if old_config is not None:
                    with open(path, "w", encoding="utf-8") as stream:
                        json.dump(old_config, stream, ensure_ascii=False)
                with mock.patch.object(hb, "CONFIG_PATH", path), \
                        mock.patch.object(i18n, "detect_language", return_value="es") as detect:
                    cfg = hb.load_config(initialize_language=True)
                    self.assertEqual(cfg["language"], "es")
                    detect.assert_called_once_with()
                    with open(path, encoding="utf-8") as stream:
                        self.assertEqual(json.load(stream)["language"], "es")
                    if old_config is not None:
                        for key, value in old_config.items():
                            self.assertEqual(cfg[key], value)
                    app = make_app(cfg)
                    prefs = find(app.build_menu(None), "Preferencias").submenu
                    languages = find(prefs, "Idioma").submenu
                    self.assertTrue(find(languages, "Español").checked)
                    find(languages, "English")(None)
                    detect.reset_mock()
                    self.assertEqual(hb.load_config(initialize_language=True)["language"], "en")
                    detect.assert_not_called()

    def test_spanish_plurals_and_estimate_boundaries(self):
        for count, ending in ((0, "0 ocultos)"), (1, "1 oculto)"), (2, "2 ocultos)")):
            self.assertTrue(i18n.translate("menu.none_shown", language="es", count=count).endswith(ending))
        for seconds, expected in ((-1, "sin autonomía restante"), (0, "sin autonomía restante"),
                                  (3599, "menos de 1 h de uso restante"),
                                  (3600, "alrededor de 1 h de uso restante"),
                                  (7200, "unas 2 h de uso restantes"),
                                  (48 * 3600, "unos 2 días de uso restantes")):
            with self.subTest(seconds=seconds):
                self.assertEqual(i18n.format_left(seconds, language="es"), expected)
        self.assertEqual(i18n.translate("duration.days", language="es", count=1),
                         "alrededor de 1 día de uso restante")

    def test_missing_spanish_message_or_plural_form_uses_english(self):
        with mock.patch.dict(es.MESSAGES, {}, clear=True):
            self.assertEqual(i18n.translate("menu.preferences", language="es"), "Preferences")
            self.assertEqual(i18n.format_left(0, language="es"), "no use left")
        with mock.patch.dict(es.MESSAGES, {"duration.days": {"other": "unos {count} días"}}):
            self.assertEqual(i18n.translate("duration.days", language="es", count=1),
                             "about 1 day of use left")

    def test_switch_updates_multiple_icons_and_placeholder_without_polling(self):
        app = make_app()
        app.history.seconds_left = lambda key, level: 7200
        refreshed = []
        for index in range(2):
            status = dev(key=str(index), name="Ratón 🎧 " + str(index), level=85)
            icon = types.SimpleNamespace(update_menu=lambda: refreshed.append("device"))
            app.icons[status.key] = types.SimpleNamespace(status=status, icon=icon)
        app.placeholder = types.SimpleNamespace(update_menu=lambda: refreshed.append("idle"))
        with mock.patch.object(hb, "save_config"):
            prefs = find(app.build_menu(None), "Preferences").submenu
            languages = find(prefs, "Language").submenu
            find(languages, "Español")(None)
            self.assertEqual(app.icons["0"].icon.title, "Ratón 🎧 0: 85%, unas 2 h de uso restantes")
            self.assertEqual(app.icons["1"].icon.title, "Ratón 🎧 1: 85%, unas 2 h de uso restantes")
            self.assertEqual(app.placeholder.title, "Halo Battery: no se encontraron dispositivos")
            self.assertEqual(refreshed, ["device", "device", "idle"])
            self.assertFalse(app.wake.is_set())
            self.assertEqual(find(app.icons["0"].icon.menu,
                                  "Configuración del dispositivo...").text,
                             "Configuración del dispositivo...")
            self.assertEqual(find(app.icons["1"].icon.menu, "Configuración...").text,
                             "Configuración...")
            self.assertEqual(find(app.placeholder.menu, "Configuración...").text,
                             "Configuración...")
            app.set_language("en")
        self.assertEqual(app.icons["1"].icon.title, "Ratón 🎧 1: 85%, about 2 h of use left")

    def test_deferred_notifications_use_spanish_at_delivery(self):
        app = make_app({"quiet_fullscreen": False})
        notes = []
        icon = types.SimpleNamespace(notify=lambda text, title: notes.append((text, title)))
        app.placeholder = icon
        with mock.patch.object(app, "quiet", return_value=True):
            app.notify(icon, "k", "", "", kind="low",
                       params={"name": "Ratón 🎧", "level": 15, "approx": False})
            app.notify(icon, "other", "", "", kind="full", params={"name": "G502"})
            app.notify(icon, "", "", "", kind="update", params={"version": "99.0.0"})
        app.cfg["language"] = "es"
        app.flush_held()
        self.assertIn(("Ratón 🎧: 15% restante. Es hora de cargar.", "Batería baja"), notes)
        self.assertIn(("G502: carga completa.", "Carga completa"), notes)
        self.assertTrue(any("Descargar v99.0.0" in text and title == "Actualización de Halo Battery"
                            for text, title in notes))
        self.assertFalse(app.held)

    def test_spanish_states_preserve_english_technical_output_and_names(self):
        app = make_app({"language": "es"})
        app.history.seconds_left = lambda key, level: 7200
        status = dev(name='G502 "Trabajo" 🎧', level=85)
        status.charging = True
        self.assertEqual(hb.describe(status, language="es"), 'G502 "Trabajo" 🎧: 85%, cargando')
        self.assertEqual(app.status_data([status])["devices"][0]["text"],
                         'G502 "Trabajo" 🎧: 85%, charging')
        status.approx = "about 50% (medium), over Bluetooth"
        status.ui_message = "state.approx_grade"
        status.ui_params = {"level": 50, "grade": "medium", "bluetooth": True}
        self.assertEqual(hb.device_state(status, language="es"), "aproximadamente 50% (medio), por Bluetooth")
        self.assertEqual(hb.device_state(status), status.approx)
        with mock.patch.object(hb.time, "time", return_value=1234.0):
            spanish = app.status_data([status])
            app.cfg["language"] = "en"
            self.assertEqual(spanish, app.status_data([status]))

    def test_spanish_whats_new_dates_and_untranslated_published_prose(self):
        self.assertEqual(hb.whatsnew.subtitle("1.14.0", "2026-10-13", language="es"),
                         "Actualizado desde la v1.14.0  ·  13 de octubre de 2026")
        self.assertEqual(hb.whatsnew.nice_date("2026-01-02", language="es"), "2 de enero de 2026")
        self.assertEqual(hb.whatsnew.localized_blocks("### Added\n- Mouse support.\n", language="es"),
                         [("heading", "Añadido"), ("item", "Mouse support.")])


if __name__ == "__main__":
    unittest.main()
