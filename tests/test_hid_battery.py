"""The standard-HID fallback provider and its duplicate rule."""
import unittest
from types import SimpleNamespace
from unittest import mock

import providers.hid_battery as mod
from providers.base import DeviceStatus
from test_hide_rename import hb


class FakeHidp:
    LEVEL_USAGES = mod.hidp.LEVEL_USAGES

    def __init__(self, readings, available=True):
        self.readings = readings
        self.reads = []
        self._available = available

    def available(self):
        return self._available

    def read(self, path, vendor_id=0):
        self.reads.append((path, vendor_id))
        return self.readings.get(path)


class FakeList:
    def __init__(self, devices):
        self.devices = devices

    def enumerate(self, vid=0):  # noqa: A002 - mirrors hidlist.enumerate
        return list(self.devices)


def device(path=b"p1", page=0x06, usage=0x20, vid=0x05AC, pid=0x0265,
           name="Magic Mouse"):
    return {"path": path, "usage_page": page, "usage": usage,
            "vendor_id": vid, "product_id": pid, "product_string": name}


def reading(level=87, charging=True, known=True):
    return SimpleNamespace(level=level, charging=charging, charging_known=known)


class ProviderTests(unittest.TestCase):
    def poll(self, devices, readings, available=True):
        fake = FakeHidp(readings, available)
        with mock.patch.object(mod, "hidp", fake), \
                mock.patch.object(mod, "hidlist", FakeList(devices)):
            found = mod.HidBatteryProvider().poll()
        return found, fake

    def test_a_standard_battery_collection_is_read(self):
        found, fake = self.poll([device()], {b"p1": reading()})
        self.assertEqual(len(found), 1)
        st = found[0]
        self.assertEqual(st.key, "hid:05ac:0265")
        self.assertEqual(st.name, "Magic Mouse")
        self.assertEqual((st.level, st.charging, st.charging_known),
                         (87, True, True))
        self.assertEqual(st.source, "hid_battery")
        self.assertTrue(st.online)
        self.assertEqual(fake.reads, [(b"p1", 0x05AC)])

    def test_a_non_battery_collection_is_never_opened(self):
        # a keyboard's vendor collection, a digitizer and a UPS all stay untouched
        devices = [device(page=0xFF42, usage=0x01),
                   device(page=0x0D, usage=0x02),
                   device(page=0x84, usage=0x04)]
        found, fake = self.poll(devices, {})
        self.assertEqual(found, [])
        self.assertEqual(fake.reads, [])

    def test_one_icon_per_physical_device(self):
        devices = [device(page=0x06, usage=0x20), device(page=0x85, usage=0x65)]
        found, _ = self.poll(devices, {b"p1": reading()})
        self.assertEqual([st.key for st in found], ["hid:05ac:0265"])

    def test_a_device_that_does_not_answer_is_skipped(self):
        found, _ = self.poll([device()], {})
        self.assertEqual(found, [])

    def test_nothing_is_read_off_windows_or_without_hid_dll(self):
        found, fake = self.poll([device()], {b"p1": reading()}, available=False)
        self.assertEqual(found, [])
        self.assertEqual(fake.reads, [])

    def test_a_missing_product_string_gets_a_readable_name(self):
        found, _ = self.poll([device(name="")], {b"p1": reading()})
        self.assertEqual(found[0].name, "Wireless device")

    def test_the_fallback_runs_last(self):
        providers = hb.make_providers()
        self.assertEqual(providers[-1].name, "hid_battery")


class DuplicateTests(unittest.TestCase):
    def status(self, source, name, key):
        return DeviceStatus(key, name, 80, False, True, source)

    def test_a_vendor_reading_wins_over_the_fallback(self):
        results = [self.status("audeze", "Audeze Maxwell", "audeze:maxwell"),
                   self.status("hid_battery", "Audeze Maxwell", "hid:3329:0001")]
        kept = hb.drop_hid_battery_duplicates(results, set())
        self.assertEqual([st.source for st in kept], ["audeze"])

    def test_the_fallback_stays_when_nothing_else_reads_the_device(self):
        results = [self.status("hid_battery", "Magic Mouse", "hid:05ac:0265")]
        kept = hb.drop_hid_battery_duplicates(results, set())
        self.assertEqual(len(kept), 1)

    def test_the_drop_is_logged_once(self):
        logged = set()
        results = [self.status("razer", "Razer DeathAdder V4 Pro", "razer:00be"),
                   self.status("hid_battery", "Razer DeathAdder V4 Pro", "hid:1532:00be")]
        with mock.patch.object(hb.log, "info") as info:
            hb.drop_hid_battery_duplicates(results, logged)
            hb.drop_hid_battery_duplicates(results, logged)
        self.assertEqual(info.call_count, 1)
        self.assertEqual(logged, {"hid:1532:00be"})


if __name__ == "__main__":
    unittest.main()
