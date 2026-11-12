"""Manual device icon choices, without hardware or native windows."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox
_sandbox.install_guards()
from test_hide_rename import HideRenameTestCase, hb, make_app
from test_controller_logos import REAL_SAVE_CONFIG, pad
from halo import i18n


class DeviceIconChoiceTests(HideRenameTestCase):
    def test_manual_choices_round_trip_export_presentation_without_changing_device_kind(self):
        for choice in ("speaker", "webcam", "health", "smart_home", "watch", "display", "tracker"):
            with self.subTest(choice=choice):
                app = make_app()
                st = pad()
                app.apply([st])
                owner = app.icons[st.key]
                path = os.path.join(_sandbox.mkdtemp(prefix="presentation_"), "config.json")
                with mock.patch.object(hb, "CONFIG_PATH", path), \
                        mock.patch.object(hb, "save_config", REAL_SAVE_CONFIG):
                    app.set_pictogram(owner, choice)
                    restarted = make_app(hb.load_config())
                self.assertEqual(restarted.pictogram(st), choice)
                self.assertEqual(st.kind, "gamepad")
                self.assertEqual(restarted.status_data([st])["devices"][0]["kind"], choice)
                self.assertEqual(app.cfg["icons"], {st.key: choice})

    def test_explicit_kinds_are_automatic_without_name_guessing(self):
        app = make_app()
        for kind in ("speaker", "webcam", "health", "smart_home", "watch", "display", "tracker"):
            st = pad(kind=kind, name="Unrelated microphone name")
            self.assertEqual(hb.badge_for(st), kind)
            self.assertEqual(app.pictogram(st), kind)
            self.assertEqual(app.status_data([st])["devices"][0]["kind"], kind)
        self.assertEqual(hb.badge_for(pad(kind="", name="Speaker Webcam Health device Smart home Smartwatch Display TV Tag Tracker")), "gamepad")

    def test_controller_preferences_survive_manual_choice_and_automatic_reset(self):
        app = make_app({"controller_brands": {"pad:1": "xbox"},
                        "controller_colors": {"pad:1": True}})
        st = pad()
        app.apply([st])
        owner = app.icons[st.key]
        for choice in ("speaker", "webcam", "health", "smart_home", "watch", "display", "tracker"):
            app.set_pictogram(owner, choice)
            self.assertEqual(app.controller_style(st), ("default", False))
            menu = next(i for i in app.build_menu(owner).items if i.text == "Controller logo")
            self.assertFalse(menu.visible)
            self.assertEqual(app.cfg["controller_brands"], {st.key: "xbox"})
            self.assertEqual(app.cfg["controller_colors"], {st.key: True})
        app.set_pictogram(owner, "")
        self.assertEqual(app.pictogram(st), "gamepad")
        self.assertEqual(app.controller_style(st), ("xbox", True))

    def test_wrong_type_config_values_fall_back(self):
        for value in (None, True, 42, [], {}, ["speaker"]):
            app = make_app({"icons": {"pad:1": value}})
            self.assertEqual(app.pictogram(pad()), "gamepad")
            self.assertEqual(app.status_data([pad()])["devices"][0]["kind"], "gamepad")
        for value in (None, True, 42, [], "webcam"):
            self.assertEqual(make_app({"icons": value}).pictogram(pad()), "gamepad")

    def test_all_choices_are_translated_and_checked_in_every_locale(self):
        for language in i18n.LANGUAGES:
            for choice in ("speaker", "webcam", "health", "smart_home", "watch", "display", "tracker"):
                with self.subTest(language=language, choice=choice):
                    app = make_app({"language": language, "icons": {"pad:1": choice}})
                    st = pad()
                    app.apply([st])
                    menu = next(i for i in app.build_menu(app.icons[st.key]).items
                                if i.text == app.tr("menu.icon")).submenu
                    label = app.tr("icon." + choice)
                    self.assertNotEqual(label, "icon." + choice)
                    self.assertEqual([i.text for i in menu.items if i.checked], [label])

    def test_aliases_and_invalid_manual_values_are_not_saved(self):
        app = make_app()
        st = pad()
        app.apply([st])
        owner = app.icons[st.key]
        for choice in ("ps4", "H", "not-an-icon", None, [], True):
            with self.subTest(choice=choice):
                app.set_pictogram(owner, choice)
                self.assertFalse(self.saved)
                self.assertEqual(app.pictogram(st), "gamepad")
        self.assertEqual(app.cfg["icons"], {})

    def test_legacy_manual_pictograms_keep_export_contract(self):
        for choice in ("mouse", "keyboard", "microphone", "headset", "gamepad",
                       "bluetooth", "dualshock", "dualsense"):
            app = make_app({"icons": {"pad:1": choice}})
            st = pad()
            self.assertEqual(app.status_data([st])["devices"][0]["kind"], choice)
            self.assertEqual(st.kind, "gamepad")
