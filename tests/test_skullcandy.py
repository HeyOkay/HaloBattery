"""Tests for providers/skullcandy.py. No hardware: the fake dongle answers with the
report of the Skull-HQ capture attached to issue #104 (Skull-HQ showed 70 %, the
indication carries 0x46), and the ask is pinned against the capture's own bytes.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import skullcandy as S  # noqa: E402

# Issue #104's capture, byte for byte: the ask (packet 8141) and the one battery
# report it was answered with (packets 8160+; 07 10 80 then the 0x5B acknowledgement
# and the 0x5D indication in a single report).
CAPTURE_ASK = bytes.fromhex("060780055a0300d60c00") + bytes(52)
CAPTURE_REPORT = bytes.fromhex(
    "071080"
    "055b0300d60c00"
    "055d0500d60c000046"
    "00000000ff00000000000100" + "00" * 30)

# The acknowledgement alone: what a reader that looks only at the first frame sees.
ACK_ONLY = bytes.fromhex("070780" "055b0300d60c00") + bytes(52)


class FakeDongle:
    """mode: "answer" (a stale report first, then the capture's), "ack" (the
    acknowledgement only), "silent"."""

    def __init__(self, mode="answer"):
        self.mode = mode
        self.writes = []
        self.queue = []
        self.opened = 0

    def open_path(self, path):
        self.opened += 1

    def write(self, data):
        self.writes.append(list(data))
        if self.mode == "answer":
            self.queue = [[0x07] + [0] * 61, CAPTURE_REPORT]
        elif self.mode == "ack":
            self.queue = [ACK_ONLY]
        return len(data)

    def get_input_report(self, report_id, size):
        return self.queue.pop(0) if self.queue else []


class FakeBus:
    def __init__(self, dongles):
        self.dongles = dongles

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.m = bus.dongles[path]
                self.m.opened += 1

            def write(self, data):
                return self.m.write(data)

            def get_input_report(self, report_id, size):
                return self.m.get_input_report(report_id, size)

            def close(self):
                pass

        return FakeDevice


def dongle_entries(pid=0x3210, usage_page=0xFF13, path=b"dongle"):
    return [{"product_id": pid, "interface_number": 5, "usage_page": usage_page,
             "usage": 1, "path": path, "product_string": "PLYR Dongle Chat"}]


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (S.hid, S.hidlist, S.time)
        S.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)

    def tearDown(self):
        S.hid, S.hidlist, S.time = self._saved

    def poll(self, entries, dongles):
        S.hid = types.SimpleNamespace(device=FakeBus(dongles).device_class())
        S.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return S.SkullcandyProvider().poll()


class CaptureTest(ProviderTest):
    def test_the_ask_is_the_captures_request_byte_for_byte(self):
        self.assertEqual(S.ASK + bytes(62 - len(S.ASK)), CAPTURE_ASK)
        self.assertEqual(S.ASK[:3], bytes([0x06, 0x07, 0x80]))     # id, length, recipient
        dongle = FakeDongle()
        self.poll(dongle_entries(), {b"dongle": dongle})
        self.assertEqual(dongle.writes[0], list(CAPTURE_ASK))
        self.assertEqual(set(dongle.writes[0][10:]), {0})           # NUL padded

    def test_the_capture_answers_70_percent(self):
        dongle = FakeDongle()
        res = self.poll(dongle_entries(), {b"dongle": dongle})
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.online, r.kind) for r in res],
                         [("skullcandy:3210", "Skullcandy PLYR", 70, False, True, "headset")])

    def test_a_stale_report_before_the_answer_is_skipped(self):
        # the fake hands out one empty report first; the answer follows
        self.assertEqual([r.level for r in
                          self.poll(dongle_entries(), {b"dongle": FakeDongle()})], [70])

    def test_the_acknowledgement_alone_is_not_a_reading(self):
        # the trap: the 0x5B ack sits in the same report, before the indication
        p = S.SkullcandyProvider()
        S.hid = types.SimpleNamespace(
            device=FakeBus({b"dongle": FakeDongle(mode="ack")}).device_class())
        S.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(dongle_entries()))
        res = p.poll()
        self.assertEqual([(r.name, r.level) for r in res], [("Skullcandy PLYR", None)])
        self.assertTrue(any("no battery indication" in line for line in p.diagnostics()))

    def test_a_silent_dongle_keeps_the_icon_without_a_level(self):
        p = S.SkullcandyProvider()
        S.hid = types.SimpleNamespace(device=FakeBus({b"dongle": FakeDongle(mode="silent")}).device_class())
        S.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(dongle_entries()))
        res = p.poll()
        self.assertEqual([(r.name, r.level, r.kind) for r in res],
                         [("Skullcandy PLYR", None, "headset")])
        self.assertTrue(any("no battery indication" in line for line in p.diagnostics()))

    def test_the_crusher_is_known_too(self):
        res = self.poll(dongle_entries(pid=0x5310), {b"dongle": FakeDongle()})
        self.assertEqual([r.name for r in res], ["Skullcandy Crusher PLYR 720"])

    def test_the_slyr_pro_is_not_claimed(self):
        dongle = FakeDongle()
        self.assertEqual(self.poll(dongle_entries(pid=0x2220), {b"dongle": dongle}), [])
        self.assertEqual(dongle.opened, 0)

    def test_only_the_vendor_collection_is_read(self):
        entries = dongle_entries(usage_page=0x000C, path=b"media") + dongle_entries()
        media, vendor = FakeDongle(), FakeDongle()
        res = self.poll(entries, {b"media": media, b"dongle": vendor})
        self.assertEqual([r.level for r in res], [70])
        self.assertEqual((media.opened, vendor.opened), (0, 1))


class ParseTest(unittest.TestCase):
    def test_the_capture_report(self):
        self.assertEqual(S.battery_percent(CAPTURE_REPORT), 70)

    def test_without_the_report_header(self):
        # a bare stream, as a longer read may present it
        self.assertEqual(S.battery_percent(CAPTURE_REPORT[3:]), 70)

    def test_the_acknowledgement_alone(self):
        self.assertIsNone(S.battery_percent(ACK_ONLY))

    def test_a_failed_status_is_not_a_reading(self):
        bad = bytearray(CAPTURE_REPORT)
        bad[16] = 1                                   # the indication's status byte
        self.assertIsNone(S.battery_percent(bytes(bad)))

    def test_out_of_range_percent_is_refused(self):
        bad = bytearray(CAPTURE_REPORT)
        bad[18] = 101
        self.assertIsNone(S.battery_percent(bytes(bad)))
        bad[18] = 255
        self.assertIsNone(S.battery_percent(bytes(bad)))

    def test_a_truncated_frame_ahead_does_not_hide_the_indication(self):
        # the slicer stops at a bogus length; the direct scan still finds the
        # indication behind it
        buf = bytes.fromhex("071080" "055affff") + bytes.fromhex("055d0500d60c000046") + bytes(45)
        self.assertEqual(S.battery_percent(buf), 70)

    def test_other_opcodes_and_short_reports(self):
        self.assertIsNone(S.battery_percent(b""))
        self.assertIsNone(S.battery_percent(b"\x07\x10\x80" + bytes(20)))
        self.assertIsNone(S.battery_percent(CAPTURE_REPORT[:18]))     # cut before the percent
        other = bytearray(CAPTURE_REPORT)
        other[15] = 0x2C                              # a different opcode
        self.assertIsNone(S.battery_percent(bytes(other)))


if __name__ == "__main__":
    unittest.main()
