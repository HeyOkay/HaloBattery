"""Tests for providers/cherry.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import cherry as C  # noqa: E402


def reply(level=66, powered=0):
    r = [C.REPORT] + [0x00] * 63
    r[3] = C.STATUS
    r[8] = level
    r[9] = powered
    return r


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


def entry(pid):
    return {"vendor_id": C.VID, "product_id": pid, "usage_page": C.PAGE, "usage": C.USAGE,
            "input_report_byte_length": C.LEN, "output_report_byte_length": C.LEN,
            "path": b"cherry-%04x" % pid, "product_string": "MX 3.0S"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class QueryTests(unittest.TestCase):
    def test_checksum_and_frame(self):
        q = C.build_query()
        self.assertEqual(len(q), 64)
        self.assertEqual(q[0], 4)
        self.assertEqual(q[3], 0x1A)
        self.assertEqual(q[4], 3)
        self.assertEqual(q[7], 0x55)
        self.assertEqual(q[1] | (q[2] << 8), sum(q[3:]) & 0xFFFF)

    def test_reply(self):
        self.assertEqual(C.parse_reply(reply(66)), (66, False))
        self.assertEqual(C.parse_reply(reply(66, 1)), (66, True))

    def test_bad_reply(self):
        for r in ([], reply(101), [0x04] + [0] * 8):
            self.assertIsNone(C.parse_reply(r), r)


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (C.hid, C.hidlist)
        self.provider = C.CherryProvider()

    def tearDown(self):
        C.hid, C.hidlist = self._saved

    def poll(self, infos, dev):
        C.hid = fake_hid(dev)
        C.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_receiver(self):
        dev = FakeDevice([reply(66)])
        res = self.poll([entry(C.DONGLE)], dev)
        self.assertEqual([(r.key, r.level, r.charging, r.via) for r in res],
                         [("cherry:mx30s", 66, False, "2.4 GHz receiver")])
        self.assertEqual(len(dev.writes[0]), 64)
        self.assertEqual(dev.writes[0][:4], list(C.build_query()[:4]))

    def test_cable_powered(self):
        dev = FakeDevice([reply(90, 1)])
        res = self.poll([entry(C.CABLE)], dev)
        self.assertTrue(res[0].charging)
        self.assertEqual(res[0].via, "USB cable")

    def test_wrong_collection_is_not_opened(self):
        dev = FakeDevice([])
        e = entry(C.DONGLE); e["input_report_byte_length"] = 63
        self.assertEqual(self.poll([e], dev), [])
        self.assertEqual(dev.writes, [])

    def test_sleep_keeps_last(self):
        self.poll([entry(C.DONGLE)], FakeDevice([reply(70)]))
        res = self.poll([entry(C.DONGLE)], FakeDevice([]))
        self.assertEqual([(r.level, r.online) for r in res], [(70, False)])


if __name__ == "__main__":
    unittest.main()
