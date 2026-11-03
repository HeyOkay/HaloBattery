"""Tests for providers/xinput.py: XInput says "wired", Windows.Gaming.Input says Discharging.

No hardware is needed: the XInput DLL, Windows.Gaming.Input and the HID device list are
replaced by fakes. The case from issue #110: an 8BitDo Ultimate controller on its dock's
2.4 GHz dongle, off the dock and off the cable. The diagnostics said

    [XInput] slot 0: rc=0 type=wired level=3
    [WGI] 'Xbox 360 Controller for Windows' 2dc8:3106 status=Discharging remain=1000 full=1000

and the tray said "Gamepad: on cable, charging".

Run from the repository root:

    python -m unittest discover -s tests
"""
import importlib.util
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers
import _sandbox  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from providers import flydigi  # noqa: E402
from providers import wgi  # noqa: E402
from providers import xinput as X  # noqa: E402


def load_app():
    """halo_battery.pyw, with its log and settings in a temporary folder."""
    real = os.environ.get("APPDATA")
    os.environ["APPDATA"] = _sandbox.mkdtemp(prefix="halo_battery_test_")
    try:
        spec = importlib.util.spec_from_file_location("halo_battery_xinput_wired",
                                                      os.path.join(ROOT, "halo_battery.pyw"))
        hb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hb)
        return hb
    finally:
        if real is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = real


class FakeDll:
    """slots: {slot: (battery type, battery level)}; other slots are empty."""

    def __init__(self, slots):
        self.slots = slots

    def XInputGetState(self, slot, state_ref):
        return X.ERROR_SUCCESS if slot in self.slots else 1167      # ERROR_DEVICE_NOT_CONNECTED

    def XInputGetBatteryInformation(self, slot, devtype, info_ref):
        info = info_ref._obj
        info.BatteryType, info.BatteryLevel = self.slots[slot]
        return X.ERROR_SUCCESS


def wgi_report(vid, pid, remain, full, status, name="Xbox 360 Controller for Windows"):
    return wgi.WgiController({"vid": vid, "pid": pid, "status": status, "remain": remain,
                              "full": full, "rate": None, "name": name})


# a wired Xbox controller on USB, for the "genuine cable" cases
XBOX_USB_HID = {"vendor_id": 0x045E, "product_id": 0x02EA, "interface_number": 0,
                "usage_page": 0x0001, "usage": 0x0005, "product_string": "Controller (Xbox One For Windows)",
                "path": b"\\\\?\\HID#VID_045E&PID_02EA&IG_00#7&1a2b3c4d&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (X.load_xinput, X.hidlist, X.wgi.query, X.flydigi)

    def tearDown(self):
        X.load_xinput, X.hidlist, X.wgi.query, X.flydigi = self._saved

    def poll(self, slots, reports, hid_devices=(), paths=(), flydigi=None):
        X.load_xinput = lambda: FakeDll(slots)
        X.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [d for d in hid_devices if d["vendor_id"] == vid],
            interface_paths=lambda: frozenset(paths))
        X.wgi.query = lambda diag: None if reports is None else list(reports)
        X.flydigi = types.SimpleNamespace(read_connected=lambda diag: flydigi)
        p = X.XInputProvider()
        return p, p.poll()


class WiredButDischargingTest(ProviderTest):


    def test_a_real_wgi_level_is_still_shown(self):
        """Only the constant 1000/1000 pair is a placeholder; a real value stays."""
        _p, res = self.poll({0: (X.TYPE_WIRED, 3)},
                            [wgi_report(0x045E, 0x028E, 800, 1000, "Discharging")])
        self.assertEqual((res[0].level, res[0].charging), (80, False))


# the Vader 4 Pro as #191 saw it: the pad on Microsoft's id with Flydigi's product
# string, so its Windows.Gaming.Input report was trusted and showed 100%
VADER4_HID = {"vendor_id": 0x045E, "product_id": 0x028E, "interface_number": 0,
              "usage_page": 0x0001, "usage": 0x0005,
              "product_string": "Controller (Flydigi VADER4)",
              "path": b"\\\\?\\HID#VID_045E&PID_028E&IG_00#8&2b3c4d5e&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"}


class VaderTest(ProviderTest):
    def test_the_vader5_own_reading_wins(self):
        """#191: the pad's vendor channel has the real level; the wired lie is ignored."""
        r = flydigi.Reading(60, False, "", "FlyDigi Vader 5 Pro")
        _p, res = self.poll({0: (X.TYPE_WIRED, 3)},
                            [wgi_report(0x37D7, 0x2401, 1000, 1000, "Discharging")],
                            flydigi=r)
        st = res[0]
        self.assertEqual((st.level, st.charging, st.name),
                         (60, False, "FlyDigi Vader 5 Pro"))
        self.assertIn("60%", load_app().describe(st))

    def test_the_vader5_charging_state_comes_from_the_pad(self):
        r = flydigi.Reading(40, True, "charging", "FlyDigi Vader 5 Pro")
        _p, res = self.poll({0: (X.TYPE_WIRED, 3)},
                            [wgi_report(0x37D7, 0x2401, 1000, 1000, "Discharging")],
                            flydigi=r)
        self.assertEqual((res[0].level, res[0].charging, res[0].approx),
                         (40, True, "charging"))

    def test_the_vader5_without_an_answer_shows_no_level(self):
        """The vendor channel silent: the honest text stays, nothing invented."""
        _p, res = self.poll({0: (X.TYPE_WIRED, 3)},
                            [wgi_report(0x37D7, 0x2401, 1000, 1000, "Discharging")])
        st = res[0]
        self.assertIsNone(st.level)
        self.assertFalse(st.charging)
        self.assertEqual(st.approx, "connected, battery level not reported")

    def test_the_vader4_placeholder_is_not_shown_as_100(self):
        """#191: the report said 100% while the controller's battery light flashed low."""
        p, res = self.poll({0: (X.TYPE_WIRED, 3)},
                           [wgi_report(0x045E, 0x028E, 1000, 1000, "Discharging")],
                           hid_devices=[VADER4_HID])
        st = res[0]
        self.assertIsNone(st.level)
        self.assertFalse(st.charging)
        text = load_app().describe(st)
        self.assertNotIn("100%", text)
        self.assertNotIn("charging", text)
        self.assertTrue(any("discharging" in line.lower() for line in p.diagnostics()))


class PlaceholderTest(unittest.TestCase):
    def test_the_constant_1000_pair_is_not_a_measurement(self):
        self.assertTrue(wgi_report(0x045E, 0x028E, 1000, 1000, "Discharging").placeholder)
        self.assertFalse(wgi_report(0x045E, 0x028E, 800, 1000, "Discharging").placeholder)
        # only the exact pair: an equal-but-other value is a real (if odd) measurement
        self.assertFalse(wgi_report(0x045E, 0x028E, 500, 500, "Discharging").placeholder)
        with_rate = wgi.WgiController({"vid": 0x045E, "pid": 0x028E, "status": "Charging",
                                       "remain": 1000, "full": 1000, "rate": 250,
                                       "name": "x"})
        self.assertFalse(with_rate.placeholder)


if __name__ == "__main__":
    unittest.main()
