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

    def test_stop_posts_the_quit_through_the_private_prototypes(self):
        user32 = mock.MagicMock()
        watcher = D.DeviceEventWatcher(lambda reason: None)
        watcher._thread_id = 4242
        with mock.patch.object(D, "_user32_kernel32", return_value=(user32, mock.MagicMock())):
            watcher.stop()
        user32.PostThreadMessageW.assert_called_once_with(4242, D.WM_QUIT, 0, 0)


class InterfaceTests(unittest.TestCase):
    def test_the_bluetooth_radio_guid_is_the_one_windows_uses(self):
        # GUID_BTHPORT_DEVICE_INTERFACE in bthdef.h, the class Windows registers
        # under the DeviceClasses key for every Bluetooth radio
        self.assertIn("{0850302A-B344-4FDA-9BE9-90576B8D46F0}", D.INTERFACES)


@unittest.skipUnless(sys.platform == "win32", "Windows API")
class WindowThreadTests(unittest.TestCase):
    def test_the_prototypes_are_private_and_take_64_bit_handles(self):
        import ctypes
        user32, kernel32 = D._user32_kernel32()
        self.assertIsNot(user32, ctypes.windll.user32)
        instance = kernel32.GetModuleHandleW(None)   # above 4 GB on 64-bit Python
        # no OverflowError and no truncated handle, just "no such class"
        self.assertFalse(user32.UnregisterClassW("HaloBatteryNoSuchClass", instance))

    def run_thread(self, window):
        user32, kernel32 = mock.MagicMock(), mock.MagicMock()
        kernel32.GetModuleHandleW.return_value = None
        user32.RegisterClassW.return_value = 0xC0DE
        user32.CreateWindowExW.return_value = window
        user32.RegisterDeviceNotificationW.return_value = 0x51
        user32.GetMessageW.return_value = 0          # WM_QUIT at once
        watcher = D.DeviceEventWatcher(lambda reason: None)
        ready = threading.Event()
        with mock.patch.object(D, "_user32_kernel32", return_value=(user32, kernel32)):
            watcher._run(ready)
        self.assertTrue(ready.is_set())
        self.assertFalse(watcher.running())
        return user32

    def test_the_window_and_its_class_are_released_when_the_loop_ends(self):
        user32 = self.run_thread(0x1234)
        self.assertEqual(len(D.INTERFACES), user32.UnregisterDeviceNotification.call_count)
        user32.DestroyWindow.assert_called_once_with(0x1234)
        user32.UnregisterClassW.assert_called_once_with("HaloBatteryDeviceEvents", None)

    def test_the_class_is_released_when_no_window_can_be_made(self):
        user32 = self.run_thread(None)
        user32.DestroyWindow.assert_not_called()
        user32.UnregisterClassW.assert_called_once_with("HaloBatteryDeviceEvents", None)


if __name__ == "__main__":
    unittest.main()
