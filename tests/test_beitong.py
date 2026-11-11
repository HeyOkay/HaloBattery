"""Captured KP40D status reports and XInput pairing; no real hardware."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import beitong as B, wgi, xinput as X


def status(level=88, state=0x50):
    # Capture frame 713, with the level/state varied for boundary tests.
    frame = bytearray.fromhex("02155800500100f00f01" + "00" * 54)
    frame[2], frame[4] = level, state
    return bytes(frame)


COLLECTION = {"vendor_id": 0x20BC, "product_id": 0x515C, "interface_number": 1,
              "usage_page": 0x00FF, "usage": 3, "path": b"beitong"}


class FakePad:
    def __init__(self, frames=(), fail=None, sent=64):
        self.frames = list(frames)
        self.fail, self.sent = fail, sent
        self.writes = []
        self.closed = False

    def open_path(self, path):
        if self.fail == "open":
            raise OSError("removed")

    def write(self, data):
        if self.fail == "write":
            raise OSError("removed")
        self.writes.append(bytes(data))
        return self.sent

    def read(self, size, timeout):
        if self.fail == "read":
            raise OSError("removed")
        return list(self.frames.pop(0)) if self.writes and self.frames else []

    def close(self):
        self.closed = True


class BeitongTests(unittest.TestCase):
    def test_captured_wireless_and_charging_states(self):
        for level, state, charging in ((88, 0x50, False), (96, 0x51, True),
                                       (98, 0x51, True), (91, 0x50, False),
                                       (0, 0x50, False), (100, 0x51, True)):
            with self.subTest(level=level, state=state):
                r = B.parse_reply(status(level, state))
                self.assertEqual((r.level, r.charging, r.name),
                                 (level, charging, "BEITONG KP40D"))

    def test_malformed_and_unknown_states_are_refused(self):
        for frame in (b"", status()[:5], status()[:-1], status() + b"\0",
                      b"\x02\x25" + bytes(62), status(101), status(255),
                      status(state=0), status(state=0x52)):
            with self.subTest(frame=frame):
                self.assertIsNone(B.parse_reply(frame))

    def read(self, pad, cols=None):
        diag = []
        with mock.patch.object(B.hidlist, "enumerate", return_value=cols if cols is not None else [COLLECTION]), \
                mock.patch.object(B.hid, "device", return_value=pad):
            result = B.read_connected(diag)
        return result, diag

    def test_only_the_captured_status_query_is_written(self):
        pad = FakePad([b"\x02\x25" + bytes(62), status()], sent=65)
        result, _ = self.read(pad)
        self.assertEqual(result.level, 88)
        self.assertEqual(pad.writes, [bytes.fromhex("0215") + bytes(62)])
        self.assertTrue(pad.closed)

    def test_disconnect_and_failed_or_short_write_are_safe(self):
        for pad in (FakePad(fail="open"), FakePad(fail="write"), FakePad(fail="read"),
                    FakePad(sent=-1), FakePad(sent=2), FakePad()):
            result, diag = self.read(pad)
            self.assertIsNone(result)
            self.assertTrue(diag)
            self.assertTrue(pad.closed)

    def test_unrelated_and_ambiguous_collections_are_never_opened(self):
        for cols in ([], [dict(COLLECTION, product_id=0x515B)],
                     [dict(COLLECTION, usage_page=1)], [dict(COLLECTION, usage=2)],
                     [dict(COLLECTION, interface_number=0)], [COLLECTION, COLLECTION]):
            pad = FakePad([status()])
            result, _ = self.read(pad, cols)
            self.assertIsNone(result)
            self.assertEqual(pad.writes, [])
            self.assertFalse(pad.closed)


class FakeXInput:
    def __init__(self, count):
        self.count = count

    def XInputGetState(self, slot, state):
        return 0 if slot < self.count else 1167

    def XInputGetBatteryInformation(self, slot, kind, info):
        info._obj.BatteryType, info._obj.BatteryLevel = X.TYPE_WIRED, 3
        return 0


def report(vid=0x20BC, pid=0x515C, state="Discharging"):
    return wgi.WgiController({"vid": vid, "pid": pid, "status": state,
                              "remain": 1000, "full": 1000})


class BeitongXInputTests(unittest.TestCase):
    def poll(self, reports, count=1, reading=B.Reading(88, False, "", "BEITONG KP40D")):
        with mock.patch.object(X, "load_xinput", return_value=FakeXInput(count)), \
                mock.patch.object(X, "hidlist", types.SimpleNamespace(
                    enumerate=lambda vid: [], interface_paths=lambda: [])), \
                mock.patch.object(X.wgi, "query", return_value=reports), \
                mock.patch.object(B, "read_connected", return_value=reading) as read:
            result = X.XInputProvider().poll()
        return result, read.call_count

    def test_one_icon_uses_the_controllers_own_reading(self):
        rows, calls = self.poll([report()])
        self.assertEqual(calls, 1)
        self.assertEqual([(r.key, r.name, r.level, r.charging) for r in rows],
                         [("xinput:0", "BEITONG KP40D", 88, False)])

    def test_charging_comes_from_beitong_not_xinput(self):
        rows, _ = self.poll([report()], reading=B.Reading(96, True, "", "BEITONG KP40D"))
        self.assertEqual((rows[0].level, rows[0].charging), (96, True))

    def test_missing_reading_does_not_show_windows_placeholder(self):
        for state in ("Discharging", "Charging"):
            rows, calls = self.poll([report(state=state)], reading=None)
            self.assertEqual(calls, 1)
            self.assertIsNone(rows[0].level)

    def test_never_queries_another_pad_or_ambiguous_slots(self):
        for reports, count in (([report(0x045E, 0x02EA)], 1), ([], 1),
                               ([report(pid=0x515B)], 1),
                               ([report(), report(0x045E, 0x02EA)], 2),
                               ([report()], 0)):
            with self.subTest(reports=reports, count=count):
                _, calls = self.poll(reports, count)
                self.assertEqual(calls, 0)

    def test_broken_hid_does_not_break_xinput(self):
        with mock.patch.object(B, "read_connected", side_effect=OSError("removed")):
            # Keep the exception here instead of poll()'s normal reading stub.
            with mock.patch.object(X, "load_xinput", return_value=FakeXInput(1)), \
                    mock.patch.object(X, "hidlist", types.SimpleNamespace(
                        enumerate=lambda vid: [], interface_paths=lambda: [])), \
                    mock.patch.object(X.wgi, "query", return_value=[report()]):
                p = X.XInputProvider()
                rows = p.poll()
            self.assertIsNone(rows[0].level)
            self.assertTrue(any("[Beitong] removed" in line for line in p.diagnostics()))


if __name__ == "__main__":
    unittest.main()
