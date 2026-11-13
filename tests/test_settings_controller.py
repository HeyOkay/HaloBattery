"""Settings use cached statuses and the exact tray renderer, without hardware."""
import os
import sys
import threading
import types
import unittest
from dataclasses import replace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import HideRenameTestCase, dev, hb, make_app
from halo.settings_model import SettingsController
REAL_DEVICE_ICON = hb.DeviceIcon


class SettingsControllerTests(HideRenameTestCase):
    def setup_controller(self, cfg=None, status=None):
        app = make_app(cfg)
        app.bt_wake, app.update_wake, app.theme_evt = (threading.Event() for _ in range(3))
        app.bt_cache, app.hid_results = [], []
        app.anim_tick = 3
        app.apply([status or dev()])
        self.controller = SettingsController(app)
        return app, self.controller

    def test_snapshot_is_detached_and_hidden_devices_remain_editable(self):
        app, controller = self.setup_controller()
        key = dev().key
        with mock.patch.object(hb, "autostart_enabled", return_value=False):
            controller.context["autostart_enabled"] = hb.autostart_enabled
            before = controller.snapshot()
            before["cfg"]["interval"] = 999
            self.assertEqual(app.cfg["interval"], 60)
            controller.set_device(key, "hidden", True)
            hidden = controller.snapshot()["devices"][0]
        self.assertTrue(hidden["hidden"])
        controller.set_device(key, "name", "Work mouse")
        self.assertEqual(app.cfg["hidden"][key], "Work mouse")
        controller.set_device(key, "reset_name")
        self.assertNotIn(key, app.cfg["names"])
        controller.set_device(key, "hidden", False)
        self.assertNotIn(key, app.cfg["hidden"])

    def test_validate_globals_and_preserve_side_effects(self):
        app, controller = self.setup_controller()
        for key, value in (("low", True), ("low", -1), ("interval", 0), ("bluetooth", "yes"),
                           ("language", "invalid"), ("icon_theme", "purple"), ("disabled_providers", [])):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                controller.set_global(key, value)
        app.bt_cache = [dev()]
        controller.set_global("bluetooth", False)
        self.assertEqual(app.bt_cache, [])
        self.assertTrue(app.bt_wake.is_set())
        controller.set_global("update_check", True)
        self.assertEqual(app.cfg["update_last"], 0)
        self.assertTrue(app.update_wake.is_set())
        app.update = types.SimpleNamespace(version="99", url="https://example.com")
        controller.set_global("update_check", False)
        self.assertIsNone(app.update)
        remove = mock.Mock()
        controller.context["remove_status_file"] = remove
        controller.set_global("status_file", False)
        remove.assert_called_once_with()

    def test_device_threshold_types_and_inheritance(self):
        app, controller = self.setup_controller()
        key = dev().key
        for value in (True, -1, 101, "20", 2.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                controller.set_device(key, "low_override", value)
        for value in (0, 100, None):
            app.alerted[key], app.low_sound_at[key] = True, 123
            controller.set_device(key, "low_override", value)
            self.assertEqual(app.device_low(key), value)
            self.assertNotIn(key, app.alerted)
            self.assertNotIn(key, app.low_sound_at)

    def test_icon_content_is_one_save_and_redraw_and_preserves_percentage_fallback(self):
        app, controller = self.setup_controller()
        for mode, percent, badges in (("percent", True, True), ("ring", False, False), ("pictogram", False, True)):
            with self.subTest(mode=mode), mock.patch.object(controller, "_save") as save, mock.patch.object(controller, "_redraw") as redraw:
                controller.set_icon_content(mode)
                self.assertEqual(app.cfg["percent_in_icon"], percent)
                self.assertEqual(app.cfg["badges"], badges)
                save.assert_called_once()
                redraw.assert_called_once()
        for mode in (None, True, [], "invalid"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                controller.set_icon_content(mode)

    def test_provider_set_is_idempotent_and_rejects_unknown_names(self):
        app, controller = self.setup_controller()
        with mock.patch.object(app, "toggle_provider", wraps=app.toggle_provider) as toggle:
            controller.set_provider("razer", True)
            toggle.assert_not_called()
            controller.set_provider("razer", False)
            controller.set_provider("razer", False)
            self.assertEqual(toggle.call_count, 1)
        with self.assertRaises(ValueError):
            controller.set_provider("invented", False)

    def test_preview_matches_shared_tray_state_at_native_eighty_pixels(self):
        cases = [dev(level=None), dev(level=0), dev(level=100),
                 replace(dev(level=15), approx="rough", charging=True),
                 replace(dev(level=None), charging=True),
                 replace(dev(), online=False, polling_paused=True)]
        for st in cases:
            with self.subTest(status=st):
                app, controller = self.setup_controller({"percent_in_icon": True, "filled_icons": True}, st)
                state = app.icon_render_state(st, size=80)
                owner = REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON)
                owner._images = {}
                expected = owner._art(state)
                if isinstance(expected, list):
                    expected = expected[app.anim_tick % len(expected)]
                actual = controller.preview(st.key)
                self.assertEqual(actual.size, (80, 80))
                self.assertEqual(actual.tobytes(), expected.tobytes())
        with self.assertRaises(ValueError):
            controller.preview(st.key, size=32)

    def test_preview_uses_controller_overrides_and_explicit_animation_frame(self):
        st = replace(dev(), kind="gamepad", charging=True)
        app, controller = self.setup_controller({"controller_brands": {st.key: "xbox"},
                                                "controller_colors": {st.key: True}, "lows": {st.key: 100}}, st)
        owner=REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON)
        owner._images={}
        expected=owner._art(app.icon_render_state(st,size=80))
        with mock.patch.object(hb.icons,"charging_frames",side_effect=AssertionError("eager preview cycle")):
            for index in (0,1,25,hb.icons.BREATH_FRAMES-1):
                self.assertEqual(controller.preview(st.key,frame=index).tobytes(),expected[index].tobytes())

    def test_no_status_preview_and_disappearance_are_safe(self):
        app, controller = self.setup_controller()
        app.icons.clear()
        self.assertEqual(controller.preview().size, (80, 80))
        controller.set_device("gone", "low_override", 20)
        self.assertEqual(app.cfg.get("lows", {}), {})


