"""Tests for providers/xinput.py: Xbox controllers over Bluetooth. No hardware is needed.

The XInput DLL, Windows.Gaming.Input and the HID device list are replaced by fakes.
The case from issue #108: an Xbox One S controller over Bluetooth (045e:02fd), no
Bluetooth service guid in any device path, XInput "disconnected" battery type, and
Windows.Gaming.Input's report remain=100 of full=1000, which is not the level.

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

from providers import wgi  # noqa: E402
from providers import xinput as X  # noqa: E402
from providers.flydigi import Reading as FlyDigiReading  # noqa: E402


def load_app():
    """halo_battery.pyw, with its log and settings in a temporary folder."""
    real = os.environ.get("APPDATA")
    os.environ["APPDATA"] = _sandbox.mkdtemp(prefix="halo_battery_test_")
    try:
        spec = importlib.util.spec_from_file_location("halo_battery_xinput",
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


def wgi_report(vid, pid, remain, full, status="Discharging"):
    return wgi.WgiController({"vid": vid, "pid": pid, "status": status, "remain": remain,
                              "full": full, "name": "Dispositivo de juego  compatible con HID"})


# the Xbox entry of the issue #108 diagnostics; the path is a USB-style path on purpose:
# the real one did not carry the Bluetooth service guid (or the provider would have caught it)
XBOX_BT_HID = {"vendor_id": 0x045E, "product_id": 0x02FD, "interface_number": -1,
               "usage_page": 0x0001, "usage": 0x0005, "product_string": "Xbox Bluetooth Gamepad",
               "path": b"\\\\?\\HID#VID_045E&PID_02FD&IG_00#a&1b2c3d4&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (X.load_xinput, X.hidlist, X.wgi.query, X.flydigi)

    def tearDown(self):
        X.load_xinput, X.hidlist, X.wgi.query, X.flydigi = self._saved

    def poll(self, slots, reports, hid_devices=(), paths=()):
        X.load_xinput = lambda: FakeDll(slots)
        X.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [d for d in hid_devices if d["vendor_id"] == vid],
            interface_paths=lambda: frozenset(paths))
        X.wgi.query = lambda diag: list(reports)
        X.flydigi = types.SimpleNamespace(read_connected=lambda diag: None)
        p = X.XInputProvider()
        return p, p.poll()


class FlyDigiSlotTest(ProviderTest):
    """#216 review: the Vader's reading belongs to the Vader's slot, and the
    placeholder drop is FlyDigi-only."""

    def poll_flydigi(self, slots, reports, reading):
        calls = []

        def read_connected(diag):
            calls.append(1)
            return reading

        X.load_xinput = lambda: FakeDll(slots)
        X.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [],
                                          interface_paths=lambda: frozenset())
        X.wgi.query = lambda diag: list(reports)
        X.flydigi = types.SimpleNamespace(VID=0x37D7, read_connected=read_connected)
        p = X.XInputProvider()
        return p, p.poll(), calls

    def test_the_vader_reading_belongs_to_the_vaders_own_slot(self):
        reading = FlyDigiReading(100, False, "", "FlyDigi Vader 5 Pro")
        p, res, calls = self.poll_flydigi({0: (X.TYPE_WIRED, 0)},
                                          [wgi_report(0x37D7, 0x2401, 1000, 1000)],
                                          reading)
        self.assertEqual([(s.name, s.level, s.charging) for s in res],
                         [("FlyDigi Vader 5 Pro", 100, False)])
        self.assertEqual(len(calls), 1)          # the pad was asked

    def test_an_xbox_pad_is_not_given_the_vaders_reading(self):
        # The reviewer's scenario: the Vader is off / in DInput mode, so its dongle
        # still enumerates while the one XInput slot is an Xbox pad.
        reading = FlyDigiReading(55, False, "", "FlyDigi Vader 5 Pro")
        p, res, calls = self.poll_flydigi({0: (X.TYPE_NIMH, 2)},
                                          [wgi_report(0x045E, 0x02EA, 500, 1000)],
                                          reading)
        self.assertEqual([(s.name, s.level) for s in res], [("Xbox controller", 50)])
        self.assertEqual(calls, [])              # the dongle is not even asked

    def test_a_full_pad_of_another_vendor_keeps_its_hundred_percent(self):
        # 1000/1000 from another vendor can be a genuinely full pack; only FlyDigi's is
        # known to be a placeholder. The coarse XInput level here would say "about 20%".
        p, res, calls = self.poll_flydigi({0: (X.TYPE_NIMH, 1)},
                                          [wgi_report(0x045E, 0x02EA, 1000, 1000)],
                                          None)
        self.assertEqual([(s.name, s.level, s.approx) for s in res],
                         [("Xbox controller", 100, "")])
        self.assertEqual(calls, [])

    def test_a_vader_placeholder_without_a_reading_does_not_show_hundred(self):
        p, res, calls = self.poll_flydigi({0: (X.TYPE_NIMH, 1)},
                                          [wgi_report(0x37D7, 0x2401, 1000, 1000)],
                                          None)
        self.assertEqual([s.level for s in res], [20])   # the coarse level, not a fake 100
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
