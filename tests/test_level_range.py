"""Tests for the central battery-level check in halo_battery.pyw.

Every provider validates its own replies, but a check missed in one of them put a wrong
percentage on an icon as if it were real. refuse_bad_levels() is the last step before a
reading reaches an icon, the history or an alert: a level that is not a number from 0 to
100 becomes "unknown" for every provider, HID and Bluetooth alike.

No tray, no hardware: the app module is loaded with the fake icons of test_hide_rename.py.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import HideRenameTestCase, hb, make_app  # noqa: E402
from providers.base import DeviceStatus  # noqa: E402


def mouse(level, **kw):
    return DeviceStatus("fake:1", "Fake Mouse", level, kw.pop("charging", False), True,
                        "fake", kind="mouse", **kw)


class FakeProvider:
    name = "fake"

    def __init__(self, results):
        self.results = list(results)

    def poll(self):
        return list(self.results)

    def diagnostics(self):
        return []


class LevelInRangeTests(unittest.TestCase):
    def test_the_whole_valid_range_is_accepted(self):
        for level in (0, 1, 50, 99, 100, 50.0):
            with self.subTest(level=level):
                self.assertTrue(hb.level_in_range(level))

    def test_everything_else_is_refused(self):
        for level in (-1, 101, 127, 200, 255, 1000, 65535, -0.5, 100.5,
                      float("nan"), float("inf"), True, False, "50", b"\x32", [50], None):
            with self.subTest(level=level):
                self.assertFalse(hb.level_in_range(level))


class RefuseBadLevelsTests(unittest.TestCase):
    def setUp(self):
        hb._refused_levels.clear()
        self.addCleanup(hb._refused_levels.clear)

    def test_a_valid_reading_passes_through_untouched(self):
        for level in (None, 0, 100, 42):
            with self.subTest(level=level):
                st = mouse(level, charging=True)
                self.assertIs(hb.refuse_bad_levels([st])[0], st)

    def test_a_bad_level_becomes_unknown_and_the_device_stays(self):
        for level in (-1, 101, 255, 1000, True, "50", float("nan")):
            with self.subTest(level=level):
                hb._refused_levels.clear()
                with self.assertLogs(hb.log, "WARNING"):
                    out = hb.refuse_bad_levels([mouse(level, charging=True)])
                self.assertEqual(len(out), 1)
                st = out[0]
                self.assertIsNone(st.level)
                self.assertEqual((st.key, st.name, st.charging, st.online, st.kind),
                                 ("fake:1", "Fake Mouse", True, True, "mouse"))

    def test_text_that_was_built_from_the_bad_level_goes_too(self):
        with self.assertLogs(hb.log, "WARNING"):
            st = hb.refuse_bad_levels([mouse(300, approx="about 300% (high)",
                                             ui_message="state.approx",
                                             ui_params={"level": 300})])[0]
        self.assertEqual((st.level, st.approx, st.ui_message, st.ui_params), (None, "", "", {}))

    def test_only_the_bad_reading_in_a_list_is_changed(self):
        good = DeviceStatus("fake:2", "Fake Headset", 80, False, True, "fake", kind="headset")
        with self.assertLogs(hb.log, "WARNING"):
            out = hb.refuse_bad_levels([mouse(255), good])
        self.assertIsNone(out[0].level)
        self.assertIs(out[1], good)

    def test_logged_once_per_value_not_on_every_poll(self):
        with self.assertLogs(hb.log, "WARNING") as first:
            hb.refuse_bad_levels([mouse(255)])
        self.assertEqual(len(first.records), 1)
        with self.assertNoLogs(hb.log, "WARNING"):
            for _ in range(5):
                hb.refuse_bad_levels([mouse(255)])

    def test_a_new_bad_value_or_a_relapse_is_logged_again(self):
        with self.assertLogs(hb.log, "WARNING") as logs:
            hb.refuse_bad_levels([mouse(255)])
            hb.refuse_bad_levels([mouse(254)])
            hb.refuse_bad_levels([mouse(50)])
            hb.refuse_bad_levels([mouse(254)])
        self.assertEqual(len(logs.records), 3)

    def test_nan_is_logged_once(self):
        with self.assertLogs(hb.log, "WARNING") as logs:
            for _ in range(3):
                hb.refuse_bad_levels([mouse(float("nan"))])
        self.assertEqual(len(logs.records), 1)

    def test_an_empty_poll_is_fine(self):
        self.assertEqual(hb.refuse_bad_levels([]), [])


class PollPathTests(HideRenameTestCase):
    def setUp(self):
        super().setUp()
        hb._refused_levels.clear()
        self.addCleanup(hb._refused_levels.clear)

    def app(self, results, bluetooth=False):
        app = make_app({"bluetooth": bluetooth})
        app.providers = [FakeProvider(results)]
        app.bt_cache = []
        app.bt_fresh = threading.Event()
        app.wake = hb.WakeEvent()
        app._bt_dup_logged = set()
        return app

    def test_a_hid_provider_cannot_put_a_bad_level_on_an_icon(self):
        app = self.app([mouse(255)])
        with self.assertLogs(hb.log, "WARNING"):
            results = app.poll_once()
        self.assertEqual([(s.key, s.level) for s in results], [("fake:1", None)])
        self.assertEqual(app.key_provider, {"fake:1": "fake"})
        app.apply(results)
        self.assertIsNone(app.icons["fake:1"].status.level)
        self.assertEqual(app.notes, [])                   # and no low-battery alert for it

    def test_a_good_level_is_shown_as_before(self):
        app = self.app([mouse(17)])
        results = app.poll_once()
        self.assertEqual([s.level for s in results], [17])

    def test_a_bluetooth_snapshot_is_checked_too(self):
        app = self.app([], bluetooth=True)
        buds = DeviceStatus("bt:AABBCCDDEEFF", "Buds", 200, False, True, "bluetooth",
                            kind="headset")
        with self.assertLogs(hb.log, "WARNING"):
            app._bt_update([buds])
        self.assertEqual([(s.key, s.level) for s in app.bt_cache], [("bt:AABBCCDDEEFF", None)])


if __name__ == "__main__":
    unittest.main()
