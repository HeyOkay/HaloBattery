"""Tests for the full-charge alert and the per-device pictogram in halo_battery.pyw.
No tray, no hardware: the app module is loaded with the fake icons of
test_hide_rename.py, and the real App methods are called.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import HideRenameTestCase, hb, make_app  # noqa: E402
from providers.base import DeviceStatus  # noqa: E402

KEY = "razer:00c8:1"


def mouse(level, charging=False, online=True, key=KEY, name="Pro Click V2 Vertical"):
    return DeviceStatus(key, name, level, charging, online, "razer", kind="mouse")


class FullChargeTests(HideRenameTestCase):
    def run_levels(self, readings, cfg=None):
        app = make_app(cfg)
        for r in readings:
            app.apply([mouse(*r)])
        return app

    def test_alert_when_a_charging_device_reaches_100(self):
        app = self.run_levels([(90, True), (99, True), (100, True)])
        self.assertEqual(app.notes, ["Pro Click V2 Vertical is fully charged."])

    def test_device_that_stops_reporting_charging_when_full(self):
        app = self.run_levels([(97, True), (100, False)])
        self.assertEqual(len(app.notes), 1)

    def test_already_full_at_start_gives_no_alert(self):
        app = self.run_levels([(100, True), (100, True), (100, False)])
        self.assertEqual(app.notes, [])

    def test_not_charging_gives_no_alert(self):
        app = self.run_levels([(90, False), (100, False)])
        self.assertEqual(app.notes, [])

    def test_99_100_jitter_on_the_charger_alerts_once(self):
        app = self.run_levels([(95, True), (100, True), (99, True), (100, True), (99, True), (100, True)])
        self.assertEqual(len(app.notes), 1)

    def test_next_charge_alerts_again(self):
        app = self.run_levels([(95, True), (100, True), (80, False), (90, True), (100, True)])
        self.assertEqual(len(app.notes), 2)

    def test_drop_below_95_on_the_charger_rearms(self):
        app = self.run_levels([(95, True), (100, True), (90, True), (100, True)])
        self.assertEqual(len(app.notes), 2)

    def test_setting_off(self):
        app = self.run_levels([(90, True), (100, True)], {"full_alert": False})
        self.assertEqual(app.notes, [])

    def test_uses_the_name_the_user_gave(self):
        app = self.run_levels([(90, True), (100, True)], {"names": {KEY: "Work mouse"}})
        self.assertEqual(app.notes, ["Work mouse is fully charged."])

    def test_asleep_or_unknown_level_changes_nothing(self):
        app = self.run_levels([(90, True), (90, True, False), (100, True)])
        self.assertEqual(len(app.notes), 1)
        app = self.run_levels([(90, True), (None, True), (100, True)])
        self.assertEqual(len(app.notes), 1)

    def test_setting_is_in_preferences_and_toggles(self):
        app = make_app()
        prefs = next(i for i in app.build_menu(None).items if i.text == "Preferences").submenu
        item = next(i for i in prefs.items if i.text == "Alert when fully charged")
        self.assertTrue(item.checked)
        item(None)
        self.assertFalse(app.cfg["full_alert"])
        self.assertFalse(item.checked)


class PictogramTests(HideRenameTestCase):
    """"Icon" in the device menu: pick the pictogram of one device."""

    def icon_menu(self, app, key=KEY):
        items = app.build_menu(app.icons[key]).items
        return next(i for i in items if i.text == "Icon").submenu

    def test_menu_lists_the_choices_with_automatic_checked(self):
        app = make_app()
        app.apply([mouse(80)])
        sub = self.icon_menu(app)
        self.assertEqual([i.text for i in sub.items],
                         ["Automatic", "Mouse", "Keyboard", "Microphone", "Headset",
                          "Controller", "Bluetooth"])
        self.assertEqual([i.text for i in sub.items if i.checked], ["Automatic"])

    def test_pick_saves_redraws_and_checks(self):
        app = make_app()
        app.apply([mouse(80)])
        ic = app.icons[KEY]
        sub = self.icon_menu(app)
        updates = len(ic.titles)
        next(i for i in sub.items if i.text == "Controller")(None)
        self.assertEqual(self.saved[-1]["icons"], {KEY: "gamepad"})
        self.assertEqual(app.pictogram(ic.status), "gamepad")
        self.assertEqual(len(ic.titles), updates + 1)       # the icon is redrawn at once
        self.assertEqual([i.text for i in self.icon_menu(app).items if i.checked], ["Controller"])

    def test_automatic_goes_back(self):
        app = make_app({"icons": {KEY: "keyboard"}})
        app.apply([mouse(80)])
        self.assertEqual(app.pictogram(app.icons[KEY].status), "keyboard")
        next(i for i in self.icon_menu(app).items if i.text == "Automatic")(None)
        self.assertEqual(self.saved[-1]["icons"], {})
        self.assertEqual(app.pictogram(app.icons[KEY].status), "mouse")

    def test_bluetooth_controller_can_show_the_controller(self):
        # the Reddit case: a controller over Bluetooth gets the Bluetooth pictogram
        pad = DeviceStatus("bt:AABBCCDDEEFF", "8BitDo Ultimate 2C", 70, False, True, "bluetooth")
        app = make_app({"icons": {"bt:AABBCCDDEEFF": "gamepad"}})
        self.assertEqual(hb.badge_for(pad), "bluetooth")
        self.assertEqual(app.pictogram(pad), "gamepad")

    def test_unknown_or_damaged_value_is_ignored(self):
        for value in ("rocket", 5, None, ["mouse"]):
            app = make_app({"icons": {KEY: value}})
            self.assertEqual(app.pictogram(mouse(80)), "mouse", repr(value))
        app = make_app({"icons": "mouse"})                  # not a dict at all
        self.assertEqual(app.pictogram(mouse(80)), "mouse")

    def test_other_devices_keep_their_own_pictogram(self):
        app = make_app({"icons": {KEY: "headset"}})
        other = mouse(50, key="logitech:C15E09CD", name="G502")
        self.assertEqual(app.pictogram(other), "mouse")

    def test_no_icon_menu_on_the_no_devices_icon(self):
        app = make_app()
        self.assertNotIn("Icon", [i.text for i in app.build_menu(None).items])


class PictogramDrawTests(unittest.TestCase):
    """The real DeviceIcon (not the fake one) must draw the picked pictogram."""

    def test_the_real_icon_draws_the_picked_pictogram(self):
        drawn = []
        ic = hb.DeviceIcon.__new__(hb.DeviceIcon)
        ic.app = make_app({"icons": {KEY: "headset"}})
        ic.key, ic.status, ic.frames, ic._state, ic._images = KEY, None, None, None, {}
        ic.icon = types.SimpleNamespace(title="", icon=None, visible=True, update_menu=lambda: None)
        with mock.patch.object(hb.icons, "render",
                               lambda *a, **k: drawn.append(a[5] if len(a) > 5 else k.get("badge"))):
            ic.update(mouse(80))
        self.assertIn("headset", drawn)


if __name__ == "__main__":
    unittest.main()
