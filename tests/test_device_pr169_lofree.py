"""Tests for providers/lofree.py. No hardware is needed.

The fake keyboard answers the transaction of Lofree's web driver on report 0x04:
start (byte 2 = 01) -> command -> answer from byte 7 -> end (byte 2 = 02).
hidapi puts the report id in front of each report, as on Windows.

Run from the repository root:

    python -m unittest discover -s tests
"""

# Independent wire fixtures: hyzen.lofree.tech battery routine; lofree.tech/home index-BTVblIUr.js power/online frame (HaloBattery #165).
# Keep reply bytes/offsets independent of the provider constants under test.
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import lofree as F  # noqa: E402


def report(b2, data=(), offset=(0, 0)):
    """Input report 0x04: id, then 31 bytes; byte 2 = b2, bytes 4-5 = offset, data at 7."""
    p = [0x00, 0x00, b2, len(data), offset[0], offset[1], 0x00] + list(data)
    return [0x04] + p + [0x00] * (31 - len(p))


class FakeKeyboard:
    """online: answer of command AA (0 = offline); battery: answer of command 1A.
    silent: never answers. noise: reports sent before each answer."""

    def __init__(self, online=1, battery=66, silent=False, noise=(), bad_offset=False):
        self.online, self.battery, self.silent = online, battery, silent
        self.noise, self.bad_offset = list(noise), bad_offset
        self.writes, self.queue, self.opened = [], [], 0

    def on_write(self, data):
        data = list(data)
        self.writes.append(data)
        if self.silent:
            return
        b2 = data[3]
        if b2 in (0x01, 0x02):
            self.queue.append(report(b2))
            return
        self.queue += [list(n) for n in self.noise]
        answer = {0xAA: self.online, 0x1A: self.battery}[b2]
        self.queue.append(report(b2, [answer], (1, 0) if self.bad_offset else (0, 0)))

    def on_read(self):
        return self.queue.pop(0) if self.queue else []


class FakeBus:
    def __init__(self, kbds):
        self.kbds = kbds

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.k = bus.kbds[path]
                self.k.opened += 1

            def write(self, data):
                self.k.on_write(data)
                return len(data)

            def read(self, n, timeout=None):
                return self.k.on_read()

            def close(self):
                pass

        return FakeDevice


def issue_82_entries(pid=0x0025):
    # the collections of the Lofree HYZEN67 in the diagnostics of issue #82
    shape = [(0, 0x0001, 0x06), (1, 0x0001, 0x06), (1, 0x000C, 0x01), (1, 0xFF1C, 0x92),
             (1, 0x0001, 0x02), (1, 0x0001, 0x80), (2, 0x000C, 0x01)]
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": b"%04x-%d-%04x-%d" % (pid, i, p, n), "product_string": "HYZEN67@Lofree"}
            for n, (i, p, u) in enumerate(shape)]


VENDOR = b"0025-1-ff1c-3"


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (F.hid, F.hidlist, F.time)
        self.clock = [1000.0]
        F.time = types.SimpleNamespace(time=lambda: self.clock[0],
                                       sleep=lambda s: None)

    def tearDown(self):
        F.hid, F.hidlist, F.time = self._saved

    def poll(self, entries, kbds):
        bus = FakeBus(kbds)

        class Clocked(bus.device_class()):
            def read(inner, n, timeout=None):
                self.clock[0] += (timeout or 0) / 1000.0
                return super().read(n, timeout)

        F.hid = types.SimpleNamespace(device=Clocked)
        F.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return F.LofreeProvider().poll()


# ------------------------------------------------------------- the Flow Lite84 (#165)

def flow_reply(cmd, value=0, charging=0, mv=0, status=0x00, header=0x08,
               break_checksum=False):
    """A 17-byte frame of the shape the Flow Lite84 dongle answers with."""
    frame = ([header, cmd, status, 0x00, 0x00, 0x00, value, charging]
             + list(mv.to_bytes(2, "big")) + [0x00] * 6)
    ck = ((0x55 - sum(frame)) & 0xFF)
    if break_checksum:
        ck ^= 0xFF
    return frame + [ck]


