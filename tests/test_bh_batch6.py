"""Tests for ROCCAT Syn Max Air and MAONO PD200W reader protocols."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import maono as M  # noqa: E402
from providers import roccat as R  # noqa: E402


class FakeHid:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.sent = []

    def open_path(self, path):
        pass

    def write(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def read(self, size, timeout):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


def run(module, provider_cls, infos, fake):
    with mock.patch.object(module.hid, "device", return_value=fake), \
            mock.patch.object(module.hidlist, "enumerate", return_value=infos):
        return provider_cls().poll()


def roccat_reply(mv=3920, charging=False):
    b = bytearray(64)
    b[0:4] = bytes([0x8A, 0x83, 0x06, 0x02])
    b[4], b[5] = 4, 0
    b[6], b[7] = mv & 0xFF, mv >> 8
    b[8], b[9] = mv & 0xFF, mv >> 8
    b[10] = 1 if charging else 0
    return bytes(b)


class RoccatTest(unittest.TestCase):
    def test_curve(self):
        self.assertEqual(R.percent_from_mv(4080), 100)
        self.assertEqual(R.percent_from_mv(4200), 100)
        self.assertEqual(R.percent_from_mv(3500), 0)
        self.assertEqual(R.percent_from_mv(3920), 78)
        self.assertEqual(R.percent_from_mv(3860), 68)

    def test_parse_and_poll(self):
        parsed = R.parse_reply(roccat_reply(3920))
        self.assertEqual((parsed["mv"], parsed["charging"]), (3920, False))
        infos = [{"path": b"r", "product_id": R.PID, "interface_number": 0,
                  "usage_page": 0xFF00, "usage": 1,
                  "input_report_byte_length": 64, "output_report_byte_length": 64}]
        found = run(R, R.RoccatProvider, infos, FakeHid(replies=[[], roccat_reply(3920)]))
        self.assertEqual((found[0].level, found[0].charging), (78, False))

    def test_charging_has_no_voltage_percent(self):
        parsed = R.parse_reply(roccat_reply(4200, True))
        self.assertTrue(parsed["charging"])
        infos = [{"path": b"r", "product_id": R.PID, "interface_number": 0,
                  "usage_page": 0xFF00, "usage": 1,
                  "input_report_byte_length": 64, "output_report_byte_length": 64}]
        found = run(R, R.RoccatProvider, infos, FakeHid(replies=[[], roccat_reply(4200, True)]))
        self.assertEqual((found[0].level, found[0].charging), (None, True))
        self.assertEqual(found[0].approx, "on cable, full")


def maono_reply(value=64):
    b = bytearray(11)
    b[0], b[1], b[4] = 0xC4, 11, 0x04
    b[5], b[6], b[7], b[8] = 0x42, 0x00, value, 0x00
    check = M.checksum(bytes(b[:9]))
    b[9], b[10] = check & 0xFF, check >> 8
    return bytes(b)


class MaonoTest(unittest.TestCase):
    def test_request_checksum(self):
        request = M.make_request()
        self.assertEqual(request[:9], [0xC4, 9, 0, 0, 4, 0x42, 0, 0xED, 0xFE])

    def test_parse_and_poll(self):
        self.assertEqual(M.parse_reply(maono_reply(64)), 64)
        bad = bytearray(maono_reply(64))
        bad[9] ^= 0xFF
        self.assertIsNone(M.parse_reply(bytes(bad)))
        infos = [{"path": b"m", "product_id": M.PID, "interface_number": 3,
                  "usage_page": 0x000C, "usage": 1,
                  "input_report_byte_length": 64, "output_report_byte_length": 64}]
        found = run(M, M.MaonoProvider, infos, FakeHid(replies=[[], maono_reply(41)]))
        self.assertEqual((found[0].level, found[0].charging_known), (41, False))


if __name__ == "__main__":
    unittest.main()
