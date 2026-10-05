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
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from providers import wgi  # noqa: E402
from providers import xinput as X  # noqa: E402



def load_app():
    """halo_battery.pyw, with its log and settings in a temporary folder."""
    real = os.environ.get("APPDATA")
    os.environ["APPDATA"] = tempfile.mkdtemp(prefix="halo_battery_test_")
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


class XboxBluetoothTest(ProviderTest):
    def test_issue_108_no_ten_percent(self):
        p, res = self.poll({0: (X.TYPE_DISCONNECTED, 0)}, [wgi_report(0x045E, 0x02FD, 100, 1000)],
                           [XBOX_BT_HID], [XBOX_BT_HID["path"].decode()])
        self.assertEqual(len(res), 1)
        st = res[0]
        self.assertIsNone(st.level)                 # not the 10% of remain=100 / full=1000
        self.assertEqual((st.via, st.name), ("bluetooth", "Xbox controller"))
        self.assertIn("Windows Bluetooth devices", st.approx)
        self.assertIn("[XInput] slot 0: connected over Bluetooth", p.diagnostics())

    def test_every_bluetooth_id(self):
        for pid in X.XBOX_BLUETOOTH_PIDS:
            _, res = self.poll({0: (X.TYPE_NIMH, 3)}, [wgi_report(0x045E, pid, 820, 1000)])
            self.assertEqual([(r.level, r.via) for r in res], [(None, "bluetooth")], hex(pid))

    def test_usb_and_adapter_ids_keep_their_level(self):
        # Xbox One S / Series X|S / Elite 2 on USB or the Xbox Wireless Adapter
        for pid in (0x02EA, 0x0B12, 0x0B00, 0x02FF):
            _, res = self.poll({0: (X.TYPE_NIMH, 3)}, [wgi_report(0x045E, pid, 820, 1000)])
            self.assertEqual([(r.level, r.via) for r in res], [(82, "")], hex(pid))

    def test_another_vendor_with_a_bluetooth_id_number_keeps_its_level(self):
        # 0x02FD is only an Xbox Bluetooth id for Microsoft's vendor id
        GAMESIR = 0x3537
        _, res = self.poll({0: (X.TYPE_NIMH, 3)}, [wgi_report(GAMESIR, 0x02FD, 640, 1000)],
                           [{"vendor_id": GAMESIR, "product_id": 0x1010, "product_string": "GameSir",
                             "path": b"usb-gamesir", "usage_page": 1, "usage": 5}])
        self.assertEqual([(r.name, r.level, r.via) for r in res], [("GameSir controller", 64, "")])

    def test_bluetooth_entry_is_dropped_when_the_bluetooth_reading_is_on(self):
        hb = load_app()
        _, res = self.poll({0: (X.TYPE_DISCONNECTED, 0)}, [wgi_report(0x045E, 0x02FD, 100, 1000)])
        self.assertEqual(hb.dedupe_controllers(res, []), res)          # Bluetooth reading off: kept
        bt = [hb.DeviceStatus("bt:x", "Xbox Wireless Controller", 82, False, True, "bluetooth",
                              kind="gamepad")]
        self.assertEqual(hb.dedupe_controllers(res, bt), [])          # on: Windows' value only


if __name__ == "__main__":
    unittest.main()
