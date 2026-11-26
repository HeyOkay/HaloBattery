"""Tests for providers/fantech.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import fantech as F  # noqa: E402


def reply(level=64, charging=0, leading=True):
    r = [0x00] * 32
    if leading:
        r[0] = 0x04
        r[1] = 0x00
        r[5] = level
        r[6] = charging
    else:
        r[0] = 0x00
        r[1] = 0x04
        r[5] = level
        r[6] = charging
    r[16] = (0x55 - sum(r[:16])) & 0xFF
    return r


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.closed = 0

    def open_path(self, path):
        self.path = path

    def send_feature_report(self, data):
        self.sent.append(list(data))
        return len(data)

    def get_feature_report(self, report_id, length):
        return []

    def read(self, length, timeout):
        if not self.replies:
            return []
        return self.replies.pop(0)

    def close(self):
        self.closed += 1


def entry(pid):
    return {"vendor_id": F.VID, "product_id": pid, "feature_report_byte_length": 17,
            "input_report_byte_length": 65, "usage_page": 0x0001, "usage": 0x0000,
            "path": b"fantech-%04x" % pid, "product_string": "Fantech"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_layouts(self):
        self.assertEqual(F.parse_reply(reply())[:2], (64, False))
        self.assertEqual(F.parse_reply(reply(level=8), tenths=True)[:2], (80, False))
        self.assertEqual(F.parse_reply(reply(level=50, charging=1))[1], True)

    def test_bad(self):
        self.assertIsNone(F.parse_reply([]))
        self.assertIsNone(F.parse_reply(reply(level=101)))
        bad = reply(); bad[16] ^= 1
        self.assertEqual(F.parse_reply(bad)[2], False)  # still plausible, checksum not valid


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (F.hid, F.hidlist)
        self.provider = F.FantechProvider()

    def tearDown(self):
        F.hid, F.hidlist = self._saved

    def poll(self, infos, dev):
        F.hid = fake_hid(dev)
        F.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_dongle(self):
        dev = FakeDevice([reply(64)])
        res = self.poll([entry(F.DONGLE)], dev)
        self.assertEqual([(r.key, r.level, r.via) for r in res],
                         [("fantech:fa7c", 64, "2.4 GHz receiver")])
        self.assertEqual(len(dev.sent[0]), 17)
        self.assertEqual(sum(dev.sent[0]) & 0xFF, 0x55)

    def test_wired(self):
        dev = FakeDevice([reply(72)])
        res = self.poll([entry(F.WIRED)], dev)
        self.assertEqual([(r.level, r.via) for r in res], [(72, "USB cable")])

    def test_redragon_tenths(self):
        dev = FakeDevice([reply(8)])
        res = self.poll([entry(F.REDRAGON)], dev)
        self.assertEqual([(r.name, r.level) for r in res],
                         [("Redragon Invader Pro", 80)])


if __name__ == "__main__":
    unittest.main()