def flow_entries(vid=0x05AC, pid=0x024F):
    """The collections of the Flow Lite84 dongle, as #165's dump lists them."""
    shape = [(0, 0x0001, 0x0006), (1, 0x0001, 0x0002), (1, 0xFF02, 0x0002),
             (1, 0xFF04, 0x0002), (1, 0x0001, 0x0006), (1, 0x000C, 0x0001),
             (1, 0x0001, 0x0080)]
    return [{"product_id": pid, "vendor_id": vid, "interface_number": i,
             "usage_page": p, "usage": u,
             "path": b"%04x-%04x-%d-%04x-%04x" % (vid, pid, n, p, u),
             "product_string": "2.4G Wireless Receiver"}
            for n, (i, p, u) in enumerate(shape)]


class FakeFlow:
    """The Flow Lite84 dongle: answers commands 0x03 and 0x04 on report 0x08.
    noise: frames sent before each answer. silent: never answers."""

    def __init__(self, online=1, level=57, charging=1, mv=3790, silent=False,
                 noise=(), break_checksum=False):
        self.online, self.level, self.charging, self.mv = online, level, charging, mv
        self.silent, self.noise, self.break_checksum = silent, list(noise), break_checksum
        self.writes, self.queue, self.opened, self.path = [], [], 0, None

    def on_write(self, data):
        data = list(data)
        self.writes.append(data)
        if self.silent:
            return
        cmd = data[1]
        self.queue += [list(n) for n in self.noise]
        if cmd == 0x03:
            self.queue.append(flow_reply(cmd, value=self.online))
        elif cmd == 0x04:
            self.queue.append(flow_reply(cmd, value=self.level, charging=self.charging,
                                         mv=self.mv, break_checksum=self.break_checksum))

    def on_read(self):
        return self.queue.pop(0) if self.queue else []


class FlowProviderTest(unittest.TestCase):
    """The Flow Lite84 dongle, and where a test says so, both Lofree keys at once.
    hidlist.enumerate() and hid.device() are replaced."""

    def setUp(self):
        self._saved = (F.hid, F.hidlist, F.flow_output_length)

    def tearDown(self):
        F.hid, F.hidlist, F.flow_output_length = self._saved

    def one_flow(self, kbd=None, vid=F.FLOW_VID, pid=F.FLOW_PID, **kw):
        kbd = kbd or FakeFlow(**kw)
        entries = flow_entries(vid=vid, pid=pid)
        return kbd, {vid: entries}, {e["path"]: kbd for e in entries}

    def poll(self, tables, devices, lengths=None):
        """tables: {vid: collection entries}. devices: {path: fake device}.
        lengths: {(usage_page, usage): OutputReportByteLength}; unknown stays None,
        which is what Windows gives for a collection it will not classify."""
        self.lengths = dict(lengths or {})
        tables = {vid: list(es) for vid, es in tables.items()}
        F.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(tables.get(vid, [])))
        by_path = {e["path"]: (e["usage_page"], e["usage"])
                   for es in tables.values() for e in es}
        F.flow_output_length = lambda path: self.lengths.get(by_path.get(path))
        bus = {"devices": devices}

        class FakeDevice:
            def open_path(self, path):
                self.k = bus["devices"][path]
                self.k.opened += 1
                self.k.path = path

            def write(self, data):
                self.k.on_write(data)
                return len(data)

            def read(self, n, timeout=None):
                return self.k.on_read()

            def close(self):
                pass

        F.hid = types.SimpleNamespace(device=FakeDevice)
        self.provider = F.LofreeProvider()
        return self.provider.poll()

    def diag(self):
        return "\n".join(self.provider.diagnostics())


