"""Tests for providers/zowie.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import zowie as Z  # noqa: E402


class FakeDevice:
    def __init__(self, replies=()):
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


def cw_entry(pid=Z.CW_RECEIVER, page=0xFF00, out=65):
    return {"vendor_id": Z.VID, "product_id": pid, "usage_page": page,
            "usage": 0x0001, "output_report_byte_length": out, "input_report_byte_length": 65,
            "path": b"zowie-%04x" % pid, "product_string": "ZOWIE"}


def dw_entries(pid=Z.DW_RECEIVER):
    return [
        {"vendor_id": Z.VID, "product_id": pid, "usage_page": 0xFF03, "usage": 0x0001,
         "output_report_byte_length": 16, "input_report_byte_length": 16,
         "path": b"zowie-cmd", "product_string": "ZOWIE"},
        {"vendor_id": Z.VID, "product_id": pid, "usage_page": 0xFF04, "usage": 0x0001,
         "output_report_byte_length": 16, "input_report_byte_length": 16,
         "path": b"zowie-rep", "product_string": "ZOWIE"},
    ]


def fake_hid(devices):
    it = iter(devices)
    return types.SimpleNamespace(device=lambda: next(it))


class CurveTests(unittest.TestCase):
    def test_curve(self):
        self.assertEqual(Z.percent_for(4200), 100)
        self.assertEqual(Z.percent_for(3500), 0)
        self.assertEqual(Z.percent_for(3890), 50)
        self.assertLess(Z.percent_for(3760), 50)
        self.assertGreater(Z.percent_for(3760), 10)


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (Z.hid, Z.hidlist)
        self.provider = Z.ZowieProvider()

    def tearDown(self):
        Z.hid, Z.hidlist = self._saved

    def test_ec_cw_receiver(self):
        dev = FakeDevice([[0x3c, 0x0f, 50]])  # 3900 mV, 50 %
        Z.hid = fake_hid([dev])
        Z.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [cw_entry()])
        res = self.provider.poll()
        self.assertEqual([(r.key, r.level, r.charging, r.via) for r in res],
                         [("zowie:8006", 50, False, "2.4 GHz receiver")])
        self.assertEqual(dev.writes[0][:3], [0x00, 0xA5, 0x03])
        self.assertEqual(len(dev.writes[0]), 65)

    def test_ec_cw_cable_is_charging(self):
        dev = FakeDevice([[0x3c, 0x0f, 50]])
        Z.hid = fake_hid([dev])
        Z.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [cw_entry(Z.CW_CABLE, 0x00FF)])
        res = self.provider.poll()
        self.assertTrue(res[0].charging)
        self.assertEqual(res[0].via, "USB cable")

    def test_dw_frames(self):
        # in_dev is created first, then out_dev; reply 09, mV 0x0F3C * 10 = 39000? scale check
        in_dev = FakeDevice([[0x09, 0x01, 0x86, 0x00, 0xC8]])
        out_dev = FakeDevice()
        Z.hid = fake_hid([in_dev, out_dev])
        Z.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: dw_entries())
        res = self.provider.poll()
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].key, "zowie:800a")
        self.assertEqual(res[0].level, Z.percent_for(0x0186 * 10))
        self.assertEqual(out_dev.writes[0][:3], [0x08, 0xB4, 0x15])

    def test_unknown_pid_is_ignored(self):
        dev = FakeDevice()
        Z.hid = fake_hid([dev])
        Z.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [cw_entry(0x1234)])
        self.assertEqual(self.provider.poll(), [])


if __name__ == "__main__":
    unittest.main()
