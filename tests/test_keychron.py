"""Tests for providers/keychron.py. No hardware is needed.

The Ultra-Link 8K (3434:d028) and M5 (3434:d048) put their vendor collection on
interface 4; the Nape Pro's "Keychron Link-KM" receiver (3434:d026, #270) puts it on
interface 1 as `ffc1:0001`, with the VIA channel on interface 3. The battery exchange is
the same documented one: a 64-byte feature report `b3 06`, answered `b4 06` with the
level at byte 20.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import keychron as K  # noqa: E402


def reply(level):
    return [K.REPORT_ID_RESP, K.CMD_STATUS] + [0x00] * 18 + [level] + [0x00] * (K.REPORT_SIZE - 21)


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

    def read(self, size, timeout):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    NAPE = [
        {"path": b"nape-mouse", "interface_number": 0,
         "usage_page": 0x0001, "usage": 0x0002, "product_id": 0xD026},
        {"path": b"nape-vendor", "interface_number": 1,
         "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD026},
        {"path": b"nape-via", "interface_number": 3,
         "usage_page": 0xFF60, "usage": 0x0061, "product_id": 0xD026},
    ]

    def setUp(self):
        self._saved = (K.hid, K.hidlist, K.time)

    def tearDown(self):
        K.hid, K.hidlist, K.time = self._saved

    def poll(self, replies, infos):
        dev = FakeDevice(replies)
        K.hid = types.SimpleNamespace(device=lambda: dev)
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        K.time = types.SimpleNamespace(sleep=lambda s: None)
        return K.KeychronProvider().poll(), dev

    def test_the_nape_pro_uses_its_vendor_collection_on_interface_1(self):
        out, dev = self.poll([reply(75)], self.NAPE)
        self.assertEqual([(s.key, s.name, s.level) for s in out],
                         [("keychron:d026", "Keychron Nape Pro", 75)])
        self.assertEqual([b"nape-vendor"], dev.opened)
        self.assertEqual([bytes(K.make_request())], dev.sent)

    def test_interface_4_still_wins_when_it_exists(self):
        infos = [
            {"path": b"iface1", "interface_number": 1,
             "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD028},
            {"path": b"iface4", "interface_number": 4,
             "usage_page": 0xFF00, "usage": 0x0001, "product_id": 0xD028},
        ]
        out, dev = self.poll([reply(42)], infos)
        self.assertEqual(42, out[0].level)
        self.assertEqual([b"iface4"], dev.opened)

    def test_a_bad_reply_is_not_a_level(self):
        self.assertIsNone(K.parse_level([0xB3, 0x06] + [0] * 62))
        self.assertIsNone(K.parse_level([0xB4, 0x06] + [0] * 10))     # too short
        self.assertIsNone(K.parse_level(reply(101)))
        self.assertIsNone(K.parse_level([]))

    def test_no_reply_reports_nothing(self):
        out, _dev = self.poll([], self.NAPE)
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
