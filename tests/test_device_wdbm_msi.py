"""Tests for providers/msi.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import msi as M  # noqa: E402


def query_reply(level=86, charging=0):
    r = [0x0F, 0x20] + [0] * 30
    r[14] = (0x80 if charging else 0) | level
    return r


def passive_reply(raw=0x4D):
    return [0xA0, raw, 0x07, 0x00] + [0] * 20


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.closed = 0

    def open_path(self, path):
        self.path = path

    def write(self, data):
        self.writes.append(list(data))
        return len(data)

    def read(self, length, timeout):
        if not self.replies:
            return []
        return self.replies.pop(0)

    def close(self):
        self.closed += 1


def entry(pid, mode="query"):
    page, usage = (0xFF10, 0x0006) if mode == "passive" else (0xFF00, 0x0001)
    return {"vendor_id": M.VID, "product_id": pid, "interface_number": 1,
            "usage_page": page, "usage": usage, "output_report_byte_length": 65,
            "input_report_byte_length": 65, "path": b"msi-%04x" % pid,
            "product_string": "MSI"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_query(self):
        self.assertEqual(M.parse_query(query_reply()), (86, False))
        self.assertEqual(M.parse_query(query_reply(50, 1)), (50, True))
        self.assertEqual(M.parse_query([0x00] + query_reply())[0], 86)

    def test_bad_query(self):
        self.assertIsNone(M.parse_query([]))
        self.assertIsNone(M.parse_query(query_reply(101)))

    def test_passive(self):
        self.assertEqual(M.parse_passive(passive_reply()), (57, False))
        self.assertIsNone(M.parse_passive([0xA0, 1, 0]))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (M.hid, M.hidlist)
        self.provider = M.MsiProvider()

    def tearDown(self):
        M.hid, M.hidlist = self._saved

    def poll(self, infos, dev):
        M.hid = fake_hid(dev)
        M.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_versa_query(self):
        dev = FakeDevice([query_reply(86)])
        e = entry(0x9E22); e["usage_page"], e["usage"] = 0xFF0D, 0x00B0
        res = self.poll([e], dev)
        self.assertEqual([(r.key, r.level, r.via) for r in res],
                         [("msi:9e22", 86, "2.4 GHz receiver")])
        self.assertEqual(dev.writes[0][:3], [0x00, 0x0F, 0x20])
        self.assertEqual(len(dev.writes[0]), 65)

    def test_gm41_passive(self):
        dev = FakeDevice([passive_reply()])
        res = self.poll([entry(0x0D4C, "passive")], dev)
        self.assertEqual([(r.key, r.level, r.charging) for r in res],
                         [("msi:0d41", 57, False)])


if __name__ == "__main__":
    unittest.main()
