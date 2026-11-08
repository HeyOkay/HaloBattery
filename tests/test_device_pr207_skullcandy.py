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

# The previous poll's snapshot: the capture's report with 65 % in it - what report 7
# still holds when the next ask goes out.
_STALE = bytearray(CAPTURE_REPORT)
_STALE[18] = 65
STALE_REPORT = bytes(_STALE)


class FakeDongle:
    """mode: "answer" (the capture's report after the ask), "ack", "silent", or
    "same" (the report never changes: a steady level). `before` is what report 7
    holds before and just after the ask - the previous poll's snapshot; the fresh
    one replaces it `pending` reads later."""

    def __init__(self, mode="answer", before=None, pending=2):
        self.mode = mode
        self.before = before
        self.default_pending = pending
        self.pending = None
        self.fresh = None
        self.writes = []
        self.opened = 0

    def open_path(self, path):
        self.opened += 1

    def write(self, data):
        self.writes.append(list(data))
        if self.mode == "answer":
            self.fresh = CAPTURE_REPORT
        elif self.mode == "same":
            self.fresh = self.before
        elif self.mode == "ack":
            self.fresh = ACK_ONLY
        else:
            self.fresh = None
        self.pending = self.default_pending if self.mode != "silent" else None
        return len(data)

    def get_input_report(self, report_id, size):
        if self.pending is None:                      # before the ask (or silent)
            return list(self.before) if self.before else []
        if self.pending > 0:
            self.pending -= 1
            return list(self.before) if self.before else []
        return list(self.fresh) if self.fresh else []


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

    def test_an_empty_report_before_the_answer_is_skipped(self):
        # nothing in report 7 yet: the empty reads are not taken for an answer
        self.assertEqual([r.level for r in
                          self.poll(dongle_entries(), {b"dongle": FakeDongle()})], [70])

    def test_a_stale_indication_is_not_taken_for_the_answer(self):
        # the previous poll's 65 % still sits in report 7 when the ask goes out:
        # it must not be shown as the fresh answer (review, @ahmedkhursheed23)
        dongle = FakeDongle(before=STALE_REPORT)
        self.assertEqual([r.level for r in
                          self.poll(dongle_entries(), {b"dongle": dongle})], [70])

    def test_a_steady_level_is_still_reported(self):
        # the report never changes because the level really is the same: the last
        # read is still parsed, so a steady value cannot leave the icon empty
        dongle = FakeDongle(mode="same", before=STALE_REPORT)
        self.assertEqual([r.level for r in
                          self.poll(dongle_entries(), {b"dongle": dongle})], [65])

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
