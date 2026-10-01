"""Tests for providers/eightbitdo.py (issue #101). No hardware is needed.

The 8BitDo Pro 2 in D-input mode only reports its battery in the "enhanced" report,
and switching it into that mode breaks DirectInput games until the controller is
turned off. The provider must therefore never send anything to the controller:
it only listens, and reads the level while Steam or a game has already switched
the controller. A fake clock replaces time, so the read windows run without waiting.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import eightbitdo as E  # noqa: E402

BT_PATH = (b"\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&00022dc8_pid&6006"
           b"#9&1a2b3c4d&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}")
USB_PATH = b"\\\\?\\hid#vid_2dc8&pid_6003#8&1234abcd&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def enhanced(rid=0x01, battery=0x4B):
    r = [rid] + [0] * 33
    r[1] = 0x08                   # hat centred
    r[E.BATTERY_BYTE] = battery
    return r


class FakePad:
    """reports: what read() returns, cycled; [] = nothing arrives (idle, ordinary mode)."""

    def __init__(self, reports):
        self.reports = reports
        self.i = 0
        self.writes = []          # anything sent to the controller

    def next(self):
        if not self.reports:
            return []
        r = self.reports[self.i % len(self.reports)]
        self.i += 1
        return list(r)


class FakeBus:
    def __init__(self, pads):
        self.pads = pads

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.pads:
                    raise OSError("cannot open")
                self.pad = bus.pads[path]

            def set_nonblocking(self, on):
                pass

            def get_feature_report(self, rid, n):
                self.pad.writes.append(("get_feature", rid))
                return [rid] + [0] * 40

            def send_feature_report(self, data):
                self.pad.writes.append(("send_feature", list(data)))
                return len(data)

            def write(self, data):
                self.pad.writes.append(("write", list(data)))
                return len(data)

            def read(self, n):
                return self.pad.next()

            def close(self):
                pass

        return FakeDevice


def entry(path, pid=0x6006, serial="e417d8aabbcc"):
    return {"product_id": pid, "interface_number": -1, "usage_page": 0x0001, "usage": 0x0005,
            "path": path, "product_string": "8BitDo Pro 2", "serial_number": serial}


class ParseTest(unittest.TestCase):
    def test_level_and_charging_bit(self):
        self.assertEqual(E.parse_battery(0x4B), (75, False))
        self.assertEqual(E.parse_battery(0x80 | 40), (40, True))
        self.assertEqual(E.parse_battery(100), (100, False))

    def test_zero_and_out_of_range_are_not_levels(self):
        self.assertIsNone(E.parse_battery(0x00))   # the padding of the ordinary report
        self.assertIsNone(E.parse_battery(0x80))   # "charging, 0 %" is not a real reading
        self.assertIsNone(E.parse_battery(0x7F))   # 127 %


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (E.hid, E.hidlist, E.time)
        self.clock = Clock()
        E.time = types.SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep)
        self.provider = E.EightBitDoProvider()

    def tearDown(self):
        E.hid, E.hidlist, E.time = self._saved

    def poll(self, entries, pads):
        bus = FakeBus(pads)
        E.hid = types.SimpleNamespace(device=bus.device_class())
        E.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()

    def test_enhanced_bluetooth_report_is_read(self):
        pad = FakePad([enhanced(0x01, 0x80 | 62)])
        out = self.poll([entry(BT_PATH)], {BT_PATH: pad})
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s.level, s.charging), (62, True))
        self.assertEqual((s.kind, s.source, s.via), ("gamepad", "8bitdo", "bluetooth"))
        self.assertEqual(s.name, "8BitDo Pro 2")
        self.assertFalse(s.approx)

    def test_nothing_is_ever_sent_to_the_controller(self):
        # issue #101: the enhanced-mode switch hides the pad from DirectInput games
        for reports in ([], [enhanced(0x03, 0)], [enhanced(0x01, 0x4B)]):
            pad = FakePad(reports)
            self.poll([entry(BT_PATH)], {BT_PATH: pad})
            self.assertEqual(pad.writes, [])

    def test_idle_ordinary_mode_shows_no_level(self):
        start = self.clock.now
        out = self.poll([entry(BT_PATH)], {BT_PATH: FakePad([])})
        self.assertIsNone(out[0].level)
        self.assertIn("Steam", out[0].approx)
        self.assertEqual(out[0].ui_message, "state.8bitdo_basic")
        self.assertLessEqual(self.clock.now - start, E.BUDGET + 0.01)
        self.assertIn("ordinary mode", "\n".join(self.provider.diagnostics()))

    def test_zero_padded_ordinary_report_is_not_zero_percent(self):
        # on Windows the ordinary report can arrive with the same id, padded with zeros:
        # that must not become a 0 % icon and a low-battery alert
        out = self.poll([entry(BT_PATH)], {BT_PATH: FakePad([enhanced(0x01, 0x00)])})
        self.assertIsNone(out[0].level)
        text = "\n".join(self.provider.diagnostics())
        self.assertIn("no level in byte 14", text)
        self.assertIn("01 08", text)                # the raw bytes go to the diagnostics

    def test_other_report_ids_are_ignored(self):
        out = self.poll([entry(BT_PATH)], {BT_PATH: FakePad([enhanced(0x03, 0x4B)])})
        self.assertIsNone(out[0].level)

    def test_usb_uses_the_usb_enhanced_report(self):
        out = self.poll([entry(USB_PATH, pid=0x6003, serial="")],
                        {USB_PATH: FakePad([enhanced(0x01, 0x4B), enhanced(0x04, 0x80 | 90)])})
        self.assertEqual((out[0].level, out[0].charging), (90, True))
        self.assertEqual(out[0].via, "")

    def test_unknown_pid_is_skipped(self):
        out = self.poll([entry(BT_PATH, pid=0x6012)], {BT_PATH: FakePad([enhanced()])})
        self.assertEqual(out, [])

    def test_unopenable_controller_still_shows_without_level(self):
        out = self.poll([entry(BT_PATH)], {})
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].level)


if __name__ == "__main__":
    unittest.main()
