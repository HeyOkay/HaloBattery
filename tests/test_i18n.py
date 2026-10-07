"""Localization and Windows UI detection without hardware or real desktop state."""
import ctypes
from collections.abc import Mapping
import json
import os
from string import Formatter
import tempfile
import types
import unittest
from unittest import mock

import i18n
from locales import en, fr
import test_hide_rename
from test_hide_rename import FakeIcon, dev, hb, make_app
from test_flyout import FakeStyle


def find(menu, text):
    return next(item for item in menu.items if item.text == text)


def extra_language():
    """A third language with three plural forms, registered only inside tests."""
    catalog = {
        key: dict(value, few=value["other"]) if isinstance(value, dict) else value
        for key, value in en.MESSAGES.items()
    }
    catalog["menu.preferences"] = "Test preferences"
    catalog["duration.hours"] = {"one": "{count} test-hour", "few": "{count} few-hours",
                                 "other": "{count} test-hours"}
    return i18n.Language(
        "Test language", catalog,
        plural_rule=lambda count: "one" if count == 1 else "few" if 2 <= count <= 4 else "other",
        windows_primary_ids=(0x3FE,), plural_forms=("one", "few", "other"))


class CatalogTests(unittest.TestCase):
    def test_complete_keys_parameters_and_plural_forms(self):
        self.check_catalogs()

    def check_catalogs(self):
        fields = lambda text: {field for _, field, _, _ in Formatter().parse(text) if field}
        for code, language in i18n.LANGUAGES.items():
            with self.subTest(language=code):
                self.assertTrue(language.name.strip())
                self.assertEqual(set(en.MESSAGES), set(language.catalog))
                for key, reference in en.MESSAGES.items():
                    translated = language.catalog[key]
                    with self.subTest(language=code, key=key):
                        if isinstance(reference, Mapping):
                            self.assertIsInstance(translated, Mapping)
                            self.assertEqual(set(translated), set(language.plural_forms))
                            expected = fields(reference["other"])
                            self.assertTrue(all(fields(text) == expected for text in reference.values()))
                            messages = translated.values()
                        else:
                            self.assertIsInstance(translated, str)
                            expected = fields(reference)
                            messages = [translated]
                        for text in messages:
                            self.assertIsInstance(text, str)
                            self.assertTrue(text.strip())
                            self.assertEqual(text, text.strip())
                            self.assertEqual(fields(text), expected)

    def test_registered_languages_have_unambiguous_detection_and_plural_rules(self):
        primary_ids = []
        for code, language in i18n.LANGUAGES.items():
            with self.subTest(language=code):
                self.assertIn("other", language.plural_forms)
                self.assertEqual(len(language.plural_forms), len(set(language.plural_forms)))
                for count in range(201):
                    self.assertIn(language.plural_rule(count), language.plural_forms)
                for primary_id in language.windows_primary_ids:
                    self.assertIsInstance(primary_id, int)
                    self.assertGreater(primary_id, 0)
                    self.assertLessEqual(primary_id, 0x3FF)
                    primary_ids.append(primary_id)
        self.assertEqual(len(primary_ids), len(set(primary_ids)))

    def test_third_language_additional_plural_form_and_english_fallback(self):
        language = extra_language()
        with mock.patch.dict(i18n.LANGUAGES, {"test": language}):
            self.check_catalogs()
            for count, expected in ((1, "1 test-hour"), (3, "3 few-hours"), (5, "5 test-hours")):
                self.assertEqual(i18n.translate("duration.hours", language="test", count=count), expected)
            del language.catalog["duration.hours"]["few"]
            self.assertEqual(i18n.translate("duration.hours", language="test", count=3),
                             "about 3 h of use left")
            del language.catalog["duration.days"]
            self.assertEqual(i18n.translate("duration.days", language="test", count=0),
                             "about 0 days of use left")

    def test_language_and_missing_translation_fallback(self):
        self.assertEqual(i18n.translate("menu.preferences", language="not-registered"), "Preferences")
        with mock.patch.dict(fr.MESSAGES, {}, clear=True):
            self.assertEqual(i18n.translate("menu.exit", language="fr", version="1.2.3"), "Exit (v1.2.3)")
            # Fallback uses the English plural rule even for a French request.
            self.assertEqual(i18n.translate("duration.days", language="fr", count=0), "about 0 days of use left")
        with mock.patch.dict(fr.MESSAGES, {"duration.days": {}}):
            self.assertEqual(i18n.translate("duration.days", language="fr", count=0), "about 0 days of use left")

    def test_unknown_key_and_missing_parameters_are_programming_errors(self):
        with self.assertRaises(KeyError):
            i18n.translate("missing")
        with self.assertRaises(KeyError):
            i18n.translate("menu.exit")
        with self.assertRaises(ValueError):
            i18n.translate("duration.days")

    def test_plural_rules_and_estimate_boundaries(self):
        self.assertIn("1 masqué)", i18n.translate("menu.none_shown", language="fr", count=1))
        self.assertIn("2 masqués)", i18n.translate("menu.none_shown", language="fr", count=2))
        self.assertIn("0 masqué)", i18n.translate("menu.none_shown", language="fr", count=0))
        self.assertEqual(i18n.format_left(3599, language="fr"), "moins d’une heure d’utilisation restante")
        self.assertEqual(i18n.format_left(3600, language="fr"), "environ 1 heure d’utilisation restante")
        self.assertEqual(i18n.format_left(7200, language="fr"), "environ 2 heures d’utilisation restantes")
        self.assertEqual(i18n.format_left(48 * 3600, language="fr"), "environ 2 jours d’utilisation restants")
        for hours in (0, 0.5, 1, 1.5, 5.4, 47.9, 48, 72, 200):
            self.assertEqual(i18n.format_left(hours * 3600), hb.history.format_left(hours * 3600))

    def test_unicode_tooltip_buffer(self):
        self.assertEqual(i18n.tooltip("Écouteurs 🎧"), "Écouteurs 🎧")
        self.assertEqual(i18n.tooltip("é" * 200), "é" * 127)
        self.assertEqual(i18n.tooltip("x" * 126 + "🎧"), "x" * 126)
        self.assertEqual(i18n.tooltip("🎧" * 100), "🎧" * 63)


