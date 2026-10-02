"""Tests for "Percentage in the icon", "Quiet while gaming", the status file, and the
re-read when the user comes back to a greyed (asleep) device (#87).
No tray, no hardware: the app module is loaded with the fake icons of
test_hide_rename.py, and the real App methods are called.

Run from the repository root:

    python -m unittest discover -s tests
"""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import FakeTrayIcon, HideRenameTestCase, hb, make_app  # noqa: E402
from providers.base import DeviceStatus  # noqa: E402
import icons  # noqa: E402

REAL_DEVICE_ICON = hb.DeviceIcon          # setUp swaps it for the fake one
KEY = "razer:00c8:1"


def mouse(level, charging=False, online=True, key=KEY, name="Viper", approx=""):
    return DeviceStatus(key, name, level, charging, online, "razer", approx, kind="mouse")


def item(menu, text):
    return next(i for i in menu.items if i.text == text)


# ---------------------------------------------------------------- percentage in the icon
class NumberRenderTests(unittest.TestCase):
    def changed_pixels(self, text):
        plain = icons.render(None, False, False, 20, False, "")
        numbered = icons.render(None, False, False, 20, False, "", text=text)
        a, b = plain.load(), numbered.load()
        return [(x, y) for x in range(icons.SIZE) for y in range(icons.SIZE) if a[x, y] != b[x, y]]

    def test_number_is_drawn(self):
        self.assertTrue(self.changed_pixels("67"))

    def test_even_100_stays_inside_the_ring(self):
        inner = 31.5 - 6.5                # ring radius minus its width, in icon pixels
        for text in ("7", "67", "100"):
            with self.subTest(text=text):
                far = max(((x + 0.5 - 32) ** 2 + (y + 0.5 - 32) ** 2) ** 0.5
                          for x, y in self.changed_pixels(text))
                self.assertLess(far, inner)

    def test_number_replaces_the_pictogram(self):
        with_badge = icons.render(60, False, True, 20, False, "mouse", text="60")
        without = icons.render(60, False, True, 20, False, "", text="60")
        self.assertEqual(with_badge.tobytes(), without.tobytes())

    def test_charging_frames_carry_the_number(self):
        frames = icons.charging_frames(40, True, 20, False, "", text="40")
        plain = icons.charging_frames(40, True, 20, False, "")
        self.assertNotEqual(frames[0].tobytes(), plain[0].tobytes())


class NumberSettingTests(HideRenameTestCase):
    def texts_drawn(self, st, cfg):
        drawn = []
        ic = REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON)
        ic.app = make_app(cfg)
        ic.app.anim_tick = 0
        ic.key, ic.status, ic.frames, ic._state, ic._images = KEY, None, None, None, {}
        ic.icon = types.SimpleNamespace(title="", icon=None, visible=True, update_menu=lambda: None)
        with mock.patch.object(icons, "render", lambda *a, **k: drawn.append(k.get("text"))), \
                mock.patch.object(icons, "charging_frames",
                                  lambda *a, **k: drawn.append(k.get("text")) or [object()]):
            ic.update(st)
        return set(drawn)

    def test_on(self):
        self.assertEqual(self.texts_drawn(mouse(67), {"percent_in_icon": True}), {"67"})

    def test_off_by_default(self):
        self.assertEqual(self.texts_drawn(mouse(67), {}), {""})

    def test_asleep_keeps_the_last_number(self):
        self.assertEqual(self.texts_drawn(mouse(67, online=False), {"percent_in_icon": True}), {"67"})

    def test_charging(self):
        self.assertEqual(self.texts_drawn(mouse(40, charging=True), {"percent_in_icon": True}), {"40"})

    def test_rough_levels_and_no_level_keep_the_pictogram(self):
        self.assertEqual(self.texts_drawn(mouse(55, approx="about 55% (medium)"),
                                          {"percent_in_icon": True}), {""})
        self.assertEqual(self.texts_drawn(mouse(None), {"percent_in_icon": True}), {""})

    def test_menu_toggles(self):
        app = make_app()
        prefs = item(app.build_menu(None), "Preferences").submenu
        entry = item(prefs, "Percentage in the icon")
        self.assertFalse(entry.checked)
        entry(FakeTrayIcon())
        self.assertTrue(app.cfg["percent_in_icon"])


