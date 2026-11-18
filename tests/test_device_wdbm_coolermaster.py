"""Tests for providers/coolermaster.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import coolermaster as C  # noqa: E402


def status(level=55, charging=0, link=1, model=0x50):
    r = [0x47, 0x81, model] + [0x00] * 32
    r[10] = level
    r[11] = charging
    r[12] = link
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


def entry(pid, page=C.PAGE, iface=C.INTERFACE):
    return {"vendor_id": C.VID, "product_id": pid, "usage_page": page,
            "interface_number": iface, "path": b"cm-%04x" % pid,
            "product_string": "Cooler Master Mice Dongle"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_receiver_status(self):
        self.assertEqual(C.parse_reply(status()), (55, False, 1, 0x50))

    def test_cable_charging(self):
        self.assertEqual(C.parse_reply(status(80, 1, 0)), (80, True, 0, 0x50))

    def test_short_or_wrong_reply(self):
        for r in ([], [0x47], status()[:12], [0x46, 0x81] + [0] * 20):
            self.assertIsNone(C.parse_reply(r), r)

    def test_invalid_levels(self):
        self.assertIsNone(C.parse_reply(status(0)))
        self.assertIsNone(C.parse_reply(status(101)))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (C.hid, C.hidlist)
        self.provider = C.CoolerMasterProvider()

    def tearDown(self):
        C.hid, C.hidlist = self._saved

    def poll(self, infos, dev):
        C.hid = fake_hid(dev)
        C.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_mm712_on_the_dongle(self):
        dev = FakeDevice([status(52)])
        res = self.poll([entry(C.DONGLE)], dev)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.via) for r in res],
                         [("coolermaster:mm712", "Cooler Master MM712", 52, False,
                           "2.4 GHz receiver")])
        self.assertEqual(dev.writes[0][:len(C.REQUEST)], list(C.REQUEST))
        self.assertEqual(len(dev.writes[0]), 32)
        self.assertEqual(dev.closed, 1)

    def test_mm731_cable(self):
        dev = FakeDevice([status(77, 1, 0, 0x00)])
        res = self.poll([entry(C.MM731_CABLE)], dev)
        self.assertEqual([(r.key, r.name, r.charging, r.via) for r in res],
                         [("coolermaster:mm731", "Cooler Master MM731", True, "USB cable")])

    def test_dongle_and_cable_merge_one_icon(self):
        dev = FakeDevice([status(60, 0, 1), status(60, 1, 0)])
        res = self.poll([entry(C.DONGLE), entry(C.MM712_CABLE)], dev)
        self.assertEqual(len(res), 1)
        self.assertEqual((res[0].key, res[0].level, res[0].charging), ("coolermaster:mm712", 60, True))

    def test_other_collections_are_not_opened(self):
        dev = FakeDevice([status()])
        self.assertEqual(self.poll([entry(C.DONGLE, page=0x0001)], dev), [])
        self.assertEqual(self.poll([entry(C.DONGLE, iface=0)], dev), [])
        self.assertEqual(dev.writes, [])

    def test_no_answer_is_empty(self):
        dev = FakeDevice([])
        self.assertEqual(self.poll([entry(C.DONGLE)], dev), [])


if __name__ == "__main__":
    unittest.main()
