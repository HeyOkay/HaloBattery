"""Tests for providers/nintendo.py. No hardware is needed.

The tests replace the HID layer with fake controllers that answer as SDL's Switch
driver and dekuNukem's notes describe: report 0x3F in the simple mode, 0x30 in
the full mode, and 0x21 as the reply to a subcommand. A fake clock replaces
time, so the timeouts run without real waiting.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import nintendo as N  # noqa: E402

# A Bluetooth HID path as Windows spells it (the "VID&0002057E" form and the HID
# service GUID), and the USB form of the same controller.
BT_PATH = (b"\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002057e_pid&2009"
           b"#9&2b0f7c5a&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}")
USB_PATH = b"\\\\?\\hid#vid_057e&pid_2009&mi_00#8&1234abcd&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"


def bt_path(pid, n=0):
    return BT_PATH.replace(b"pid&2009", b"pid&%04x" % pid).replace(b"&0&0000", b"&0&000%d" % n)


# ------------------------------------------------------------------ fakes
class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def report(rid, battery, extra=()):
    r = [rid, 0x5A, battery] + [0] * 46
    for i, v in extra:
        r[i] = v
    return r


class FakePad:
    """mode:
    "simple"  sends nothing by itself; answers subcommand 0x02 with report 0x21
    "full"    streams report 0x30 (Steam or another app switched the mode)
    "late"    like "simple", but only answers the second subcommand
    "silent"  never answers
    """

    def __init__(self, mode="simple", battery=0x60, noise=()):
        self.mode = mode
        self.battery = battery
        self.queue = [list(r) for r in noise]
        self.writes = []
        self.opened = 0

    def on_write(self, data):
        self.writes.append(list(data))
        if data[0] != N.OUTPUT_RUMBLE_AND_SUBCOMMAND or data[10] != N.SUBCOMMAND_DEVICE_INFO:
            return
        if self.mode == "simple" or (self.mode == "late" and len(self.writes) >= 2):
            # ack byte 13 (0x80 = ack, 0x02 = data follows), subcommand id byte 14
            self.queue.append(report(0x21, self.battery, [(13, 0x82), (14, 0x02)]))

    def on_read(self):
        if self.mode == "full":
            return report(0x30, self.battery)
        return self.queue.pop(0) if self.queue else []


class FakeBus:
    def __init__(self, pads):
        self.pads = pads          # {path: FakePad}

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.pads:
                    raise OSError("cannot open")
                self.pad = bus.pads[path]
                self.pad.opened += 1

            def set_nonblocking(self, on):
                pass

            def write(self, data):
                self.pad.on_write(data)
                return len(data)

            def read(self, n):
                return self.pad.on_read()

            def close(self):
                pass

        return FakeDevice


def entry(pid, path, mac="98b6e9123456"):
    # the shape of the issue #63 diagnostics: one collection, gamepad usage, iface -1
    return {"product_id": pid, "interface_number": -1, "usage_page": 0x0001, "usage": 0x0005,
            "path": path, "product_string": "Wireless Gamepad", "serial_number": mac}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (N.hid, N.hidlist, N.time)
        self.clock = Clock()
        N.time = types.SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep)
        self.provider = N.NintendoProvider()

    def tearDown(self):
        N.hid, N.hidlist, N.time = self._saved

    def poll(self, entries, pads):
        bus = FakeBus(pads)
        N.hid = types.SimpleNamespace(device=bus.device_class())
        N.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()


# ------------------------------------------------------------------ tests
class ParseTest(unittest.TestCase):
    def test_every_battery_byte(self):
        for byte in range(256):
            level = (byte & 0xE0) >> 4
            res = N.parse_battery(byte)
            if level > 8:
                self.assertIsNone(res, hex(byte))
                continue
            pct, charging, text = res
            self.assertEqual(pct, round(level / 8 * 100), hex(byte))      # SDL's formula
            self.assertEqual(charging, bool(byte & 0x10), hex(byte))
            self.assertIn(f"({N.LEVEL_NAMES[level]})", text)
            self.assertEqual(text.endswith(", charging"), charging)

    def test_level_names(self):
        self.assertEqual(N.parse_battery(0x80), (100, False, "about 100% (full)"))
        self.assertEqual(N.parse_battery(0x60), (75, False, "about 75% (medium)"))
        self.assertEqual(N.parse_battery(0x40), (50, False, "about 50% (low)"))
        self.assertEqual(N.parse_battery(0x20), (25, False, "about 25% (critical)"))
        self.assertEqual(N.parse_battery(0x00), (0, False, "about 0% (empty)"))
        self.assertEqual(N.parse_battery(0x5E), (50, True, "about 50% (low), charging"))

    def test_the_connection_nibble_does_not_change_the_level(self):
        for low in range(16):
            self.assertEqual(N.parse_battery(0x60 | low)[0], 75)

    def test_subcommand_packet(self):
        pkt = N.subcommand_packet(0x11, 0x02)
        self.assertEqual(len(pkt), 49)
        self.assertEqual(pkt[:11], [0x01, 0x01, 0x00, 0x01, 0x40, 0x40, 0x00, 0x01, 0x40, 0x40, 0x02])
        self.assertEqual(set(pkt[11:]), {0})


class PollTest(ProviderTest):
    def test_simple_mode_issue_63(self):
        pad = FakePad("simple", battery=0x60, noise=[[0x3F, 0, 0x08] + [0] * 9])
        res = self.poll([entry(0x2009, BT_PATH)], {BT_PATH: pad})
        self.assertEqual(len(res), 1)
        st = res[0]
        self.assertEqual((st.name, st.level, st.charging, st.online),
                         ("Nintendo Switch Pro Controller", 75, False, True))
        self.assertEqual(st.approx, "about 75% (medium)")
        self.assertEqual((st.ui_message, st.ui_params),
                         ("state.approx_grade", {"level": 75, "grade": "medium", "charging": False}))
        self.assertEqual((st.key, st.kind, st.source), ("switch:2009:98b6e9123456", "gamepad", "nintendo"))
        self.assertEqual(len(pad.writes), 1)
        self.assertEqual(pad.writes[0][10], N.SUBCOMMAND_DEVICE_INFO)

    def test_full_mode_writes_nothing(self):
        pad = FakePad("full", battery=0x90)             # level 8, charging
        res = self.poll([entry(0x2009, BT_PATH)], {BT_PATH: pad})
        self.assertEqual([(r.level, r.charging) for r in res], [(100, True)])
        self.assertEqual(pad.writes, [])

    def test_second_try(self):
        pad = FakePad("late", battery=0x40)
        res = self.poll([entry(0x2009, BT_PATH)], {BT_PATH: pad})
        self.assertEqual([r.level for r in res], [50])
        self.assertEqual(len(pad.writes), 2)

    def test_silent_controller_gives_no_icon_and_at_most_two_writes(self):
        pad = FakePad("silent")
        self.assertEqual(self.poll([entry(0x2009, BT_PATH)], {BT_PATH: pad}), [])
        self.assertEqual(len(pad.writes), 2)

    def test_silent_after_a_reading_keeps_the_last_value_greyed_for_a_while(self):
        pad = FakePad("simple", battery=0x70)           # 75 %, charging
        e = [entry(0x2009, BT_PATH)]
        self.poll(e, {BT_PATH: pad})
        pad.mode = "silent"
        res = self.poll(e, {BT_PATH: pad})
        self.assertEqual([(r.level, r.charging, r.online) for r in res], [(75, False, False)])
        self.assertEqual(res[0].approx, "about 75% (medium) (last known value)")
        self.assertEqual(res[0].ui_params, {"level": 75, "grade": "medium", "last_known": True})
        self.clock.now += N.ASLEEP_KEEP
        self.assertEqual(self.poll(e, {BT_PATH: pad}), [])

    def test_undefined_level_is_refused(self):
        pad = FakePad("simple", battery=0xF0)
        self.assertEqual(self.poll([entry(0x2009, BT_PATH)], {BT_PATH: pad}), [])

    def test_usb_is_not_opened(self):
        pad = FakePad("simple")
        self.assertEqual(self.poll([entry(0x2009, USB_PATH)], {USB_PATH: pad}), [])
        self.assertEqual((pad.opened, pad.writes), (0, []))

    def test_other_nintendo_devices_are_not_opened(self):
        # N64 / SNES Online controllers and the Switch 2 Pro Controller: not in the table
        pads = {bt_path(pid): FakePad("simple") for pid in (0x2017, 0x2019, 0x2069, 0x0306)}
        res = self.poll([entry(pid, bt_path(pid)) for pid in (0x2017, 0x2019, 0x2069, 0x0306)], pads)
        self.assertEqual(res, [])
        self.assertTrue(all(p.opened == 0 and not p.writes for p in pads.values()))

    def test_two_joycons_are_two_icons(self):
        left, right = bt_path(0x2006, 1), bt_path(0x2007, 2)
        res = self.poll([entry(0x2006, left, "aa"), entry(0x2007, right, "bb")],
                        {left: FakePad("simple", 0x80), right: FakePad("simple", 0x20)})
        self.assertEqual(sorted((r.name, r.level) for r in res),
                         [("Nintendo Joy-Con (L)", 100), ("Nintendo Joy-Con (R)", 25)])

    def test_packet_counter_counts_up_and_wraps(self):
        pad = FakePad("simple")
        for _ in range(17):
            self.poll([entry(0x2009, BT_PATH)], {BT_PATH: pad})
        self.assertEqual([w[1] for w in pad.writes], list(range(16)) + [0])


class PathTest(unittest.TestCase):
    def test_bluetooth_and_usb_paths(self):
        self.assertTrue(N.is_bluetooth(BT_PATH))
        self.assertTrue(N.is_bluetooth(BT_PATH.decode()))
        self.assertFalse(N.is_bluetooth(USB_PATH))


if __name__ == "__main__":
    unittest.main()
