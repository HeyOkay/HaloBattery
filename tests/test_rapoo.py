"""Tests for providers/rapoo.py (the report-7 generation). No hardware is needed.

The report shape is from Rapoo's own web driver (hub.rapoo.com, "RAPOO HUB -
VT Generation 2 Series Web Driver"): every device it supports pushes a status
report on input report 7 whose byte 6 is the battery state (0 invalid,
1 on battery, 2 charging) and byte 7 the level 0..100. The collection shape is
the reporter's diagnostics dump in issue #193 (24ae:1413: the ff00:0002,
ff00:000e, ff00:000f, ff00:0010, ff00:0011 and ff00:0012 vendor collections on
interface 1 and ff00:0001 on interface 2; 24ae:4824: ff00:0002, ff00:000e and
ff00:000f). No frames were captured from these two devices - the app has not
run against them yet.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import rapoo as R  # noqa: E402


# a status push: report id 7, receiver connection, state 1, 100 % - built from
# the layout in the web driver's parser, not captured from hardware
REFERENCE = [0x07, 0x02, 0x01, 0xD0, 0x07, 0xD0, 0x07, 0x01, 0x64, 0x00, 0x01, 0x01, 0x01]
# the V700DIY-98's own frame, byte for byte from the reporter's 2026-10-03
# capture: state 1 (on battery), level 0x54 = 84 (#193)
BD_REFERENCE = [0xBD, 0x01, 0x00, 0x07, 0x00, 0x46, 0x00, 0x01, 0x54, 0xAD, 0xCF, 0x7D]


class FakeCollection:
    """frames: what the device pushes, in order; an entry of None reads as silence."""

    def __init__(self, frames=()):
        self.frames = list(frames)
        self.opened = 0

    def read(self):
        if self.frames:
            f = self.frames.pop(0)
            return f if f is not None else []
        return []


def fake_device_class(cols, order_log):
    class FakeDevice:
        def open_path(self, path):
            self.c = cols[path]
            self.c.opened += 1
            order_log.append(path)

        def read(self, n, timeout):
            return self.c.read()

        def write(self, *a):
            raise AssertionError("the Rapoo provider must never write")

        def send_feature_report(self, *a):
            raise AssertionError("the Rapoo provider must never write")

        def close(self):
            pass
    return FakeDevice


def receiver_entries(pid=R.VT7_RECEIVER, prefix=b"recv"):
    # the collections of the VT7 receiver from the issue #193 dump, in dump order
    shape = [(0, 0x0001, 0x02), (1, 0x0001, 0x06), (1, 0x000C, 0x01), (1, 0x0001, 0x80),
             (1, 0xFF00, 0x0E), (1, 0xFF00, 0x0F), (1, 0xFF00, 0x12), (1, 0xFF00, 0x10),
             (1, 0xFF00, 0x11), (1, 0xFF00, 0x02), (2, 0xFF00, 0x01)]
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": prefix + b"-%d-%04x-%d" % (i, p, n),
             "product_string": "Rapoo Gaming Device"}
            for n, (i, p, u) in enumerate(shape)]


def keyboard_entries(pid=R.V700DIY_KEYBOARD, prefix=b"kb"):
    # the collections of the V700DIY-98 from the issue #193 dump, in dump order
    shape = [(0, 0x0001, 0x06), (1, 0x0001, 0x02), (2, 0xFF00, 0x0F), (2, 0x000C, 0x01),
             (2, 0x0001, 0x80), (2, 0xFF00, 0x02), (2, 0xFF00, 0x0E)]
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": prefix + b"-%d-%04x-%d" % (i, p, n),
             "product_string": "Rapoo Gaming Keyboard"}
            for n, (i, p, u) in enumerate(shape)]


def first_vendor(entries, frame=REFERENCE, prefix=b""):
    """The ff00:0002 collection answers; every other one is silent."""
    first = next(e for e in entries
                 if (e["usage_page"], e["usage"]) == (0xFF00, 0x0002))
    return {e["path"]: FakeCollection((frame,) if e["path"] == first["path"] else ())
            for e in entries}


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (R.hid, R.hidlist, R.time)
        self.now = [1000.0]
        R.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        self.provider = R.RapooProvider()
        self.order = []

    def tearDown(self):
        R.hid, R.hidlist, R.time = self._saved

    def poll(self, entries, cols):
        R.hid = types.SimpleNamespace(device=fake_device_class(cols, self.order))
        R.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()

    # ---------------------------------------------------------------- parsing

    def test_the_reference_frame_reads_100_on_battery(self):
        self.assertEqual(R.parse_report(REFERENCE), (100, False))

    def test_a_charging_frame_reads_charging(self):
        frame = REFERENCE.copy()
        frame[R.STATUS_INDEX] = 0x02
        frame[R.LEVEL_INDEX] = 0x50
        self.assertEqual(R.parse_report(frame), (80, True))

    def test_the_invalid_state_is_not_a_reading(self):
        frame = REFERENCE.copy()
        frame[R.STATUS_INDEX] = 0x00
        self.assertIsNone(R.parse_report(frame))

    def test_an_unknown_state_is_refused(self):
        frame = REFERENCE.copy()
        frame[R.STATUS_INDEX] = 0x03
        self.assertIsNone(R.parse_report(frame))

    def test_a_level_above_100_is_refused(self):
        frame = REFERENCE.copy()
        frame[R.LEVEL_INDEX] = 0x65
        self.assertIsNone(R.parse_report(frame))

    def test_another_report_id_is_refused(self):
        frame = REFERENCE.copy()
        frame[0] = 0x06
        self.assertIsNone(R.parse_report(frame))

    def test_a_short_frame_is_refused(self):
        self.assertIsNone(R.parse_report(REFERENCE[:R.LEVEL_INDEX]))
        self.assertIsNone(R.parse_report([]))

    # ------------------------------------------------- the keyboard's 0xBD frame

    def test_the_keyboards_own_frame_reads_84_on_battery(self):
        self.assertEqual(R.parse_report(BD_REFERENCE), (84, False))

    def test_the_keyboards_charging_frame_reads_charging(self):
        frame = BD_REFERENCE.copy()
        frame[R.BD_STATE_INDEX] = 0x02
        self.assertEqual(R.parse_report(frame), (84, True))

    def test_a_bd_frame_with_an_invalid_state_is_refused(self):
        frame = BD_REFERENCE.copy()
        frame[R.BD_STATE_INDEX] = 0x00
        self.assertIsNone(R.parse_report(frame))

    def test_a_bd_frame_with_a_level_above_100_is_refused(self):
        frame = BD_REFERENCE.copy()
        frame[R.BD_LEVEL_INDEX] = 0xAD
        self.assertIsNone(R.parse_report(frame))

    def test_a_bd_frame_of_another_variant_is_refused(self):
        frame = BD_REFERENCE.copy()
        frame[1] = 0xA0
        self.assertIsNone(R.parse_report(frame))

    def test_a_short_bd_frame_is_refused(self):
        self.assertIsNone(R.parse_report(BD_REFERENCE[:R.BD_LEVEL_INDEX]))

    # ---------------------------------------------------------------- polling

    def test_the_mouse_receiver_is_read_listen_only(self):
        entries = receiver_entries()
        out = self.poll(entries, first_vendor(entries))
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s.key, s.name, s.level, s.charging, s.online, s.source, s.kind),
                         ("rapoo:1413", "Rapoo VT7 (Gen-2)", 100, False, True, "rapoo", "mouse"))

    def test_the_keyboard_is_read_listen_only(self):
        # the keyboard answers with its own 0xBD frame, not report 7
        entries = keyboard_entries()
        out = self.poll(entries, first_vendor(entries, frame=BD_REFERENCE))
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s.key, s.name, s.level, s.charging, s.online, s.source, s.kind),
                         ("rapoo:4824", "Rapoo V700DIY-98", 84, False, True, "rapoo", "keyboard"))

    def test_the_keyboard_on_its_cable_reads_under_the_same_icon(self):
        entries = keyboard_entries(pid=R.V700DIY_RECEIVER, prefix=b"kbrecv")
        out = self.poll(entries, first_vendor(entries, frame=BD_REFERENCE))
        self.assertEqual([(s.key, s.name, s.level, s.kind) for s in out],
                         [("rapoo:4824", "Rapoo V700DIY-98", 84, "keyboard")])

    def test_junk_before_the_keyboards_frame_is_skipped(self):
        variant = BD_REFERENCE.copy()
        variant[1] = 0xA0                     # the other 0xBD message, not a reading
        entries = keyboard_entries()
        cols = first_vendor(entries, frame=BD_REFERENCE)
        first = next(e for e in entries
                     if (e["usage_page"], e["usage"]) == (0xFF00, 0x0002))
        cols[first["path"]] = FakeCollection((variant, BD_REFERENCE))
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.charging) for s in out], [(84, False)])

    def test_both_devices_get_their_own_icon(self):
        recv = receiver_entries()
        kb = keyboard_entries()
        cols = dict(first_vendor(recv))
        cols.update(first_vendor(kb))
        out = self.poll(recv + kb, cols)
        self.assertEqual(sorted(s.key for s in out), ["rapoo:1413", "rapoo:4824"])
        kinds = {s.key: s.kind for s in out}
        self.assertEqual(kinds["rapoo:1413"], "mouse")
        self.assertEqual(kinds["rapoo:4824"], "keyboard")

    def test_the_mouse_on_its_cable_reads_under_the_same_icon(self):
        # 2026-10-03: while the VT7 charges its receiver id disappears and
        # 24ae:4613 shows up instead - one icon either way
        entries = receiver_entries(pid=R.VT7_CABLE, prefix=b"cable")
        out = self.poll(entries, first_vendor(entries))
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.kind) for s in out],
                         [("rapoo:1413", "Rapoo VT7 (Gen-2)", 100, False, "mouse")])

    def test_the_keyboard_receiver_reads_under_the_same_icon(self):
        # the keyboard wireless presents as 24ae:1924 (2026-10-03)
        entries = keyboard_entries(pid=R.V700DIY_RECEIVER, prefix=b"kbrecv")
        out = self.poll(entries, first_vendor(entries, frame=BD_REFERENCE))
        self.assertEqual([(s.key, s.name, s.kind) for s in out],
                         [("rapoo:4824", "Rapoo V700DIY-98", "keyboard")])

    def test_a_cable_reading_carries_the_charging_state(self):
        frame = REFERENCE.copy()
        frame[R.STATUS_INDEX] = 0x02
        frame[R.LEVEL_INDEX] = 0x50
        entries = receiver_entries(pid=R.VT7_CABLE, prefix=b"cable")
        out = self.poll(entries, first_vendor(entries, frame=frame))
        self.assertEqual([(s.level, s.charging) for s in out], [(80, True)])

    def test_one_icon_when_both_ids_answer_and_charging_wins(self):
        recv = receiver_entries()
        cable = receiver_entries(pid=R.VT7_CABLE, prefix=b"cable")
        charge = REFERENCE.copy()
        charge[R.STATUS_INDEX] = 0x02
        charge[R.LEVEL_INDEX] = 0x50
        cols = dict(first_vendor(recv))
        cols.update(first_vendor(cable, frame=charge))
        out = self.poll(recv + cable, cols)
        self.assertEqual([(s.key, s.level, s.charging, s.online) for s in out],
                         [("rapoo:1413", 80, True, True)])

    def test_the_pinned_ids(self):
        self.assertEqual((R.VT7_RECEIVER, R.VT7_CABLE, R.V700DIY_KEYBOARD,
                          R.V700DIY_RECEIVER), (0x1413, 0x4613, 0x4824, 0x1924))

    def test_junk_before_the_frame_is_skipped(self):
        junk = [0x06, 0x02, 0x01, 0xD0, 0x07, 0xD0, 0x07, 0x01, 0x64]
        invalid = REFERENCE.copy()
        invalid[R.STATUS_INDEX] = 0x00
        entries = receiver_entries()
        cols = first_vendor(entries)
        first = next(e for e in entries if (e["usage_page"], e["usage"]) == (0xFF00, 0x0002))
        cols[first["path"]] = FakeCollection((junk, invalid, REFERENCE))
        out = self.poll(entries, cols)
        self.assertEqual(out[0].level, 100)

    def test_a_vendor_collection_beyond_the_first_two_answers(self):
        entries = receiver_entries()
        third = next(e for e in entries if (e["usage_page"], e["usage"]) == (0xFF00, 0x000F))
        cols = {e["path"]: FakeCollection((REFERENCE,) if e["path"] == third["path"] else ())
                for e in entries}
        out = self.poll(entries, cols)
        self.assertEqual(out[0].level, 100)
        self.assertEqual(self.provider._chosen[R.VT7_RECEIVER], third["path"])

    def test_the_answered_collection_is_tried_first_next_poll(self):
        entries = receiver_entries()
        second = next(e for e in entries if (e["usage_page"], e["usage"]) == (0xFF00, 0x000E))
        cols = {e["path"]: FakeCollection((REFERENCE,) if e["path"] == second["path"] else ())
                for e in entries}
        self.poll(entries, cols)
        self.order.clear()
        self.poll(entries, cols)
        self.assertEqual(self.order[0], second["path"])

    def test_silence_keeps_the_last_level_greyed_out(self):
        entries = receiver_entries()
        self.poll(entries, first_vendor(entries))
        self.now[0] += 60
        out = self.poll(entries, {e["path"]: FakeCollection() for e in entries})
        self.assertEqual((out[0].level, out[0].online), (100, False))

    def test_silence_after_the_keep_window_drops_the_device(self):
        entries = receiver_entries()
        self.poll(entries, first_vendor(entries))
        self.now[0] += R.ASLEEP_KEEP + 1
        out = self.poll(entries, {e["path"]: FakeCollection() for e in entries})
        self.assertEqual(out, [])

    def test_no_reading_yet_gives_nothing(self):
        entries = receiver_entries()
        out = self.poll(entries, {e["path"]: FakeCollection() for e in entries})
        self.assertEqual(out, [])

    def test_no_device_gives_nothing(self):
        out = self.poll([], {})
        self.assertEqual(out, [])

    def test_a_quiet_device_is_not_fully_rescanned_every_poll(self):
        entries = receiver_entries()
        silent = {e["path"]: FakeCollection() for e in entries}
        self.poll(entries, silent)                 # first full scan, nothing heard
        self.order.clear()
        self.poll(entries, silent)
        self.assertEqual(len(self.order), 1)       # then only the first candidate

    def test_a_quiet_device_is_fully_rescanned_occasionally(self):
        entries = receiver_entries()
        silent = {e["path"]: FakeCollection() for e in entries}
        for _ in range(R.QUIET_RESCAN_EVERY):
            self.poll(entries, silent)
        opened = self.order[:]
        self.order.clear()
        self.poll(entries, silent)
        # a fresh full scan opens the vendor candidates again, not just one
        self.assertGreater(len(self.order), 1)
        self.assertGreater(len(opened), 1)

    # ---------------------------------------------------------------- order

    def test_candidates_put_the_vendor_collections_first_and_dedupe(self):
        cand = R.candidates(receiver_entries())
        usages = [(d["usage_page"], d["usage"]) for d in cand]
        self.assertEqual(usages[:2], [(0xFF00, 0x0002), (0xFF00, 0x000E)])
        self.assertEqual(len(set(usages)), len(usages))          # each usage listed once
        self.assertNotIn((0x0001, 0x0002), usages)               # the mouse is never opened
        self.assertNotIn((0x0001, 0x0006), usages)               # nor the keyboard role

    def test_keyboard_candidates_prefer_the_vendor_collections(self):
        cand = R.candidates(keyboard_entries())
        usages = [(d["usage_page"], d["usage"]) for d in cand]
        self.assertEqual(usages[:2], [(0xFF00, 0x0002), (0xFF00, 0x000E)])
        self.assertNotIn((0x0001, 0x0006), usages)


if __name__ == "__main__":
    unittest.main()
