"""Held-only families have no active settings switch; no hardware or windows."""
import copy
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import HideRenameTestCase, hb, make_app
from halo.settings_model import SettingsController
from halo.settings_host import SettingsHost, _Session


HELD = {"endgame_4k", "lamzu_sw"}
USEFUL = {"endgame_we", "ajazz", "turtlebeach", "sony_inzone", "logitech_centurion", "lamzu"}


class HeldProviderControlTests(HideRenameTestCase):
    def app_and_controller(self, disabled=None):
        app = make_app({"disabled_providers": list(disabled or [])})
        app.bt_cache, app.hid_results = [], []
        controller = SettingsController(app)
        controller.context["autostart_enabled"] = lambda: False
        return app, controller

    def fallback_providers(self, app):
        app._settings_fallback = True
        preferences = next(item for item in app.build_tray_menu(None).items
                           if item.text == app.tr("menu.preferences"))
        self.assertTrue(preferences.visible)
        return next(item for item in preferences.submenu.items
                    if item.text == app.tr("preferences.providers")).submenu.items

    def test_actual_native_snapshot_excludes_only_fully_held_families(self):
        app, controller = self.app_and_controller()
        expected = set(hb.PROVIDER_LABELS) - HELD
        snapshot = controller.snapshot()
        self.assertEqual({row["name"] for row in snapshot["providers"]}, expected)
        host = SettingsHost(controller)
        wire = host._state(_Session("advanced", None))
        self.assertEqual({row["name"] for row in wire["providers"]}, expected)
        self.assertTrue(USEFUL <= expected)
        # Diagnostic/provider identity metadata remains complete.
        self.assertTrue(HELD <= hb.PROVIDER_LABELS.keys())
        self.assertTrue(HELD <= {provider.name for provider in hb.make_providers()})

    def test_actual_preferences_fallback_excludes_only_fully_held_families(self):
        app, _ = self.app_and_controller()
        labels = {item.text for item in self.fallback_providers(app)}
        self.assertEqual(labels, {app.tr("provider." + name) for name in set(hb.PROVIDER_LABELS) - HELD})
        self.assertTrue({app.tr("provider." + name) for name in USEFUL} <= labels)
        self.assertTrue({app.tr("provider." + name) for name in HELD}.isdisjoint(labels))

    def test_held_setting_commands_and_direct_toggles_refuse_without_mutation(self):
        for disabled in ([], sorted(HELD)):
            app, controller = self.app_and_controller(disabled)
            before = copy.deepcopy(app.cfg)
            with mock.patch.object(app, "configure_polling") as configure:
                for name in HELD:
                    for enabled in (False, True):
                        with self.subTest(disabled=disabled, name=name, enabled=enabled), self.assertRaises(ValueError):
                            controller.set_provider(name, enabled)
                    with self.subTest(direct=name), self.assertRaises(ValueError):
                        app.toggle_provider(name)
                configure.assert_not_called()
            self.assertEqual(app.cfg, before)
            self.assertEqual(self.saved, [])
            self.assertFalse(app.wake.is_set())

    def test_saved_held_preferences_survive_views_and_other_provider_changes(self):
        original = ["lamzu_sw", "endgame_4k"]
        app, controller = self.app_and_controller(original)
        controller.snapshot()
        self.fallback_providers(app)
        self.assertEqual(app.cfg["disabled_providers"], original)
        self.assertEqual(self.saved, [])
        controller.set_provider("razer", False)
        self.assertEqual(set(app.cfg["disabled_providers"]), HELD | {"razer"})
        controller.set_provider("razer", True)
        self.assertEqual(set(app.cfg["disabled_providers"]), HELD)
        self.assertTrue(all(HELD <= set(saved["disabled_providers"]) for saved in self.saved))
