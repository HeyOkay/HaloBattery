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
        self._saved = (X.load_xinput, X.hidlist, X.wgi.query)

    def tearDown(self):
        X.load_xinput, X.hidlist, X.wgi.query = self._saved

    def poll(self, slots, reports, hid_devices=(), paths=()):
        X.load_xinput = lambda: FakeDll(slots)
        X.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [d for d in hid_devices if d["vendor_id"] == vid],
            interface_paths=lambda: frozenset(paths))
        X.wgi.query = lambda diag: list(reports)
        p = X.XInputProvider()
        return p, p.poll()


class NamingTest(ProviderTest):
    """#523 review: a name may only come from the slot's own report."""

    U2 = (0x2DC8, 0x310B)
    XBOX_HID = {"vendor_id": 0x045E, "product_id": 0x02FD,
                "product_string": "Controller (Xbox One For Windows)"}

    def test_the_pad_is_named_from_its_own_wgi_report(self):
        _p, res = self.poll({0: (X.TYPE_WIRED, 0)}, [wgi_report(*self.U2, 1000, 1000)])
        self.assertEqual([s.name for s in res], ["8BitDo Ultimate 2"])

    def test_an_idle_receiver_does_not_rename_the_pad_in_the_slot(self):
        # the Ultimate 2 receiver is enumerated, but the slot holds an Xbox pad with
        # no WGI data: the generic vendor name must stay, never 8BitDo's.
        receiver = {"vendor_id": 0x2DC8, "product_id": 0x310B,
                    "product_string": "8BitDo Ultimate 2 Wireless Controller for PC"}
        _p, res = self.poll({0: (X.TYPE_NIMH, 2)}, [],
                            hid_devices=[self.XBOX_HID, receiver])
        self.assertEqual([s.name for s in res], ["Xbox controller"])

    # slot 0: an Xbox pad on its adapter; slot 1: the Ultimate 2 on its dongle ("wired")
    TWO_SLOTS = {0: (X.TYPE_NIMH, 3), 1: (X.TYPE_WIRED, 0)}
    XBOX_REPORT = (0x045E, 0x0B12, 800, 1000)

    def test_reversed_wgi_order_does_not_name_the_xbox_pad(self):
        # #523 review: Windows.Gaming.Input lists the Ultimate 2 first although it is in
        # slot 1. Equal counts do not say which report is whose, so no slot is renamed.
        _p, res = self.poll(self.TWO_SLOTS,
                            [wgi_report(*self.U2, 1000, 1000), wgi_report(*self.XBOX_REPORT)],
                            hid_devices=[self.XBOX_HID])
        self.assertEqual([s.key for s in res], ["xinput:0", "xinput:1"])
        self.assertEqual([s.name for s in res if "8BitDo" in s.name], [])

    def test_two_active_slots_keep_the_generic_name_in_slot_order_too(self):
        _p, res = self.poll(self.TWO_SLOTS,
                            [wgi_report(*self.XBOX_REPORT), wgi_report(*self.U2, 1000, 1000)],
                            hid_devices=[self.XBOX_HID])
        self.assertEqual([s.key for s in res], ["xinput:0", "xinput:1"])
        self.assertEqual([s.name for s in res if "8BitDo" in s.name], [])

    def test_one_slot_with_a_second_report_keeps_the_generic_name(self):
        # one XInput slot, but Windows.Gaming.Input also lists an Ultimate 2 (an idle
        # receiver, or a pad XInput does not see): the report is not known to be the slot's
        _p, res = self.poll({0: (X.TYPE_NIMH, 3)},
                            [wgi_report(*self.U2, 1000, 1000), wgi_report(*self.XBOX_REPORT)],
                            hid_devices=[self.XBOX_HID])
        self.assertEqual([s.name for s in res], ["Xbox controller"])

    def test_no_wgi_data_keeps_the_generic_name(self):
        receiver = {"vendor_id": 0x2DC8, "product_id": 0x310B,
                    "product_string": "8BitDo Ultimate 2 Wireless Controller for PC"}
        _p, res = self.poll({0: (X.TYPE_NIMH, 2)}, [], hid_devices=[receiver])
        self.assertEqual([s.name for s in res], ["Gamepad"])

    def test_the_vendor_id_stays_out_of_the_trusted_wgi_set(self):
        self.assertNotIn(0x2DC8, X.NAMED_VIDS)


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
            # not Windows.Gaming.Input's 82%: XInput's coarse "full" level (#236)
            self.assertEqual([(r.level, r.via) for r in res], [(100, "bluetooth")], hex(pid))

    def test_issue_236_coarse_level_when_windows_has_no_bluetooth_reading(self):
        # Xbox One S (045e:02e0) on AA cells: Windows.Gaming.Input jumps between 10, 70 and
        # 100 %, the Bluetooth reading finds nothing, XInput says alkaline / medium
        hb = load_app()
        _, res = self.poll({0: (X.TYPE_ALKALINE, 2)}, [wgi_report(0x045E, 0x02E0, 100, 1000)])
        self.assertEqual([(r.level, r.via) for r in res], [(55, "bluetooth")])
        self.assertIn("over Bluetooth", res[0].approx)
        self.assertEqual(hb.dedupe_controllers(res, []), res)
        bt = [hb.DeviceStatus("bt:x", "Xbox Wireless Controller", 82, False, True, "bluetooth",
                              kind="gamepad")]
        self.assertEqual(hb.dedupe_controllers(res, bt), [])          # Windows' value wins

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