class FlowPollTest(FlowProviderTest):
    def test_an_apple_keyboard_with_the_same_id_is_never_written(self):
        # 05AC:024F is also Apple's wired Aluminium Keyboard id, and that keyboard has
        # no FF02:0002 collection. It must not be written to at all (#169 review,
        # @ahmedkhursheed23).
        shape = [(0, 0x0001, 0x0006), (1, 0x0001, 0x0002), (1, 0x000C, 0x0001),
                 (1, 0x0001, 0x0080)]
        entries = [{"product_id": F.FLOW_PID, "vendor_id": F.FLOW_VID,
                    "interface_number": i, "usage_page": p, "usage": u,
                    "path": b"apple-%d-%04x-%04x" % (n, p, u),
                    "product_string": "Apple Keyboard"}
                   for n, (i, p, u) in enumerate(shape)]
        kbd = FakeFlow()
        found = self.poll({F.FLOW_VID: entries}, {e["path"]: kbd for e in entries})
        self.assertEqual([], found)
        self.assertEqual([], kbd.writes)                  # nothing was sent
        self.assertEqual(0, kbd.opened)
        self.assertIn("no FF02:0002 control collection", self.diag())

    def test_the_lite84_is_read(self):
        kbd, tables, devices = self.one_flow(level=57, charging=1, mv=3790)
        found = self.poll(tables, devices)
        self.assertEqual(1, len(found))
        d = found[0]
        self.assertEqual("lofree:024f", d.key)
        self.assertEqual("Lofree Flow Lite84", d.name)
        self.assertEqual(57, d.level)
        self.assertTrue(d.charging)
        self.assertTrue(d.online)
        self.assertEqual("keyboard", d.kind)
        self.assertIn("[Lofree] Lofree Flow Lite84 pid=024f", self.diag())
        self.assertIn("3790 mV", self.diag())

    def test_the_compx_vendor_id_dongle_is_read(self):
        # #557: the same receiver also ships as 3554:FA0A under Compx's own id
        kbd, tables, devices = self.one_flow(vid=0x3554, pid=0xFA0A, level=64, charging=0)
        found = self.poll(tables, devices)
        self.assertEqual(1, len(found))
        self.assertEqual("lofree:fa0a", found[0].key)
        self.assertEqual(64, found[0].level)
        self.assertFalse(found[0].charging)
        self.assertTrue(found[0].online)
        self.assertIn("[Lofree] Lofree Flow Lite84 pid=fa0a", self.diag())

    def test_compxs_id_alone_is_not_enough(self):
        # 3554 is shared by every Compx receiver: the Flow product string and
        # the FF02:0002 collection must still match, or nothing is written.
        entries = flow_entries(vid=0x3554, pid=0xFA0A)
        for e in entries:
            e["product_string"] = "CX Keyboard"
        kbd = FakeFlow()
        found = self.poll({0x3554: entries}, {e["path"]: kbd for e in entries})
        self.assertEqual([], found)
        self.assertEqual([], kbd.writes)
        self.assertEqual(0, kbd.opened)

    def test_the_frames_follow_the_family_rule(self):
        kbd, tables, devices = self.one_flow()
        self.poll(tables, devices)
        pad = [0x00] * 10
        self.assertEqual(kbd.writes, [
            [0x08, 0x03, 0x00, 0x00, 0x00, 0x80]
            + pad + [0xCA],
            [0x08, 0x04, 0x00, 0x00, 0x00, 0x80]
            + pad + [0xC9],
        ])
        self.assertTrue(all(len(w) == 17 for w in kbd.writes))
        self.assertTrue(all(sum(w) % 256 == 0x55 for w in kbd.writes))

    def test_only_the_control_collection_is_opened(self):
        kbd, tables, devices = self.one_flow()
        self.poll(tables, devices)
        control = next(e for e in tables[F.FLOW_VID]
                       if (e["usage_page"], e["usage"]) == F.FLOW_CONTROL_USAGE)
        self.assertEqual(kbd.path, control["path"])

    def test_incompatible_control_never_falls_back_to_unsourced_collections(self):
        # The named Flow driver opens FF02:0002 only. A shape on the same PID
        # is not authority to send this command to another endpoint.
        for length in (0, 9, 33, False, 17.0):
            with self.subTest(length=length):
                kbd, tables, devices = self.one_flow()
                self.assertEqual(self.poll(tables, devices,
                                          lengths={F.FLOW_CONTROL_USAGE: length}), [])
                self.assertEqual(kbd.opened, 0)
                self.assertEqual(kbd.writes, [])
                self.assertIn("cannot take a 17-byte frame; skipped", self.diag())

    def test_offline_keyboard_gives_no_icon_and_no_battery_request(self):
        kbd, tables, devices = self.one_flow(online=0)
        self.assertEqual(self.poll(tables, devices), [])
        self.assertEqual([w[1] for w in kbd.writes], [0x03])
        self.assertIn("keyboard offline", self.diag())

    def test_silent_dongle_times_out(self):
        kbd, tables, devices = self.one_flow(silent=True)
        self.assertEqual(self.poll(tables, devices), [])
        self.assertEqual([w[1] for w in kbd.writes], [0x03])

    def test_noise_and_a_broken_checksum_are_skipped(self):
        noise = [flow_reply(0x0A, value=3),                               # an event push
                 flow_reply(0x04, value=99, break_checksum=True),
                 flow_reply(0x04, value=98, status=0x02)]   # an error frame
        kbd, tables, devices = self.one_flow(level=40, noise=noise)
        self.assertEqual([r.level for r in self.poll(tables, devices)], [40])

    def test_not_charging_is_reported(self):
        kbd, tables, devices = self.one_flow(level=57, charging=0)
        found = self.poll(tables, devices)
        self.assertEqual([(r.level, r.charging) for r in found], [(57, False)])

    def test_level_out_of_range_is_refused(self):
        kbd, tables, devices = self.one_flow(level=0xC8)
        self.assertEqual(self.poll(tables, devices), [])

    def test_both_lofree_keyboards_in_one_poll(self):
        h = FakeKeyboard(battery=66)
        f = FakeFlow(level=57)
        entries165 = flow_entries()
        res = self.poll({F.LOFREE_VID: issue_82_entries(), F.FLOW_VID: entries165},
                        {VENDOR: h, **{e["path"]: f for e in entries165}})
        self.assertEqual([(r.key, r.name, r.level, r.kind, r.source) for r in res],
                         [("lofree:0025", "Lofree HYZEN67", 66, "keyboard", "lofree"),
                          ("lofree:024f", "Lofree Flow Lite84", 57, "keyboard", "lofree")])

    def test_exact_aula_sibling_with_unknown_caps_never_receives_flow_command(self):
        kbd, tables, devices = self.one_flow()
        aula = FakeFlow()
        entry = {"vendor_id": 0x05AC, "product_id": 0x024F,
                 "interface_number": 3, "usage_page": 0xFF60, "usage": 0x0061,
                 "product_string": "F108Pro Dongle", "path": b"aula-control"}
        tables[F.FLOW_VID].append(entry)
        devices[entry["path"]] = aula
        self.assertEqual(self.poll(tables, devices,
                                   lengths={F.FLOW_CONTROL_USAGE: 0}), [])
        self.assertEqual((kbd.opened, aula.opened), (0, 0))
        self.assertEqual((kbd.writes, aula.writes), ([], []))

    def test_another_receiver_cannot_lend_its_alternate_collection(self):
        kbd, tables, devices = self.one_flow()
        foreign = FakeFlow()
        entry = {"vendor_id": F.FLOW_VID, "product_id": F.FLOW_PID,
                 "interface_number": 1, "usage_page": 0xFF04, "usage": 2,
                 "product_string": "2.4G Wireless Receiver", "path": b"other-physical-receiver"}
        tables[F.FLOW_VID].append(entry)
        devices[entry["path"]] = foreign
        self.assertEqual(self.poll(tables, devices,
                                   lengths={F.FLOW_CONTROL_USAGE: 9}), [])
        self.assertEqual((kbd.opened, foreign.opened), (0, 0))
        self.assertEqual((kbd.writes, foreign.writes), ([], []))

    def test_shared_pid_control_shape_does_not_override_foreign_identity(self):
        for product in ("F108Pro Dongle", "Apple Keyboard", "Other Receiver", "", None, 123):
            with self.subTest(product=product):
                kbd, tables, devices = self.one_flow()
                for entry in tables[F.FLOW_VID]:
                    entry["product_string"] = product
                self.assertEqual(self.poll(tables, devices), [])
                self.assertEqual((kbd.opened, kbd.writes), (0, []))

    def test_unknown_caps_preserve_verified_flow_control(self):
        kbd, tables, devices = self.one_flow(level=61)
        for entry in tables[F.FLOW_VID]:
            entry["product_string"] = " 2.4G WIRELESS RECEIVER "
        self.assertEqual([s.level for s in self.poll(tables, devices)], [61])
        control = next(entry for entry in tables[F.FLOW_VID]
                       if (entry["usage_page"], entry["usage"]) == F.FLOW_CONTROL_USAGE)
        self.assertEqual(kbd.path, control["path"])
        self.assertEqual([frame[1] for frame in kbd.writes], [0x03, 0x04])

    def test_wrong_vendor_or_product_never_receives_flow_command(self):
        for field, value in (("vendor_id", 0x1234), ("product_id", 0x024E)):
            with self.subTest(field=field):
                kbd, tables, devices = self.one_flow()
                for entry in tables[F.FLOW_VID]:
                    entry[field] = value
                self.assertEqual(self.poll(tables, devices), [])
                self.assertEqual((kbd.opened, kbd.writes), (0, []))


if __name__ == "__main__":
    unittest.main()
