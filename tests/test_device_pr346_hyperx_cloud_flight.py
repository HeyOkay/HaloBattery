"""Tests for providers/hyperx_cloud_flight.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import hyperx_cloud_flight as P  # noqa: E402


class FakeDevice:
    def __init__(self, reply):
        self.reply = reply
        self.opened = []
        self.writes = []
        self.closed = False

    def open_path(self, path):
        self.opened.append(path)

    def write(self, packet):
        self.writes.append(list(packet))
        return len(packet)

    def read(self, length, timeout_ms):
        return self.reply

    def close(self):
        self.closed = True


def response(voltage, length=15):
    return [0x00, 0x00, 0x00, voltage >> 8, voltage & 0xFF] + [0] * (length - 5)


class ReplyParsing(unittest.TestCase):
    def test_voltage_curve_estimates_the_level(self):
        self.assertEqual(P.estimate_level(3648), 5)
        self.assertEqual(P.estimate_level(3800), 55)
        self.assertEqual(P.estimate_level(3975), 100)
        self.assertEqual(P.estimate_level(3976), 100)

    def test_charging_threshold_has_no_fabricated_percentage(self):
        self.assertEqual(P.parse_reply(response(0x100B)), (100, False))
        self.assertEqual(P.parse_reply(response(0x100C)), (None, True))

    def test_accepts_both_documented_reply_lengths(self):
        self.assertEqual(P.parse_reply(response(3800, 15)), (55, False))
        self.assertEqual(P.parse_reply(response(3800, 20)), (55, False))

    def test_refuses_unexpected_reply_lengths(self):
        self.assertIsNone(P.parse_reply(response(3800, 14)))
        self.assertIsNone(P.parse_reply(response(3800, 21)))

    def test_request_shape(self):
        self.assertEqual(len(P.REQUEST), 20)
        self.assertEqual(P.REQUEST[:3], [0x21, 0xFF, 0x05])
        self.assertEqual(set(P.REQUEST[3:]), {0})


class Polling(unittest.TestCase):
    def setUp(self):
        self._hid = P.hid
        self._enumerate = P.hidlist.enumerate

    def tearDown(self):
        P.hid, P.hidlist.enumerate = self._hid, self._enumerate

    def poll(self, pid, usage_page, reply):
        path = b"cloud-flight"
        device = FakeDevice(reply)
        entry = {"product_id": pid, "path": path, "interface_number": 3,
                 "usage_page": usage_page, "usage": P.USAGE}
        calls = []
        P.hid = types.SimpleNamespace(device=lambda: device)
        P.hidlist.enumerate = lambda vid: calls.append(vid) or [entry]
        provider = P.HyperXCloudFlightProvider()
        found = provider.poll()
        return found, provider.diagnostics(), device, calls

    def test_reads_new_receiver_collection(self):
        found, diag, device, calls = self.poll(0x1723, 0xFF90, response(3800))
        self.assertEqual(calls, [P.VID])
        self.assertEqual([(d.key, d.name, d.level, d.charging, d.kind) for d in found],
                         [("hyperx:1723", "HyperX Cloud Flight Wireless", 55, False, "headset")])
        self.assertEqual(device.opened, [b"cloud-flight"])
        self.assertEqual(device.writes, [P.REQUEST])
        self.assertTrue(device.closed)
        self.assertTrue(any("ff90:0303" in line for line in diag))

    def test_reads_older_receiver_collection(self):
        found, _, _, _ = self.poll(0x16C4, 0xFF53, response(3800))
        self.assertEqual([d.key for d in found], ["hyperx:16c4"])

    def test_charging_status_keeps_the_icon_without_a_fake_level(self):
        found, _, _, _ = self.poll(0x1723, 0xFF90, response(0x100C))
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0].level)
        self.assertTrue(found[0].charging)
        self.assertEqual(found[0].approx, "charging (level unavailable)")

    def test_wrong_collection_is_not_written(self):
        found, diag, device, _ = self.poll(0x1723, 0xFF00, response(3800))
        self.assertEqual(found, [])
        self.assertEqual(device.writes, [])
        self.assertTrue(any("no battery collection" in line for line in diag))

    def test_no_reply_does_not_create_a_reading(self):
        found, diag, device, _ = self.poll(0x1723, 0xFF90, [])
        self.assertEqual(found, [])
        self.assertTrue(device.closed)
        self.assertIn("  no battery reply", diag)


if __name__ == "__main__":
    unittest.main()
