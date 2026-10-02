"""Tests for Preferences > Device types, the low battery alert per device and the
estimated time left (history.py). No tray, no hardware: the app module is loaded with
the fake icons of test_hide_rename.py, and the real App methods are called.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import FakeTrayIcon, HideRenameTestCase, hb, make_app  # noqa: E402
from providers.base import DeviceStatus, Provider  # noqa: E402
from providers import playstation  # noqa: E402
import history  # noqa: E402

REAL_DEVICE_ICON = hb.DeviceIcon          # setUp swaps it for the fake one

KEY = "razer:00c8:1"
H = 3600.0


def mouse(level, charging=False, online=True, key=KEY, name="Viper", source="razer"):
    return DeviceStatus(key, name, level, charging, online, source, kind="mouse")


class FakeProvider(Provider):
    def __init__(self, name, found):
        self.name, self.found, self.polls = name, found, 0

    def poll(self):
        self.polls += 1
        return list(self.found)


def poll_app(cfg=None):
    app = make_app(dict({"bluetooth": False}, **(cfg or {})))
    app.bt_cache = []
    app._bt_dup_logged = set()
    app.razer = FakeProvider("razer", [mouse(80)])
    app.logi = FakeProvider("logitech", [mouse(60, key="logitech:1", name="G502", source="logitech")])
    app.providers = [app.razer, app.logi]
    return app


def item(menu, text):
    return next(i for i in menu.items if i.text == text)


# ---------------------------------------------------------------- device types
class DeviceTypeTests(HideRenameTestCase):
    def test_every_provider_has_a_label(self):
        self.assertEqual({p.name for p in hb.make_providers()}, set(hb.PROVIDER_LABELS))

    def test_disabled_provider_is_not_polled(self):
        app = poll_app({"disabled_providers": ["razer"]})
        res = app.poll_once()
        self.assertEqual(app.razer.polls, 0)
        self.assertEqual([s.key for s in res], ["logitech:1"])

    def test_turning_off_removes_its_icons_at_once_and_back_on(self):
        app = poll_app()
        app.apply(app.poll_once())
        self.assertEqual(set(app.icons), {KEY, "logitech:1"})
        razer_icon = app.icons[KEY]
        app.toggle_provider("razer")
        self.assertEqual(app.cfg["disabled_providers"], ["razer"])
        self.assertEqual(set(app.icons), {"logitech:1"})
        self.assertTrue(razer_icon.stopped)
        self.assertTrue(app.wake.is_set())
        app.toggle_provider("razer")
        self.assertEqual(app.cfg["disabled_providers"], [])
        app.apply(app.poll_once())
        self.assertIn(KEY, app.icons)

    def test_damaged_settings_value_counts_as_none_off(self):
        for value in ("razer", 5, None, {"razer": 1}):
            with self.subTest(value=value):
                app = poll_app({"disabled_providers": value})
                app.poll_once()
                self.assertEqual(app.razer.polls, 1)

    def test_menu_lists_every_type_and_toggles(self):
        app = poll_app()
        prefs = item(app.build_menu(None), "Preferences").submenu
        types = item(prefs, "Device types").submenu
        labels = [i.text for i in types.items]
        for label in hb.PROVIDER_LABELS.values():
            self.assertIn(label, labels)
        razer = item(types, "Razer mice and headsets")
        self.assertTrue(razer.checked)
        razer(FakeTrayIcon())
        self.assertIn("razer", app.cfg["disabled_providers"])
        self.assertFalse(item(item(prefs, "Device types").submenu, "Razer mice and headsets").checked)

    def test_playstation_full_mode_stays_in_preferences(self):
        # #96 is handled by "PlayStation full mode (Bluetooth)" (off = listen only), so
        # Device types has no Bluetooth switch of its own for PlayStation controllers
        app = poll_app()
        prefs = item(app.build_menu(None), "Preferences").submenu
        types = item(prefs, "Device types").submenu
        self.assertFalse(any("Bluetooth" in i.text for i in types.items))
        self.assertIn("PlayStation full mode (Bluetooth)", [i.text for i in prefs.items])

    def test_full_mode_setting_reaches_the_playstation_provider(self):
        app = poll_app({"playstation_full_mode": True})
        ps = playstation.PlayStationProvider()
        with mock.patch.object(ps, "poll", return_value=[]):
            app.providers = [ps]
            app.poll_once()
        self.assertTrue(ps.switch_bluetooth)

    def test_disabled_playstation_is_not_polled_even_in_full_mode(self):
        app = poll_app({"playstation_full_mode": True, "disabled_providers": ["playstation"]})
        ps = playstation.PlayStationProvider()
        with mock.patch.object(ps, "poll", return_value=[]) as p:
            app.providers = [ps]
            app.poll_once()
        self.assertEqual(p.call_count, 0)

    def test_pending_of_a_disabled_provider_does_not_speed_up_polling(self):
        app = poll_app({"disabled_providers": ["razer"], "interval": 60})
        app.razer.pending = True
        app.stop_evt = mock.Mock(is_set=lambda: False)
        app.change_signature = lambda: None
        waits = []
        app.wake = mock.Mock(wait=lambda t: waits.append(t) or True)
        app.wait_next(sig=None)
        self.assertEqual(waits, [2.5])          # not the 3 s re-check


# ---------------------------------------------------------------- low per device
class DeviceLowTests(HideRenameTestCase):
    def test_own_level_alerts_instead_of_the_default(self):
        app = make_app({"low": 20, "lows": {KEY: 40}})
        app.apply([mouse(45)])
        self.assertEqual(app.notes, [])
        app.apply([mouse(40)])
        self.assertEqual(len(app.notes), 1)

    def test_other_devices_keep_the_default(self):
        app = make_app({"low": 20, "lows": {"other": 40}})
        app.apply([mouse(40)])
        self.assertEqual(app.notes, [])
        app.apply([mouse(20)])
        self.assertEqual(len(app.notes), 1)

    def test_off_for_one_device(self):
        app = make_app({"low": 20, "lows": {KEY: 0}})
        app.apply([mouse(5)])
        self.assertEqual(app.notes, [])

    def test_damaged_value_is_ignored(self):
        for value in ("40", True, 150, -1, None, 12.5):
            with self.subTest(value=value):
                app = make_app({"low": 20, "lows": {KEY: value}})
                self.assertEqual(app.low_for(mouse(50)), 20)

    def test_device_menu_sets_and_resets(self):
        app = make_app({"low": 20})
        app.apply([mouse(35)])
        ic = app.icons[KEY]
        sub = item(app.build_menu(ic), "Low battery alert at (this device)").submenu
        self.assertEqual(sub.items[0].text, "Default (20%)")
        self.assertTrue(sub.items[0].checked)
        item(sub, "30%")(FakeTrayIcon())
        self.assertEqual(app.cfg["lows"], {KEY: 30})
        self.assertTrue(item(item(app.build_menu(ic), "Low battery alert at (this device)").submenu, "30%").checked)
        # a new level that the device is already under alerts on the next reading
        item(sub, "Default (20%)")(FakeTrayIcon())
        self.assertEqual(app.cfg["lows"], {})
        item(sub, "30%")(FakeTrayIcon())
        app.apply([mouse(29)])
        self.assertEqual(len(app.notes), 1)

    def test_icon_is_drawn_with_the_own_level(self):
        app = make_app({"low": 20, "lows": {KEY: 50}})
        app.light_taskbar = False
        app.anim_tick = 0
        app.pictogram = lambda st: ""
        app.display_name = lambda st: st.name
        drawn = []
        ic = REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON)
        ic.app, ic.key, ic._state, ic._images, ic.frames = app, KEY, None, {}, None
        ic.icon = mock.Mock(title="", visible=True)
        with mock.patch.object(hb.icons, "render",
                               lambda level, ch, on, low, lt, badge, **kw: drawn.append(low) or object()):
            ic._update(mouse(45))
        self.assertEqual(set(drawn), {50})


# ---------------------------------------------------------------- history / time left
def feed(h, readings, key=KEY, start=0.0, step=60.0):
    """readings: level, or (level, charging, online). One reading per `step` seconds."""
    t = start
    for r in readings:
        level, charging, online = r if isinstance(r, tuple) else (r, False, True)
        h.record(key, level, charging, online, t)
        t += step
    return t


class HistoryTests(unittest.TestCase):
    def drain(self, h, start_level, hours, per_hour, key=KEY, start=0.0):
        """Steady drain: `per_hour` points an hour, a reading every minute."""
        n = int(hours * 60)
        levels = [round(start_level - per_hour * i / 60) for i in range(n + 1)]
        return feed(h, levels, key, start=start)

    def test_steady_drain_gives_the_right_estimate(self):
        h = history.History()
        self.drain(h, 100, 3, 5)          # 5 %/h for 3 h -> 85 %
        left = h.seconds_left(KEY, 85)
        self.assertAlmostEqual(left / H, 17, delta=1)

    def test_no_estimate_too_early(self):
        h = history.History()
        self.drain(h, 100, 0.25, 20)      # 15 min only
        self.assertIsNone(h.seconds_left(KEY, 95))
        h = history.History()
        self.drain(h, 100, 2, 1)          # 2 h but only 2 points dropped
        self.assertIsNone(h.seconds_left(KEY, 98))

    def test_charging_starts_over(self):
        h = history.History()
        t = self.drain(h, 100, 3, 5)
        h.record(KEY, 85, True, True, t)
        self.assertIsNone(h.rate(KEY))
        self.assertEqual(h.devices[KEY]["samples"], [])

    def test_a_jump_up_without_charging_starts_over(self):
        h = history.History()
        t = self.drain(h, 80, 3, 5)
        h.record(KEY, 100, False, True, t + 60)
        self.assertEqual(h.devices[KEY]["samples"], [[0.0, 100]])

    def test_small_rise_is_jitter(self):
        h = history.History()
        feed(h, [80, 79, 80, 79])
        self.assertEqual([s[1] for s in h.devices[KEY]["samples"]], [80, 79])

    def test_asleep_time_does_not_count(self):
        h = history.History()
        t = feed(h, [90, 89])
        h.record(KEY, 89, False, False, t)          # asleep for 10 h
        h.record(KEY, 89, False, True, t + 10 * H)
        self.assertLess(h.devices[KEY]["use"], 200)

    def test_long_gap_counts_as_max_gap(self):
        h = history.History()
        h.record(KEY, 90, False, True, 0)
        h.record(KEY, 89, False, True, 8 * H)       # PC suspended overnight
        self.assertEqual(h.devices[KEY]["use"], history.MAX_GAP)

    def test_coarse_and_unknown_readings_are_ignored(self):
        h = history.History()
        h.record(KEY, 50, False, True, 0, coarse=True)
        h.record("k2", None, False, True, 0)
        self.assertEqual(h.devices, {})

    def test_stalled_level_slows_the_estimate(self):
        h = history.History()
        t = self.drain(h, 100, 3, 5)
        fast = h.seconds_left(KEY, 85)
        feed(h, [85] * 180, start=t)               # 3 more hours without a drop
        self.assertGreater(h.seconds_left(KEY, 85), fast)

    def test_save_and_load(self):
        folder = tempfile.mkdtemp(prefix="halo_battery_test_")
        path = os.path.join(folder, "history.json")
        h = history.History(path)
        self.drain(h, 100, 3, 5, start=time.time() - 4 * H)
        h.save(force=True)
        again = history.History(path)
        again.load()
        self.assertAlmostEqual(again.seconds_left(KEY, 85), h.seconds_left(KEY, 85), delta=1)
        self.assertIsNone(again.devices[KEY]["last"])   # the time the app was closed does not count

    def test_damaged_file_is_ignored(self):
        folder = tempfile.mkdtemp(prefix="halo_battery_test_")
        path = os.path.join(folder, "history.json")
        for text in ("not json", "[1, 2]", '{"devices": {"a": {"samples": [["x", 1]]}, "b": 5}}'):
            with self.subTest(text=text):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                h = history.History(path)
                h.load()
                self.assertEqual(h.devices, {})

    def test_format(self):
        self.assertEqual(history.format_left(1800), "less than 1 h of use left")
        self.assertEqual(history.format_left(5.4 * H), "about 5 h of use left")
        self.assertEqual(history.format_left(72 * H), "about 3 days of use left")


class TimeLeftTooltipTests(HideRenameTestCase):
    def app_with_history(self, cfg=None):
        app = make_app(cfg)
        levels = [round(100 - 5 * i / 60) for i in range(181)]
        feed(app.history, levels)
        return app

    def test_tooltip_shows_time_left(self):
        app = self.app_with_history()
        text = hb.describe(mouse(85), "Viper", app.time_left_text(mouse(85)))
        self.assertRegex(text, r"^Viper: 85%, about 1\d h of use left$")

    def test_not_while_charging_or_asleep(self):
        app = self.app_with_history()
        left = app.time_left_text(mouse(85))
        self.assertEqual(hb.describe(mouse(85, charging=True), None, left), "Viper: 85%, charging")
        self.assertEqual(hb.describe(mouse(85, online=False), None, left),
                         "Viper: 85% (last known value, device asleep)")

    def test_setting_off(self):
        app = self.app_with_history({"time_left": False})
        self.assertEqual(app.time_left_text(mouse(85)), "")

    def test_apply_records_history(self):
        app = make_app()
        with mock.patch.object(hb.time, "time", side_effect=[0.0, 60.0]):
            app.apply([mouse(80)])
            app.apply([mouse(79)])
        self.assertEqual([s[1] for s in app.history.devices[KEY]["samples"]], [80, 79])

    def test_hidden_device_has_no_history(self):
        app = make_app({"hidden": {KEY: "Viper"}})
        app.apply([mouse(80)])
        self.assertEqual(app.history.devices, {})


if __name__ == "__main__":
    unittest.main()
