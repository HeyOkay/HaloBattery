"""Tests for providers/logitech_g435_rtk.py. No hardware is needed.

The reply shapes are the ones measured on a real G435 and its Realtek receiver
(046D:0ACB) with an independent reader
(https://github.com/fr0styJS/g435-lightspeed-battery):

  * 06 80 00 <mv lo> <mv hi> 00 00      -> 0x0F00 = 3840 mV
  * 06 80 00 66 00 00 00                -> the receiver's own local answer (102 mV),
                                           must never become a level
  * the first read after entering bootloader can arrive without the "P I" prefix

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import logitech_g435_rtk as G  # noqa: E402


def reply(payload):
    return list(bytes([0x06]) + payload)


GOOD_3840 = reply(bytes.fromhex("06 80 00 00 0f 00 00"))
GOOD_3840_PREFIXED = reply(bytes.fromhex("50 49 01 c2 24 a8 05 00 07 00 06 80 00 00 0f 00 00"))
LOCAL_102 = reply(bytes.fromhex("06 80 00 66 00 00 00"))
SHORT = [0x06, 0x80]
GARBAGE = reply(bytes.fromhex("11 22 33 44 55 66 77"))


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.opened = []

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def get_feature_report(self, report_id, length):
        if self.replies:
            return self.replies.pop(0)
        return []

    def close(self):
        self.closed = True

    def read(self, size, timeout):            # not used by this provider
        return []


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (G.hid, G.hidlist, G.time)
        self.now = [1000.0]
        G.time = types.SimpleNamespace(
            monotonic=lambda: self.now[0],
            time=lambda: self.now[0],
            sleep=lambda s: self.now.__setitem__(0, self.now[0] + s),
        )
        self.device = FakeDevice([])
        G.hid = types.SimpleNamespace(device=lambda: self.device)
        self.entries = [{"path": b"receiver-1", "interface_number": 3,
                         "usage_page": G.VENDOR_PAGE, "usage": 0x0001,
                         "product_id": G.PID,
                         "product_string": "G435 Wireless Gaming Headset"}]
        G.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.entries))
        self.provider = G.LogitechG435RtkProvider()

    def tearDown(self):
        G.hid, G.hidlist, G.time = self._saved

    def poll_with(self, replies):
        self.device.replies = list(replies)
        return self.provider.poll()

    # ------------------------------------------------------------- parsing

    def test_the_measured_reply_reads_3840(self):
        self.assertEqual(3840, G.parse_mv(GOOD_3840))

    def test_a_reply_that_keeps_the_pi_prefix_is_read_too(self):
        self.assertEqual(3840, G.parse_mv(GOOD_3840_PREFIXED))

    def test_the_local_answer_is_never_a_level(self):
        self.assertIsNone(G.parse_mv(LOCAL_102))

    def test_short_and_garbage_replies_are_refused(self):
        self.assertIsNone(G.parse_mv(SHORT))
        self.assertIsNone(G.parse_mv(GARBAGE))
        self.assertIsNone(G.parse_mv([]))

    def test_the_voltage_window_is_enforced(self):
        self.assertIsNone(G.parse_mv(reply(bytes.fromhex("06 80 00 b7 0b 00 00"))))  # 2999
        self.assertEqual(3000, G.parse_mv(reply(bytes.fromhex("06 80 00 b8 0b 00 00"))))
        self.assertEqual(4300, G.parse_mv(reply(bytes.fromhex("06 80 00 cc 10 00 00"))))
        self.assertIsNone(G.parse_mv(reply(bytes.fromhex("06 80 00 cd 10 00 00"))))  # 4301

    # ---------------------------------------------------------------- poll

    def test_a_reading_becomes_an_approximate_level(self):
        status, = self.poll_with([[], GOOD_3840])
        self.assertEqual("logitech:0acb", status.key)
        self.assertEqual(56, status.level)
        self.assertEqual("about 56%", status.approx)
        self.assertTrue(status.online)
        self.assertFalse(status.charging)
        self.assertFalse(status.charging_known)
        self.assertEqual("headset", status.kind)

    def test_a_silent_headset_is_offline_and_keeps_the_last_level(self):
        self.poll_with([[], GOOD_3840])
        self.now[0] += G.POLL_FLOOR_S + 1
        status, = self.poll_with([[], []])          # no reply, read times out
        self.assertEqual(56, status.level)          # kept, but greyed
        self.assertFalse(status.online)

    def test_without_a_previous_level_a_silent_headset_has_none(self):
        self.now[0] += G.POLL_FLOOR_S + 1
        status, = self.poll_with([[], []])
        self.assertIsNone(status.level)
        self.assertFalse(status.online)

    def test_the_cache_avoids_a_second_bootloader_session(self):
        self.poll_with([[], GOOD_3840])
        opened = len(self.device.opened)
        self.now[0] += 60
        self.poll_with([[], GOOD_3840])
        self.assertEqual(opened, len(self.device.opened))   # no new open

    def test_the_cache_expires_after_fifteen_minutes(self):
        self.poll_with([[], GOOD_3840])
        opened = len(self.device.opened)
        self.now[0] += G.POLL_FLOOR_S - 1
        self.poll_with([[], GOOD_3840])
        self.assertEqual(opened, len(self.device.opened))
        self.now[0] += 2
        self.poll_with([[], GOOD_3840])
        self.assertEqual(opened + 1, len(self.device.opened))

    def test_a_changed_receiver_path_drops_the_cache(self):
        self.poll_with([[], GOOD_3840])
        opened = len(self.device.opened)
        self.entries[0]["path"] = b"receiver-2"     # replug on another port
        self.now[0] += 10
        self.poll_with([[], GOOD_3840])
        self.assertEqual(opened + 1, len(self.device.opened))

    def test_a_missing_receiver_returns_nothing_and_forgets_the_level(self):
        self.poll_with([[], GOOD_3840])
        self.entries[:] = []
        self.assertEqual([], self.provider.poll())
        self.entries[:] = [{"path": b"receiver-1", "interface_number": 3,
                            "usage_page": G.VENDOR_PAGE, "usage": 0x0001,
                            "product_id": G.PID, "product_string": "G435"}]
        self.now[0] += 5
        status, = self.poll_with([[], GOOD_3840])
        self.assertEqual(56, status.level)          # re-read, not the old cache

    # ------------------------------------------------------------- cleanup

    def test_bootloader_is_exited_and_remote_mode_is_left(self):
        self.poll_with([[], GOOD_3840])
        frames = self.device.sent
        self.assertEqual(1, frames[0][1])                       # enter
        self.assertEqual(8, frames[-1][1])                      # exit last
        self.assertTrue(any(f[1] == 4 and f[10] == 0xE1 and f[11] == 0x00 for f in frames))

    def test_a_failed_read_still_leaves_remote_mode_and_bootloader(self):
        self.poll_with([[], GARBAGE])
        frames = self.device.sent
        self.assertEqual(8, frames[-1][1])
        self.assertTrue(any(f[1] == 4 and f[10] == 0xE1 and f[11] == 0x00 for f in frames))


if __name__ == "__main__":
    unittest.main()

