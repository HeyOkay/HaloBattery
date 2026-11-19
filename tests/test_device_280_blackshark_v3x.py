"""Tests for providers/blackshark_v3x.py (Razer BlackShark V3 X, #280).

No hardware: the frames follow everyoneexe/blackshark-linux's captured protocol
(`PROTOCOL.md`, `protocol.rs`): 64-byte feature reports, report id 0x07,
class 0x07, GET battery command 0x80, level at byte 10.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import blackshark_v3x as B, razer as R  # noqa: E402


def reply(level, extra=b"", status=B.STATUS_OK):
    frame = bytearray(64)
    frame[0] = B.REPORT_ID
    frame[1] = status
    frame[2] = B.TRANSACTION
    frame[6] = 0x02
    frame[7] = B.CLASS_BATTERY
    frame[8] = B.CMD_BATTERY
    frame[9] = 0x00
    frame[10] = level
    frame[62] = B.checksum(frame)
    return bytes(frame)


def charge_reply(state, status=B.STATUS_OK):
    frame = bytearray(64)
    frame[0] = B.REPORT_ID
    frame[1] = status
    frame[2] = B.TRANSACTION
    frame[6] = 0x02
    frame[7] = B.CLASS_BATTERY
    frame[8] = B.CMD_CHARGING
    frame[9] = 0x00
    frame[10] = state
    frame[62] = B.checksum(frame)
    return bytes(frame)


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.opened = []
        self.closed = False

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data) if self.sent[-1] and self.sent[-1][0] == B.REPORT_ID else -1

    def get_feature_report(self, rid, length):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        self.closed = True


INFO = [
    {"path": b"ff90", "interface_number": 0, "usage_page": 0xFF90, "usage": 1,
     "product_id": B.PID, "product_string": "BlackShark V3 X"},
    {"path": b"ff82", "interface_number": 0, "usage_page": 0xFF82, "usage": 1,
     "product_id": B.PID, "product_string": "BlackShark V3 X"},
]

CABLE_INFO = [
    {"path": b"cable-ff90", "interface_number": 0, "usage_page": 0xFF90, "usage": 1,
     "product_id": B.PID_CABLE, "product_string": "BlackShark V3 X USB"},
    {"path": b"cable-ff82", "interface_number": 0, "usage_page": 0xFF82, "usage": 1,
     "product_id": B.PID_CABLE, "product_string": "BlackShark V3 X USB"},
]

# The non-vendor pages both identities also publish (#280 diagnostics).
OTHER_PAGES = [
    {"path": b"%s-%04x" % (tag, page), "interface_number": 0, "usage_page": page,
     "usage": 1, "product_id": pid, "product_string": name}
    for tag, pid, name in ((b"rx", B.PID_RECEIVER, "BlackShark V3 X"),
                           (b"cable", B.PID_CABLE, "BlackShark V3 X USB"))
    for page in (0x000C, 0x000B)
]


class ProtocolTests(unittest.TestCase):
    def test_the_query_matches_the_captured_layout(self):
        q = B.make_query()
        self.assertEqual(64, len(q))
        self.assertEqual((0x07, 0x00, 0x03), (q[0], q[1], q[2]))
        self.assertEqual(0x02, q[6])
        self.assertEqual((0x07, 0x80, 0x00), (q[7], q[8], q[9]))
        self.assertEqual(B.checksum(q), q[62])
        self.assertEqual(0, q[63])

    def test_the_charging_query_uses_command_0x84(self):
        q = B.make_query(command=B.CMD_CHARGING)
        self.assertEqual((0x02, 0x07, 0x84, 0x00, 0x00),
                         (q[6], q[7], q[8], q[9], q[10]))

    def test_a_charge_reply_is_byte_ten(self):
        self.assertEqual(1, B.parse_reply(charge_reply(1), B.CMD_CHARGING))
        self.assertEqual(0, B.parse_reply(charge_reply(0), B.CMD_CHARGING))
        self.assertIsNone(B.parse_reply(charge_reply(2), B.CMD_CHARGING))
        self.assertIsNone(B.parse_reply(charge_reply(1), B.CMD_BATTERY))

    def test_the_reported_cable_reply_reads_charging(self):
        # Exact shape from the #280 test-build 4 diagnostic:
        # 07 02 03 00 00 00 02 07 84 00 01 ...
        frame = bytearray(64)
        frame[0:11] = bytes.fromhex("07 02 03 00 00 00 02 07 84 00 01")
        frame[62] = B.checksum(frame)
        self.assertEqual(1, B.parse_reply(frame, B.CMD_CHARGING))

    def test_the_reply_level_is_byte_ten(self):
        self.assertEqual(92, B.parse_reply(reply(92)))
        self.assertIsNone(B.parse_reply(reply(101)))
        self.assertIsNone(B.parse_reply(b""))

    def test_short_ack_is_not_a_zero_percent(self):
        # Exact frame from the #280 test-build 1 diagnostic.
        self.assertIsNone(B.parse_reply(bytes.fromhex(
            '07 01 03 00 00 00 02 07 80 00 00 00')))

    def test_busy_and_not_run_statuses_are_not_levels(self):
        for status in (0x00, 0x01, 0x03, 0x04, 0x05):
            with self.subTest(status=status):
                self.assertIsNone(B.parse_reply(reply(53, status=status)))

    def test_bad_checksum_is_not_a_level(self):
        frame = bytearray(reply(53))
        frame[62] ^= 0x01
        self.assertIsNone(B.parse_reply(frame))

    def test_a_real_zero_percent_is_kept(self):
        self.assertEqual(0, B.parse_reply(reply(0)))

    def test_wrong_class_or_command_is_not_a_level(self):
        frame = bytearray(reply(80))
        frame[7] = 0x18
        self.assertIsNone(B.parse_reply(frame))
        frame = bytearray(reply(80))
        frame[8] = 0x81
        self.assertIsNone(B.parse_reply(frame))

    def test_razer_standard_path_does_not_claim_the_v3x(self):
        self.assertFalse(R.maybe_wireless(B.PID, "BlackShark V3 X"))

    def test_razer_standard_path_does_not_claim_the_057c_cable(self):
        self.assertFalse(R.maybe_wireless(B.PID_CABLE, "BlackShark V3 X USB"))


class RazerReaderTests(unittest.TestCase):
    """The general Razer reader must leave both V3 X identities alone (#280)."""

    def setUp(self):
        self._saved = (R.hid, R.hidlist)

    def tearDown(self):
        R.hid, R.hidlist = self._saved

    def test_the_057c_cable_and_057d_receiver_are_not_polled_by_the_razer_reader(self):
        opened = []

        class Untouchable:
            def open_path(self, path):
                opened.append(path)
                raise AssertionError("the Razer reader opened a V3 X collection")

        infos = [dict(d, serial_number="") for d in CABLE_INFO + INFO + OTHER_PAGES]
        R.hid = type("HID", (), {"device": Untouchable})
        R.hidlist = type("HL", (), {"enumerate": staticmethod(lambda vid=0: list(infos))})
        provider = R.RazerProvider()
        self.assertEqual([], provider.poll())
        self.assertEqual([], opened)
        diag = "\n".join(provider.diagnostics())
        self.assertIn("pid=057c", diag)
        self.assertIn("pid=057d", diag)


class PollTests(unittest.TestCase):
    def setUp(self):
        self._saved = (B.hid, B.hidlist)

    def tearDown(self):
        B.hid, B.hidlist = self._saved

    def poll(self, replies, infos=None):
        dev = FakeDevice(replies)
        B.hid = type("HID", (), {"device": staticmethod(lambda: dev)})
        B.hidlist = type("HL", (), {
            "enumerate": staticmethod(lambda vid=0: list(INFO if infos is None else infos))})
        return B.BlackSharkV3XProvider().poll(), dev

    def test_the_vendor_page_is_read(self):
        out, dev = self.poll([reply(77)])
        self.assertEqual([(s.key, s.name, s.level, s.online, s.kind, s.charging_known)
                          for s in out],
                         [("blackshark_v3x:057d", "Razer BlackShark V3 X HyperSpeed",
                           77, True, "headset", False)])
        self.assertEqual([b"ff82"], dev.opened)
        self.assertEqual(bytes(B.make_query()), dev.sent[0])
        self.assertTrue(dev.closed)

    def test_the_charge_reply_is_read(self):
        out, dev = self.poll([reply(77), charge_reply(1)])
        self.assertEqual((out[0].level, out[0].charging, out[0].charging_known),
                         (77, True, True))
        self.assertEqual([s[8] for s in dev.sent], [B.CMD_BATTERY, B.CMD_CHARGING])

    def test_the_cable_identity_is_read_with_the_same_protocol(self):
        out, dev = self.poll([reply(88), charge_reply(1)], infos=CABLE_INFO)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.charging_known)
                          for s in out],
                         [("blackshark_v3x:057d",
                           "Razer BlackShark V3 X HyperSpeed (USB cable)",
                           88, True, True)])
        self.assertEqual([b"cable-ff82"], dev.opened)
        self.assertEqual([s[8] for s in dev.sent], [B.CMD_BATTERY, B.CMD_CHARGING])

    def test_the_cable_is_tried_before_the_receiver(self):
        out, dev = self.poll([reply(66)], infos=CABLE_INFO + INFO)
        self.assertEqual([(s.name, s.level) for s in out],
                         [("Razer BlackShark V3 X HyperSpeed (USB cable)", 66)])
        self.assertEqual([b"cable-ff82"], dev.opened)

    def test_an_unknown_charge_state_keeps_the_level(self):
        out, _dev = self.poll([reply(77), charge_reply(2)])
        self.assertEqual((out[0].level, out[0].charging, out[0].charging_known),
                         (77, False, False))

    def test_a_silent_cable_falls_through_to_the_receiver(self):
        out, dev = self.poll([[], [], [], [], reply(64)], infos=CABLE_INFO + INFO)
        # the cable's ff82 returns no reply on all four reads, the receiver's answers
        self.assertEqual([s.level for s in out], [64])
        self.assertEqual([b"cable-ff82", b"ff82"], dev.opened)

    def test_only_iface0_ff82_is_written(self):
        # ff90 refused every write in #280; the consumer/telephony pages were never
        # shown to answer. With the headset off, only the two ff82 collections get
        # the battery query, once each.
        out, dev = self.poll([], infos=CABLE_INFO + INFO + OTHER_PAGES)
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])
        self.assertEqual([b"cable-ff82", b"ff82"], dev.opened)
        self.assertEqual([B.CMD_BATTERY, B.CMD_BATTERY], [s[8] for s in dev.sent])

    def test_no_ff82_collection_means_nothing_is_sent(self):
        out, dev = self.poll([reply(50)], infos=[INFO[0]] + OTHER_PAGES)
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])
        self.assertEqual([], dev.opened)
        self.assertEqual([], dev.sent)

    def test_ff82_on_another_interface_is_not_written(self):
        moved = [dict(INFO[1], interface_number=1)]
        _out, dev = self.poll([reply(50)], infos=moved)
        self.assertEqual([], dev.sent)

    def test_no_reply_keeps_the_icon_without_a_level(self):
        out, _dev = self.poll([])
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])

    def test_no_receiver_is_harmless(self):
        out, _dev = self.poll([], infos=[])
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
