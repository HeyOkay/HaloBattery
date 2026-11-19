"""Tests for the MCHOSE K99 V3 GLW reader, #458; no hardware needed.

Request ``00 | 55 05 00 38 38 ...``; reply ``AA 05 00 <checksum> 38 ...``
with the battery byte at payload offset 32 (bit 7 charging, bits 0..6 level).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import mchose_k99 as K  # noqa: E402


def reply(level=83, charging=False, report_id=False, size=56):
    payload = [0x00] * size
    if size > K.BATTERY_AT:
        payload[K.BATTERY_AT] = (level | 0x80) if charging else level
    r = [0x00] * 64
    checksum = (size + 0 + 0 + 0 + sum(payload)) & 0xFF
    r[0:8] = [0xAA, 0x05, 0x00, checksum, size, 0x00, 0x00, 0x00]
    r[8:8 + size] = payload
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
        return self.replies.pop(0) if self.replies else []


class FakeActivity:
    def __init__(self, active=True):
        self.is_active = active
        self.paths = []
        self.held = []

    def configure(self, paths):
        self.paths = list(paths)

    def start(self):
        return True

    def active(self):
        return self.is_active

    def hold(self, seconds):
        self.held.append(seconds)

    def close(self):
        pass


class ParseTests(unittest.TestCase):
    def test_the_request_is_the_glw_func_config_read(self):
        req = K.make_request()
        self.assertEqual(len(req), 64)
        self.assertEqual(list(req[:8]), [0x55, 0x05, 0x00, 0x38, 0x38, 0x00, 0x00, 0x00])
        wire = K.wire_request()
        self.assertEqual(len(wire), 65)
        self.assertEqual(wire[0], 0x00)
        self.assertEqual(wire[1:], req)

    def test_the_reply_checksum_and_battery_byte_are_read(self):
        self.assertEqual(K.parse_reply(reply(90, False)), (90, False, 90))
        self.assertEqual(K.parse_reply(reply(52, True)), (52, True, 0xB4))
        self.assertEqual(K.parse_reply(reply(41, False, report_id=True)), (41, False, 41))

    def test_a_foreign_reply_bad_checksum_or_bad_level_is_refused(self):
        bad = reply()
        bad[0] = 0x55
        self.assertIsNone(K.parse_reply(bad))
        bad = reply()
        bad[3] ^= 0xFF
        self.assertIsNone(K.parse_reply(bad))
        self.assertIsNone(K.parse_reply(reply(101)))
        self.assertIsNone(K.parse_reply([]))
        self.assertIsNone(K.parse_reply(reply(83, size=32)))


class PollTests(unittest.TestCase):
    def setUp(self):
        self._hid = K.hid
        self._enumerate = K.hidlist.enumerate

    def tearDown(self):
        K.hid = self._hid
        K.hidlist.enumerate = self._enumerate

    def poll(self, fake, activity=None):
        K.hid = types.SimpleNamespace(device=lambda: fake)
        K.hidlist.enumerate = lambda vid=0: entries()
        p = K.MchoseK99Provider(activity=activity or FakeActivity())
        return p.poll(), p.diagnostics()

    def test_the_keyboard_is_read_with_charging(self):
        fake = FakeDev([reply(90, charging=True, report_id=True)])
        out, diag = self.poll(fake)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.charging_known, s.kind)
                          for s in out],
                         [("mchose_k99:3033", "MCHOSE K99 V3", 90, True, True, "keyboard")])
        self.assertEqual(len(fake.sent), 1)
        self.assertEqual(len(fake.sent[0]), 65)
        self.assertTrue(any("raw=da" in line for line in diag))

    def test_a_short_push_before_the_answer_is_skipped(self):
        fake = FakeDev([[0x55, 0x05, 0x00, 0x00], reply(77)])
        out, _ = self.poll(fake)
        self.assertEqual(out[0].level, 77)

    def test_no_answer_keeps_an_icon_without_a_level(self):
        out, diag = self.poll(FakeDev([]))
        self.assertEqual([(s.level, s.online, s.charging_known) for s in out],
                         [(None, False, False)])
        self.assertTrue(any("no answer" in line for line in diag))

    def test_without_keyboard_activity_no_query_is_sent(self):
        fake = FakeDev([reply(90)])
        out, diag = self.poll(fake, activity=FakeActivity(active=False))
        self.assertEqual([(s.level, s.online, s.polling_paused) for s in out],
                         [(None, False, True)])
        self.assertEqual(fake.sent, [])
        self.assertTrue(any("waiting for keyboard activity" in line for line in diag))


if __name__ == "__main__":
    unittest.main()
