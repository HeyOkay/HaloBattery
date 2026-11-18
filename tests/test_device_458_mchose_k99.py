"""Tests for the MCHOSE K99 V3 reader, #458; no hardware needed.

The K99 V3 uses the 64-byte generic-desktop HID report on 3837:3033:
request ``55 03 00 38 38 ...``; reply ``AA 03 00 38 ...`` with the level at
byte 8.  The byte-6 status is logged but its charge semantics are not
documented, so the reader reports the charger as unknown.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import mchose_k99 as K  # noqa: E402


def reply(level=83, status=0x00, report_id=False):
    r = [0x00] * 64
    r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[8] = 0xAA, 0x03, 0x00, 0x38, 0x00, 0x00, status, level
    return ([0x00] + r) if report_id else r


def entries():
    return [{"vendor_id": 0x3837, "product_id": 0x3033, "interface_number": 1,
             "usage_page": 0x0001, "usage": 0x0000,
             "path": b"k99", "product_string": "MCHOSE K99 V3 2.4G"}]


class FakeDev:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.sent = []
        self.path = None

    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def set_nonblocking(self, value):
        pass

    def write(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        if not self.sent:
            return []
        return self.replies.pop(0) if self.replies else []


class ParseTests(unittest.TestCase):
    def test_the_request_is_the_k99_v3_battery_query(self):
        req = K.make_request()
        self.assertEqual(len(req), 64)
        self.assertEqual(list(req[:8]), [0x55, 0x03, 0x00, 0x38, 0x38, 0x00, 0x00, 0x00])
        wire = K.wire_request()
        self.assertEqual(len(wire), 65)
        self.assertEqual(wire[0], 0x00)
        self.assertEqual(wire[1:], req)

    def test_the_reply_echoes_the_command_and_reads_the_level(self):
        self.assertEqual(K.parse_reply(reply(83, 0x10)), (83, 0x10))
        self.assertEqual(K.parse_reply(reply(1, 0x00)), (1, 0x00))
        self.assertEqual(K.parse_reply(reply(41, 0x10, report_id=True)), (41, 0x10))

    def test_a_foreign_reply_or_bad_level_is_refused(self):
        bad = reply()
        bad[0] = 0x55
        self.assertIsNone(K.parse_reply(bad))
        bad = reply()
        bad[3] = 0x39
        self.assertIsNone(K.parse_reply(bad))
        self.assertIsNone(K.parse_reply(reply(0)))
        self.assertIsNone(K.parse_reply(reply(101)))
        self.assertIsNone(K.parse_reply([]))


class PollTests(unittest.TestCase):
    def setUp(self):
        self._hid = K.hid
        self._enumerate = K.hidlist.enumerate

    def tearDown(self):
        K.hid = self._hid
        K.hidlist.enumerate = self._enumerate

    def poll(self, fake):
        K.hid = types.SimpleNamespace(device=lambda: fake)
        K.hidlist.enumerate = lambda vid=0: entries()
        p = K.MchoseK99Provider()
        return p.poll(), p.diagnostics()

    def test_the_keyboard_is_read_and_the_charger_is_unknown(self):
        fake = FakeDev([reply(83, 0x10, report_id=True)])
        out, diag = self.poll(fake)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.charging_known, s.kind)
                          for s in out],
                         [("mchose_k99:3033", "MCHOSE K99 V3", 83, False, False, "keyboard")])
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(len(fake.sent[0]), 65)
        self.assertTrue(any("status=0x10" in line for line in diag))

    def test_a_status_push_before_the_answer_is_skipped(self):
        fake = FakeDev([[0x55, 0x03, 0x00, 0x00], reply(77)])
        out, _ = self.poll(fake)
        self.assertEqual(out[0].level, 77)

    def test_no_answer_keeps_an_icon_without_a_level(self):
        out, diag = self.poll(FakeDev([]))
        self.assertEqual([(s.level, s.online, s.charging_known) for s in out],
                         [(None, False, False)])
        self.assertTrue(any("no answer" in line for line in diag))


if __name__ == "__main__":
    unittest.main()