class DetectionTests(unittest.TestCase):
    def detect(self, result=None, error=None):
        api = mock.Mock(return_value=result, side_effect=error)
        win = types.SimpleNamespace(kernel32=types.SimpleNamespace(GetUserDefaultUILanguage=api))
        with mock.patch.object(i18n.sys, "platform", "win32"), mock.patch.object(ctypes, "windll", win, create=True):
            detected = i18n.detect_language()
        self.assertEqual(api.argtypes, [])
        self.assertIs(api.restype, ctypes.c_ushort)
        return detected

    def test_all_regional_french_variants(self):
        for langid in (0x040C, 0x080C, 0x0C0C, 0x100C, 0x140C, 0x180C):
            with self.subTest(langid=langid):
                self.assertEqual(self.detect(langid), "fr")

    def test_other_languages_invalid_results_and_api_failure(self):
        for langid in (0, -1, 0x1000C, None, "fr", True, 1.0):
            with self.subTest(langid=langid):
                self.assertEqual(self.detect(langid), "en")
        self.assertEqual(self.detect(error=OSError("unavailable")), "en")
        with mock.patch.object(i18n.sys, "platform", "win32"), \
                mock.patch.object(ctypes, "windll", types.SimpleNamespace(), create=True):
            self.assertEqual(i18n.detect_language(), "en")

    def test_every_registered_windows_language_and_regional_variant(self):
        for code, language in i18n.LANGUAGES.items():
            for primary_id in language.windows_primary_ids:
                for region in (1, 2, 3, 4, 63):
                    with self.subTest(language=code, primary_id=primary_id, region=region):
                        self.assertEqual(self.detect((region << 10) | primary_id), code)
        self.assertEqual(self.detect(0x0400), "en")  # unregistered primary ID 0

    def test_third_language_detection_uses_registry(self):
        with mock.patch.dict(i18n.LANGUAGES, {"test": extra_language()}):
            self.assertEqual(self.detect((1 << 10) | 0x3FE), "test")
            self.assertEqual(self.detect((4 << 10) | 0x3FE), "test")

    def test_off_windows(self):
        with mock.patch.object(i18n.sys, "platform", "linux"):
            self.assertEqual(i18n.detect_language(), "en")


class LanguageConfigTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="halo_language_")
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "config.json")
        patch = mock.patch.object(hb, "CONFIG_PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)

    def test_ordinary_load_does_not_detect_or_write(self):
        with mock.patch.object(i18n, "detect_language") as detect:
            self.assertEqual(hb.load_config()["language"], "en")
        detect.assert_not_called()
        self.assertFalse(os.path.exists(self.path))

    def test_first_start_detects_and_persists_then_never_detects_again(self):
        with mock.patch.object(i18n, "detect_language", return_value="fr") as detect:
            self.assertEqual(hb.load_config(initialize_language=True)["language"], "fr")
        detect.assert_called_once_with()
        with mock.patch.object(i18n, "detect_language", return_value="en") as detect:
            self.assertEqual(hb.load_config(initialize_language=True)["language"], "fr")
        detect.assert_not_called()
        with open(self.path, encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["language"], "fr")

    def test_old_settings_preserve_names_options_and_unknown_keys(self):
        self.write({"interval": 30, "names": {"key": 'Écouteurs 🎧 "bureau"'}, "future": True})
        with mock.patch.object(i18n, "detect_language", return_value="fr"):
            cfg = hb.load_config(initialize_language=True)
        self.assertEqual(cfg["interval"], 30)
        self.assertEqual(cfg["names"], {"key": 'Écouteurs 🎧 "bureau"'})
        self.assertTrue(cfg["future"])
        self.assertEqual(hb.load_config(), cfg)

    def test_saved_languages_and_invalid_values_never_trigger_detection(self):
        for value in (*i18n.LANGUAGES, "not-registered", "FR", None, False, 1, {}, []):
            with self.subTest(value=value):
                self.write({"language": value, "interval": 30})
                with mock.patch.object(i18n, "detect_language") as detect:
                    cfg = hb.load_config(initialize_language=True)
                detect.assert_not_called()
                valid = isinstance(value, str) and value in i18n.LANGUAGES
                self.assertEqual(cfg["language"], value if valid else "en")
                self.assertEqual(cfg["interval"], 30)

    def test_damaged_config_keeps_existing_recovery_and_english_default(self):
        with open(self.path, "w") as stream:
            stream.write("{broken")
        with mock.patch.object(i18n, "detect_language") as detect:
            self.assertEqual(hb.load_config(initialize_language=True), hb.DEFAULTS)
        detect.assert_not_called()
        self.assertTrue(os.path.exists(self.path + ".bad"))

    def test_manual_selection_persists_and_overrides_next_windows_detection(self):
        app = make_app()
        app.set_language("fr")
        with mock.patch.object(i18n, "detect_language", return_value="en") as detect:
            self.assertEqual(hb.load_config(initialize_language=True)["language"], "fr")
        detect.assert_not_called()

    def test_third_language_is_selectable_persisted_and_kept_on_restart(self):
        with mock.patch.dict(i18n.LANGUAGES, {"test": extra_language()}):
            app = make_app()
            prefs = find(app.build_menu(None), "Preferences").submenu
            menu = find(prefs, "Language / Langue").submenu
            find(menu, "Test language")(None)
            self.assertEqual(app.cfg["language"], "test")
            with mock.patch.object(i18n, "detect_language") as detect:
                self.assertEqual(hb.load_config(initialize_language=True)["language"], "test")
            detect.assert_not_called()
            prefs = find(app.build_menu(None), "Test preferences").submenu
            menu = find(prefs, "Language / Langue").submenu
            self.assertTrue(find(menu, "Test language").checked)

    def test_localized_low_sound_option_persists_without_changing_language(self):
        for code in i18n.LANGUAGES:
            with self.subTest(language=code):
                app = make_app({"language": code})
                prefs = find(app.build_menu(None), app.tr("menu.preferences")).submenu
                option = find(prefs, app.tr("preferences.low_sound"))
                self.assertFalse(option.checked)
                option(None)
                self.assertTrue(option.checked)
                self.assertEqual(app.cfg["language"], code)
                cfg = hb.load_config()
                self.assertTrue(cfg["low_sound"])
                self.assertEqual(cfg["language"], code)


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app({"language": "fr", "quiet_fullscreen": False})

    def test_exact_states_and_unmodified_names(self):
        st = dev(name='G502 "Bureau" 🎧', level=85)
        self.assertEqual(hb.describe(st), 'G502 "Bureau" 🎧: 85%')
        self.assertEqual(hb.describe(st, language="fr"), 'G502 "Bureau" 🎧 : 85 %')
        st.charging = True
        self.assertEqual(hb.device_state(st, language="fr"), "85 %, en charge")
        st.charging, st.online = False, False
        self.assertIn("dernière valeur connue", hb.device_state(st, language="fr"))
        st.level = None
        self.assertEqual(hb.device_state(st, language="fr"), "aucune liaison (éteint ou en veille)")

    def test_real_device_icon_uses_selected_language_during_a_poll(self):
        icon = test_hide_rename.MenuRefreshTests().make_icon()
        icon.app.cfg["language"] = "fr"
        st = dev(name="G502 🎧", level=85)
        icon.update(st)
        self.assertEqual(icon.icon.title, "G502 🎧 : 85 %")
        self.assertEqual(icon.icon.rebuilt, 1)

    def test_structured_coarse_states_do_not_parse_english_text(self):
        st = dev(level=50)
        st.approx = "about 50% (good)"
        st.ui_message, st.ui_params = "state.approx_grade", {"level": 50, "grade": "good"}
        self.assertEqual(hb.device_state(st), st.approx)
        self.assertEqual(hb.device_state(st, language="fr"), "environ 50 % (bonne)")
        st.ui_params["charging"] = True
        self.assertIn("en charge", hb.device_state(st, language="fr"))
        st.ui_params = {"level": 50, "grade": "good", "last_known": True}
        self.assertIn("dernière valeur connue", hb.device_state(st, language="fr"))
        st.ui_message = "state.audeze_stuck"
        st.ui_params = {}
        self.assertIn("débranchez", hb.device_state(st, language="fr"))

    def test_every_provider_and_pictogram_is_translated(self):
        for code in i18n.LANGUAGES:
            with self.subTest(language=code):
                app = make_app({"language": code})
                prefs = find(app.build_menu(None), app.tr("menu.preferences")).submenu
                labels = [item.text for item in find(prefs, app.tr("preferences.providers")).submenu]
                self.assertEqual(set(labels), {i18n.translate("provider." + key, language=code)
                                               for key in hb.PROVIDER_LABELS})
                # New providers must also have reference keys, not just translations.
                self.assertTrue(all("provider." + key in en.MESSAGES for key in hb.PROVIDER_LABELS))
                if code == "en":
                    self.assertEqual(set(labels), set(hb.PROVIDER_LABELS.values()))
        owner = types.SimpleNamespace(status=dev())
        labels = [item.text for item in find(self.app.build_menu(owner), "Icône").submenu]
        self.assertEqual(labels, ["Automatique", "Souris", "Clavier", "Casque", "Manette", "Bluetooth"])

    def test_immediate_switch_all_icons_placeholder_and_both_directions(self):
        app = self.app
        app.history.seconds_left = lambda key, level: 7200
        menus = []
        for index in range(2):
            icon = types.SimpleNamespace(update_menu=lambda: menus.append("refresh"))
            app.icons[str(index)] = types.SimpleNamespace(status=dev(key=str(index)), icon=icon)
        app.placeholder = types.SimpleNamespace(update_menu=lambda: menus.append("idle"))
        with mock.patch.object(hb, "save_config") as save:
            app.set_language("fr")
            self.assertIn("heures d’utilisation restantes", app.icons["0"].icon.title)
            self.assertIn("aucun périphérique", app.placeholder.title)
            self.assertEqual(len(menus), 3)
            self.assertFalse(app.wake.is_set())
            save.assert_called_once_with(app.cfg)
            app.set_language("en")
        self.assertIn("h of use left", app.icons["1"].icon.title)
        self.assertEqual(find(app.icons["0"].icon.menu, "Preferences").text, "Preferences")

    def test_language_menu_selects_and_marks_saved_language(self):
        prefs = find(self.app.build_menu(None), "Préférences").submenu
        languages = find(prefs, "Language / Langue").submenu
        self.assertEqual([item.text for item in languages],
                         [language.name for language in i18n.LANGUAGES.values()])
        self.assertTrue(find(languages, "Français").checked)
        with mock.patch.object(hb, "save_config"):
            find(languages, "English")(None)
        self.assertEqual(self.app.cfg["language"], "en")
        self.assertTrue(find(languages, "English").checked)

    def test_status_and_diagnostics_remain_english(self):
        st = dev(level=85)
        self.app.history.seconds_left = lambda key, level: 7200
        french = self.app.status_data([st])["devices"]
        self.app.cfg["language"] = "en"
        self.assertEqual(french, self.app.status_data([st])["devices"])
        self.assertEqual(french[0]["text"], "G502 LIGHTSPEED: 85%, about 2 h of use left")
        st.approx, st.ui_message, st.ui_params = "about 50%", "state.approx", {"level": 50}
        self.app.cfg["language"] = "fr"
        self.assertEqual(self.app.status_data([st])["devices"][0]["approx"], "about 50%")
        self.assertEqual(hb.describe(st), "G502 LIGHTSPEED: about 50%")

    def test_full_diagnostic_report_is_language_independent(self):
        app = self.app
        app.cfg["bluetooth"] = False
        app.providers, app.win_events = [], None
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(hb, "DIAG_PATH", os.path.join(folder, "diagnostics.txt")), \
                mock.patch.object(hb, "LOG_PATH", os.path.join(folder, "empty.log")), \
                mock.patch.object(hb, "dump_hid", return_value=["VID=046d PID=c539"]), \
                mock.patch.object(hb.icons, "theme_report", return_value=["Windows theme: dark"]), \
                mock.patch.object(hb, "fullscreen_app_running", return_value=False), \
                mock.patch.object(hb.time, "strftime", return_value="2026-10-01 12:00:00"), \
                mock.patch.object(hb.os, "startfile", create=True):
            french = app.write_diag([dev(level=85)])
            app.cfg["language"] = "en"
            self.assertEqual(french, app.write_diag([dev(level=85)]))
        self.assertIn("=== Poll result ===\nG502 LIGHTSPEED: 85%", french)

    def test_french_header_and_long_names_wrap_at_dpi_scales(self):
        owner = types.SimpleNamespace(status=dev(name="Écouteurs 🎧 " * 20))
        for scale in (1, 1.25, 1.5, 2):
            rows = hb.flyout.build_rows(self.app.build_menu(owner))
            measure = lambda text: round(7 * len(text) * scale)
            width, height = hb.flyout.layout(rows, FakeStyle(scale), measure, measure)
            self.assertGreater(len(rows[0].lines), 1)
            self.assertGreater(height, 0)
            for text, _ in rows[0].lines:
                self.assertLessEqual(measure(text), hb.flyout.header_text_width(width, FakeStyle(scale)))


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app({"language": "fr", "quiet_fullscreen": False})
        self.notes = []
        self.icon = types.SimpleNamespace(notify=lambda text, title: self.notes.append((text, title)))

    def test_immediate_texts_and_titles(self):
        self.app.notify(self.icon, "key", "", "", kind="low", params={"name": "G502", "level": 15, "approx": False})
        self.app.notify(self.icon, "key", "", "", kind="full", params={"name": "G502"})
        self.app.notify(self.icon, "", "", "", kind="update", params={"version": "2.0.0"})
        self.assertEqual(self.notes[0], ("G502 : 15 % restants. Pensez à recharger.", "Batterie faible"))
        self.assertEqual(self.notes[1], ("La batterie de G502 est pleine.", "Batterie pleine"))
        self.assertIn("Télécharger la v2.0.0", self.notes[2][0])
        self.assertEqual(self.notes[2][1], "Mise à jour de Halo Battery")

    def test_held_notification_uses_current_language_and_stable_kind(self):
        app = self.app
        app.placeholder = self.icon
        with mock.patch.object(app, "quiet", return_value=True):
            app.notify(self.icon, "key", "English low", "Low battery", kind="low",
                       params={"name": "G502", "level": 15, "approx": False})
            app.cfg["language"] = "en"
            app.notify(self.icon, "key", "English low", "Low battery", kind="low",
                       params={"name": "G502", "level": 12, "approx": False})
        self.assertEqual(len(app.held), 1)
        app.cfg["language"] = "fr"
        app.flush_held()
        self.assertEqual(self.notes, [("G502 : 12 % restants. Pensez à recharger.", "Batterie faible")])

    def test_held_low_alert_is_dropped_after_charge_even_if_language_changed(self):
        app = self.app
        with mock.patch.object(app, "quiet", return_value=True):
            app.notify(self.icon, "key", "", "", kind="low", params={"name": "G502", "level": 15, "approx": False})
        st = dev(key="key", level=100)
        st.charging = True
        app.icons["key"] = types.SimpleNamespace(status=st, icon=self.icon)
        app.cfg["language"] = "en"
        app.flush_held()
        self.assertEqual(self.notes, [])

    def test_held_full_and_update_notifications_are_translated_on_delivery(self):
        app = self.app
        app.placeholder = self.icon
        app.cfg["language"] = "en"
        with mock.patch.object(app, "quiet", return_value=True):
            app.notify(self.icon, "key", "", "", kind="full", params={"name": "G502"})
            app.notify(self.icon, "", "", "", kind="update", params={"version": "2.0.0"})
        app.cfg["language"] = "fr"
        app.flush_held()
        self.assertEqual(len(self.notes), 2)
        self.assertEqual(self.notes[0][1], "Batterie pleine")
        self.assertEqual(self.notes[1][1], "Mise à jour de Halo Battery")

    def test_real_alert_behavior_is_preserved_in_french(self):
        with mock.patch.object(hb, "DeviceIcon", FakeIcon):
            self.app.apply([dev(level=10)])
            self.app.apply([dev(level=9)])
        self.assertEqual(self.app.notes, ["G502 LIGHTSPEED : 10 % restants. Pensez à recharger."])

    def test_rename_passes_localized_text_safely_through_environment(self):
        with mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.object(hb.subprocess, "run", return_value=types.SimpleNamespace(stdout="Écouteurs 🎧".encode())) as run:
            self.assertEqual(hb.ask_name('G502 "bureau"', language="fr"), "Écouteurs 🎧")
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["HALO_BATTERY_CANCEL"], "Annuler")
        self.assertEqual(env["HALO_BATTERY_PROMPT"], "Nouveau nom du périphérique :")
        command = " ".join(run.call_args.args[0])
        self.assertNotIn("bureau", command)
        self.assertNotIn("Annuler", command)


