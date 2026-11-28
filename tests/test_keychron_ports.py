"""Tests for the Keychron 4K / 1K / keyboard additions."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import keychron as K  # noqa: E402


def four_k_reply(level, charging=False):
    b = bytearray(64)
    b[0] = 0x41
    b[3] = 0x01
    b[5] = 1 if charging else 0
    b[6] = level
    return bytes(b)


def legacy_reply(level, charging=False, mouse=0xD032):
    b = bytearray(1001)
    b[0], b[1], b[2] = 0x51, 0x06, 0x01
    b[4], b[5] = 0x34, 0x34
    b[6], b[7] = mouse & 0xFF, mouse >> 8
    b[11] = level
    b[12] = 1 if charging else 0
    return bytes(b)


class ParserTest(unittest.TestCase):
    def test_4k(self):
        self.assertEqual(K.parse_4k(four_k_reply(77, True)), (77, True))
        self.assertEqual(K.parse_4k(b"\x00" + four_k_reply(50)), (50, False))

    def test_legacy(self):
        self.assertEqual(K.parse_legacy(legacy_reply(64, True)), (0xD032, 64, True))
        self.assertEqual(K.parse_legacy(legacy_reply(30, False, 0xD03F)), (0xD03F, 30, False))

    def test_raw_keyboard_a4(self):
        self.assertEqual(K.parse_raw_keyboard(bytes([0x00, 0xA4, 80, 0, 0, 1]), 0x0A50),
                         (80, True))
        self.assertEqual(K.parse_raw_keyboard(bytes([0x00, 0xA4, 80, 0, 0, 2]), 0x0A50),
                         (80, False))

    def test_raw_keyboard_a7(self):
        self.assertEqual(K.parse_raw_keyboard(bytes([0x00, 0xA7, 0x31, 66, 1]), 0x1260),
                         (66, True))

    def test_slot(self):
        slots = bytes([0x00, 0xB2, 1, 0x34, 0x34, 0x50, 0x0A, 1, 0, 0])
        self.assertEqual(K.KeychronProvider._slot_pid(slots, {0x0A50}), 0x0A50)
        self.assertIsNone(K.KeychronProvider._slot_pid(slots, {0x1260}))

    def test_4k_request_checksum(self):
        req = K.make_4k_request(0x41)
        self.assertEqual(req[0], 0x00)
        self.assertEqual(req[64], (0xA1 - sum(req[1:64])) & 0xFF)


if __name__ == "__main__":
    unittest.main()
