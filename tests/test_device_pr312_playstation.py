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


    # ---- a controller that never yields a battery reading (another app holds it)
    def poll_unreadable(self, entries=None):
        entries = [entry(BT_PATH)] if entries is None else entries
        return self.poll(entries, {})               # open fails on every poll

    def test_unreadable_controller_stops_pending_after_the_window_for_good(self):
        out = self.poll_unreadable()
        self.assertTrue(self.provider.pending)
        self.assertIn("not reported yet", out[0].approx)
        self.clock.now += P.PENDING_WINDOW - 30
        self.poll_unreadable()
        self.assertTrue(self.provider.pending)
        self.clock.now += 60                        # window over
        for _ in range(5):
            out = self.poll_unreadable()
            self.assertFalse(self.provider.pending)
            self.assertIn("not readable", out[0].approx)
            self.clock.now += 3

    def test_give_up_message_is_reported_once(self):
        self.poll_unreadable()
        self.clock.now += P.PENDING_WINDOW + 1
        lines = []
        for _ in range(4):
            self.poll_unreadable()
            lines += [d for d in self.provider._diag if "no battery report" in d]
            self.clock.now += 3
        self.assertEqual(len(lines), 1)

    def test_unplugged_controller_gets_a_fresh_window_when_it_returns(self):
        self.poll_unreadable()
        self.clock.now += P.PENDING_WINDOW + 1
        self.poll_unreadable()
        self.assertFalse(self.provider.pending)
        self.poll_unreadable([])                    # unplugged
        self.clock.now += P.PENDING_WINDOW + 1
        self.poll_unreadable()                      # plugged in again
        self.assertTrue(self.provider.pending)

    # ---- two controllers of the same model on USB
    # Over USB hidapi reports no serial number for these controllers, so the serial
    # cannot tell two of them apart; the device instance in the HID path can.


if __name__ == "__main__":
    unittest.main()
