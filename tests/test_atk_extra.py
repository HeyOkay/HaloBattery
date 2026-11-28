"""Tests for providers/atk_extra.py. No hardware is needed."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import atk_extra as A  # noqa: E402
from providers import atk_bitmouse as B  # noqa: E402


class NanoTest(unittest.TestCase):
    def test_level_and_charging(self):
        report = bytes([0x82, 0, 0, 0, 0, 0, 0, 0, 0xE4, 1] + [0] * 5)
        self.assertEqual(A.parse_nano(report), (100, True))

    def test_unlinked_mouse_is_ignored(self):
        report = bytes([0x82, 0, 0, 0, 0, 0, 0, 0, 60, 0] + [0] * 5)
        self.assertIsNone(A.parse_nano(report))

    def test_leading_report_id_is_tolerated(self):
        report = bytes([0x00, 0x82, 0, 0, 0, 0, 0, 0, 0, 50, 1] + [0] * 5)
        self.assertEqual(A.parse_nano(report), (50, False))


class Z87Test(unittest.TestCase):
    def test_charging_values(self):
        reply = bytes([0x04, 0x03, 0x00, 0x1A, 0, 0, 0, 0, 77, 1])
        self.assertEqual(A.parse_z87(reply, 3), (77, True))
        reply = bytes([0x04, 0x03, 0x00, 0x1A, 0, 0, 0, 0, 77, 2])
        self.assertEqual(A.parse_z87(reply, 3), (77, True))
        reply = bytes([0x04, 0x03, 0x00, 0x1A, 0, 0, 0, 0, 77, 0])
        self.assertEqual(A.parse_z87(reply, 3), (77, False))

    def test_sleeping_and_wrong_sequence(self):
        self.assertIsNone(A.parse_z87(bytes([0x04, 3, 0, 0xFF]), 3))
        self.assertIsNone(A.parse_z87(bytes([0x04, 4, 0, 0x1A, 0, 0, 0, 0, 77, 0]), 3))


class FakeFeatureDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def open_path(self, path):
        pass

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def get_feature_report(self, report_id, length):
        return self.replies.pop(0) if self.replies else b""

    def close(self):
        pass


def memory_reply(value):
    return bytes([0xAA, 0x03, 0x04]) + value.to_bytes(4, "little") + bytes(57)


class MercuryTest(unittest.TestCase):
    def test_mailbox_reads_pointer_then_capacity(self):
        hello = bytes([0xAA, 0x01, 0, 0, 0, 0x07]) + bytes(58)
        dev = FakeFeatureDevice([hello, memory_reply(A.MERCURY_EXPECTED), memory_reply(90)])
        with mock.patch.object(A.hid, "device", return_value=dev), \
                mock.patch.object(A.time, "sleep", return_value=None):
            self.assertEqual(A.read_mercury(b"path"), 90)
        self.assertEqual(len(dev.sent), 3)

    def test_wrong_pointer_is_rejected(self):
        hello = bytes([0xAA, 0x01, 0, 0, 0, 0x07]) + bytes(58)
        dev = FakeFeatureDevice([hello, memory_reply(0x0100FE00)])
        with mock.patch.object(A.hid, "device", return_value=dev), \
                mock.patch.object(A.time, "sleep", return_value=None):
            self.assertIsNone(A.read_mercury(b"path"))


class ZeroTest(unittest.TestCase):
    def test_zero_reply(self):
        reply = [0x08, 0x72, 0x00, 0x01, 0x00, B.CMD_BATTERY, 0x01, 0x5A]
        self.assertEqual(B.parse_zero_reply(reply, B.CMD_BATTERY), 0x5A)
        self.assertIsNone(B.parse_zero_reply(reply, B.CMD_CHARGING))
        reply[2] = 0xFF
        self.assertIsNone(B.parse_zero_reply(reply, B.CMD_BATTERY))

    def test_zero_request_uses_the_zero_checksum_and_target(self):
        cable = B.make_request(B.CMD_BATTERY, target=0, checksum=0x7D)
        receiver = B.make_request(B.CMD_BATTERY, target=1, checksum=0x7D)
        self.assertEqual((cable[1], cable[5]), (0x7D, 0))
        self.assertEqual((receiver[1], receiver[5]), (0x7D, 1))


class FakeZeroDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.written = False

    def open_path(self, path):
        pass

    def write(self, data):
        self.writes.append(bytes(data))
        self.written = True
        return len(data)

    def read(self, size, timeout):
        if not self.written:
            return []
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


def zero_reply(command, value):
    return [0x08, 0x72, 0x00, 1, 0, command, 1, value] + [0] * 56


class ZeroReadTest(unittest.TestCase):
    def test_cable_reads_level_and_charging(self):
        dev = FakeZeroDevice([zero_reply(B.CMD_BATTERY, 77), zero_reply(B.CMD_CHARGING, 2)])
        with mock.patch.object(B.hid, "device", return_value=dev):
            self.assertEqual(B.read_zero_power(b"path", [], wired=True), (77, True))
        self.assertEqual(dev.writes[0][5], 0)

    def test_receiver_has_no_charging_query(self):
        dev = FakeZeroDevice([zero_reply(B.CMD_BATTERY, 55)])
        with mock.patch.object(B.hid, "device", return_value=dev):
            self.assertEqual(B.read_zero_power(b"path", [], wired=False), (55, False))
        self.assertEqual(len(dev.writes), 1)


if __name__ == "__main__":
    unittest.main()
