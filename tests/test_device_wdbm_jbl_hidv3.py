"""Tests for providers/jbl_hidv3.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import jbl_hidv3 as J  # noqa: E402


def reply(raw, report_id=0x86, feature=0x000D, status=0x0000, size=1):
    r = [report_id, 0x00, 0xDD, 0x01, 0x00, 0x01, 0x00, 0x02,
         0x00, 0x00, status >> 8,
         feature & 0xFF, feature >> 8, size & 0xFF, size >> 8]
    r += [0x00] * 32
    r[15] = raw
    return r


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.reads = 0
        self.closed = 0

    def open_path(self, path):
        self.path = path

    def send_feature_report(self, data):
        self.sent.append(list(data))
        return len(data)

    def read(self, length, timeout):
        self.reads += 1
        if not self.replies:
            return []
        return self.replies.pop(0)

    def close(self):
        self.closed += 1


def entry(pid, page=J.PAGE, feat=11, usage=1):
    return {"vendor_id": J.VID, "product_id": pid, "usage_page": page, "usage": usage,
            "feature_report_byte_length": feat, "input_report_byte_length": 64,
            "path": b"jbl-%04x" % pid, "product_string": "JBL Quantum"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_level_and_charging(self):
        self.assertEqual(J.parse_reply(reply(0x55)), (85, False))
        self.assertEqual(J.parse_reply(reply(0x80 | 42)), (42, True))

    def test_error_status_is_refused(self):
        self.assertIsNone(J.parse_reply(reply(50, status=0x8000)))

    def test_wrong_feature_or_empty_value_is_refused(self):
        self.assertIsNone(J.parse_reply(reply(50, feature=0x000E)))
        self.assertIsNone(J.parse_reply(reply(50, size=0)))

    def test_short_or_wrong_report_is_refused(self):
        for r in ([], [0x86], reply(50, report_id=0x85), [0x86] + [0] * 14):
            self.assertIsNone(J.parse_reply(r), r)

    def test_level_above_100_is_refused(self):
        self.assertIsNone(J.parse_reply(reply(101)))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (J.hid, J.hidlist)
        self.provider = J.JblHidV3Provider()

    def tearDown(self):
        J.hid, J.hidlist = self._saved

    def poll(self, infos, dev):
        J.hid = fake_hid(dev)
        J.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_reads_a_650_on_the_receiver(self):
        dev = FakeDevice([[], reply(67)])
        res = self.poll([entry(0x2125)], dev)
        self.assertEqual([(r.key, r.level, r.charging, r.online, r.via) for r in res],
                         [("jbl:650", 67, False, True, "2.4 GHz receiver")])
        self.assertEqual(dev.sent[0][:len(J.REQUEST)], list(J.REQUEST))
        self.assertEqual(len(dev.sent[0]), 11)
        self.assertEqual(dev.closed, 1)

    def test_cable_and_receiver_share_one_key_and_charging_wins(self):
        # two collections, each with its own fake handle
        devices = {b"jbl-2125": FakeDevice([[], reply(60)]),
                   b"jbl-2126": FakeDevice([reply(0x80 | 60)])}
        J.hid = types.SimpleNamespace(device=lambda: _Next(devices))
        J.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [entry(0x2125), entry(0x2126)])
        res = self.provider.poll()
        self.assertEqual(len(res), 1)
        self.assertEqual((res[0].key, res[0].level, res[0].charging), ("jbl:650", 60, True))

    def test_no_answer_is_offline_and_keeps_last(self):
        dev = FakeDevice([reply(44)])
        self.poll([entry(0x219A)], dev)
        off = FakeDevice([])
        res = self.poll([entry(0x219A)], off)
        self.assertEqual([(r.level, r.online) for r in res], [(44, False)])

    def test_only_ff12_with_a_long_enough_feature_is_opened(self):
        dev = FakeDevice([reply(50)])
        self.assertEqual(self.poll([entry(0x219A, page=0x0001)], dev), [])
        self.assertEqual(dev.sent, [])
        dev2 = FakeDevice([reply(50)])
        self.assertEqual(self.poll([entry(0x219A, feat=10)], dev2), [])
        self.assertEqual(dev2.sent, [])

    def test_unlisted_pid_is_not_claimed(self):
        dev = FakeDevice([reply(50)])
        self.assertEqual(self.poll([entry(0x2088)], dev), [])
        self.assertEqual(dev.sent, [])


class _Next:
    """Hands out one fake handle per open_path call, in path order."""

    def __init__(self, devices):
        self.devices = devices
        self.current = None

    def open_path(self, path):
        self.current = self.devices[path]

    def send_feature_report(self, data):
        return self.current.send_feature_report(data)

    def read(self, length, timeout):
        return self.current.read(length, timeout)

    def close(self):
        return self.current.close()


if __name__ == "__main__":
    unittest.main()
