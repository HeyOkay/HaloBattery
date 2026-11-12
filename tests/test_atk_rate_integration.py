"""Exercise receiver rate limits and transport changes with real activity tracking."""
import types
import unittest
from unittest import mock

from providers import atk_keyboard as A
from providers.atk_activity import KeyboardActivity
from test_atk_keyboard import FakeReceiver, entry


class RateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.device = FakeReceiver()
        self.receiver = entry()
        self.keys = entry(usage_page=1, usage=6, path=b"atk-keys")
        self.cable = entry(product_id=A.WIRED_PID, product_string=A.WIRED_PRODUCT,
                           path=b"atk-cable")
        self.entries = [self.receiver, self.keys]
        self.activity = KeyboardActivity()
        self.activity.wake = mock.Mock()
        self.provider = A.AtkKeyboardProvider(activity=self.activity)
        for patch in (
            mock.patch.object(A, "hid", types.SimpleNamespace(device=lambda: self.device)),
            mock.patch.object(A.hidlist, "enumerate", side_effect=lambda vid: self.entries),
            mock.patch.object(A.time, "monotonic", side_effect=lambda: self.now),
            mock.patch.object(self.activity, "start", return_value=True),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        # Let poll configure the actual keyboard interface before any input.
        self.assertTrue(self.provider.poll()[0].polling_paused)

    def type_key(self):
        self.activity.observe(1, self.keys["path"])

    def test_success_hold_expires_without_losing_typing_activity(self):
        self.type_key()
        self.assertEqual(self.provider.poll()[0].level, 59)
        self.activity.wake.reset_mock()
        self.device.level = 58
        for elapsed in (100, 200, A.REFRESH - 1):
            self.now = 100 + elapsed
            self.type_key()
            self.assertEqual(self.provider.poll()[0].level, 59)
        self.assertTrue(self.activity.active())
        self.activity.wake.assert_not_called()
        self.assertEqual(len(self.device.writes), 1)
        self.now = 100 + A.REFRESH
        self.type_key()
        self.activity.wake.assert_called_once_with()
        self.assertEqual(self.provider.poll()[0].level, 58)
        self.assertEqual(len(self.device.writes), 2)

    def test_failed_attempt_throttles_both_input_wakes_and_requests(self):
        self.device.error = "write"
        self.type_key()
        self.assertFalse(self.provider.poll()[0].online)
        self.activity.wake.reset_mock()
        self.device.error = None
        for elapsed in (1, 15, A.RETRY - 1):
            self.now = 100 + elapsed
            self.type_key()
            self.assertFalse(self.provider.poll()[0].online)
        self.activity.wake.assert_not_called()
        self.assertEqual(len(self.device.writes), 1)
        self.now = 100 + A.RETRY
        self.type_key()
        self.activity.wake.assert_called_once_with()
        self.assertTrue(self.provider.poll()[0].online)
        self.assertEqual(len(self.device.writes), 2)

    def test_transport_switch_discards_receiver_success_and_failure_timers(self):
        for failed in (False, True):
            with self.subTest(failed=failed):
                # Start each case with a clean receiver after a full disconnect.
                self.entries = []
                self.provider.poll()
                self.entries = [self.receiver, self.keys]
                self.provider.poll()
                self.device.error = "write" if failed else None
                self.device.state = 1
                self.type_key()
                receiver = self.provider.poll()[0]
                count = len(self.device.writes)
                self.device.error = None
                self.device.level, self.device.state = 91, 1
                self.entries.append(self.cable)
                cable = self.provider.poll()[0]
                self.assertEqual(cable.key, receiver.key)
                self.assertEqual((cable.level, cable.charging), (91, True))
                self.assertEqual(len(self.device.writes), count + 1)
                self.entries = [self.receiver, self.keys]
                paused = self.provider.poll()[0]
                self.assertEqual((paused.level, paused.charging, paused.polling_paused),
                                 (91, False, True))
                self.device.level, self.device.state = 90, 0
                self.type_key()
                resumed = self.provider.poll()[0]
                self.assertEqual((resumed.key, resumed.level, resumed.charging, resumed.online),
                                 (receiver.key, 90, False, True))
                self.assertEqual(self.device.opens[-2:], [b"atk-cable", self.receiver["path"]])
                self.assertEqual(len(self.device.writes), count + 2)

    def test_disconnect_reconnect_discards_fresh_reading_and_waits_for_input(self):
        self.type_key()
        first = self.provider.poll()[0]
        self.activity.wake.reset_mock()
        self.entries = []
        self.assertEqual(self.provider.poll(), [])
        self.entries = [self.receiver, self.keys]
        pending = self.provider.poll()[0]
        self.assertIsNone(pending.level)
        self.assertTrue(pending.polling_paused)
        self.assertEqual(len(self.device.writes), 1)
        self.device.level = 40
        self.type_key()
        self.activity.wake.assert_called_once_with()
        reconnected = self.provider.poll()[0]
        self.assertEqual((reconnected.key, reconnected.level, reconnected.online),
                         (first.key, 40, True))
        self.assertEqual(len(self.device.writes), 2)


if __name__ == "__main__":
    unittest.main()
