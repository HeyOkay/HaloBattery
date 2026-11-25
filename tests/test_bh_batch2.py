"""Tests for the BatteryHub batch-2 providers: Zebronics, Semico, CROCIRIS, Scyrox."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import crociris as C  # noqa: E402
from providers import scyrox as S  # noqa: E402
from providers import semico as M  # noqa: E402
from providers import zebronics as Z  # noqa: E402


class FakeHid:
    def __init__(self, replies=(), feature=b""):
        self.replies = list(replies)
        self.feature = bytes(feature)
        self.written = False
        self.sent = []

    def open_path(self, path):
        pass

    def write(self, data):
        self.written = True
        self.sent.append(bytes(data))
        return len(data)

    def read(self, size, timeout):
        if not self.written:
            return []
        return self.replies.pop(0) if self.replies else []

    def get_feature_report(self, report_id, length):
        return self.feature

    def close(self):
        pass


class ZebronicsTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(Z.parse_level(bytes([6, 0, 2, 0, 0xDB, 100, 0x14, 0x80])), 100)
        self.assertEqual(Z.parse_level(bytes([0, 6, 0, 2, 0, 0, 55, 0, 0])), 55)
        self.assertIsNone(Z.parse_level(bytes([6, 1, 2, 0, 0, 55, 0, 0])))

    def test_poll_reports_unknown_charging(self):
        infos = [{"path": b"z", "product_id": Z.PID, "usage_page": 0xFF02, "usage": 2,
                  "feature_report_byte_length": 8}]
        fake = FakeHid(feature=bytes([6, 0, 2, 0, 0, 75, 0, 0]))
        with mock.patch.object(Z.hid, "device", return_value=fake), \
                mock.patch.object(Z.hidlist, "enumerate", return_value=infos):
            found = Z.ZebronicsProvider().poll()
        self.assertEqual((found[0].level, found[0].charging_known), (75, False))


class SemicoTest(unittest.TestCase):
    def test_parse(self):
        reply = bytes([0xAA, 0x55, 0xCC, 0x33, 0x0E, 77, 1, 0, 0])
        self.assertEqual(M.parse_status(reply), (True, 77))
        self.assertEqual(M.parse_status(b"\x00" + reply), (True, 77))
        self.assertEqual(M.parse_status(reply[:6] + b"\x00"), (False, 77))
        self.assertIsNone(M.parse_status(b"\xAA\x55"))

    def test_poll(self):
        infos = [{"path": b"k", "product_id": M.DONGLE, "interface_number": 0,
                  "usage_page": 0xFF02, "usage": 2,
                  "input_report_byte_length": 65, "output_report_byte_length": 65}]
        reply = bytes([0xAA, 0x55, 0xCC, 0x33, 0x0E, 64, 1]) + bytes(58)
        fake = FakeHid(replies=[reply])
        with mock.patch.object(M.hid, "device", return_value=fake), \
                mock.patch.object(M.hidlist, "enumerate", return_value=infos):
            found = M.SemicoProvider().poll()
        self.assertEqual((found[0].level, found[0].name), (64, "Wireless keyboard"))
        self.assertFalse(found[0].charging_known)


class CrocirisTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(C.parse_reply(bytes([0x55, 0x12, 2, 0, 1, 88, 0])), (88, True))
        self.assertEqual(C.parse_reply(bytes([0, 0x55, 0x12, 2, 0, 0, 90, 0])), (90, False))
        self.assertIsNone(C.parse_reply(bytes([0x55, 0x12, 2, 0, 1, 0, 0])))
        self.assertIsNone(C.parse_reply(bytes([0x55, 0x12, 2, 0, 2, 50, 0])))

    def test_poll(self):
        infos = [{"path": b"c", "product_id": C.PID, "usage_page": 0xFF0A, "usage": 1,
                  "input_report_byte_length": 65, "output_report_byte_length": 65,
                  "product_string": "X7 Receiver 8K"}]
        fake = FakeHid(replies=[bytes([0x55, 0x12, 2, 0, 1, 97, 0])])
        with mock.patch.object(C.hid, "device", return_value=fake), \
                mock.patch.object(C.hidlist, "enumerate", return_value=infos):
            found = C.CrocirisProvider().poll()
        self.assertEqual((found[0].level, found[0].charging, found[0].name),
                         (97, True, "CROCIRIS X7"))


class ScyroxTest(unittest.TestCase):
    def frame(self, level=66, active=True):
        b = bytearray(64)
        b[0], b[1], b[3] = 0x08, 0x01, 1 if active else 0
        b[4], b[5], b[7] = 2, 2, 3
        b[20] = level
        return bytes(b)

    def test_parse(self):
        self.assertEqual(S.parse_profile(self.frame()), 66)
        self.assertIsNone(S.parse_profile(self.frame(active=False)))
        bad = bytearray(self.frame())
        bad[4] = 3
        self.assertIsNone(S.parse_profile(bytes(bad)))

    def test_poll(self):
        infos = [{"path": b"s", "product_id": S.PID, "usage_page": 0xFF00, "usage": 2,
                  "input_report_byte_length": 64, "output_report_byte_length": 64}]
        fake = FakeHid(replies=[self.frame(55)])
        with mock.patch.object(S.hid, "device", return_value=fake), \
                mock.patch.object(S.hidlist, "enumerate", return_value=infos):
            found = S.ScyroxProvider().poll()
        self.assertEqual((found[0].level, found[0].charging_known), (55, False))


if __name__ == "__main__":
    unittest.main()
