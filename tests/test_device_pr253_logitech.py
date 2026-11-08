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
CHARGING_100 = {L.F_VOLTAGE: (6, {0: [0x10, 0x6C, 0x80]})}         # G502 on its cable


def g502(battery=VOLTAGE_76, unit=(0xC1, 0x5E, 0x09, 0xCD)):
    return FakeHidpp({**battery, L.F_NAME: (3, {2: [3]}),
                      L.F_INFO: (2, {0: [3, *unit]})}, name="G502 LIGHTSPEED")


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


def win_path(instance, col):
    """A real-shaped Windows HID path: the collection number ends the instance field."""
    return (rb"\?\HID#VID_046D&PID_C52B&MI_02&Col0" + str(col + 1).encode() + b"#"
            + instance + b"&0&000" + str(col).encode() + b"#{4d1e55b2-f16f-11cf-88cb-001111000030}")


class TwoReceiverTests(LogitechTestCase):
    def receiver_at(self, instance):
        short, long_ = win_path(instance, 0), win_path(instance, 1)
        return [entry(0xC52B, 0xFF00, 1, short, "USB Receiver"),
                entry(0xC52B, 0xFF00, 2, long_, "USB Receiver")], short, long_


    def test_two_identical_receivers_get_two_icons(self):
        a, sa, la = self.receiver_at(b"7&aaaa")
        b, sb, lb = self.receiver_at(b"7&bbbb")
        self.use(a + b, {la: ({1: g502()}, sa), lb: ({1: g502(unit=(1, 2, 3, 4))}, sb)})
        keys = sorted(st.key for st in L.LogitechProvider().poll())
        self.assertEqual(keys, ["logitech:01020304", "logitech:C15E09CD"])

    def test_two_identical_receivers_without_unit_ids_get_two_icons(self):
        """Without a unit id the key is the slot, and both receivers have a slot 1."""
        a, sa, la = self.receiver_at(b"7&aaaa")
        b, sb, lb = self.receiver_at(b"7&bbbb")
        self.use(a + b, {la: ({1: FakeHidpp(VOLTAGE_76)}, sa), lb: ({1: FakeHidpp(VOLTAGE_76)}, sb)})
        keys = sorted(st.key for st in L.LogitechProvider().poll())
        self.assertEqual(len(keys), 2)
        self.assertNotEqual(keys[0], keys[1])

    def test_mouse_on_its_cable_keeps_one_icon(self):
        """Two receivers of the same kind (a G502 X PLUS and a G915 are both 0xC547), then
        the mouse is plugged in to charge. The cable reads the same unit id, so the greyed
        copy from the receiver must not stay next to the charging icon."""
        a, sa, la = self.receiver_at(b"7&aaaa")
        b, sb, lb = self.receiver_at(b"7&bbbb")
        self.use(a + b, {la: ({1: g502()}, sa), lb: ({}, sb)})
        p = L.LogitechProvider()
        [st] = p.poll()
        self.assertEqual(st.key, "logitech:C15E09CD")
        cable = [entry(0xC08D, 0xFF00, 1, b"cshort", "G502 LIGHTSPEED"),
                 entry(0xC08D, 0xFF00, 2, b"clong", "G502 LIGHTSPEED")]
        self.use(a + b + cable, {la: ({1: FakeHidpp(silent=True)}, sa), lb: ({}, sb),
                                 b"clong": ({0xFF: g502(CHARGING_100)}, b"cshort")})
        [st] = p.poll()
        self.assertEqual((st.key, st.online, st.charging), ("logitech:C15E09CD", True, True))


# ------------------------------------------------------------------ headsets


# ------------------------------------------------------------------ safety


# ------------------------------------------------------------------ reply parsing


if __name__ == "__main__":
    unittest.main()
