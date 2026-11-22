"""Tests for providers/logitech_g325.py. No hardware is needed.

Frames are the working G325 exchange from Sapd/HeadsetControl issue #581:

    request:  50 23 0b 00 03 1a 00 03 00 07 0a ...
    ACK:      50 23 03 00 03 1a 00 00 ...
    battery:  50 23 0b 00 03 10 00 06 00 07 0a XX YY ZZ
              byte 12 = displayed percentage, byte 13 = 0x02 charging.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import logitech_g325 as G  # noqa: E402


def frame(level=90, charging=0x00, raw=None):
    f = [0x50, 0x23, 0x0B, 0x00, 0x03, 0x10, 0x00, 0x06,
         0x00, 0x07, 0x0A, level if raw is None else raw, level, charging]
    return f + [0x00] * (64 - len(f))


ACK = [0x50, 0x23, 0x03, 0x00, 0x03, 0x1A, 0x00, 0x00] + [0x00] * 56
CAPTURED_90 = frame(90, 0x00)
CAPTURED_90_CHARGING = frame(90, 0x02, raw=89)
CAPTURED_100 = frame(100, 0x02)


class ParseTest(unittest.TestCase):
    def test_the_request_is_the_captured_one(self):
        req = G.make_request()
        self.assertEqual(64, len(req))
        self.assertEqual(G.BATTERY_REQUEST, req[:len(G.BATTERY_REQUEST)])
        self.assertEqual([0] * (64 - len(G.BATTERY_REQUEST)),
                         req[len(G.BATTERY_REQUEST):])

    def test_the_battery_reply_is_read(self):
        self.assertEqual((90, False, True), G.parse_reply(CAPTURED_90))
        self.assertEqual((90, True, True), G.parse_reply(CAPTURED_90_CHARGING))
        self.assertEqual((100, True, True), G.parse_reply(CAPTURED_100))

    def test_the_ack_is_not_a_reading(self):
        self.assertIsNone(G.parse_reply(ACK))

    def test_garbage_and_other_packets_are_refused(self):
        self.assertIsNone(G.parse_reply([]))
        self.assertIsNone(G.parse_reply([0x50, 0x23, 0x0B]))
        bad = list(CAPTURED_90)
        bad[0] = 0x51
        self.assertIsNone(G.parse_reply(bad))
        bad = list(CAPTURED_90)
        bad[2] = 0x03
        self.assertIsNone(G.parse_reply(bad))
        self.assertIsNone(G.parse_reply(frame(0)))
        self.assertIsNone(G.parse_reply(frame(101)))

    def test_an_unknown_charge_byte_keeps_the_level_but_marks_it_unknown(self):
        self.assertEqual((90, False, False), G.parse_reply(frame(90, 0x01)))


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.reads = []
        self.opened = []

    def open_path(self, path):
        self.opened.append(path)

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms):
        self.reads.append((size, timeout_ms))
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    INFO = [{"path": b"ffa0", "interface_number": 3,
             "usage_page": 0xFFA0, "usage": 0x0001,
             "product_id": G.PID, "product_string": "G325 LIGHTSPEED - Wireless Mode"}]

    def setUp(self):
        self._saved = (G.hid, G.hidlist, G.time)
        self.now = [1000.0]

    def tearDown(self):
        G.hid, G.hidlist, G.time = self._saved

    def make(self, replies, infos=None):
        dev = FakeDevice(replies)
        G.time = types.SimpleNamespace(time=lambda: self.now[0])
        G.hid = types.SimpleNamespace(device=lambda: dev)
        G.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO if infos is None else infos))
        return G.LogitechG325Provider(), dev

    def test_the_g325_reads_the_battery_and_skips_the_ack(self):
        provider, dev = self.make([ACK, CAPTURED_90])
        out = provider.poll()
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in out],
                         [("logitech:g325", "Logitech G325 LIGHTSPEED",
                           90, False, True, "headset")])
        self.assertEqual([bytes(G.make_request())], dev.writes)
        self.assertTrue(out[0].wake_on_input)

    def test_charging_is_reported(self):
        provider, _dev = self.make([ACK, CAPTURED_90_CHARGING])
        self.assertTrue(provider.poll()[0].charging)

    def test_an_unknown_status_suppresses_alerts(self):
        provider, _dev = self.make([ACK, frame(90, 0x01)])
        out = provider.poll()
        self.assertEqual((90, False, False), (out[0].level, out[0].charging,
                                              out[0].charging_known))

    def test_no_reply_keeps_the_last_reading_greyed(self):
        provider, dev = self.make([ACK, CAPTURED_90])
        self.assertEqual(90, provider.poll()[0].level)
        dev.replies = []
        self.now[0] += 30
        out = provider.poll()
        self.assertEqual([(90, False, False)],
                         [(s.level, s.charging, s.online) for s in out])
        self.now[0] += G.ASLEEP_KEEP + 1
        out = provider.poll()
        self.assertEqual([(None, False)], [(s.level, s.online) for s in out])

    def test_no_g325_reports_nothing(self):
        provider, _dev = self.make([], infos=[])
        self.assertEqual([], provider.poll())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
