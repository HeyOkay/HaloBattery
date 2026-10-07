"""Tests for how a Bluetooth snapshot reaches the tray in halo_battery.pyw.

The Bluetooth watcher sends a snapshot at least once a minute and several after each
connect or disconnect. A snapshot must show the new Bluetooth state at once, but it
must not poll the HID devices: they are polled at the user's "Poll interval".

No tray, no hardware: the app module is loaded with the fake icons of
test_hide_rename.py, and the real App.loop() runs in a thread.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import FakeIcon, FakeTrayIcon, hb, make_app  # noqa: E402
from providers.base import DeviceStatus  # noqa: E402

MOUSE = DeviceStatus("razer:00c8:1", "Viper V2 Pro", 70, False, True, "razer", kind="mouse")
BUDS = DeviceStatus("bt:AABBCCDDEEFF", "WH-1000XM4", 40, False, True, "bluetooth", kind="headset")
PAD_BT = DeviceStatus("bt:112233445566", "Xbox Wireless Controller", 80, False, True, "bluetooth",
                      kind="gamepad")
PAD_XI = DeviceStatus("xinput:0", "Xbox Wireless Controller", 60, False, True, "xinput", kind="gamepad")


class FakeProvider:
    """A HID provider that counts its polls (each poll is hardware I/O in the real app)."""
    name = "fake"

    def __init__(self, results):
        self.results = list(results)
        self.polls = 0

    def poll(self):
        self.polls += 1
        return list(self.results)

    def diagnostics(self):
        return []


def until(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class WakeEventTests(unittest.TestCase):
    def test_bluetooth_wake_is_not_a_full_poll(self):
        w = hb.WakeEvent()
        w.bluetooth()
        self.assertTrue(w.is_set())
        self.assertFalse(w.take())
        self.assertFalse(w.is_set())

    def test_set_is_a_full_poll_once(self):
        w = hb.WakeEvent()
        w.set()
        w.bluetooth()
        self.assertTrue(w.take())
        w.bluetooth()
        self.assertFalse(w.take())

    def test_clear_forgets_a_full_poll(self):
        """The poll thread clears the event after each poll, which has just run."""
        w = hb.WakeEvent()
        w.set()
        w.clear()
        self.assertFalse(w.is_set())
        w.bluetooth()
        self.assertFalse(w.take())


class BluetoothSnapshotTests(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(hb, "DeviceIcon", FakeIcon),
            mock.patch.object(hb.pystray, "Icon", FakeTrayIcon),
            mock.patch.object(hb, "save_config", lambda cfg: None),
            # The real desktop's full-screen state must not extend the test interval.
            mock.patch.object(hb, "fullscreen_app_running", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def start(self, hid, interval=300):
        app = make_app({"bluetooth": True, "interval": interval})
        app.wake = hb.WakeEvent()
        app.stop_evt = threading.Event()
        app.diag_requested = threading.Event()
        app.bt_fresh = threading.Event()
        app.bt_cache = []
        app._bt_dup_logged = set()
        app.was_quiet = False                   # read by loop() (quiet while gaming)
        app.provider = FakeProvider(hid)
        app.providers = [app.provider]
        app.change_signature = lambda: "nothing plugged or unplugged"
        t = threading.Thread(target=app.loop, daemon=True)
        t.start()

        def stop():
            app.stop_evt.set()
            app.wake.set()
            t.join(5)
        self.addCleanup(stop)
        self.assertTrue(until(lambda: all(s.key in app.icons for s in hid)), "first poll")
        self.assertTrue(until(lambda: app.provider.polls == 1))
        return app

    def test_snapshot_shows_bluetooth_device_without_polling_hid(self):
        app = self.start([MOUSE])
        app._bt_update([BUDS])
        self.assertTrue(until(lambda: BUDS.key in app.icons), "the Bluetooth icon did not appear")
        self.assertEqual(app.icons[BUDS.key].status.level, 40)
        self.assertIn(MOUSE.key, app.icons)
        time.sleep(0.1)
        self.assertEqual(app.provider.polls, 1, "a Bluetooth snapshot polled the HID devices")

    def test_many_snapshots_do_not_poll_hid(self):
        app = self.start([MOUSE])
        for level in (40, 39, 38, 37, 36):
            app._bt_update([DeviceStatus(BUDS.key, BUDS.name, level, False, True, "bluetooth",
                                         kind="headset")])
            self.assertTrue(until(lambda: BUDS.key in app.icons
                                  and app.icons[BUDS.key].status.level == level))
        time.sleep(0.1)
        self.assertEqual(app.provider.polls, 1)

    def test_snapshot_removes_disconnected_bluetooth_device_without_polling_hid(self):
        app = self.start([MOUSE])
        app._bt_update([BUDS])
        self.assertTrue(until(lambda: BUDS.key in app.icons))
        icon = app.icons[BUDS.key]
        app._bt_update([])
        self.assertTrue(until(lambda: BUDS.key not in app.icons), "the Bluetooth icon stayed")
        self.assertTrue(icon.stopped)
        self.assertIn(MOUSE.key, app.icons)
        self.assertEqual(app.provider.polls, 1)

    def test_snapshot_drops_the_xinput_copy_of_a_bluetooth_controller(self):
        """The merge with the last HID results still runs: a controller that now shows up
        over Bluetooth replaces its XInput copy at once, as a full poll did."""
        app = self.start([PAD_XI])
        self.assertTrue(until(lambda: PAD_XI.key in app.icons))
        app._bt_update([PAD_BT])
        self.assertTrue(until(lambda: PAD_BT.key in app.icons and PAD_XI.key not in app.icons))
        self.assertEqual(app.provider.polls, 1)

    def test_snapshot_does_not_count_a_missed_hid_device_again(self):
        """A HID device the last poll did not see is removed after two missed polls.
        A Bluetooth snapshot between the polls is not a poll, so it must not count as
        the second miss."""
        app = self.start([MOUSE])
        app.provider.results = []               # the next poll misses the mouse once
        app.wake.set()
        self.assertTrue(until(lambda: app.provider.polls == 2))
        self.assertTrue(until(lambda: app.missing.get(MOUSE.key) == 1))
        app._bt_update([BUDS])
        self.assertTrue(until(lambda: BUDS.key in app.icons))
        self.assertIn(MOUSE.key, app.icons, "one missed poll and a snapshot removed the mouse")
        self.assertEqual(app.missing.get(MOUSE.key), 1)

    def test_refresh_now_still_polls_hid(self):
        app = self.start([MOUSE])
        app._bt_update([BUDS])                  # a snapshot and a "Refresh now" close together
        app.wake.set()
        self.assertTrue(until(lambda: app.provider.polls == 2), "Refresh now did not poll")
        self.assertTrue(until(lambda: BUDS.key in app.icons))

    def test_refresh_during_a_poll_is_not_lost_to_a_snapshot(self):
        """A "Refresh now" while a poll runs, then a snapshot before the poll ends: the
        refresh still gives a full poll."""
        app = self.start([MOUSE])
        polled = app.provider.poll

        def slow_poll():
            if app.provider.polls == 1:
                app.wake.set()                  # "Refresh now" during this poll
                app._bt_update([BUDS])          # and a snapshot too
            return polled()
        app.provider.poll = slow_poll
        app.wake.set()
        self.assertTrue(until(lambda: app.provider.polls >= 3), "the refresh was lost")

    def test_poll_interval_still_polls_hid(self):
        app = self.start([MOUSE], interval=0.3)
        self.assertTrue(until(lambda: app.provider.polls >= 3))

    def test_fallback_poll_shows_bluetooth_without_polling_hid(self):
        """Without the watcher, bt_loop's one-shot poll once a minute works the same way."""
        app = self.start([MOUSE])
        app.bt_watch = None
        app.bt_watch_failed = True
        app.bt_wake = threading.Event()
        app.bt = FakeProvider([BUDS])
        t = threading.Thread(target=app.bt_loop, daemon=True)
        t.start()
        self.assertTrue(until(lambda: BUDS.key in app.icons))
        app.stop_evt.set()
        app.bt_wake.set()
        t.join(5)
        self.assertTrue(app.bt_fresh.is_set())
        self.assertEqual(app.provider.polls, 1)


if __name__ == "__main__":
    unittest.main()
