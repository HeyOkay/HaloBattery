"""Tests for providers/playstation.py. No hardware is needed.

The main case is issue #96: over Bluetooth, reading the "full report" feature report
switches a DualSense / DualShock 4 into a mode that games using DirectInput cannot
read until the controller is turned off. The app must not send it over Bluetooth
unless the user turned on "PlayStation full mode". A fake clock replaces time, so
the read windows run without real waiting.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import playstation as P  # noqa: E402

# Windows paths: Bluetooth uses the HID service GUID and "VID&0002054C", USB "VID_054C".
BT_PATH = (b"\\\\?\\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&0002054c_pid&0ce6"
           b"#9&2b0f7c5a&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}")
USB_PATH = b"\\\\?\\hid#vid_054c&pid_0ce6&mi_03#8&1234abcd&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"
DS4_BT_PATH = BT_PATH.replace(b"pid&0ce6", b"pid&09cc")
# a second controller of the same model on another USB port: another device instance
USB_PATH_2 = USB_PATH.replace(b"8&1234abcd", b"8&5678ef01")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def basic_report():
    """The stripped-down Bluetooth report 0x01: sticks and buttons, no battery."""
    return [0x01, 0x80, 0x80, 0x80, 0x80, 0x08, 0x00, 0x00, 0x00, 0x00]


def full_report(dualsense=True, status=0x07):
    if dualsense:
        r = [0x31] + [0] * 77
        r[54] = status          # low nibble level 0..10, high nibble charging state
    else:
        r = [0x11] + [0] * 77
        r[32] = status          # low nibble level 0..10, bit 4 cable
    return r


class FakePad:
    """mode:
    "basic"  Bluetooth, nobody switched it: streams report 0x01 until the feature
             report is read, then streams the full report
    "full"   Bluetooth, Steam or a game already switched it: streams the full report
    """

    def __init__(self, mode="basic", dualsense=True, status=0x07):
        self.mode = mode
        self.dualsense = dualsense
        self.status = status
        self.features = []

    def on_feature(self, rid):
        self.features.append(rid)
        self.mode = "full"          # the switch stays on until the controller is turned off
        return [rid] + [0] * 40

    def on_read(self):
        if self.mode == "full":
            return full_report(self.dualsense, self.status)
        return basic_report()


def usb_pad(status):
    """A DualSense on USB: streams report 0x01 with the status byte at 53."""
    pad = FakePad("full")
    report = [0x01] + [0] * 77
    report[53] = status
    pad.on_read = lambda: report
    return pad


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
                return self.pad.on_feature(rid)

            def read(self, n):
                return self.pad.on_read()

            def close(self):
                pass

        return FakeDevice


def entry(path, pid=0x0CE6, mac="a0ab51123456"):
    return {"product_id": pid, "interface_number": -1, "usage_page": 0x0001, "usage": 0x0005,
            "path": path, "product_string": "Wireless Controller", "serial_number": mac}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (P.hid, P.hidlist, P.time)
        self.clock = Clock()
        P.time = types.SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep)
        self.provider = P.PlayStationProvider()

    def tearDown(self):
        P.hid, P.hidlist, P.time = self._saved

    def poll(self, entries, pads):
        bus = FakeBus(pads)
        P.hid = types.SimpleNamespace(device=bus.device_class())
        P.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()

    # ---- issue #96: never switch a Bluetooth controller by default
    def test_bluetooth_basic_mode_is_not_switched_by_default(self):
        pad = FakePad("basic")
        out = self.poll([entry(BT_PATH)], {BT_PATH: pad})
        self.assertEqual(pad.features, [])          # the feature report was not read
        self.assertEqual(pad.mode, "basic")         # the controller stays as the game left it
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].level)
        self.assertTrue(out[0].approx)
        self.assertIn("Steam", out[0].approx)
        self.assertEqual(out[0].ui_message, "state.playstation_basic")

    def test_bluetooth_basic_mode_does_not_start_the_fast_recheck(self):
        # the basic mode is the normal state now, not a controller that is still starting
        self.poll([entry(BT_PATH)], {BT_PATH: FakePad("basic")})
        self.assertFalse(self.provider.pending)

    def test_bluetooth_basic_mode_returns_at_once(self):
        start = self.clock.now
        self.poll([entry(BT_PATH)], {BT_PATH: FakePad("basic")})
        self.assertLess(self.clock.now - start, 0.1)

    def test_bluetooth_already_full_is_read_without_switching(self):
        pad = FakePad("full", status=0x07)          # 70 %, on battery
        out = self.poll([entry(BT_PATH)], {BT_PATH: pad})
        self.assertEqual(pad.features, [])
        self.assertEqual((out[0].level, out[0].charging), (70, False))
        self.assertFalse(out[0].approx)

    def test_bluetooth_dualshock4_is_not_switched_either(self):
        pad = FakePad("basic", dualsense=False)
        out = self.poll([entry(DS4_BT_PATH, pid=0x09CC)], {DS4_BT_PATH: pad})
        self.assertEqual(pad.features, [])
        self.assertIsNone(out[0].level)

    def test_full_mode_setting_switches_and_reads(self):
        self.provider.switch_bluetooth = True
        pad = FakePad("basic", status=0x15)         # 50 %, charging
        out = self.poll([entry(BT_PATH)], {BT_PATH: pad})
        self.assertEqual(pad.features, [0x05])
        self.assertEqual((out[0].level, out[0].charging), (50, True))

    def test_usb_still_reads_the_feature_report(self):
        # harmless over USB, and the USB path has always worked this way
        pad = FakePad("full")
        pad.dualsense = True
        usb_report = [0x01] + [0] * 77
        usb_report[53] = 0x08
        pad.on_read = lambda: usb_report
        out = self.poll([entry(USB_PATH, mac="")], {USB_PATH: pad})
        self.assertEqual(pad.features, [0x05])
        self.assertEqual(out[0].level, 80)

    def test_cable_reading_wins_over_basic_bluetooth(self):
        bt = FakePad("basic")
        usb = FakePad("full")
        usb_report = [0x01] + [0] * 77
        usb_report[53] = 0x16                       # 60 %, charging
        usb.on_read = lambda: usb_report
        out = self.poll([entry(BT_PATH), entry(USB_PATH, mac="")], {BT_PATH: bt, USB_PATH: usb})
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].level, out[0].charging), (60, True))
        self.assertEqual(bt.features, [])

    # ---- two controllers of the same model on USB
    # Over USB hidapi reports no serial number for these controllers, so the serial
    # cannot tell two of them apart; the device instance in the HID path can.
    def test_two_usb_controllers_get_two_icons(self):
        a, b = usb_pad(0x08), usb_pad(0x13)          # 80 % on battery, 30 % charging
        out = self.poll([entry(USB_PATH, mac=""), entry(USB_PATH_2, mac="")],
                        {USB_PATH: a, USB_PATH_2: b})
        self.assertEqual(len(out), 2)
        self.assertEqual(len({s.key for s in out}), 2)
        self.assertEqual(sorted((s.level, s.charging) for s in out), [(30, True), (80, False)])

    def test_two_usb_controller_keys_stay_the_same_between_polls(self):
        entries = [entry(USB_PATH, mac=""), entry(USB_PATH_2, mac="")]
        pads = {USB_PATH: usb_pad(0x08), USB_PATH_2: usb_pad(0x13)}
        first = {s.level: s.key for s in self.poll(entries, pads)}
        second = {s.level: s.key for s in self.poll(list(reversed(entries)), pads)}
        self.assertEqual(first, second)

    def test_one_usb_controller_keeps_its_old_key(self):
        # names and hidden settings are saved under the key
        out = self.poll([entry(USB_PATH, mac="")], {USB_PATH: usb_pad(0x08)})
        self.assertEqual([s.key for s in out], ["ps:0ce6:"])

    def test_collections_of_one_usb_controller_stay_one_icon(self):
        # one controller with two collections: same instance, the last part is the
        # collection number
        c1 = USB_PATH.replace(b"mi_03#", b"mi_03&col01#")
        c2 = USB_PATH.replace(b"mi_03#", b"mi_03&col02#").replace(b"&0&0000#", b"&0&0001#")
        out = self.poll([entry(c1, mac=""), entry(c2, mac="")],
                        {c1: usb_pad(0x08), c2: usb_pad(0x08)})
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].key, "ps:0ce6:")

    def test_bluetooth_and_two_usb_controllers_keep_every_controller(self):
        # one controller on Bluetooth and its cable, plus a second one on USB only
        out = self.poll([entry(BT_PATH), entry(USB_PATH, mac=""), entry(USB_PATH_2, mac="")],
                        {BT_PATH: FakePad("basic"), USB_PATH: usb_pad(0x16),
                         USB_PATH_2: usb_pad(0x04)})
        self.assertEqual(len(out), 2)
        self.assertIn("ps:0ce6:a0ab51123456", {s.key for s in out})

    def test_diagnostics_say_why(self):
        self.poll([entry(BT_PATH)], {BT_PATH: FakePad("basic")})
        text = "\n".join(self.provider.diagnostics())
        self.assertIn("basic Bluetooth mode", text)


if __name__ == "__main__":
    unittest.main()
