"""ATK V75X: real reply fixture, request isolation, and failed reads."""
import types
import unittest
from unittest import mock

from providers import atk_keyboard as A


CAPTURE = bytes.fromhex("04 01 6a 1a 00 00 00 00 3b 00" + " 00" * 54)
CHARGING_CAPTURE = bytes.fromhex("04 01 7b 1a 00 00 00 00 3c 01" + " 00" * 54)
UNPLUGGED_CAPTURE = bytes.fromhex("04 01 7c 1a 00 00 00 00 3e 00" + " 00" * 54)


def entry(**changes):
    result = {"vendor_id": A.VID, "product_id": A.PID,
              "product_string": A.PRODUCT, "usage_page": A.USAGE_PAGE,
              "usage": A.USAGE, "path": b"v75x-config"}
    result.update(changes)
    return result


class FakeReceiver:
    def __init__(self):
        self.writes, self.queue, self.opens = [], [], []
        self.closed = False
        self.level, self.state, self.silent = 59, 0, False
        self.noise, self.error = [], None

    def open_path(self, path):
        self.opens.append(path)
        if self.error == "open":
            raise OSError("busy")

    def write(self, request):
        self.writes.append(list(request))
        if self.error == "write":
            raise OSError("disconnected")
        response = list(CAPTURE)
        response[1:3] = request[1:3]
        response[8:10] = [self.level, self.state]
        self.queue.extend(self.noise)
        if not self.silent:
            self.queue.append(response)
        return 64

    def read(self, size, timeout):
        if self.error == "read":
            raise OSError("disconnected")
        return self.queue.pop(0) if self.queue else []

    def close(self):
        self.closed = True


class ReplyTests(unittest.TestCase):
    def test_real_59_percent_capture(self):
        self.assertEqual(A.battery_reply(CAPTURE, 0x6A01), (59, False))

    def test_vendor_charge_state_2_is_still_supported(self):
        report = list(CAPTURE)
        report[9] = 2
        self.assertEqual(A.battery_reply(report, 0x6A01), (59, True))

    def test_real_charging_capture(self):
        self.assertEqual(A.battery_reply(CHARGING_CAPTURE, 0x7B01), (60, True))

    def test_real_unplugged_capture(self):
        self.assertEqual(A.battery_reply(UNPLUGGED_CAPTURE, 0x7C01), (62, False))

    def test_zero_and_full_are_valid(self):
        for level in (0, 100):
            report = list(CAPTURE)
            report[8] = level
            self.assertEqual(A.battery_reply(report, 0x6A01), (level, False))

    def test_other_commands_requests_offsets_and_invalid_data_are_ignored(self):
        for index, value in ((0, 1), (1, 2), (2, 0), (3, 0xFF), (5, 1),
                             (6, 1), (8, 101), (8, 255), (9, 255)):
            with self.subTest(index=index, value=value):
                report = list(CAPTURE)
                report[index] = value
                self.assertIsNone(A.battery_reply(report, 0x6A01))

    def test_short_or_empty_reports_are_ignored(self):
        for size in range(10):
            self.assertIsNone(A.battery_reply(CAPTURE[:size], 0x6A01))


