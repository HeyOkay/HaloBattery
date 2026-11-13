"""GameSir G7 Pro 8K state reports and XInput pairing; no real hardware.

The frames follow the owner's diagnostics of 2026-11-13 (Aimlabs edition): report 0x12,
bytes 35-36 "00 62" on the dongle (98 %) and "01 60" on the cable and the dock (96 %,
charging). The pad stays silent until it has been asked something.
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import gamesir as G, wgi, xinput as X

DONGLE, CABLE = 0x10C6, 0x10C5


def state(level=98, charge=0):
    frame = bytearray(64)
    frame[0], frame[G.CHARGE_BYTE], frame[G.LEVEL_BYTE] = G.STATE_REPORT, charge, level
    return bytes(frame)


COLLECTION = {"vendor_id": 0x3537, "product_id": DONGLE, "interface_number": 1,
              "usage_page": 0xFFF0, "usage": 0x40, "path": b"gamesir"}


class FakePad:
    """Silent until asked, like the real pad."""

    def __init__(self, frames=(), fail=None):
        self.frames = list(frames)
        self.fail = fail
        self.writes = []
        self.closed = False

    def open_path(self, path):
        if self.fail == "open":
            raise OSError("removed")

    def write(self, data):
        if self.fail == "write":
            raise OSError("removed")
        self.writes.append(bytes(data))
        return 64

    def read(self, size, timeout):
        if self.fail == "read":
            raise OSError("removed")
        return list(self.frames.pop(0)) if self.writes and self.frames else []

    def close(self):
        self.closed = True


class GameSirTests(unittest.TestCase):
    def test_states_seen_on_hardware(self):
        for level, charge, charging in ((0x64, 0, False), (0x62, 0, False), (0x61, 0, False),
                                        (0x60, 1, True), (0, 0, False)):
            with self.subTest(level=level, charge=charge):
                r = G.parse_state(state(level, charge), DONGLE)
                self.assertEqual((r.level, r.charging, r.name),
                                 (level, charging, "GameSir G7 Pro 8K Aimlabs"))

    def test_names(self):
        self.assertEqual(G.parse_state(state(), 0x10B9).name, "GameSir G7 Pro 8K")
        self.assertEqual(G.parse_state(state(), 0x10FE).name, "GameSir G7 Pro 8K Golden Apple")

    def test_every_id_of_gamesir_connect(self):
        self.assertEqual(set(G.EDITIONS), {4279, 4281, 4299, 4300, 4301, 4302, 4293, 4294,
                                           4295, 4296, 4297, 4298, 4349, 4350})

    def test_other_reports_and_impossible_levels_are_refused(self):
        for frame in (b"", state()[:30], b"\x0f\x0b" + bytes(62), state(101), state(255)):
            with self.subTest(frame=frame):
                self.assertIsNone(G.parse_state(frame, DONGLE))

    def read(self, pad, cols=None):
        diag = []
        with mock.patch.object(G.hidlist, "enumerate",
                               return_value=cols if cols is not None else [COLLECTION]), \
                mock.patch.object(G.hid, "device", return_value=pad):
            result = G.read_connected(diag)
        return result, diag

    def test_only_the_read_only_request_is_written(self):
        pad = FakePad([b"\x0f\x0b" + bytes(62), state(0x60, 1)])
        result, _ = self.read(pad)
        self.assertEqual((result.level, result.charging), (96, True))
        self.assertEqual(pad.writes, [bytes.fromhex("0f0b")])
        self.assertTrue(pad.closed)

    def test_cable_id(self):
        result, _ = self.read(FakePad([state(0x60, 1)]), [dict(COLLECTION, product_id=CABLE)])
        self.assertEqual((result.level, result.charging), (96, True))

    def test_disconnect_and_silence_are_safe(self):
        for pad in (FakePad(fail="open"), FakePad(fail="write"), FakePad(fail="read"), FakePad()):
            result, diag = self.read(pad)
            self.assertIsNone(result)
            self.assertTrue(diag)
            self.assertTrue(pad.closed)

    def test_unrelated_and_ambiguous_collections_are_never_opened(self):
        # 0x0575: the idle dongle while the pad is on its cable
        for cols in ([], [dict(COLLECTION, product_id=0x0575)],
                     [dict(COLLECTION, usage_page=1)], [COLLECTION, COLLECTION]):
            pad = FakePad([state()])
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


def report(vid=0x3537, pid=DONGLE):
    return wgi.WgiController({"vid": vid, "pid": pid, "status": "Discharging",
                              "remain": 1000, "full": 1000})


AIMLABS = G.Reading(98, False, "", "GameSir G7 Pro 8K Aimlabs")


class GameSirXInputTests(unittest.TestCase):
    def poll(self, reports, count=1, reading=AIMLABS):
        with mock.patch.object(X, "load_xinput", return_value=FakeXInput(count)), \
                mock.patch.object(X, "hidlist", types.SimpleNamespace(
                    enumerate=lambda vid: [], interface_paths=lambda: [])), \
                mock.patch.object(X.wgi, "query", return_value=reports), \
                mock.patch.object(G, "read_connected", return_value=reading) as read:
            result = X.XInputProvider().poll()
        return result, read.call_count

    def test_one_icon_uses_the_controllers_own_reading(self):
        rows, calls = self.poll([report()])
        self.assertEqual(calls, 1)
        self.assertEqual([(r.key, r.name, r.level, r.charging) for r in rows],
                         [("xinput:0", "GameSir G7 Pro 8K Aimlabs", 98, False)])

    def test_charging_comes_from_the_pad(self):
        rows, _ = self.poll([report(pid=CABLE)], reading=AIMLABS._replace(level=96, charging=True))
        self.assertEqual((rows[0].level, rows[0].charging), (96, True))

    def test_no_reading_shows_no_level_rather_than_the_placeholder(self):
        rows, calls = self.poll([report()], reading=None)
        self.assertEqual(calls, 1)
        self.assertEqual([(r.level, r.charging) for r in rows], [(None, False)])

    def test_other_gamesir_pads_keep_the_windows_level(self):
        # the earlier G7 Pro gets a real level from Windows: its id is not in the table
        rows, calls = self.poll([wgi.WgiController({"vid": 0x3537, "pid": 0x1010,
                                                    "status": "Discharging",
                                                    "remain": 640, "full": 1000})])
        self.assertEqual(calls, 0)
        self.assertEqual([r.level for r in rows], [64])

    def test_two_pads_are_not_paired(self):
        _, calls = self.poll([report(), report()], count=2)
        self.assertEqual(calls, 0)


if __name__ == "__main__":
    unittest.main()
