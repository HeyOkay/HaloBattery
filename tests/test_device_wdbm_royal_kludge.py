"""Tests for providers/royal_kludge.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import royal_kludge as R  # noqa: E402


def reply(pct=64, plugged=0, mv=3900, access=1, cmd=0x02):
    r = [0x01, 0x01, cmd, access << 4] + [0x00] * 32
    r[4] = mv & 0xFF
    r[5] = mv >> 8
    r[6] = pct & 0xFF
    r[7] = pct >> 8
    r[10] = plugged
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


def entry(pid, page=None):
    cable = pid in R.CABLE_PIDS
    page = page if page is not None else (0xFFF2 if cable else 0xFF00)
    return {"vendor_id": R.VID, "product_id": pid, "usage_page": page, "usage": 0x0001,
            "output_report_byte_length": 65, "input_report_byte_length": 65,
            "path": b"rk-%04x" % pid, "product_string": "RK MG6 Pro"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_valid_reply(self):
        self.assertEqual(R.parse_reply(reply()), (64, False, 3900))

    def test_access_res_zero_is_not_a_reading(self):
        self.assertIsNone(R.parse_reply(reply(access=0)))

    def test_wrong_command_and_short(self):
        self.assertIsNone(R.parse_reply(reply(cmd=3)))
        self.assertIsNone(R.parse_reply(reply()[:10]))

    def test_level_above_100(self):
        self.assertIsNone(R.parse_reply(reply(101)))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (R.hid, R.hidlist)
        self.provider = R.RoyalKludgeProvider()

    def tearDown(self):
        R.hid, R.hidlist = self._saved

    def poll(self, infos, dev):
        R.hid = fake_hid(dev)
        R.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_receiver_reading(self):
        dev = FakeDevice([reply()])
        res = self.poll([entry(0x00A6)], dev)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.via) for r in res],
                         [("rk:mg6", "RK MG6 Pro", 64, False, "2.4 GHz receiver")])
        self.assertEqual(dev.writes[0][:len(R.REQUEST)], list(R.REQUEST))
        self.assertEqual(len(dev.writes[0]), 65)
        self.assertEqual(dev.closed, 1)

    def test_cable_is_charging(self):
        dev = FakeDevice([reply(80, 0)])
        res = self.poll([entry(0x00A5)], dev)
        self.assertTrue(res[0].charging)
        self.assertEqual(res[0].via, "USB cable")

    def test_plugged_flag(self):
        dev = FakeDevice([reply(70, 1)])
        res = self.poll([entry(0x00E6)], dev)
        self.assertTrue(res[0].charging)

    def test_wrong_collection_shape_not_opened(self):
        dev = FakeDevice([reply()])
        bad = entry(0x00A6)
        bad["output_report_byte_length"] = 64
        self.assertEqual(self.poll([bad], dev), [])
        self.assertEqual(dev.writes, [])

    def test_sleep_reply_keeps_last(self):
        first = self.poll([entry(0x00A6)], FakeDevice([reply(55)]))
        self.assertEqual(first[0].level, 55)
        off = self.poll([entry(0x00A6)], FakeDevice([reply(access=0), reply(access=0), reply(access=0)]))
        self.assertEqual([(r.level, r.online) for r in off], [(55, False)])


if __name__ == "__main__":
    unittest.main()
