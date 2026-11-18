"""Tests for providers/moondrop.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import moondrop as M  # noqa: E402


def battery(level=50, link=1):
    r = [M.REPORT, M.READ, M.CMD_BATTERY, 6] + [0] * 32
    r[8] = link
    r[9] = level
    return r


def link_reply(link=1):
    r = [M.REPORT, M.READ, M.CMD_LINK, 1] + [0] * 32
    r[4] = link
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


def entry(path=b"\\\\?\\hid#vid_35d8&col01#moondrop"):
    return {"vendor_id": M.VID, "product_id": M.PID, "interface_number": M.IFACE,
            "usage_page": M.PAGE, "output_report_byte_length": 64,
            "input_report_byte_length": 64, "path": path,
            "product_string": "MOONDROP GM01 Pro"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_battery_and_link(self):
        self.assertEqual(M.parse_battery(battery()), (True, 50))
        self.assertEqual(M.parse_link(link_reply()), True)
        self.assertEqual(M.parse_link(link_reply(0)), False)

    def test_bad_frames(self):
        self.assertIsNone(M.parse_battery([]))
        self.assertIsNone(M.parse_battery(battery(level=101)))
        self.assertIsNone(M.parse_link([M.REPORT, M.READ, M.CMD_LINK]))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (M.hid, M.hidlist)
        self.provider = M.MoondropProvider()

    def tearDown(self):
        M.hid, M.hidlist = self._saved

    def poll(self, infos, dev):
        M.hid = fake_hid(dev)
        M.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_dongle_battery(self):
        dev = FakeDevice([battery()])
        res = self.poll([entry()], dev)
        self.assertEqual([(r.key, r.level, r.online, r.via) for r in res],
                         [("moondrop:gm01", 50, True, "2.4 GHz receiver")])
        self.assertEqual(dev.writes[0][:4], [0x4B, 0x80, 0x27, 0x00])
        self.assertEqual(len(dev.writes[0]), 64)

    def test_unlinked(self):
        dev = FakeDevice([battery(link=0)])
        res = self.poll([entry()], dev)
        self.assertEqual([(r.level, r.online) for r in res], [(None, False)])

    def test_link_answered_but_no_battery_is_not_zero(self):
        class ByCommand(FakeDevice):
            def write(self, data):
                self.cmd = data[2]
                return len(data)
            def read(self, length, timeout):
                return link_reply(1) if self.cmd == M.CMD_LINK else []
        res = self.poll([entry()], ByCommand([]))
        self.assertEqual([(r.level, r.online) for r in res], [(None, True)])

    def test_cable_without_control_channel(self):
        dev = FakeDevice([])
        res = self.poll([entry(b"\\\\?\\hid#vid_35d8&mi_03#no-col")], dev)
        self.assertEqual([(r.level, r.charging, r.via) for r in res],
                         [(None, True, "USB cable")])

    def test_no_cable_single_collection_when_control_answered(self):
        dev = FakeDevice([battery()])
        res = self.poll([entry()], dev)
        self.assertFalse(res[0].charging)


if __name__ == "__main__":
    unittest.main()