# ---------------------------------------------------------------- quiet while gaming
class QuietTests(HideRenameTestCase):
    def game(self, on):
        p = mock.patch.object(hb, "fullscreen_app_running", lambda: on)
        p.start()
        self.addCleanup(p.stop)

    def test_alerts_are_held_and_shown_after_the_game(self):
        app = make_app({"low": 20})
        self.game(True)
        app.apply([mouse(15)])
        self.assertEqual(app.notes, [])
        self.assertEqual(len(app.held), 1)
        self.game(False)
        app.flush_held()
        self.assertEqual(app.notes, ["Viper: 15% left. Time to charge."])
        self.assertEqual(app.held, {})

    def test_one_held_alert_per_device_and_kind(self):
        app = make_app({"low": 20})
        self.game(True)
        app.apply([mouse(90, charging=True)])
        app.apply([mouse(100, charging=True)])          # fully charged, held
        app.apply([mouse(15)])                          # low, held
        app.alerted[KEY] = False
        app.apply([mouse(12)])                          # low again: replaces the first one
        self.assertEqual(len(app.held), 2)
        self.game(False)
        app.flush_held()
        self.assertEqual(sorted(app.notes),
                         ["Viper is fully charged.", "Viper: 12% left. Time to charge."])

    def test_low_alert_is_dropped_when_it_was_charged_meanwhile(self):
        app = make_app({"low": 20})
        self.game(True)
        app.apply([mouse(15)])
        app.apply([mouse(16, charging=True)])
        self.game(False)
        app.flush_held()
        self.assertEqual(app.notes, [])

    def test_setting_off_shows_alerts_during_the_game(self):
        app = make_app({"low": 20, "quiet_fullscreen": False})
        self.game(True)
        app.apply([mouse(15)])
        self.assertEqual(len(app.notes), 1)

    def waits(self, app, stop_after=1):
        app.stop_evt = mock.Mock(is_set=lambda: False)
        app.change_signature = lambda: None
        waits = []
        app.wake = mock.Mock(wait=lambda t: waits.append(t) or len(waits) >= stop_after)
        return waits

    def test_polls_every_5_minutes_during_a_game(self):
        app = make_app({"interval": 30})
        app.providers = [types.SimpleNamespace(name="playstation", pending=True)]
        self.game(True)
        clock = iter(range(0, 10000, 3))
        with mock.patch.object(hb.time, "time", lambda: next(clock)):
            waits = self.waits(app, stop_after=10 ** 6)
            app.stop_evt = mock.Mock(is_set=lambda: len(waits) > 200)
            app.wait_next(sig=None)
        # 3 s of fake time per call: the deadline is 300 s away, not 3 s or 30 s
        self.assertGreater(len(waits), 40)

    def test_wakes_when_the_game_is_closed(self):
        app = make_app({"interval": 60})
        app.providers = []
        states = iter([True, True, False])
        p = mock.patch.object(hb, "fullscreen_app_running", lambda: next(states, False))
        p.start()
        self.addCleanup(p.stop)
        waits = self.waits(app, stop_after=10 ** 6)
        app.wait_next(sig=None)
        self.assertEqual(len(waits), 2)

    def test_detection_runs_on_this_machine(self):
        self.assertIsInstance(REAL_FULLSCREEN(), bool)

    def test_menu_toggles(self):
        app = make_app()
        entry = item(item(app.build_menu(None), "Preferences").submenu, "Quiet while gaming")
        self.assertTrue(entry.checked)
        entry(FakeTrayIcon())
        self.assertFalse(app.cfg["quiet_fullscreen"])


REAL_FULLSCREEN = hb.fullscreen_app_running


