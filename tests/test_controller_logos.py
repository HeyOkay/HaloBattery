"""Controller presentation settings and physical-size invalidation, without hardware."""
import copy
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import HideRenameTestCase, hb, make_app
from providers.base import DeviceStatus
import _sandbox

REAL_SAVE_CONFIG = hb.save_config


def pad(key="pad:1", kind="gamepad", name="Xbox Wireless Controller"):
    return DeviceStatus(key, name, 75, False, True, "xinput", kind=kind)


class ControllerLogoTests(HideRenameTestCase):
    def owner(self, app, st=None):
        st = st or pad()
        app.apply([st])
        return app.icons[st.key]

    def logo_menu(self, app, owner):
        return next(i for i in app.build_menu(owner).items if i.text == "Controller logo")

    def test_default_is_generic_even_for_known_controller_names(self):
        app = make_app()
        for st in (pad(), pad(kind="dualshock", name="DualShock 4"),
                   pad(kind="dualsense", name="DualSense")):
            self.assertEqual(app.controller_style(st), ("default", False))

    def test_menu_selects_brand_and_colour_and_redraws(self):
        app = make_app()
        owner = self.owner(app)
        menu = self.logo_menu(app, owner)
        self.assertTrue(menu.visible)
        self.assertEqual([i.text for i in menu.submenu.items if i.radio],
                         ["Default", "Xbox", "PlayStation", "Nintendo Switch"])
        self.assertFalse(next(i for i in menu.submenu.items if i.text == "Color logo").visible)
        count = len(owner.titles)
        next(i for i in menu.submenu.items if i.text == "PlayStation")(None)
        colour = next(i for i in menu.submenu.items if i.text == "Color logo")
        self.assertTrue(colour.visible)
        colour(None)
        self.assertTrue(colour.checked)
        self.assertEqual(app.controller_style(owner.status), ("playstation", True))
        self.assertEqual(len(owner.titles), count + 2)

    def test_choices_persist_independently_after_restart(self):
        app = make_app()
        owner = self.owner(app)
        app.set_controller_brand(owner, "xbox")
        app.set_controller_color(owner, True)
        restarted = make_app(copy.deepcopy(self.saved[-1]))
        self.assertEqual(restarted.controller_style(pad()), ("xbox", True))
        self.assertEqual(restarted.controller_style(pad("pad:2")), ("default", False))

    def test_real_config_file_round_trip_preserves_logo_settings(self):
        path = os.path.join(_sandbox.mkdtemp(prefix="controller_logo_"), "config.json")
        app = make_app()
        owner = self.owner(app)
        with mock.patch.object(hb, "CONFIG_PATH", path), \
                mock.patch.object(hb, "save_config", REAL_SAVE_CONFIG):
            app.set_controller_brand(owner, "nintendo")
            app.set_controller_color(owner, True)
            restarted = make_app(hb.load_config())
        self.assertEqual(restarted.controller_style(pad()), ("nintendo", True))
        self.assertEqual(restarted.controller_style(pad("pad:2")), ("default", False))

    def test_default_and_automatic_reset_separate_preferences(self):
        app = make_app({"icons": {"pad:1": "dualshock"}})
        owner = self.owner(app)
        app.set_controller_brand(owner, "playstation")
        app.set_controller_color(owner, True)
        app.set_pictogram(owner, "")
        self.assertEqual(app.pictogram(owner.status), "gamepad")
        self.assertEqual(app.controller_style(owner.status), ("playstation", True))
        app.set_pictogram(owner, "dualsense")
        app.set_controller_brand(owner, "default")
        self.assertEqual(app.pictogram(owner.status), "dualsense")
        self.assertEqual(app.controller_style(owner.status), ("default", False))
        self.assertNotIn("pad:1", self.saved[-1]["controller_brands"])

    def test_bad_maps_and_values_fall_back_safely(self):
        for value in (None, "xbox", [], 42):
            app = make_app({"controller_brands": value, "controller_colors": value})
            self.assertEqual(app.controller_style(pad()), ("default", False))
        for value in (None, "steam", [], {}, 42, True):
            app = make_app({"controller_brands": {"pad:1": value}})
            self.assertEqual(app.controller_style(pad()), ("default", False))
        for value in (None, "true", 1, [], {}):
            app = make_app({"controller_brands": {"pad:1": "xbox"},
                            "controller_colors": {"pad:1": value}})
            self.assertEqual(app.controller_style(pad()), ("xbox", False))

    def test_non_controller_hides_logo_and_ignores_saved_style(self):
        app = make_app({"controller_brands": {"pad:1": "xbox"},
                        "controller_colors": {"pad:1": True}})
        owner = self.owner(app, pad(kind="mouse", name="Mouse"))
        self.assertEqual(app.controller_style(owner.status), ("default", False))
        self.assertFalse(self.logo_menu(app, owner).visible)
        self.assertNotIn("Controller logo", [i.text for i in app.build_menu(None).items])

    def test_status_json_keeps_semantic_controller_ids(self):
        for kind in ("gamepad", "dualshock", "dualsense"):
            app = make_app({"icons": {"pad:1": kind},
                            "controller_brands": {"pad:1": "playstation"},
                            "controller_colors": {"pad:1": True}})
            self.assertEqual(app.status_data([pad()])["devices"][0]["kind"], kind)


class PhysicalRenderCacheTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app()
        self.app.anim_tick = 0
        self.app.tray_size = 20
        self.ic = hb.DeviceIcon.__new__(hb.DeviceIcon)
        self.ic.app = self.app
        self.ic.key, self.ic.status = "pad:1", None
        self.ic.frames, self.ic._state, self.ic._images = None, None, {}
        self.discards = []
        self.ic.icon = types.SimpleNamespace(
            title="", icon=None, visible=True, update_menu=lambda: None,
            discard_cached_handles=lambda: self.discards.append(self.ic.icon.icon))
        self.app.icons = {"pad:1": self.ic}

    def test_brand_and_colour_invalidate_images_without_cross_theme_reuse(self):
        with mock.patch.object(hb.icons, "render", side_effect=lambda *a, **k: object()) as render:
            self.ic.update(pad())
            generic = self.ic.icon.icon
            self.app.cfg["controller_brands"] = {"pad:1": "xbox"}
            self.ic.update(pad())
            mono = self.ic.icon.icon
            self.app.cfg["controller_colors"] = {"pad:1": True}
            self.ic.update(pad())
            self.assertIsNot(generic, mono)
            self.assertIsNot(mono, self.ic.icon.icon)
            self.assertEqual(render.call_count, 3)
            self.assertEqual(render.call_args.kwargs["size"], 20)
            self.assertEqual(render.call_args.kwargs["brand"], "xbox")
            self.assertIs(render.call_args.kwargs["brand_color"], True)
            self.assertEqual(len(self.ic._images), 1)

    def test_dpi_change_refreshes_animation_and_placeholder_before_releasing_handles(self):
        st = pad()
        st.charging = True
        self.app.placeholder = types.SimpleNamespace(icon=None)
        self.app.compute_light = lambda: False
        self.ic.update(st)
        old_frames = self.ic.frames
        with mock.patch.object(hb.tray_dpi, "tray_pixels", return_value=24):
            self.app.refresh_theme()
        self.assertIsNot(old_frames, self.ic.frames)
        self.assertTrue(all(frame.size == (24, 24) for frame in self.ic.frames))
        self.assertEqual(self.app.placeholder.icon.size, (24, 24))
        self.assertEqual(self.discards, [self.ic.icon.icon])
        self.assertTrue(all(state[9] == 24 for state in self.ic._images))

    def test_failed_dpi_render_retries_on_next_theme_tick(self):
        self.ic.update(pad())
        old = self.ic.icon.icon
        self.app.compute_light = lambda: False
        with mock.patch.object(hb.tray_dpi, "tray_pixels", return_value=24):
            with mock.patch.object(hb.icons, "render", side_effect=OSError("temporary ICO failure")):
                with self.assertRaises(OSError):
                    self.app.refresh_theme()
            self.assertEqual(self.app.tray_size, 20)
            self.assertIs(self.ic.icon.icon, old)
            self.app.refresh_theme()
        self.assertEqual(self.app.tray_size, 24)
        self.assertEqual(self.ic.icon.icon.size, (24, 24))
        self.assertEqual(self.ic._state[9], 24)

    def test_failed_native_swap_retries_an_unchanged_reading(self):
        class FlakyIcon:
            title, visible = "", True
            def __init__(self):
                self.fail = True
                self._icon = None
            @property
            def icon(self):
                return self._icon
            @icon.setter
            def icon(self, image):
                if self.fail:
                    self.fail = False
                    raise OSError("temporary LoadImage failure")
                self._icon = image
            def update_menu(self):
                pass
        self.ic.icon = FlakyIcon()
        with self.assertRaises(OSError):
            self.ic.update(pad())
        self.assertIsNone(self.ic._state)
        self.ic.update(pad())
        self.assertEqual(self.ic.icon.icon.size, (20, 20))
        self.assertEqual(self.ic._state[9], 20)

    def test_theme_only_retains_opposite_theme_images(self):
        self.ic.update(pad())
        dark = self.ic.icon.icon
        self.app.compute_light = lambda: True
        with mock.patch.object(hb.tray_dpi, "tray_pixels", return_value=20):
            self.app.refresh_theme()
        self.assertEqual(len(self.ic._images), 2)
        self.assertEqual(self.discards, [])
        self.app.compute_light = lambda: False
        with mock.patch.object(hb.tray_dpi, "tray_pixels", return_value=20):
            self.app.refresh_theme()
        self.assertIs(self.ic.icon.icon, dark)


if __name__ == "__main__":
    unittest.main()
