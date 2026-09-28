"""Tests for providers/blackshark2020.py and its branch in providers/razer.py.
No hardware is needed.

The frames are built from the two sources the request comes from: the Synapse
capture in openrazer#1280 (the request, ff 0a 00 fd 04 12 f1 02 05 at offset
0x40 of a 64-byte feature report) and Modzeleczek/RazerNariBatteryLevel (the
reply header and the offsets - its own capture reads 3552 mV in bytes 12-13
and 0x1e in byte 14). The state bytes 0x01/0x09 and the charging bit 0x08 are
the 2020 receiver's own, from the tests on the headset in the README; the Nari
capture has 0x02 in that position, and a test below pins what happens then.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import blackshark2020 as B  # noqa: E402
from providers import razer as R  # noqa: E402

HEADER = bytes.fromhex("ff 0f 05 fe 12 04 1f 08 05 03 05")
STALE = bytes.fromhex("ff 01 00 fe 12 04 1f 08 05 03 05")


def frame(state, mv, level, header=HEADER, tail=b"lient\x00"):
    """A 64-byte feature reply: header(11) state mv[2] level tail..."""
    body = header + bytes([state]) + mv.to_bytes(2, "big") + bytes([level]) + tail
    return body.ljust(B.REPORT_LEN, b"\x00")[:B.REPORT_LEN]


# the Nari capture as the reference README describes it: state 0x02, 3552 mV, 0x1e
NARI = frame(0x02, 3552, 0x1E, tail=b"lient\x00")


class FakeDevice:
    def __init__(self, reply=(), written=B.REPORT_LEN, raise_on=None):
        self.reply = bytes(reply)
        self.written = written
        self.raise_on = raise_on
        self.sent = []
        self.closed = 0

    def open_path(self, path):
        if self.raise_on == "open":
            raise OSError("no such device")

    def send_feature_report(self, data):
        if self.raise_on == "write":
            raise OSError("write failed")
        self.sent.append(bytes(data))
        return self.written if self.written is not None else len(data)

    def get_feature_report(self, report_id, n):
        return list(self.reply.ljust(n, b"\x00")[:n])

    def close(self):
        self.closed += 1


def with_device(dev):
    B.hid = types.SimpleNamespace(device=lambda: dev)
    return dev


class ParseReplyTests(unittest.TestCase):
    def test_the_request_is_the_captured_one(self):
        self.assertEqual(B.REQUEST[:9], bytes.fromhex("ff 0a 00 fd 04 12 f1 02 05"))
        self.assertEqual(len(B.REQUEST), 64)
        self.assertEqual(B.REPORT_ID, 0xFF)

    def test_a_complete_reply_gives_state_voltage_and_level(self):
        self.assertEqual(B.parse_reply(frame(0x01, 3700, 55)), (0x01, 3700, 55))

    def test_the_nari_frame_parses_with_its_own_state_byte(self):
        # 3552 mV and 0x1e are that reference's own numbers
        self.assertEqual(B.parse_reply(NARI), (0x02, 3552, 0x1E))

    def test_short_frame_is_rejected(self):
        self.assertIsNone(B.parse_reply(frame(0x01, 3700, 55)[:40]))

    def test_the_request_echo_is_not_a_reply(self):
        self.assertIsNone(B.parse_reply(B.REQUEST))

    def test_the_stale_header_is_not_a_reply(self):
        self.assertIsNone(B.parse_reply(STALE + bytes(53)))

    def test_all_zero_frame_is_rejected(self):
        self.assertIsNone(B.parse_reply(bytes(64)))

    def test_a_level_above_100_is_rejected(self):
        self.assertIsNone(B.parse_reply(frame(0x01, 3700, 101)))


class CandidateTests(unittest.TestCase):
    def test_only_the_ff00_0001_collection_is_taken(self):
        self.assertTrue(B.is_candidate({"usage_page": 0xFF00, "usage": 0x01}))
        self.assertFalse(B.is_candidate({"usage_page": 0xFF00, "usage": 0x02}))
        self.assertFalse(B.is_candidate({"usage_page": 0x000C, "usage": 0x01}))
        self.assertFalse(B.is_candidate({"usage_page": None, "usage": None}))
        self.assertFalse(B.is_candidate({}))


class ReadBatteryTests(unittest.TestCase):
    def read(self, dev):
        with_device(dev)
        diag = []
        return B.read_battery(b"path", diag) + (diag,)

    def test_battery_reading_and_not_charging(self):
        res, level, charging, diag = self.read(FakeDevice(frame(0x01, 3700, 55)))
        self.assertEqual((res, level, charging), ("ok", 55, False))
        self.assertTrue(any("power=01" in d for d in diag))

    def test_charging_flag_is_the_0x08_bit(self):
        res, level, charging, _ = self.read(FakeDevice(frame(0x09, 4100, 60)))
        self.assertEqual((res, level, charging), ("ok", 60, True))

    def test_zero_voltage_is_offline(self):
        res, level, charging, _ = self.read(FakeDevice(frame(0x01, 0, 40)))
        self.assertEqual((res, level, charging), ("offline", None, False))

    def test_the_stale_header_means_offline(self):
        res, level, charging, diag = self.read(FakeDevice(STALE + bytes(53)))
        self.assertEqual((res, level, charging), ("offline", None, False))
        self.assertTrue(any("no fresh headset reply" in d for d in diag))

    def test_an_unexpected_state_is_refused_not_shown(self):
        # the Nari's 0x02 arrives here when the reply is otherwise well formed
        res, level, charging, diag = self.read(FakeDevice(NARI))
        self.assertEqual((res, level, charging), ("fail", None, False))
        self.assertTrue(any("unknown power state" in d for d in diag))

    def test_nothing_at_all_is_a_failure(self):
        res, level, charging, _ = self.read(FakeDevice(bytes(64)))
        self.assertEqual((res, level, charging), ("fail", None, False))

    def test_a_short_feature_write_is_a_failure(self):
        res, level, charging, diag = self.read(FakeDevice(frame(0x01, 3700, 55), written=8))
        self.assertEqual((res, level, charging), ("fail", None, False))
        self.assertTrue(any("short feature write" in d for d in diag))

    def test_an_unopenable_collection_is_a_failure(self):
        res, level, charging, _ = self.read(FakeDevice(raise_on="open"))
        self.assertEqual((res, level, charging), ("fail", None, False))

    def test_the_device_is_closed_after_a_read(self):
        dev = with_device(FakeDevice(frame(0x01, 3700, 55)))
        B.read_battery(b"path", [])
        self.assertEqual(dev.closed, 1)

    def test_the_device_is_closed_even_when_the_read_fails(self):
        dev = with_device(FakeDevice(bytes(64)))
        B.read_battery(b"path", [])
        self.assertEqual(dev.closed, 1)


class RazerRoutingTests(unittest.TestCase):
    def provider(self):
        obj = R.RazerProvider.__new__(R.RazerProvider)
        obj._diag = []
        return obj

    def test_the_candidate_collection_is_used_and_reported_ok(self):
        obj = self.provider()
        with_device(FakeDevice(frame(0x01, 3900, 70)))
        st = obj._poll_2020([{"path": b"good", "usage_page": 0xFF00, "usage": 0x01,
                              "interface_number": 3}])
        self.assertEqual(st, (R.STATUS_OK, 70, False))

    def test_other_collections_are_not_touched(self):
        obj = self.provider()
        called = []
        real = B.read_battery
        B.read_battery = lambda path, diag: called.append(path) or ("ok", 50, False)
        try:
            st = obj._poll_2020([{"path": b"other", "usage_page": 0x000C, "usage": 0x01,
                                  "interface_number": 0}])
        finally:
            B.read_battery = real
        self.assertIsNone(st)
        self.assertEqual(called, [])

    def test_a_switched_off_headset_is_a_timeout_not_a_failure(self):
        obj = self.provider()
        B.read_battery_backup = B.read_battery
        B.read_battery = lambda path, diag: ("offline", None, False)
        try:
            st = obj._poll_2020([{"path": b"good", "usage_page": 0xFF00, "usage": 0x01}])
        finally:
            B.read_battery = B.read_battery_backup
        self.assertEqual(st, (R.STATUS_TIMEOUT, None, None))


if __name__ == "__main__":
    unittest.main()
