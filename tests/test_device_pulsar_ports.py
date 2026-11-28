"""Tests for the Pulsar X2 V3 / Feinmann / ZYWOO additions."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import pulsar as P  # noqa: E402


class SonixTest(unittest.TestCase):
    def test_charging_reply(self):
        reply = [0x00, 0x01, 0x08, 0x83, 0x01] + [0x00] * 2 + [1] + [0x00] * 57
        self.assertTrue(P.parse_sonix_charging(reply))
        reply[7] = 0
        self.assertFalse(P.parse_sonix_charging(reply))
        self.assertIsNone(P.parse_sonix_charging([0x00, 0x01, 0x08, 0x81, 0x01] + [0] * 60))

    def test_battery_request_carries_the_command(self):
        req = P.make_sonix_request(P.SONIX_CHARGING_COMMAND)
        self.assertEqual(req[2:5], [0x08, 0x83, 0x01])
        self.assertEqual(req[63:65], [sum(req[1:63]) & 0xFF, (sum(req[1:63]) >> 8) & 0xFF])

    def test_new_ids_are_claimed(self):
        for pid in (0x5402, 0x3406, 0x5404):
            self.assertIn(pid, P.SONIX_PIDS)


class B4Test(unittest.TestCase):
    def test_dongle_reply(self):
        reply = [0xB4, 64, 0, 0, 0x85, 0x40, 1] + [0] * 58
        self.assertEqual(P.parse_b4(reply, False), (64, True))

    def test_wired_reply_can_use_86(self):
        reply = bytes([0x00, 0xB4, 90, 0, 0, 0x86, 0x40, 0] + [0] * 57)
        self.assertEqual(P.parse_b4(reply, True), (90, False))
        self.assertIsNone(P.parse_b4(reply, False))

    def test_bad_marker_is_rejected(self):
        self.assertIsNone(P.parse_b4([0xB4, 64, 0, 0, 0x84, 0x41, 0], True))


if __name__ == "__main__":
    unittest.main()