class PollTests(unittest.TestCase):
    def setUp(self):
        self.device = FakeReceiver()
        self.entries = [entry()]
        self.clock = 0.0
        def tick():
            self.clock += 0.1
            return self.clock
        patches = [mock.patch.object(A, "hid", types.SimpleNamespace(device=lambda: self.device)),
                   mock.patch.object(A.hidlist, "enumerate", side_effect=lambda vid: self.entries),
                   mock.patch.object(A.time, "monotonic", side_effect=tick)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.activity = mock.Mock()
        self.activity.active.return_value = True
        self.provider = A.AtkKeyboardProvider(activity=self.activity)

    def test_poll_reports_keyboard_and_sends_only_one_battery_query(self):
        status, = self.provider.poll()
        self.assertEqual((status.name, status.level, status.charging, status.online,
                          status.kind, status.source),
                         ("ATK V75X", 59, False, True, "keyboard", "atk_keyboard"))
        self.assertEqual(self.device.writes, [[4, 1, 0, 0x1A] + [0] * 28])
        self.assertTrue(self.device.closed)
        self.assertIn("59%", " ".join(self.provider.diagnostics()))

    def test_cable_changes_update_the_existing_keyboard_status(self):
        self.device.level, self.device.state = 60, 1
        charging, = self.provider.poll()
        self.assertEqual((charging.level, charging.charging, charging.online), (60, True, True))
        self.device.level, self.device.state = 62, 0
        unplugged, = self.provider.poll()
        self.assertEqual((unplugged.level, unplugged.charging, unplugged.online), (62, False, True))
        self.assertEqual(charging.key, unplugged.key)

    def test_other_collections_and_shared_vid_pid_keyboards_are_not_opened(self):
        other = [entry(usage_page=1, usage=6, path=b"typing"),
                 entry(usage_page=0xFFEF, usage=0, path=b"other-vendor"),
                 entry(product_string="Rainy 75", path=b"other-keyboard"),
                 entry(product_id=0x5055, path=b"wired")]
        self.entries = other + [entry()]
        self.provider.poll()
        self.assertEqual(self.device.opens, [b"v75x-config"])

    def test_no_matching_receiver_produces_no_icon(self):
        for entries in ([], [entry(product_string="Rainy 75")], [entry(product_string=None)]):
            self.entries = entries
            self.assertEqual(self.provider.poll(), [])
        self.assertEqual(self.device.opens, [])

    def test_stale_or_unrelated_reports_before_reply_are_skipped(self):
        self.device.noise = [list(CAPTURE), [1, 0, 0, 0], []]
        self.assertEqual(self.provider.poll()[0].level, 59)

    def test_each_poll_uses_a_fresh_request_id_and_wraps_to_nonzero(self):
        self.provider._sequence = 0xFFFF
        self.provider.poll()
        self.provider.poll()
        self.assertEqual([w[1:3] for w in self.device.writes], [[1, 0], [2, 0]])

    def test_silent_receiver_shows_unknown_offline_instead_of_zero(self):
        self.device.silent = True
        status, = self.provider.poll()
        self.assertEqual((status.level, status.online, status.charging), (None, False, False))
        self.assertFalse(status.wake_on_input)
        self.assertFalse(status.polling_paused)
        self.assertTrue(self.device.closed)
        self.assertLess(self.clock, 1.5)

    def test_recovery_after_sleep_reads_a_fresh_level(self):
        self.device.silent = True
        self.assertFalse(self.provider.poll()[0].online)
        self.device.silent, self.device.level = False, 58
        status, = self.provider.poll()
        self.assertEqual((status.level, status.online, status.wake_on_input), (58, True, False))

    def test_malformed_battery_does_not_become_a_displayed_percentage(self):
        self.device.level = 255
        self.assertIsNone(self.provider.poll()[0].level)

    def test_io_failures_close_handle_and_produce_unknown(self):
        for operation in ("open", "write", "read"):
            with self.subTest(operation=operation):
                self.device.error, self.device.closed = operation, False
                status, = self.provider.poll()
                self.assertIsNone(status.level)
                self.assertFalse(status.online)
                self.assertTrue(self.device.closed)

    def test_idle_poll_never_opens_receiver_and_retains_cached_level(self):
        self.provider.poll()
        self.activity.active.return_value = False
        count = len(self.device.writes)
        for _ in range(10):
            status, = self.provider.poll()
            self.assertEqual(status.level, 59)
            self.assertFalse(status.online)
            self.assertFalse(status.charging)
            self.assertTrue(status.polling_paused)
            self.assertEqual(status.approx, "")
            self.assertFalse(status.wake_on_input)
        self.assertEqual(len(self.device.writes), count)

    def test_startup_waits_for_typing_without_sending_a_query(self):
        self.activity.active.return_value = False
        status, = self.provider.poll()
        self.assertIsNone(status.level)
        self.assertEqual(self.device.opens, [])

    def test_listener_failure_does_not_fall_back_to_active_polling(self):
        self.activity.start.return_value = False
        self.provider.poll()
        self.assertEqual(self.device.writes, [])

    def test_disconnect_clears_cached_level(self):
        self.provider.poll()
        self.entries = []
        self.provider.poll()
        self.entries = [entry()]
        self.activity.active.return_value = False
        self.assertIsNone(self.provider.poll()[0].level)

    def test_missing_hid_library_is_harmless(self):
        with mock.patch.object(A, "hid", None):
            self.assertEqual(self.provider.poll(), [])


if __name__ == "__main__":
    unittest.main()
