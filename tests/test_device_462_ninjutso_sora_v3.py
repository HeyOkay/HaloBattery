"""Tests for the Ninjutso Sora V3 reader, #462; no hardware.

The Sora V3 receiver is 093a:eb02.  Its current NinjaForce protocol uses
feature report 6, a 15-byte payload, and two read commands: 0x12 battery and
0x11 charging.  The reply carries the value in the first data byte (byte 8
when the report id is included).  Source: OpenMouse-Project/mouse-protocol
(src/ninjutso, src/drivers/ninjutso), derived from NinjaForce's WebHID client.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import ninjutso_sora_v3 as N  # noqa: E402


def reply(command, value):
    r = [0] * 16
    r[0] = N.REPORT_ID
    r[1] = command
    r[3] = 1
    r[6] = 1
    r[8] = value
    return r


def entries():
    return [
        {"vendor_id": N.VID, "product_id": N.RECEIVER_PID, "interface_number": 2,
         "usage_page": 0xFF00, "usage": 1, "path": b"secure",
         "product_string": "Ninjutso Sora V3"},
        {"vendor_id": N.VID, "product_id": N.RECEIVER_PID, "interface_number": 2,
         "usage_page": 0xFF01, "usage": 1, "path": b"main",
         "product_string": "Ninjutso Sora V3"},
    ]


class FakeDev:
    def __init__(self, answers=None):
        self.answers = dict(answers or {})
        self.sent = []
        self.last = None

    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def send_feature_report(self, packet):
        self.sent.append(list(packet))
        self.last = packet[1]
        return len(packet)

    def get_feature_report(self, report_id, size):
        return self.answers.get(self.last)


class RequestTests(unittest.TestCase):
    def test_the_request_is_the_current_protocol_frame(self):
        self.assertEqual(N.make_request(N.CMD_BATTERY),
                         [0x12, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0])

    def test_a_reply_is_read_only_from_the_matching_command(self):
        self.assertEqual(N.parse_value(reply(N.CMD_BATTERY, 83), N.CMD_BATTERY), 83)
        self.assertIsNone(N.parse_value(reply(N.CMD_BATTERY, 83), N.CMD_CHARGING))
        self.assertIsNone(N.parse_value([], N.CMD_BATTERY))
        self.assertIsNone(N.parse_value([0x06] * 8, N.CMD_BATTERY))

    def test_a_raw_reply_without_the_report_id_is_accepted(self):
        self.assertEqual(N.parse_value([N.CMD_BATTERY] + [0] * 6 + [77], N.CMD_BATTERY), 77)


class PollTests(unittest.TestCase):
    def setUp(self):
        self._hid = N.hid
        self._enumerate = N.hidlist.enumerate
        self._sleep = N.time.sleep
        N.time.sleep = lambda _s: None
        N.hidlist.enumerate = lambda vid=0: [e for e in entries()
                                              if e["vendor_id"] == vid]

    def tearDown(self):
        N.hid = self._hid
        N.hidlist.enumerate = self._enumerate
        N.time.sleep = self._sleep

    def poll(self, answers):
        fake = FakeDev(answers)
        N.hid = types.SimpleNamespace(device=lambda: fake)
        p = N.NinjutsoSoraV3Provider()
        result = p.poll()
        self.fake = fake
        self.diag = p.diagnostics()
        return result[0] if result else None

    def test_battery_and_charging_are_read_with_two_commands(self):
        got = self.poll({N.CMD_BATTERY: reply(N.CMD_BATTERY, 83),
                         N.CMD_CHARGING: reply(N.CMD_CHARGING, 1)})
        self.assertEqual((got.level, got.charging, got.charging_known, got.online),
                         (83, True, True, True))
        self.assertEqual([p[1] for p in self.fake.sent], [N.CMD_BATTERY, N.CMD_CHARGING])
        self.assertEqual(self.fake.sent[0][0], N.REPORT_ID)
        self.assertEqual(len(self.fake.sent[0]), 16)

    def test_a_missing_charge_reply_leaves_the_level_valid_but_unknown(self):
        got = self.poll({N.CMD_BATTERY: reply(N.CMD_BATTERY, 54)})
        self.assertEqual((got.level, got.charging, got.charging_known), (54, False, False))
        self.assertTrue(any("charger unknown" in line for line in self.diag))

    def test_no_answer_is_offline_not_zero_percent(self):
        got = self.poll({})
        self.assertIsNone(got.level)
        self.assertFalse(got.online)
        self.assertTrue(any("no answer" in line for line in self.diag))

    def test_the_cable_is_tried_before_the_receiver_when_both_are_present(self):
        N.hidlist.enumerate = lambda vid=0: (
            [e for e in entries() if e["vendor_id"] == vid]
            + [{"vendor_id": N.VID, "product_id": N.WIRED_PID, "interface_number": 2,
                "usage_page": N.PAGE_MAIN, "usage": 1, "path": b"wired",
                "product_string": "Ninjutso Sora V3"}])
        got = self.poll({N.CMD_BATTERY: reply(N.CMD_BATTERY, 95),
                         N.CMD_CHARGING: reply(N.CMD_CHARGING, 1)})
        self.assertEqual((got.level, got.charging, got.charging_known), (95, True, True))
        self.assertEqual(self.fake.path, b"wired")
        self.assertTrue(any("e010" in line for line in self.diag))

    def test_the_main_collection_is_tried_first(self):
        got = self.poll({N.CMD_BATTERY: reply(N.CMD_BATTERY, 99),
                         N.CMD_CHARGING: reply(N.CMD_CHARGING, 0)})
        self.assertEqual(got.level, 99)
        self.assertEqual(self.fake.path, b"main")


if __name__ == "__main__":
    unittest.main()
