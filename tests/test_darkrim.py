"""Tests for providers/darkrim.py. No hardware is needed."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import darkrim as D  # noqa: E402


def reply(seq, value=0x28):
    frame = bytes([0xFE, seq, 0x10, 0x00, 0x02, 0x00, 0x00, 0x03, 0x00,
                   0x01, 0x01, value])
    check = D.crc16(frame)
    return frame + bytes([check >> 8, check & 0xFF])


class FakeDevice:
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


class DarkrimTest(unittest.TestCase):
    def test_request_matches_the_hardware_capture(self):
        self.assertEqual(D.make_request(3)[1:14].hex(" "),
                         "fe 03 10 00 01 00 01 02 00 01 00 9b 15")

    def test_response_parses_percent_and_charging(self):
        self.assertEqual(D.parse_reply(reply(3, 0x28), 3), (40, False))
        self.assertEqual(D.parse_reply(reply(4, 0xDA), 4), (90, True))

    def test_wrong_sequence_and_crc_are_rejected(self):
        self.assertIsNone(D.parse_reply(reply(3), 4))
        broken = bytearray(reply(3))
        broken[12] ^= 0xFF
        self.assertIsNone(D.parse_reply(bytes(broken), 3))

    def test_poll_reads_the_receiver(self):
        dev = FakeDevice([reply(1)])
        infos = [{"path": b"rim", "product_id": D.PID, "interface_number": D.INTERFACE,
                  "usage_page": 0xFF02, "usage": 0x0002}]
        with mock.patch.object(D.hidlist, "enumerate", return_value=infos), \
                mock.patch.object(D.hid, "device", return_value=dev):
            found = D.DarkrimProvider().poll()
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].name, found[0].level, found[0].charging),
                         ("DARKRIM SHADOW", 40, False))


if __name__ == "__main__":
    unittest.main()
