"""Global pictogram style: persistence, menus and cached device redraws."""
import json
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import HideRenameTestCase, dev, hb, make_app
from providers.base import DeviceStatus

RealDeviceIcon = hb.DeviceIcon


class IconStyleSettingTests(HideRenameTestCase):
    def style_menu(self, app):
        menu = app.build_menu(None)
        preferences = next(i.submenu for i in menu.items if i.text == "Preferences")
        return next(i.submenu for i in preferences.items if i.text == "Icon style")

    def test_outline_default_and_menu_checked(self):
        app = make_app()
        self.assertIs(app.cfg["filled_icons"], False)
        self.assertEqual([i.text for i in self.style_menu(app).items if i.checked], ["Outline"])
        self.style_menu(app).items[1](None)
        self.assertEqual([i.text for i in self.style_menu(app).items if i.checked], ["Filled"])
        self.style_menu(app).items[0](None)
        self.assertIs(app.cfg["filled_icons"], False)

    def test_saves_and_redraws_all_devices_immediately(self):
        app = make_app()
        app.apply([dev(), dev(key="second", name="Second mouse")])
        before = [len(owner.titles) for owner in app.icons.values()]
        with mock.patch.object(app, "refresh_menus") as refresh:
            app.set_icon_style(True)
        self.assertEqual([len(owner.titles) for owner in app.icons.values()],
                         [count + 1 for count in before])
        refresh.assert_called_once_with()
        self.assertIs(self.saved[-1]["filled_icons"], True)
        with mock.patch("builtins.open", mock.mock_open(read_data=json.dumps(self.saved[-1]))):
            self.assertIs(hb.load_config()["filled_icons"], True)

    def test_one_native_failure_does_not_block_other_devices_or_menus(self):
        app = make_app()
        app.apply([dev(), dev(key="second", name="Second mouse")])
        first, second = app.icons.values()
        before = len(second.titles)
        with mock.patch.object(first, "update", side_effect=OSError("native update failed")), \
                mock.patch.object(app, "refresh_menus") as refresh, \
                mock.patch.object(hb.log, "warning") as warning:
            app.set_icon_style(True)
        self.assertIs(self.saved[-1]["filled_icons"], True)
        self.assertIs(app.cfg["filled_icons"], True)
        self.assertEqual(len(second.titles), before + 1)
        refresh.assert_called_once_with()
        warning.assert_called_once()

    def test_invalid_settings_and_setter_values_are_rejected(self):
        app = make_app()
        for value in (0, 1, "filled", None, [], {}):
            with self.subTest(value=value):
                self.assertFalse(hb._valid_setting("filled_icons", value))
                app.set_icon_style(value)
                self.assertIs(app.cfg["filled_icons"], False)
                with mock.patch("builtins.open", mock.mock_open(
                        read_data=json.dumps({"filled_icons": value}))):
                    self.assertIs(hb.load_config()["filled_icons"], False)
        self.assertEqual(self.saved, [])


class IconStyleCacheTests(unittest.TestCase):
    def make_icon(self):
        app = make_app()
        app.anim_tick = 0
        icon = RealDeviceIcon.__new__(RealDeviceIcon)
        icon.app = app
        icon.key, icon.status = "test", None
        icon.frames, icon._state, icon._images = None, None, {}
        icon.icon = types.SimpleNamespace(title="", icon=None, visible=True,
                                          update_menu=lambda: None)
        return app, icon

    def test_style_changes_cached_art_and_preserves_lazy_theme_reuse(self):
        app, icon = self.make_icon()
        with mock.patch.object(hb.icons, "render", side_effect=lambda *a, **k: object()) as render:
            icon.update(dev())
            outline = icon.icon.icon
            icon.update(dev())
            self.assertEqual(render.call_count, 1)
            app.cfg["filled_icons"] = True
            icon.update(dev())
            self.assertIsNot(icon.icon.icon, outline)
            self.assertIs(render.call_args.kwargs["filled"], True)
            app.light_taskbar = True
            icon.update(dev())
            app.light_taskbar = False
            icon.update(dev())
            self.assertEqual(render.call_count, 3)
            app.cfg["filled_icons"] = False
            icon.update(dev())
            self.assertEqual(render.call_count, 4)
            self.assertIs(render.call_args.kwargs["filled"], False)

    def test_charging_frames_receive_style_and_restart_on_change(self):
        app, icon = self.make_icon()
        status = DeviceStatus("test", "Mouse", 50, True, True, "logitech", kind="mouse")
        with mock.patch.object(hb.icons, "charging_frames",
                               side_effect=lambda *a, **k: [object()]) as frames:
            icon.update(status)
            first = icon.frames
            app.cfg["filled_icons"] = True
            icon.update(status)
            self.assertIsNot(icon.frames, first)
            self.assertEqual(frames.call_count, 2)
            self.assertIs(frames.call_args.kwargs["filled"], True)


if __name__ == "__main__":
    unittest.main()
