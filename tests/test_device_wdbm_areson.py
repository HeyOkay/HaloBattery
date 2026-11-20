"""Tests for providers/areson.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import areson as A  # noqa: E402


def reply(level=8, charging=0):
    r = [0x09, 0x04, 0x00] + [0] * 14
    r[6] = level
    r[7] = charging
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

    def read(self, length, timeout):
        if not self.replies:
            return []
        return self.replies.pop(0)

    def close(self):
        self.closed += 1


def entries(pid=0xFA07):
    return [
        {"vendor_id": A.VID, "product_id": pid, "interface_number": A.IFACE,
         "usage_page": A.CMD_PAGE, "usage": A.CMD_USAGE, "feature_report_byte_length": 17,
         "path": b"areson-cmd", "product_string": "2.4G Receiver"},
        {"vendor_id": A.VID, "product_id": pid, "interface_number": A.IFACE,
         "usage_page": A.REP_PAGE, "usage": A.REP_USAGE, "input_report_byte_length": 17,
         "path": b"areson-rep", "product_string": "2.4G Receiver"},
    ]


def fake_hid(devices):
    it = iter(devices)
    return types.SimpleNamespace(device=lambda: next(it))


class ParseTests(unittest.TestCase):
    def test_request_checksum(self):
        q = A.build_request()
        self.assertEqual(len(q), 17)
        self.assertEqual(sum(q) & 0xFF, 0x55)

    def test_reply(self):
        self.assertEqual(A.parse_reply(reply()), (80, False))
        self.assertEqual(A.parse_reply(reply(10, 1)), (100, True))

    def test_bad_checksum_or_level(self):
        bad = reply(); bad[6] = 11
        self.assertIsNone(A.parse_reply(bad))
        bad = reply(); bad[16] ^= 1
        self.assertIsNone(A.parse_reply(bad))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist)
        self.provider = A.AresonProvider()

    def tearDown(self):
        A.hid, A.hidlist = self._saved

    def poll(self, infos, devs):
        A.hid = fake_hid(devs)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_receiver(self):
        cmd, rep = FakeDevice([]), FakeDevice([reply()])
        res = self.poll(entries(), [cmd, rep])  # _read creates the command handle first
        self.assertEqual([(r.key, r.level, r.charging) for r in res],
                         [("areson:fa07", 80, False)])
        self.assertEqual(len(cmd.sent[0]), 17)
        self.assertEqual(sum(cmd.sent[0]) & 0xFF, 0x55)

    def test_wrong_collections_not_opened(self):
        cmd, rep = FakeDevice([]), FakeDevice([])
        inf = entries(); inf[0]["feature_report_byte_length"] = 16
        self.assertEqual(self.poll(inf, [rep, cmd]), [])
        self.assertEqual(cmd.sent, [])


if __name__ == "__main__":
    unittest.main()
