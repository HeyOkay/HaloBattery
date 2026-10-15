"""Tests for providers/lofree.py. No hardware is needed.

The fake keyboard answers the transaction of Lofree's web driver on report 0x04:
start (byte 2 = 01) -> command -> answer from byte 7 -> end (byte 2 = 02).
hidapi puts the report id in front of each report, as on Windows.

Run from the repository root:

    python -m unittest discover -s tests
"""
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
    return [F.REPORT_ID] + p + [0x00] * (31 - len(p))


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
        if b2 in (F.START, F.END):
            self.queue.append(report(b2))
            return
        self.queue += [list(n) for n in self.noise]
        answer = {F.CMD_ONLINE: self.online, F.CMD_BATTERY: self.battery}[b2]
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


class PollTest(ProviderTest):
    def test_hyzen_on_the_dongle_issue_82(self):
        kbd = FakeKeyboard(battery=66)
        res = self.poll(issue_82_entries(), {VENDOR: kbd})
        self.assertEqual([(r.name, r.level, r.charging, r.kind, r.source) for r in res],
                         [("Lofree HYZEN67", 66, False, "keyboard", "lofree")])
        pad = [0x00] * 28
        self.assertEqual(kbd.writes, [
            [0x04, 0x00, 0x00, 0x01] + pad,                                   # start
            [0x04, 0x00, 0x00, 0xAA, 0x00, 0x00, 0x00, 0x00] + [0x00] * 24,     # online?
            [0x04, 0x00, 0x00, 0x02] + pad,                                   # end
            [0x04, 0x00, 0x00, 0x01] + pad,
            [0x04, 0x00, 0x00, 0x1A, 0x00, 0x00, 0x00, 0x00] + [0x00] * 24,     # battery
            [0x04, 0x00, 0x00, 0x02] + pad,
        ])
        self.assertTrue(all(len(w) == 32 for w in kbd.writes))

    def test_only_the_ff1c_collection_is_opened(self):
        kbds = {e["path"]: FakeKeyboard() for e in issue_82_entries()}
        self.poll(issue_82_entries(), kbds)
        self.assertEqual([p for p, k in kbds.items() if k.opened], [VENDOR])

    def test_offline_keyboard_gives_no_icon_and_no_battery_request(self):
        kbd = FakeKeyboard(online=0)
        self.assertEqual(self.poll(issue_82_entries(), {VENDOR: kbd}), [])
        self.assertNotIn(0x1A, [w[3] for w in kbd.writes])

    def test_on_the_cable_the_dongle_is_not_read(self):
        entries = issue_82_entries() + [dict(issue_82_entries(0x0024)[3])]
        kbd = FakeKeyboard()
        self.assertEqual(self.poll(entries, {VENDOR: kbd, b"0024-1-ff1c-3": FakeKeyboard()}), [])
        self.assertEqual(kbd.opened, 0)

    def test_silent_keyboard_times_out(self):
        kbd = FakeKeyboard(silent=True)
        self.assertEqual(self.poll(issue_82_entries(), {VENDOR: kbd}), [])
        self.assertEqual([w[3] for w in kbd.writes], [0x01])      # no command without a start ack

    def test_other_reports_are_skipped(self):
        noise = [[0x01, 0x00, 0x04, 0x00, 0x00, 0x00, 0x00, 0x00],     # a key report (id 1)
                 report(0x55, [9], (3, 0))]                          # another frame, wrong offset
        res = self.poll(issue_82_entries(), {VENDOR: FakeKeyboard(battery=40, noise=noise)})
        self.assertEqual([r.level for r in res], [40])

    def test_wrong_offset_is_not_a_level(self):
        res = self.poll(issue_82_entries(), {VENDOR: FakeKeyboard(bad_offset=True)})
        self.assertEqual(res, [])

    def test_level_out_of_range_is_refused(self):
        self.assertEqual(self.poll(issue_82_entries(), {VENDOR: FakeKeyboard(battery=0xC8)}), [])

    def test_the_cable_pid_itself_is_not_read(self):
        e = [issue_82_entries(0x0024)[3]]
        kbd = FakeKeyboard()
        self.assertEqual(self.poll(e, {b"0024-1-ff1c-3": kbd}), [])
        self.assertEqual(kbd.opened, 0)


# ------------------------------------------------------------- the Flow Lite84 (#165)

def flow_reply(cmd, value=0, charging=0, mv=0, status=0x00, header=F.FLOW_HEADER,
               break_checksum=False):
    """A 17-byte frame of the shape the Flow Lite84 dongle answers with."""
    frame = ([header, cmd, status, 0x00, 0x00, 0x00, value, charging]
             + list(mv.to_bytes(2, "big")) + [0x00] * 6)
    ck = F.flow_checksum(frame)
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
        if cmd == F.FLOW_CMD_ONLINE:
            self.queue.append(flow_reply(cmd, value=self.online))
        elif cmd == F.FLOW_CMD_BATTERY:
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

    def one_flow(self, kbd=None, **kw):
        kbd = kbd or FakeFlow(**kw)
        entries = flow_entries()
        return kbd, {F.FLOW_VID: entries}, {e["path"]: kbd for e in entries}

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

    def test_the_frames_follow_the_family_rule(self):
        kbd, tables, devices = self.one_flow()
        self.poll(tables, devices)
        pad = [0x00] * 10
        self.assertEqual(kbd.writes, [
            [F.FLOW_HEADER, F.FLOW_CMD_ONLINE, 0x00, 0x00, 0x00, F.FLOW_FLAG_KEYBOARD]
            + pad + [0xCA],
            [F.FLOW_HEADER, F.FLOW_CMD_BATTERY, 0x00, 0x00, 0x00, F.FLOW_FLAG_KEYBOARD]
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

    def test_a_control_collection_that_cannot_take_the_frame_is_skipped(self):
        kbd, tables, devices = self.one_flow()
        found = self.poll(tables, devices, lengths={F.FLOW_CONTROL_USAGE: 9})
        self.assertEqual([r.level for r in found], [57])
        control = next(e for e in tables[F.FLOW_VID]
                       if (e["usage_page"], e["usage"]) == F.FLOW_CONTROL_USAGE)
        self.assertNotEqual(kbd.path, control["path"])
        self.assertIn("cannot take a 17-byte frame; skipped", self.diag())

    def test_offline_keyboard_gives_no_icon_and_no_battery_request(self):
        kbd, tables, devices = self.one_flow(online=0)
        self.assertEqual(self.poll(tables, devices), [])
        self.assertEqual([w[1] for w in kbd.writes], [F.FLOW_CMD_ONLINE])
        self.assertIn("keyboard offline", self.diag())

    def test_silent_dongle_times_out(self):
        kbd, tables, devices = self.one_flow(silent=True)
        self.assertEqual(self.poll(tables, devices), [])
        self.assertEqual([w[1] for w in kbd.writes], [F.FLOW_CMD_ONLINE])

    def test_noise_and_a_broken_checksum_are_skipped(self):
        noise = [flow_reply(0x0A, value=3),                               # an event push
                 flow_reply(F.FLOW_CMD_BATTERY, value=99, break_checksum=True),
                 flow_reply(F.FLOW_CMD_BATTERY, value=98, status=0x02)]   # an error frame
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


if __name__ == "__main__":
    unittest.main()