# ------------------------------------------------ coming back to the PC (#87)
class UserReturnTests(HideRenameTestCase):
    """A sleeping mouse is awake by the time its first movement lands in
    GetLastInputInfo, so a greyed device should be re-read on that tick."""

    def game(self, on):
        p = mock.patch.object(hb, "fullscreen_app_running", lambda: on)
        p.start()
        self.addCleanup(p.stop)

    def loop(self, app, inputs, stop_after=10 ** 6):
        app.stop_evt = mock.Mock(is_set=lambda: False)
        app.change_signature = lambda: None
        waits = []
        app.wake = mock.Mock(wait=lambda t: waits.append(t) or len(waits) >= stop_after)
        p = mock.patch.object(hb, "last_input_ms", lambda: next(inputs, None))
        p.start()
        self.addCleanup(p.stop)
        return waits

    def test_returning_to_a_greyed_device_polls_at_once(self):
        app = make_app({"interval": 60})
        app.providers = []
        app.apply([mouse(40, online=False)])        # a sleeping mouse, kept greyed
        self.game(False)
        inputs = iter([1000, 1000, 1000, 61000])    # ... and the user comes back
        clock = iter(range(0, 100000, 3))
        waits = self.loop(app, inputs)
        with mock.patch.object(hb.time, "time", lambda: next(clock)):
            app.wait_next(sig=None)
        self.assertEqual(3, len(waits), "the poll starts on the tick the input returns")

    def test_nothing_greyed_keeps_the_normal_cadence(self):
        app = make_app({"interval": 600})
        app.providers = []
        app.apply([mouse(77)])                      # every reading fresh
        self.game(False)
        inputs = iter([1000, 1000, 61000] * 100)
        clock = iter(range(0, 100000, 3))
        waits = self.loop(app, inputs, stop_after=10 ** 6)
        with mock.patch.object(hb.time, "time", lambda: next(clock)):
            app.stop_evt = mock.Mock(is_set=lambda: len(waits) > 30)
            app.wait_next(sig=None)
        self.assertGreater(len(waits), 25, "no early poll when nothing is greyed")

    def test_quiet_mode_keeps_the_slow_cadence(self):
        app = make_app({"interval": 60})
        app.providers = []
        app.apply([mouse(40, online=False)])
        self.game(True)                             # a game is full screen
        inputs = iter([1000, 1000, 61000] * 100)
        clock = iter(range(0, 600000, 3))
        waits = self.loop(app, inputs, stop_after=10 ** 6)
        with mock.patch.object(hb.time, "time", lambda: next(clock)):
            app.stop_evt = mock.Mock(is_set=lambda: len(waits) > 30)
            app.wait_next(sig=None)
        self.assertGreater(len(waits), 25, "quiet mode must not start polling on input")


# ---------------------------------------------------------------- status file
class StatusFileTests(HideRenameTestCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.mkdtemp(prefix="halo_battery_test_")
        self.path = os.path.join(folder, "status.json")
        p = mock.patch.object(hb, "STATUS_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def test_written_when_on(self):
        app = make_app({"status_file": True, "names": {KEY: "Work mouse"}, "lows": {KEY: 30}})
        app.write_status([mouse(67)])
        data = self.read()
        self.assertTrue(data["running"])
        self.assertEqual(data["version"], hb.VERSION)
        dev = data["devices"][0]
        self.assertEqual((dev["name"], dev["level"], dev["charging"], dev["online"], dev["kind"],
                          dev["low_alert_at"], dev["text"]),
                         ("Work mouse", 67, False, True, "mouse", 30, "Work mouse: 67%"))
        self.assertIsNone(dev["seconds_left"])

    def test_not_written_when_off(self):
        app = make_app()
        app.write_status([mouse(67)])
        self.assertFalse(os.path.exists(self.path))

    def test_hidden_devices_are_left_out(self):
        app = make_app({"status_file": True, "hidden": {KEY: "Viper"}})
        app.write_status([mouse(67), mouse(50, key="other", name="Other")])
        self.assertEqual([d["name"] for d in self.read()["devices"]], ["Other"])

    def test_time_left_only_on_battery(self):
        app = make_app({"status_file": True})
        app.history.seconds_left = lambda key, level: 7200.0
        app.write_status([mouse(67), mouse(50, key="c", charging=True), mouse(50, key="s", online=False)])
        self.assertEqual([d["seconds_left"] for d in self.read()["devices"]], [7200, None, None])

    def test_turning_off_removes_the_file(self):
        app = make_app({"status_file": True})
        app.write_status([mouse(67)])
        entry = item(item(app.build_menu(None), "Preferences").submenu, "Status file for other apps")
        self.assertTrue(entry.checked)
        entry(FakeTrayIcon())
        self.assertFalse(os.path.exists(self.path))

    def test_exit_leaves_running_false(self):
        app = make_app({"status_file": True})
        data = app.status_data([], running=False)
        self.assertEqual((data["running"], data["devices"]), (False, []))


if __name__ == "__main__":
    unittest.main()
