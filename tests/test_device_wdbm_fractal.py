"""Tests for providers/fractal.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import fractal as F  # noqa: E402


def state(device_type, level=55, charging=0, linked=1):
    r = [F.REPORT, device_type, F.GET_STATE] + [0x00] * 32
    r[4] = linked
    r[15] = level
    r[23] = charging
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
    return {"vendor_id": F.VID, "product_id": pid, "usage_page": F.PAGE, "usage": F.USAGE,
            "output_report_byte_length": 64, "input_report_byte_length": 64,
            "path": b"fractal-%04x" % pid, "product_string": "Fractal Scape"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class Tests(unittest.TestCase):
    def setUp(self):
        self._saved = (F.hid, F.hidlist)
        self.provider = F.FractalProvider()

    def tearDown(self):
        F.hid, F.hidlist = self._saved

    def poll(self, infos, dev):
        F.hid = fake_hid(dev)
        F.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_dongle_and_headset_state(self):
        dev = FakeDevice([state(F.TYPE_DONGLE), state(F.TYPE_HEADSET, 67, 0)])
        res = self.poll([entry(F.DONGLE)], dev)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.via) for r in res],
                         [("fractal:scape", "Fractal Scape", 67, False, "2.4 GHz dongle")])
        self.assertEqual(dev.writes[0][:3], [0x02, 0x11, 0x21])
        self.assertEqual(dev.writes[1][:3], [0x02, 0xF1, 0x21])
        self.assertEqual(len(dev.writes[0]), 64)

    def test_cable_reports_charging(self):
        dev = FakeDevice([state(F.TYPE_HEADSET, 90, 1)])
        res = self.poll([entry(F.CABLE)], dev)
        self.assertEqual([(r.level, r.charging, r.via) for r in res],
                         [(90, True, "USB cable")])
        self.assertEqual(dev.writes[0][:3], [0x02, 0xF1, 0x21])

    def test_dongle_unlinked_is_not_a_reading(self):
        dev = FakeDevice([state(F.TYPE_DONGLE, linked=0)])
        res = self.poll([entry(F.DONGLE)], dev)
        self.assertEqual([(r.level, r.online) for r in res], [(None, False)])
        self.assertEqual(len(dev.writes), 1)

    def test_bad_level_is_refused(self):
        dev = FakeDevice([state(F.TYPE_HEADSET, 101)])
        res = self.poll([entry(F.CABLE)], dev)
        self.assertIsNone(res[0].level)

    def test_wrong_collection_is_not_opened(self):
        dev = FakeDevice([])
        e = entry(F.DONGLE); e["usage"] = 2
        self.assertEqual(self.poll([e], dev), [])
        self.assertEqual(dev.writes, [])


if __name__ == "__main__":
    unittest.main()
