"""Tests for providers/lamzu_sw.py. No hardware is needed.

The Lamzu Tachi's 8K receiver (37b0:000c) publishes the Glorious Model O's collection
layout (`ffa0:0001`, `ffff:0001/0000`), so the Sinowealth framed query is tried on it:

    request  00 | 00 00 02 02 00 83 | 00 ...
    reply    [1] status A1 awake / A4 asleep, [6] 0x83, [7] charging, [8] level

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import lamzu_sw as L  # noqa: E402


def reply(level, charging=0x00, status=0xA1):
    return [0x00, status, 0x00, 0x00, 0x00, 0x00, 0x83, charging, level] + [0x00] * 56


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.opened = []
        self.sent = []

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def get_feature_report(self, rid, length):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    INFO = [{"path": b"tachi-vendor", "interface_number": 1,
             "usage_page": 0xFFA0, "usage": 0x0001,
             "product_id": L.PID, "product_string": "LAMZU TACHI 8K Receiver"}]

    def setUp(self):
        self._saved = (L.hid, L.hidlist, L.time, L.feature_length)
        self.now = [1000.0]

    def tearDown(self):
        L.hid, L.hidlist, L.time, L.feature_length = self._saved

    def poll(self, replies, infos=None):
        dev = FakeDevice(replies)
        L.feature_length = lambda path: 65
        L.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        L.hid = types.SimpleNamespace(device=lambda: dev)
        L.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO if infos is None else infos))
        return L.LamzuSinowealthProvider().poll(), dev

    def test_the_tachi_is_read_with_the_sinowealth_query(self):
        out, dev = self.poll([reply(75)])
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.kind) for s in out],
                         [("lamzu:000c", "Lamzu Tachi", 75, False, "mouse")])
        self.assertEqual([b"tachi-vendor"], dev.opened)
        self.assertEqual([bytes(L.make_request(65))], dev.sent)

    def test_charging_is_reported(self):
        out, _dev = self.poll([reply(50, charging=0x01)])
        self.assertTrue(out[0].charging)

    def test_an_asleep_mouse_is_no_reading(self):
        out, _dev = self.poll([reply(50, status=0xA4)])
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)

    def test_no_receiver_reports_nothing(self):
        out, _dev = self.poll([], infos=[])
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
