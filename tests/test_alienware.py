"""Tests for providers/alienware.py. No hardware is needed.

The frames are the ones Alienware Command Center v6.14.54.0 exchanged with the
Alienware Pro Wireless keyboard's receiver in the #287 USBPcap captures:

  * SET_REPORT, feature, report id 0, 64 bytes: ``00 | 0e a1 00 ...``
  * input report: ``0e a1 25 <state> ...`` -> 0x25 = 37 %, the percentage Command Center
    showed; the charging capture (process3) has byte 3 at 0x04 while the cable is in.

The collection is the one from the reporter's 2.4 GHz diagnostics: 413c:211d, vendor
usage ff00:0001 on interface 1 (the keyboard and consumer collections carry no battery
exchange).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import alienware as A  # noqa: E402


def frame(cmd, value, pad=0x00):
    f = [A.REPORT_MARK, cmd, value] + [pad] * (A.REPORT_SIZE - 3)
    return f


CAPTURED_37 = frame(A.BATTERY_CMD, 0x25)
CAPTURED_37_PREFIXED = [A.REPORT_ID] + CAPTURED_37   # hidapi keeps the report id in front
CAPTURED_CHARGING = frame(A.BATTERY_CMD, 0x13)       # 19 %, charging
CAPTURED_CHARGING[3] = 0x04
CAPTURED_STATE = frame(0x05, 0x00, pad=0xAC)      # the receiver's unsolicited state report


class FakeDevice:
    def __init__(self, replies, accept=True):
        self.replies = list(replies)
        self.accept = accept
        self.sent = []
        self.opened = []

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data) if self.accept else -1

    def read(self, size, timeout):
        if self.replies:
            return self.replies.pop(0)
        return []


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist, A.time)
        self.now = [1000.0]
        A.time = types.SimpleNamespace(time=lambda: self.now[0])
        self.device = FakeDevice([CAPTURED_STATE, CAPTURED_37])
        A.hid = types.SimpleNamespace(device=lambda: self.device)
        self.entries = [{"path": b"kb-vendor", "interface_number": 1,
                         "usage_page": 0xFF00, "usage": 0x0001,
                         "product_id": A.PID, "product_string": "Alienware Pro KB Gaming Receiver"}]
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.entries))
        self.provider = A.AlienwareProvider()

    def tearDown(self):
        A.hid, A.hidlist, A.time = self._saved

    # ---------------------------------------------------------------- frames

    def test_the_request_carries_the_report_id_in_front(self):
        req = A.make_request()
        self.assertEqual(A.WIRE_SIZE, len(req))
        self.assertEqual([A.REPORT_ID, 0x0E, 0xA1], req[:3])
        self.assertEqual([0] * (A.WIRE_SIZE - 3), req[3:])

    def test_the_captured_reply_reads_37(self):
        self.assertEqual((37, False), A.parse_reply(CAPTURED_37))

    def test_a_reply_that_keeps_the_report_id_in_front_is_read_too(self):
        self.assertEqual((37, False), A.parse_reply(CAPTURED_37_PREFIXED))

    def test_the_charging_capture_sets_the_charging_state(self):
        self.assertEqual((19, True), A.parse_reply(CAPTURED_CHARGING))

    def test_other_reports_are_ignored(self):
        self.assertIsNone(A.parse_reply(CAPTURED_STATE))
        self.assertIsNone(A.parse_reply(frame(0x05, 0x00)))
        self.assertIsNone(A.parse_reply([0x0E, 0xA1]))
        self.assertIsNone(A.parse_reply([]))

    def test_a_zero_or_oversized_level_is_refused(self):
        self.assertIsNone(A.parse_reply(frame(A.BATTERY_CMD, 0)))
        self.assertIsNone(A.parse_reply(frame(A.BATTERY_CMD, 101)))

    # ---------------------------------------------------------------- polling

    def test_a_charging_reply_shows_charging(self):
        self.device.replies = [CAPTURED_CHARGING]
        out = self.provider.poll()
        self.assertEqual(19, out[0].level)
        self.assertTrue(out[0].charging)

    def test_the_level_is_read_from_the_vendor_collection(self):
        out = self.provider.poll()
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("alienware:211d", "Alienware Pro Wireless Gaming Keyboard", 37,
                           False, True, "alienware", "keyboard")])
        self.assertTrue(out[0].charging_known)      # byte 3 tells the cable apart
        self.assertEqual([b"kb-vendor"], self.device.opened)
        self.assertEqual([bytes(A.make_request())], self.device.sent)

    def test_silence_keeps_the_last_level_greyed_out(self):
        self.provider.poll()
        self.device.replies = []
        self.now[0] += 30
        out = self.provider.poll()
        self.assertEqual(37, out[0].level)
        self.assertFalse(out[0].online)
        self.now[0] += A.ASLEEP_KEEP + 1
        out = self.provider.poll()
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)

    def test_no_receiver_reports_nothing(self):
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [])
        self.assertEqual([], self.provider.poll())

    def test_a_refused_feature_write_is_not_a_reading(self):
        self.device.accept = False
        out = self.provider.poll()
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)
        diag = "\n".join(self.provider.diagnostics())
        self.assertIn("feature write refused", diag)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
