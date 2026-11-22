"""Tests for providers/machenike.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import machenike as M  # noqa: E402


def status(level=61, charging=0, link=1, cable=False):
    r = [M.REPORT, M.COMMAND] + [0x00] * 19
    r[4:8] = [0x8A, 0x24, 0x4E, 0xFF]
    r[10] = (0 if cable else 1) | (8 if link else 0)
    r[11] = level
    r[12] = charging
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
    cable = pid == M.CABLE
    return {"vendor_id": M.VID, "product_id": pid,
            "interface_number": 2 if cable else 1,
            "usage_page": M.PAGE, "usage": M.USAGE,
            "input_report_byte_length": 33, "output_report_byte_length": 33,
            "path": b"mac-%04x" % pid, "product_string": "L7 Mix"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_receiver(self):
        self.assertEqual(M.parse_status(status(), False), (True, 61, False))

    def test_cable(self):
        self.assertEqual(M.parse_status(status(cable=True), True), (True, 61, False))

    def test_offline(self):
        self.assertEqual(M.parse_status(status(link=0), False), (False, None, False))

    def test_bad_marker_or_level(self):
        self.assertIsNone(M.parse_status(status()[:20], False))
        bad = status(); bad[4] = 0
        self.assertIsNone(M.parse_status(bad, False))
        self.assertIsNone(M.parse_status(status(level=101), False))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (M.hid, M.hidlist)
        self.provider = M.MachenikeProvider()

    def tearDown(self):
        M.hid, M.hidlist = self._saved

    def poll(self, infos, dev):
        M.hid = fake_hid(dev)
        M.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_receiver_reading(self):
        dev = FakeDevice([status()])
        res = self.poll([entry(M.RECEIVER)], dev)
        self.assertEqual([(r.key, r.level, r.via) for r in res],
                         [("machenike:l7", 61, "2.4 GHz receiver")])
        self.assertEqual(len(dev.sent[0]), 21)
        self.assertEqual(dev.sent[0][:2], [0x51, 0x06])

    def test_cable_preferred(self):
        dev = FakeDevice([status(level=80, cable=True)])
        res = self.poll([entry(M.RECEIVER), entry(M.CABLE)], dev)
        self.assertEqual([(r.level, r.via) for r in res], [(80, "USB cable")])

    def test_non_l7_product_is_not_claimed(self):
        dev = FakeDevice([status()])
        e = entry(M.RECEIVER); e["product_string"] = "AJAZZ keyboard"
        self.assertEqual(self.poll([e], dev), [])
        self.assertEqual(dev.sent, [])


if __name__ == "__main__":
    unittest.main()
