"""Tests for providers/arbiter.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import arbiter as A  # noqa: E402


def reply(level=72, charging=0, status=0xA1, leading=True):
    r = [0x00] * 32
    if leading:
        r[0] = 0x00
        r[4] = 0x02
        r[6] = 0x83
        r[1] = status
        r[7] = charging
        r[8] = level
    else:
        r[3] = 0x02
        r[5] = 0x83
        r[0] = status
        r[6] = charging
        r[7] = level
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
        if not self.replies:
            return []
        return self.replies.pop(0)

    def close(self):
        self.closed += 1


def entry(pid):
    return {"vendor_id": A.VID, "product_id": pid, "usage_page": A.PAGE,
            "usage": 0, "feature_report_byte_length": 65,
            "path": b"arb-%04x" % pid, "product_string": "Akitsu"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_both_shapes(self):
        self.assertEqual(A.parse_reply(reply()), (72, False, "battery"))
        self.assertEqual(A.parse_reply(reply(level=88, charging=1, leading=False)),
                         (88, True, "battery"))

    def test_asleep(self):
        self.assertEqual(A.parse_reply(reply(status=0xA0)), (None, False, "asleep"))

    def test_bad_status_or_level(self):
        self.assertIsNone(A.parse_reply(reply(status=0xA3)))
        self.assertIsNone(A.parse_reply(reply(level=101)))
        self.assertIsNone(A.parse_reply([]))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist)
        self.provider = A.ArbiterProvider()

    def tearDown(self):
        A.hid, A.hidlist = self._saved

    def poll(self, infos, dev):
        A.hid = fake_hid(dev)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_receiver(self):
        dev = FakeDevice([reply()])
        res = self.poll([entry(0x000A)], dev)
        self.assertEqual([(r.key, r.name, r.level, r.via) for r in res],
                         [("arbiter:000a", "Arbiter Studio Akitsu", 72, "2.4 GHz receiver")])
        self.assertEqual(dev.sent[0][3:7], [0x02, 0x02, 0x00, 0x83])
        self.assertEqual(len(dev.sent[0]), 65)

    def test_cable_shares_key(self):
        dev = FakeDevice([reply()])
        res = self.poll([entry(0x0009)], dev)
        self.assertEqual([(r.key, r.via) for r in res], [("arbiter:000a", "USB cable")])

    def test_missing_feature_is_not_opened(self):
        dev = FakeDevice([])
        e = entry(0x000A); e["feature_report_byte_length"] = 64
        self.assertEqual(self.poll([e], dev), [])
        self.assertEqual(dev.sent, [])


if __name__ == "__main__":
    unittest.main()
