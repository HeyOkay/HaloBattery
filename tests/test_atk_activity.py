import unittest
from unittest import mock
from providers.atk_activity import KeyboardActivity

class ActivityTests(unittest.TestCase):
    def test_only_matching_keyboard_input_enables_queries(self):
        a = KeyboardActivity()
        a.configure([b"ATK-KEYS"])
        a.wake = mock.Mock()
        with mock.patch("providers.atk_activity.time.monotonic", return_value=10):
            for kind, path in ((0,"atk-keys"),(2,"atk-keys"),(1,"other")):
                a.observe(kind,path)
                self.assertFalse(a.active())
            a.observe(1,"atk-keys")
            self.assertTrue(a.active())
            a.wake.assert_called_once()
            a.observe(1,"atk-keys")
            a.wake.assert_called_once()
        with mock.patch("providers.atk_activity.time.monotonic", return_value=19.999):
            self.assertTrue(a.active())
        with mock.patch("providers.atk_activity.time.monotonic", return_value=20):
            self.assertFalse(a.active())

    def test_typing_bursts_wake_at_most_once_per_retry_gap(self):
        from providers.atk_activity import RETRY
        a = KeyboardActivity()
        a.configure(["keys"])
        a.wake = mock.Mock()
        for t in (0, 15, RETRY - 0.1):     # pause, type again: no new full poll
            with mock.patch("providers.atk_activity.time.monotonic", return_value=t):
                a.observe(1, "keys")
        a.wake.assert_called_once()
        with mock.patch("providers.atk_activity.time.monotonic", return_value=RETRY):
            a.observe(1, "keys")
        self.assertEqual(a.wake.call_count, 2)

    def test_hold_suppresses_wakes_until_the_reading_is_due(self):
        a = KeyboardActivity()
        a.configure(["keys"])
        a.wake = mock.Mock()
        with mock.patch("providers.atk_activity.time.monotonic", return_value=100):
            a.hold(300)
        with mock.patch("providers.atk_activity.time.monotonic", return_value=399):
            a.observe(1, "keys")
            self.assertTrue(a.active())
        a.wake.assert_not_called()
        with mock.patch("providers.atk_activity.time.monotonic", return_value=400):
            a.observe(1, "keys")
        a.wake.assert_called_once()
        a.configure(["other"])
        self.assertIsNone(a.next_wake)

    def test_device_change_and_clock_reversal_cancel_activity(self):
        a=KeyboardActivity()
        a.configure(["keys"])
        with mock.patch("providers.atk_activity.time.monotonic",return_value=20):
            a.observe(1,"keys")
        with mock.patch("providers.atk_activity.time.monotonic",return_value=10):
            self.assertFalse(a.active())
        a.configure([])
        self.assertIsNone(a.last)

    def test_hid_and_keyboard_interface_guids_match_the_same_device_instance(self):
        a=KeyboardActivity()
        a.configure([r"\\?\hid#vid_320f&pid_5088&mi_00#instance-a#{hid-guid}\kbd"])
        a.observe(1,r"\\?\HID#VID_320F&PID_5088&MI_00#instance-b#{keyboard-guid}")
        self.assertFalse(a.active())
        a.observe(1,r"\\?\HID#VID_320F&PID_5088&MI_00#instance-a#{keyboard-guid}")
        self.assertTrue(a.active())
