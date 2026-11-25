"""Device and power broadcasts without a window: the mapping and the coalescing."""
import sys
import threading
import time
import unittest
from unittest import mock

from halo import device_events as D


class ClassifyTests(unittest.TestCase):
    def test_a_device_arrival_or_removal_asks_for_a_poll(self):
        for reason in (D.DBT_DEVICEARRIVAL, D.DBT_DEVICEREMOVECOMPLETE, D.DBT_DEVNODES_CHANGED):
            self.assertEqual("device", D.classify(D.WM_DEVICECHANGE, reason))

    def test_resume_asks_for_a_poll(self):
        for reason in (D.PBT_APMRESUMESUSPEND, D.PBT_APMRESUMEAUTOMATIC):
            self.assertEqual("resume", D.classify(D.WM_POWERBROADCAST, reason))

    def test_suspend_and_unrelated_messages_are_ignored(self):
        self.assertIsNone(D.classify(D.WM_POWERBROADCAST, D.PBT_APMSUSPEND))
        self.assertIsNone(D.classify(D.WM_DEVICECHANGE, 0x0001))   # DBT_CONFIGCHANGED
        self.assertIsNone(D.classify(0x0400, D.DBT_DEVICEARRIVAL))  # a private message


class DebounceTests(unittest.TestCase):
    def test_a_storm_of_device_messages_polls_once(self):
        seen = []
        done = threading.Event()

        def callback(reason):
            seen.append(reason)
            done.set()

        watcher = D.DeviceEventWatcher(callback, device_delay=0.05)
        for _ in range(6):
            watcher._signal("device")
        self.assertTrue(done.wait(2.0))
        time.sleep(0.1)                     # a second callback would land here
        self.assertEqual(["device"], seen)

    def test_a_resume_takes_over_a_pending_device_message(self):
        seen = []
        done = threading.Event()

        def callback(reason):
            seen.append(reason)
            done.set()

        watcher = D.DeviceEventWatcher(callback, device_delay=0.05, resume_delay=0.05)
        watcher._signal("device")
        watcher._signal("resume")
        self.assertTrue(done.wait(2.0))
        time.sleep(0.1)
        self.assertEqual(["resume"], seen)

    def test_stop_drops_the_pending_callback(self):
        seen = []
        watcher = D.DeviceEventWatcher(seen.append, device_delay=0.2)
        watcher._signal("device")
        watcher.stop()
        time.sleep(0.35)
        self.assertEqual([], seen)

    def test_start_is_a_no_op_outside_windows(self):
        watcher = D.DeviceEventWatcher(lambda reason: None)
        with mock.patch.object(D.sys, "platform", "linux"):
            self.assertFalse(watcher.start())
        self.assertFalse(watcher.running())


if __name__ == "__main__":
    unittest.main()