class CompactTrayTests(HideRenameTestCase):
    def test_real_menu_contains_only_quick_actions_and_no_configuration_submenus(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        app.open_settings = mock.Mock()
        menu = app.build_tray_menu(owner)
        visible = [i for i in menu.items if i.visible]
        self.assertEqual(len(visible), 6)
        self.assertFalse(any(i.submenu for i in visible))
        visible[1](None)
        app.open_settings.assert_called_once_with(owner.key, page="devices")
        app.wake.clear()
        visible[2](None)
        self.assertTrue(app.wake.is_set())

    @unittest.skipUnless(sys.platform == "win32", "Windows tray notifications")
    def test_double_click_opens_once_and_single_click_does_not_refresh(self):
        from pystray._util import win32
        original, callback = mock.Mock(), mock.Mock()
        icon = types.SimpleNamespace(_message_handlers={win32.WM_NOTIFY: original})
        hb.connect_settings_click(icon, callback)
        notify = icon._message_handlers[win32.WM_NOTIFY]
        with mock.patch.object(hb.time, "monotonic", side_effect=[1, 1.1, 2]):
            notify(0, hb.WM_LBUTTONUP)
            callback.assert_not_called()
            notify(0, hb.WM_LBUTTONUP)
            callback.assert_called_once_with()
            notify(0, hb.WM_LBUTTONUP)
        notify(0, hb.WM_RBUTTONUP)
        original.assert_called_once_with(0, hb.WM_RBUTTONUP)
        callback.reset_mock()
        notify(0, hb.WM_LBUTTONDBLCLK)
        notify(0, hb.WM_LBUTTONUP)
        callback.assert_called_once_with()

    def test_settings_dispatch_uses_one_native_companion_without_tk_host(self):
        app = make_app()
        app.settings = object()
        from halo import settings_host
        with mock.patch.object(settings_host, "SettingsHost") as manager:
            app.open_settings("mouse:1", page="devices")
            app.open_settings(page="general")
        manager.assert_called_once_with(app.settings)
        self.assertEqual(manager.return_value.open.call_args_list,
                         [mock.call("mouse:1", page="devices"), mock.call(None, page="general")])
