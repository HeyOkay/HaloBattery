"""Plantronics BT600 (047f:02f7 / 047f:02ee): the Poly get/set tunnel. No hardware.

The frames follow HeadsetControl's plantronics_bt600.hpp: the attach sequence
(06 01, HELLO to the adapter, HELLO to the headset) and the battery GET (0A/1A).
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import plantronics as P  # noqa: E402


def reply_frame(op, grp, item, addr, value=()):
    f = [0x07, 0x01, 0x01, 0x10, 0x0B, addr, 0x00, 0x00, op, grp, item] + list(value)
    return f + [0x00] * (64 - len(f))


def battery_frame(level=7, count=11, charging=0, minutes=200):
    return reply_frame(0x03, 0x0A, 0x1A, 0x02,
                       [level, count, charging, (minutes >> 8) & 0xFF, minutes & 0xFF, 0x01])


class FakeAdapter:
    def __init__(self, battery=None, acks=True, catalogue=True):
        self.battery = battery or {}
        self.acks = acks
        self.catalogue = catalogue
        self.queue = []
        self.writes = []

    def write(self, data):
        data = list(data)
        self.writes.append(data)
        if data[:2] == [0x06, 0x01]:
            return 2
        op, addr, grp, item = data[8], data[5], data[9], data[10]
        if op == 0x01:                                  # HELLO
            if not self.acks:
                return len(data)
            if self.catalogue:                          # the dongle's setting catalogue
                self.queue.append([0x07, 0x02, 0x00, 0x01, 0x10, 0x00] + [0] * 58)
            self.queue.append(reply_frame(0x08, 0x01, 0x02, 0x00 if addr == 0x00 else 0x02))
        elif op == 0x02:                                # GET
            self.queue.append(battery_frame(**self.battery))
        return len(data)

    def read(self, size, timeout_ms):
        return self.queue.pop(0) if self.queue else []


class FakeBus:
    def __init__(self, adapters):
        self.adapters = adapters

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.adapters:
                    raise OSError("cannot open")
                self.adapter = bus.adapters[path]

            def write(self, data):
                return self.adapter.write(data)

            def read(self, size, timeout_ms):
                return self.adapter.read(size, timeout_ms)

            def close(self):
                pass

        return FakeDevice


def entry(pid, path, iface, page, usage):
    return {"product_id": pid, "interface_number": iface, "usage_page": page,
            "usage": usage, "path": path, "product_string": "Plantronics BT600"}


class Bt600Test(unittest.TestCase):
    def setUp(self):
        self._saved = (P.hid, P.hidlist)

    def tearDown(self):
        P.hid, P.hidlist = self._saved

    def poll(self, adapters, entries=None):
        if entries is None:
            # the reporter's layout: the vendor collections are on interface 3
            entries = [entry(0x02F7, b"consumer", 3, 0x000C, 0x0001),
                       entry(0x02F7, b"basic", 3, 0x000B, 0x0005),
                       entry(0x02F7, b"ffa2", 3, 0xFFA2, 0x0003),
                       entry(0x02F7, b"ffa0", 3, 0xFFA0, 0x0003),
                       entry(0x02F7, b"ff99", 3, 0xFF99, 0x0001)]
        P.hid = types.SimpleNamespace(device=FakeBus(adapters).device_class())
        P.hidlist = types.SimpleNamespace(enumerate=lambda vid: list(entries))
        return P.PlantronicsProvider().poll()

    def test_the_level_is_poly_s_own_scale(self):
        # level 7 of 10 steps (count 11 includes 0) is Poly's 70 %
        [st] = self.poll({b"ffa2": FakeAdapter(battery={"level": 7, "count": 11})})
        self.assertEqual((st.name, st.level, st.charging, st.kind),
                         ("Plantronics headset", 70, False, "headset"))

    def test_a_charging_flag(self):
        [st] = self.poll({b"ffa2": FakeAdapter(battery={"level": 6, "count": 11, "charging": 1})})
        self.assertEqual((st.level, st.charging), (60, True))

    def test_the_attach_sequence_and_the_battery_get(self):
        adapter = FakeAdapter(battery={"level": 7, "count": 11})
        self.poll({b"ffa2": adapter})
        nudge, hello_dongle, hello_headset, get = adapter.writes[:4]
        self.assertEqual(nudge, [0x06, 0x01])
        self.assertEqual(len(hello_dongle), 64)
        self.assertEqual(hello_dongle[:6], [0x07, 0x01, 0x01, 0x10, 0x07, 0x00])
        self.assertEqual(hello_dongle[8:11], [0x01, 0x01, 0x02])
        self.assertEqual(hello_headset[5], 0x20)
        self.assertEqual(get[:6], [0x07, 0x01, 0x01, 0x10, 0x06, 0x20])
        self.assertEqual(get[8:11], [0x02, 0x0A, 0x1A])

    def test_the_catalogue_frames_are_skipped(self):
        [st] = self.poll({b"ffa2": FakeAdapter(battery={"level": 4, "count": 11})})
        self.assertEqual(st.level, 40)

    def test_a_headset_that_is_not_linked_shows_nothing(self):
        provider = P.PlantronicsProvider()
        P.hid = types.SimpleNamespace(device=FakeBus({b"ffa2": FakeAdapter(acks=False)}).device_class())
        P.hidlist = types.SimpleNamespace(enumerate=lambda vid: [entry(0x02F7, b"ffa2", 3, 0xFFA2, 3),
                                                                 entry(0x02F7, b"ffa0", 3, 0xFFA0, 3)])
        self.assertEqual(provider.poll(), [])
        self.assertTrue(any("not linked" in line for line in provider.diagnostics()))

    def test_only_the_ffa2_collection_is_asked(self):
        decoy, real = FakeAdapter(), FakeAdapter(battery={"level": 7, "count": 11})
        self.poll({b"ffa0": decoy, b"ffa2": real})
        self.assertEqual(decoy.writes, [])
        self.assertTrue(real.writes)

    def test_parse_battery_bounds(self):
        self.assertEqual(P.parse_battery(battery_frame(7, 11, 0)), (70, False))
        self.assertIsNone(P.parse_battery(battery_frame(7, 1, 0)))       # no denominator
        self.assertIsNone(P.parse_battery(battery_frame(11, 11, 0)))     # level >= count
        self.assertIsNone(P.parse_battery(battery_frame(7, 11, 0)[:10]))
        bad = battery_frame(7, 11, 0)
        bad[8] = 0x0A                                                    # not a GET reply
        self.assertIsNone(P.parse_battery(bad))


if __name__ == "__main__":
    unittest.main()