class AsianLanguageTests(unittest.TestCase):
    def test_single_plural_form_for_all_counts(self):
        for language in ("zh-TW", "zh-CN", "ja"):
            for count in (0, 1, 2, 5, 100):
                with self.subTest(language=language, count=count):
                    self.assertEqual(i18n.LANGUAGES[language].plural_rule(count), "other")
                    self.assertIn(str(count), i18n.translate("duration.hours", language=language, count=count))

    def test_localized_menus_and_independent_languages(self):
        for language, preferences, refresh in (("zh-TW", "偏好設定", "立即更新"),
                                                ("zh-CN", "首选项", "立即刷新"),
                                                ("ja", "設定", "今すぐ取得")):
            with self.subTest(language=language):
                app = make_app({"language": language})
                menu = app.build_menu(None)
                find(menu, refresh)
                submenu = find(menu, preferences).submenu
                options = find(submenu, app.tr("preferences.language")).submenu.items
                self.assertEqual([item.text for item in options if item.checked],
                                 [i18n.LANGUAGES[language].name])
                self.assertEqual(app.tr("rename.cancel"), "キャンセル" if language == "ja" else "取消")
        self.assertEqual(i18n.translate("menu.preferences"), "Preferences")

    def test_localized_notifications_and_states(self):
        for language in ("zh-TW", "zh-CN", "ja"):
            with self.subTest(language=language):
                self.assertIn("Mouse", i18n.translate("notification.low", language=language, name="Mouse", level=15))
                self.assertIn("15", i18n.translate("notification.low", language=language, name="Mouse", level=15))
                self.assertEqual(i18n.translate("state.charging", language=language, state="50%"),
                                 {"zh-TW": "50%，充電中", "zh-CN": "50%，充电中", "ja": "50%、充電中"}[language])
                self.assertEqual(i18n.translate("provider.8bitdo", language=language),
                                 "8BitDo コントローラー" if language == "ja" else "8BitDo 控制器")

    def test_exact_chinese_and_japanese_detection(self):
        detector = DetectionTests()
        for langid in (0x0404, 0x0C04, 0x1404, 0x7C04):
            with self.subTest(langid=langid):
                self.assertEqual(detector.detect(langid), "zh-TW")
        for langid in (0x0804, 0x1004, 0x0004, 0x7804):
            with self.subTest(langid=langid):
                self.assertEqual(detector.detect(langid), "zh-CN")
        self.assertEqual(detector.detect(0x0411), "ja")

    def test_exact_windows_ids_are_unambiguous(self):
        owners = {}
        for code, language in i18n.LANGUAGES.items():
            for langid in language.windows_language_ids:
                self.assertNotIn(langid, owners)
                owners[langid] = code
                for other_code, other in i18n.LANGUAGES.items():
                    if code != other_code:
                        self.assertNotIn(langid & 0x3FF, other.windows_primary_ids)


if __name__ == "__main__":
    unittest.main()
