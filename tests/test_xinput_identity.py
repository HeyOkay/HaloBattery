"""XInput slot lifetimes and WGI identity, with literal SDK values and no hardware."""
import unittest
from types import SimpleNamespace
from unittest import mock

from providers import xinput, wgi


class ControllerDll:
    def __init__(self, slots):
        self.slots = dict(slots)

    def XInputGetState(self, slot, _target):
        return 0 if slot in self.slots else 1167

    def XInputGetBatteryInformation(self, slot, _kind, target):
        target._obj.BatteryType, target._obj.BatteryLevel = self.slots[slot]
        return 0


class ControllerIdentityTests(unittest.TestCase):
    def setUp(self):
        # Literal values from XINPUT_BATTERY_INFORMATION: NiMH=3, wired=1,
        # disconnected=0; empty/low/medium/full=0/1/2/3.
        self.dll = ControllerDll({0: (3, 1), 1: (3, 2)})
        self.reports = []
        self.queries = 0
        self.hid_devices = []
        def query(_diag):
            self.queries += 1
            return [wgi.WgiController(dict(row)) for row in self.reports]
        self.patchers = [mock.patch.object(xinput, "load_xinput", return_value=self.dll),
            mock.patch.object(xinput.wgi, "query", side_effect=query),
            mock.patch.object(xinput, "hidlist", SimpleNamespace(
                enumerate=lambda vid: [d for d in self.hid_devices if d["vendor_id"] == vid],
                interface_paths=lambda: frozenset()))]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def report(self, name, remaining, status="Discharging"):
        return {"name": name, "vid": 0x045e, "pid": 0x02ff, "remain": remaining,
                "full": 1000, "rate": None, "status": status}

    def test_reordering_two_wgi_controllers_cannot_swap_slot_readings(self):
        self.reports = [self.report("Controller A", 100), self.report("Controller B", 900)]
        forward = xinput.XInputProvider().poll()
        self.reports.reverse()
        reverse = xinput.XInputProvider().poll()
        for readings in (forward, reverse):
            self.assertEqual([(s.key, s.level, s.charging) for s in readings],
                             [("xinput:0", 20, False), ("xinput:1", 55, False)])
            self.assertTrue(all("about" in s.approx for s in readings))
            self.assertTrue(all(s.charging_known for s in readings))
        self.assertEqual([s.name for s in forward], [s.name for s in reverse])

    def test_one_filtered_report_is_not_assigned_to_one_of_two_slots(self):
        self.reports = [self.report("Controller A", 990)]
        readings = xinput.XInputProvider().poll()
        self.assertEqual([(s.key, s.level) for s in readings], [("xinput:0", 20), ("xinput:1", 55)])

    def test_global_hid_vendor_name_is_not_reused_for_all_slots(self):
        self.hid_devices = [{"vendor_id": 0x3537, "product_id": 0x105e,
                             "product_string": "GameSir G7 PRO"}]
        readings = xinput.XInputProvider().poll()
        self.assertEqual([s.name for s in readings], ["Gamepad 1", "Gamepad 2"])

    def test_ambiguous_wgi_charging_statuses_are_not_paired_by_order(self):
        self.dll.slots = {0: (1, 0), 1: (1, 0)}
        self.reports = [self.report("A", 1000, "Charging"), self.report("B", 1000)]
        forward = xinput.XInputProvider().poll()
        self.reports.reverse()
        reverse = xinput.XInputProvider().poll()
        self.reports = []
        native = xinput.XInputProvider().poll()
        summarize = lambda values: [(s.key, s.level, s.charging, s.approx) for s in values]
        self.assertEqual(summarize(forward), summarize(reverse))
        self.assertEqual(summarize(forward), summarize(native))
        self.assertTrue(all(value.level is None and not value.charging and not value.charging_known
                            for value in forward + reverse + native))

    def test_ambiguous_wired_slot_does_not_blind_another_slots_real_coarse_level(self):
        self.dll.slots = {0: (3, 2), 1: (1, 0)}
        self.reports = [self.report("A", 900), self.report("B", 1000, "Charging")]
        for reports in (list(self.reports), list(reversed(self.reports))):
            self.reports = reports
            values = xinput.XInputProvider().poll()
            self.assertEqual([(value.key, value.level, value.charging, value.charging_known) for value in values],
                             [("xinput:0", 55, False, True), ("xinput:1", None, False, False)])

    def test_disconnect_poll_drops_previous_controllers_wireless_level(self):
        self.dll.slots = {0: (3, 1)}
        provider = xinput.XInputProvider()
        self.assertEqual(provider.poll()[0].level, 20)
        self.dll.slots = {}
        self.assertEqual(provider.poll(), [])
        self.assertNotIn(0, provider._last)
        self.dll.slots = {0: (1, 0)}
        replacement = provider.poll()[0]
        self.assertEqual((replacement.level, replacement.charging), (100, True))

    def test_signature_disconnect_invalidates_old_wgi_before_slot_reuse(self):
        self.dll.slots = {0: (3, 1)}
        self.reports = [self.report("Old controller", 100)]
        provider = xinput.XInputProvider()
        self.assertEqual(provider.poll()[0].level, 10)
        self.assertEqual(self.queries, 1)
        self.dll.slots = {}
        self.assertEqual(provider.connected_slots(), frozenset())
        self.assertIsNone(provider._wgi_res)
        self.assertNotIn(0, provider._last)
        self.dll.slots = {0: (1, 0)}
        self.reports = []
        self.assertEqual(provider.connected_slots(), frozenset({0}))
        replacement = provider.poll()[0]
        self.assertEqual(self.queries, 2)
        self.assertEqual((replacement.level, replacement.charging), (100, True))
        self.assertNotEqual(replacement.name, "Old controller")

    def test_single_gamesir_full_report_is_not_blanket_rejected(self):
        self.dll.slots = {0: (0, 0)}
        self.reports = [{"name": "GameSir G7 PRO", "vid": 0x3537, "pid": 0x105e,
                         "remain": 1000, "full": 1000, "rate": None, "status": "Discharging"}]
        reading = xinput.XInputProvider().poll()[0]
        self.assertEqual((reading.name, reading.level, reading.charging, reading.approx),
                         ("GameSir G7 PRO", 100, False, ""))


if __name__ == "__main__":
    unittest.main()
