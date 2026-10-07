"""Tests for providers/logitech.py. No hardware is needed.

The tests replace the HID layer with fake devices. The fake devices answer HID++
requests as the protocol describes (Solaar, HeadsetControl). Then the tests run the
real provider code against them.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import logitech as L  # noqa: E402


# ------------------------------------------------------------------ fake devices
class FakeHidpp:
    """One HID++ 2.0 device in a slot.

    features: {feature id: (feature index, {function: reply params or "error"})}
    error:    an error code that the receiver sends for every request (08, 09, 01 ...)
    silent:   the device does not answer at all (asleep)
    """

    def __init__(self, features=None, name=None, error=None, silent=False):
        self.features = features or {}
        self.name = name
        self.error = error
        self.silent = silent


class FakeBus:
    """The fake HID paths. paths: {long path: ({slot: FakeHidpp}, short path or None)}."""

    def __init__(self, paths):
        self.slots = {p: s for p, (s, _) in paths.items()}
        self.short_of = {p: sh for p, (_, sh) in paths.items()}
        self.queues = {}
        self.writes = []            # (path, bytes) of every write
        # if set: before each "find feature" reply, a late reply to the PREVIOUS
        # request arrives first (it carries that request's swid)
        self.late_replies = False
        self.prev_swid = None

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.path = path
                bus.queues.setdefault(path, [])

            def set_nonblocking(self, flag):
                pass

            def close(self):
                pass

            def read(self, n, timeout=None):
                q = bus.queues.get(self.path, [])
                return q.pop(0) if q else []

            def write(self, data):
                bus.write(self.path, list(data))
                return len(data)

        return FakeDevice

    def _reply(self, path, idx, feat, fn, params):
        self.queues[path].append([0x11, idx, feat, fn] + list(params) + [0] * (16 - len(params)))

    def _error(self, path, idx, feat, fn, code):
        short = self.short_of.get(path)
        if short:       # a receiver sends error replies as short reports
            self.queues.setdefault(short, []).append([0x10, idx, 0x8F, feat, fn, code, 0])
        else:
            self.queues[path].append([0x11, idx, 0xFF, feat, fn, code] + [0] * 14)

    def write(self, path, data):
        self.writes.append((path, data))
        if path not in self.slots:
            return
        idx, feat, fn = data[1], data[2], data[3]
        prev, self.prev_swid = self.prev_swid, fn & 0x0F
        if self.late_replies and feat == 0 and fn >> 4 == 0 and prev is not None:
            self._reply(path, idx, 0, prev, [9])     # "feature index 9": wrong for this request
        dev = self.slots[path].get(idx)
        if dev is None:
            return self._error(path, idx, feat, fn, L.ERR_EMPTY_SLOT)
        if dev.silent:
            return
        if dev.error is not None:
            return self._error(path, idx, feat, fn, dev.error)
        func, params = fn >> 4, data[4:]
        if feat == 0:                                   # root feature
            if func == 1:
                return self._reply(path, idx, feat, fn, [4, 2, 0])
            fid = (params[0] << 8) | params[1]
            return self._reply(path, idx, feat, fn, [dev.features.get(fid, (0, {}))[0]])
        for fid, (index, fns) in dev.features.items():
            if index != feat:
                continue
            if fid == L.F_NAME and func in (0, 1) and dev.name is not None:
                raw = dev.name.encode()
                return self._reply(path, idx, feat, fn,
                                   [len(raw)] if func == 0 else list(raw[params[0]:params[0] + 16]))
            if func in fns:
                if fns[func] == "error":
                    return self._error(path, idx, feat, fn, 0x05)
                return self._reply(path, idx, feat, fn, fns[func])
        return self._error(path, idx, feat, fn, 0x02)


def entry(pid, page, usage, path, product=""):
    return {"product_id": pid, "usage_page": page, "usage": usage, "path": path,
            "product_string": product, "interface_number": 2}


def receiver(pid=0xC539, product="USB Receiver"):
    return [entry(pid, 0xFF00, 1, b"short", product), entry(pid, 0xFF00, 2, b"long", product)]


VOLTAGE_76 = {L.F_VOLTAGE: (6, {0: [0x0F, 0x7B, 0x00]})}           # 3963 mV -> 76 %


def g502():
    return FakeHidpp({**VOLTAGE_76, L.F_NAME: (3, {2: [3]}),
                      L.F_INFO: (2, {0: [3, 0xC1, 0x5E, 0x09, 0xCD]})}, name="G502 LIGHTSPEED")


def slots(keys):
    """(product id, device index) of each slot record, whatever else the record holds."""
    return {(k[0], k[-1]) for k in keys}


def adc(params, index=8):
    return {L.F_ADC: (index, {0: params})}


class LogitechTestCase(unittest.TestCase):
    """Swaps in the fake HID layer for the provider module only, then restores it."""

    def setUp(self):
        self._saved = (L.hid, L.hidlist, L.TIMEOUT, L.PING_TIMEOUT)
        L.TIMEOUT, L.PING_TIMEOUT = 0.05, 0.1

    def tearDown(self):
        L.hid, L.hidlist, L.TIMEOUT, L.PING_TIMEOUT = self._saved

    def use(self, entries, paths):
        self.bus = FakeBus(paths)
        L.hid = types.SimpleNamespace(device=self.bus.device_class())
        L.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.bus


# ------------------------------------------------------------------ receivers
class ReceiverTests(LogitechTestCase):
    def test_mouse_in_slot_1(self):
        self.use(receiver(), {b"long": ({1: g502()}, b"short")})
        p = L.LogitechProvider()
        [st] = p.poll()
        self.assertEqual((st.name, st.level, st.charging, st.kind, st.key),
                         ("G502 LIGHTSPEED", 76, False, "mouse", "logitech:C15E09CD"))
        self.assertEqual(slots(p._asleep), set(), "empty slots answer with error 08, they are not asleep")

    def test_receiver_known_by_product_id(self):
        self.use(receiver(0xC547, "LIGHTSPEED"), {b"long": ({1: g502()}, b"short")})
        self.assertEqual(len(L.LogitechProvider().poll()), 1)

    def test_switched_off_keeps_grey_icon(self):
        bus = self.use(receiver(), {b"long": ({1: g502()}, b"short")})
        p = L.LogitechProvider()
        p.poll()
        bus.slots[b"long"][1] = FakeHidpp(error=L.ERR_UNREACHABLE)
        [st] = p.poll()
        self.assertFalse(st.online)
        self.assertEqual(slots(p._asleep), set())
        self.assertTrue(any("switched off" in line for line in p.diagnostics()))

    def test_silent_device_is_asleep(self):
        self.use(receiver(), {b"long": ({1: FakeHidpp(silent=True)}, b"short")})
        p = L.LogitechProvider()
        p.poll()
        self.assertIn((0xC539, 1), slots(p._asleep))

    def test_empty_slot_forgets_old_name(self):
        bus = self.use(receiver(), {b"long": ({1: g502()}, b"short")})
        p = L.LogitechProvider()
        p.poll()
        self.assertIn((0xC539, 1), slots(p._ids))
        bus.slots[b"long"].clear()
        p.poll()
        self.assertNotIn((0xC539, 1), slots(p._ids))

    def test_hidpp10_device_is_noted(self):
        self.use(receiver(0xC52B), {b"long": ({1: FakeHidpp(error=L.ERR_OLD_PROTOCOL)}, b"short")})
        p = L.LogitechProvider()
        self.assertEqual(p.poll(), [])
        self.assertTrue(any("HID++ 1.0" in line for line in p.diagnostics()))

    def test_error_reply_for_another_app_is_ignored(self):
        """G HUB talks to the same receiver. Its error replies carry its own swid."""
        bus = self.use(receiver(), {b"long": ({1: g502()}, b"short")})
        bus.queues[b"short"] = [[0x10, 1, 0x8F, 0x00, 0x13, 0x09, 0]]
        bus.queues[b"long"] = [[0x11, 1, 0xFF, 0x00, 0x13, 0x09] + [0] * 14]
        [st] = L.LogitechProvider().poll()
        self.assertEqual(st.level, 76)


    def test_late_reply_to_an_earlier_request_is_ignored(self):
        """A reply that comes after its request timed out carries an older swid."""
        bus = self.use(receiver(), {b"long": ({1: g502()}, b"short")})
        bus.late_replies = True
        [st] = L.LogitechProvider().poll()
        self.assertEqual(st.level, 76)

    def test_failed_identity_read_is_not_cached(self):
        """Just after a wake the name request can time out. The next poll tries again."""
        dev = g502()
        bus = self.use(receiver(), {b"long": ({1: dev}, b"short")})
        saved = dev.features
        dev.features = VOLTAGE_76                    # no name / info feature this time
        p = L.LogitechProvider()
        [st] = p.poll()
        self.assertEqual(st.name, "Logitech device")
        self.assertNotIn((0xC539, 1), slots(p._ids))
        dev.features = saved
        [st] = p.poll()
        self.assertEqual((st.name, st.key), ("G502 LIGHTSPEED", "logitech:C15E09CD"))

    def test_unified_battery_without_percentage(self):
        dev = FakeHidpp({L.F_UNIFIED: (5, {1: [0, 4, 0, 0]})})
        self.use(receiver(), {b"long": ({1: dev}, b"short")})
        [st] = L.LogitechProvider().poll()
        self.assertEqual((st.level, st.approx), (50, "about 50% (good)"))
        self.assertEqual((st.ui_message, st.ui_params),
                         ("state.approx_grade", {"level": 50, "grade": "good"}))

    def test_localization_metadata_survives_sleep_cache(self):
        dev = FakeHidpp({L.F_UNIFIED: (5, {1: [0, 4, 0, 0]})})
        self.use(receiver(), {b"long": ({1: dev}, b"short")})
        provider = L.LogitechProvider()
        [awake] = provider.poll()
        dev.silent = True
        [asleep] = provider.poll()
        self.assertFalse(asleep.online)
        self.assertEqual(asleep.approx, awake.approx)
        self.assertEqual((asleep.ui_message, asleep.ui_params), (awake.ui_message, awake.ui_params))
        self.assertIsNot(asleep.ui_params, awake.ui_params)


def win_path(instance, col):
    """A real-shaped Windows HID path: the collection number ends the instance field."""
    return (rb"\?\HID#VID_046D&PID_C52B&MI_02&Col0" + str(col + 1).encode() + b"#"
            + instance + b"&0&000" + str(col).encode() + b"#{4d1e55b2-f16f-11cf-88cb-001111000030}")


class TwoReceiverTests(LogitechTestCase):
    def receiver_at(self, instance):
        short, long_ = win_path(instance, 0), win_path(instance, 1)
        return [entry(0xC52B, 0xFF00, 1, short, "USB Receiver"),
                entry(0xC52B, 0xFF00, 2, long_, "USB Receiver")], short, long_

    def test_short_and_long_collection_are_one_receiver(self):
        """Col01 and Col02 have different instance endings. They must still group."""
        es, short, long_ = self.receiver_at(b"7&aaaa")
        self.assertEqual(L._instance(es[0]), L._instance(es[1]))
        self.use(es, {long_: ({1: g502()}, short)})
        p = L.LogitechProvider()
        [st] = p.poll()
        self.assertEqual(st.key, "logitech:C15E09CD", "one receiver keeps the plain key")
        self.assertEqual(slots(p._asleep), set(), "empty slots are seen as empty, not asleep")

    def test_two_identical_receivers_get_two_icons(self):
        a, sa, la = self.receiver_at(b"7&aaaa")
        b, sb, lb = self.receiver_at(b"7&bbbb")
        self.use(a + b, {la: ({1: g502()}, sa), lb: ({1: g502()}, sb)})
        keys = sorted(st.key for st in L.LogitechProvider().poll())
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])


# ------------------------------------------------------------------ headsets
class HeadsetTests(LogitechTestCase):
    def headset(self, pid, params, page=0xFF43, usage=0x0202):
        self.use([entry(pid, page, usage, b"hs")], {b"hs": ({0xFF: FakeHidpp(adc(params))}, None)})
        return L.LogitechProvider()

    def test_g935_charging(self):
        [st] = self.headset(0x0A87, [0x0F, 0x7B, 0x03]).poll()
        self.assertEqual((st.name, st.level, st.charging, st.kind), ("Logitech G935", 76, True, "headset"))

    def test_headset_off(self):
        self.assertEqual(self.headset(0x0ABA, [0x0F, 0x7B, 0x00]).poll(), [])

    def test_headset_inactive_error(self):
        self.use([entry(0x0ABA, 0xFF43, 0x0202, b"hs")],
                 {b"hs": ({0xFF: FakeHidpp({L.F_ADC: (6, {0: "error"})})}, None)})
        p = L.LogitechProvider()
        self.assertEqual(p.poll(), [])
        self.assertTrue(any("inactive" in line for line in p.diagnostics()))

    def test_g535_uses_consumer_collection(self):
        [st] = self.headset(0x0AC4, [0x0E, 0xA0, 0x01], page=0x000C, usage=0x0001).poll()
        self.assertEqual((st.level, st.charging), (28, False))


# ------------------------------------------------------------------ safety
class SafetyTests(LogitechTestCase):
    def test_ff43_is_used_only_for_known_headsets(self):
        """A device with a long ff43:0202 collection and no ff00 one, that is not in
        HEADSETS, gets no request: main never wrote there for such a device."""
        dev = FakeHidpp({L.F_ADC: (6, {0: [0x0F, 0x02, 0x01]})})
        bus = self.use([entry(0x0B99, 0xFF43, 0x0202, b"hs")], {b"hs": ({0xFF: dev}, None)})
        self.assertEqual(L.LogitechProvider().poll(), [])
        self.assertEqual(bus.writes, [])

    def test_no_write_to_other_consumer_collections(self):
        bus = self.use([entry(0xC539, 0x000C, 0x0001, b"consumer")], {})
        L.LogitechProvider().poll()
        self.assertEqual(bus.writes, [])

    def test_ff00_wins_over_ff43_in_any_order(self):
        for reverse in (False, True):
            es = [entry(0xC08D, 0xFF43, 0x0202, b"hs"), entry(0xC08D, 0xFF00, 1, b"short"),
                  entry(0xC08D, 0xFF00, 2, b"long")]
            if reverse:
                es.reverse()
            bus = self.use(es, {b"long": ({0xFF: FakeHidpp(VOLTAGE_76)}, b"short"), b"hs": ({}, None)})
            L.LogitechProvider().poll()
            self.assertEqual({path for path, _ in bus.writes}, {b"long"})


# ------------------------------------------------------------------ reply parsing
class ParseTests(unittest.TestCase):
    CASES = [
        # feature, params, (level, charging, approximate text)
        (L.F_UNIFIED, [55, 4, 0], (55, False, "")),       # discharging
        (L.F_UNIFIED, [55, 4, 1], (55, True, "")),        # charging
        (L.F_UNIFIED, [95, 8, 2], (95, True, "")),        # almost full
        (L.F_UNIFIED, [100, 8, 3], (100, True, "")),      # full, on the charger
        (L.F_UNIFIED, [40, 4, 4], (40, True, "")),        # slow charging
        (L.F_UNIFIED, [40, 4, 5], (40, False, "")),       # invalid battery
        (L.F_STATUS, [40, 30, 4], (40, True, "")),
        (L.F_STATUS, [0, 0, 0], (None, False, "")),       # no level
        (L.F_VOLTAGE, [0x10, 0x6C, 0x80], (100, True, "")),   # G502 on its cable (hardware)
        (L.F_VOLTAGE, [0x0F, 0x7B, 0x02], (76, False, "")),   # low bits alone: not charging
        (L.F_ADC, [0x0F, 0x7B, 0x01], (76, False, "")),
        (L.F_ADC, [0x0F, 0x7B, 0x03], (76, True, "")),
        (L.F_ADC, [0x0F, 0x7B, 0x02], (None, False, "")),     # bit 0 clear: headset off
        (L.F_ADC, [0x00, 0x00, 0x01], (None, False, "")),     # no real voltage
        (L.F_UNIFIED, [0, 8, 3], (90, True, "about 90% (full)")),   # no percentage
        (L.F_UNIFIED, [0, 1, 0], (5, False, "about 5% (critical)")),
        (L.F_UNIFIED, [0, 0, 0], (None, False, "")),                 # nothing reported
    ]

    def test_parse_battery(self):
        for feature, params, expected in self.CASES:
            with self.subTest(feature=hex(feature), params=params):
                self.assertEqual(L.parse_battery(feature, params), expected)

    def test_voltage_curve_ends(self):
        self.assertEqual(L.voltage_to_percent(4300), 100)
        self.assertEqual(L.voltage_to_percent(3744), 28)      # G502, matches G HUB (hardware)
        self.assertEqual(L.voltage_to_percent(3000), 0)


if __name__ == "__main__":
    unittest.main()
