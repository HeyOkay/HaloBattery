"""Tests for providers/inphic.py. No hardware is needed.

The frames are the ones INPHIC HUB's own hidapi driver consumes - the vendor
app the reporter linked in #160. It enumerates 1d57:fa65, opens the
collection with usage page 0x000A and usage 0x0000, and checks byte 0 for
the report id 0x03, byte 1 for the model code (0x95 / 0x90 / 0x93 / 0x99),
byte 2 for the command (0x40 is the battery one) and byte 3 for the
sub-command: 0x01 carries the level in byte 4, 0x02 makes it show 100 %,
and 0x03 starts its charging animation and keeps the last level. The
collection shape is the reporter's diagnostics dump (1d57:fa65: a keyboard
and a mouse collection, then the four status collections on interface 2,
with the vendor app reading 000a:0000 and keeping ff00:0001 for its writes).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import inphic as I  # noqa: E402

# frames as the vendor app's read buffer sees them (report id first; the rest
# of the 100-byte read is zeros)
LEVEL75 = [0x03, 0x95, 0x40, 0x01, 0x4B] + [0x00] * 95
LEVEL80 = [0x03, 0x90, 0x40, 0x01, 0x50] + [0x00] * 95
CHARGING = [0x03, 0x93, 0x40, 0x03, 0x00] + [0x00] * 95
FULL = [0x03, 0x99, 0x40, 0x02, 0x00] + [0x00] * 95


class FakeCollection:
    """replies: frames the collection answers with, in order."""

    def __init__(self, replies=()):
        self.replies = list(replies)
        self.opened = 0
        self.reads = 0

    def read(self):
        self.reads += 1
        if self.replies:
            f = self.replies.pop(0)
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

        def close(self):
            pass
    return FakeDevice


def receiver_entries(pid=I.PID, prefix=b"kp"):
    # the collections of the reporter's dongle in the #160 dump order
    shape = [(0, 0x0001, 0x0006),      # keyboard collection: never opened
             (1, 0x0001, 0x0002),      # the mouse: never opened
             (2, 0x0001, 0x0080),      # system control
             (2, 0x000C, 0x0001),      # consumer control
             (2, 0x000A, 0x0000),      # where the vendor app reads the frames
             (2, 0xFF00, 0x0001),      # the vendor page (the app's write handle)
             (3, 0x0001, 0x0006)]      # the second keyboard collection
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": prefix + b"-%d-%04x-%02x" % (i, p, u),
             "product_string": "Inphic KP 8K"}
            for i, p, u in shape]


STATUS_PATH = receiver_entries()[4]["path"]
VENDOR_PATH = receiver_entries()[5]["path"]
SYSTEM_PATH = receiver_entries()[2]["path"]
CONSUMER_PATH = receiver_entries()[3]["path"]
KEYBOARD_PATH = receiver_entries()[0]["path"]
MOUSE_PATH = receiver_entries()[1]["path"]


class ParseTest(unittest.TestCase):
    def test_the_level_frame_reads_75(self):
        self.assertEqual(I.parse_frame(LEVEL75), (75, False))

    def test_every_model_code_the_vendor_app_accepts_reads(self):
        for code in (0x95, 0x90, 0x93, 0x99):
            frame = [0x03, code, 0x40, 0x01, 0x32] + [0x00] * 95
            self.assertEqual(I.parse_frame(frame), (50, False), hex(code))

    def test_a_frame_without_the_report_id_reads_too(self):
        self.assertEqual(I.parse_frame(LEVEL75[1:]), (75, False))

    def test_the_charging_frame_keeps_the_level_and_shows_charging(self):
        # byte 4 is not a level on a 0x03 frame; the vendor app keeps its last
        self.assertEqual(I.parse_frame(CHARGING), (None, True))

    def test_the_full_frame_shows_100_and_charging(self):
        self.assertEqual(I.parse_frame(FULL), (100, True))

    def test_the_sibling_family_model_code_is_refused(self):
        # 0x55 is the older 8K receiver family's code (#69); only the four
        # models INPHIC HUB accepts are read here
        self.assertIsNone(I.parse_frame([0x03, 0x55, 0x40, 0x01, 0x4B] + [0x00] * 95))

    def test_another_command_is_refused(self):
        self.assertIsNone(I.parse_frame([0x03, 0x95, 0x41, 0x01, 0x4B] + [0x00] * 95))
        self.assertIsNone(I.parse_frame([0x03, 0x95, 0x10, 0x00, 0x00] + [0x00] * 95))

    def test_levels_outside_1_to_100_are_refused(self):
        self.assertIsNone(I.parse_frame([0x03, 0x95, 0x40, 0x01, 0x00] + [0x00] * 95))
        self.assertIsNone(I.parse_frame([0x03, 0x95, 0x40, 0x01, 0x65] + [0x00] * 95))
        self.assertEqual(I.parse_frame([0x03, 0x95, 0x40, 0x01, 0x01] + [0x00] * 95), (1, False))
        self.assertEqual(I.parse_frame([0x03, 0x95, 0x40, 0x01, 0x64] + [0x00] * 95), (100, False))

    def test_a_short_frame_is_refused(self):
        self.assertIsNone(I.parse_frame([0x03, 0x95, 0x40]))
        self.assertIsNone(I.parse_frame([0x95, 0x40]))
        self.assertIsNone(I.parse_frame([]))
        self.assertIsNone(I.parse_frame(None))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (I.hid, I.hidlist, I.time)
        self.now = [1000.0]
        I.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        self.provider = I.InphicProvider()
        self.order = []

    def tearDown(self):
        I.hid, I.hidlist, I.time = self._saved

    def poll(self, entries, cols):
        I.hid = types.SimpleNamespace(device=fake_device_class(cols, self.order))
        I.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()

    def cols(self, entries, replies=(LEVEL75,), answer=STATUS_PATH):
        return {e["path"]: FakeCollection(replies if e["path"] == answer else ())
                for e in entries}

    def test_the_announced_level_is_read_without_writing_anything(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        out = self.poll(entries, cols)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("inphic:fa65", "Inphic In9 Pro", 75, False, True,
                           "inphic", "mouse")])
        self.assertEqual(self.provider._chosen, STATUS_PATH)
        self.assertEqual(cols[STATUS_PATH].opened, 1)

    def test_the_charging_frame_then_a_level_frame_reads_75_charging_false(self):
        entries = receiver_entries()
        cols = self.cols(entries, replies=(CHARGING,))
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.charging, s.online) for s in out], [(None, True, True)])
        cols[STATUS_PATH].replies = [LEVEL75]
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.charging, s.online) for s in out], [(75, False, True)])

    def test_the_charging_frame_keeps_the_last_level(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        cols[STATUS_PATH].replies = [CHARGING]
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.charging, s.online) for s in out], [(75, True, True)])

    def test_the_full_frame_shows_100_while_charging(self):
        entries = receiver_entries()
        cols = self.cols(entries, replies=(FULL,))
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.charging, s.online) for s in out], [(100, True, True)])

    def test_the_collection_that_delivers_is_remembered_and_alone_is_enough(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.order.clear()
        out = self.poll(entries, cols)
        self.assertEqual([s.level for s in out], [75])
        self.assertEqual(self.order, [STATUS_PATH])          # only the known one is opened
        self.assertEqual(cols[VENDOR_PATH].opened, 0)

    def test_the_mouse_and_keyboard_collections_are_never_opened(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.assertEqual(cols[MOUSE_PATH].opened, 0)
        self.assertEqual(cols[KEYBOARD_PATH].opened, 0)                  # interface 0
        self.assertEqual(cols[receiver_entries()[6]["path"]].opened, 0)  # interface 3

    def test_the_first_delivering_collection_wins_and_stops_the_sweep(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.assertEqual(cols[SYSTEM_PATH].opened, 0)        # never reached
        self.assertEqual(cols[CONSUMER_PATH].opened, 0)

    def test_a_missed_poll_keeps_the_level_lit_for_a_while(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.now[0] += 60                                    # < ONLINE_FRESH
        for c in cols.values():
            c.replies = []
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.online) for s in out], [(75, True)])

    def test_a_longer_silence_greys_the_icon(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.now[0] += 150                                   # ONLINE_FRESH .. ASLEEP_KEEP
        for c in cols.values():
            c.replies = []
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.online) for s in out], [(75, False)])

    def test_silence_after_the_keep_window_drops_the_device(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.now[0] += I.ASLEEP_KEEP + 1
        for c in cols.values():
            c.replies = []
        self.assertEqual(self.poll(entries, cols), [])

    def test_a_gone_device_is_swept_for_again_on_all_collections(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.now[0] += I.ASLEEP_KEEP + 1
        cols[STATUS_PATH].replies = []
        cols[VENDOR_PATH].replies = [LEVEL80]      # it comes back on the vendor page
        out = self.poll(entries, cols)
        self.assertEqual([s.level for s in out], [80])
        self.assertEqual(self.provider._chosen, VENDOR_PATH)

    def test_a_heartbeat_without_a_level_refreshes_the_clock(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.now[0] += 80
        cols[STATUS_PATH].replies = [CHARGING]       # seen: heartbeat, no level
        self.poll(entries, cols)
        self.now[0] += 40                            # 120 s since the level, 40 since the frame
        cols[STATUS_PATH].replies = []
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.charging, s.online) for s in out], [(75, True, True)])

    def test_silence_on_the_first_poll_gives_nothing(self):
        entries = receiver_entries()
        cols = self.cols(entries, replies=())
        out = self.poll(entries, cols)
        self.assertEqual(out, [])
        self.assertTrue(any("no frame" in line for line in self.provider.diagnostics()))

    def test_no_dongle_gives_nothing(self):
        self.assertEqual(self.poll([], {}), [])

    def test_other_pids_are_ignored(self):
        entries = [dict(e, product_id=0xFA66) for e in receiver_entries()]
        cols = self.cols(entries)
        self.assertEqual(self.poll(entries, cols), [])
        self.assertTrue(all(not c.opened for c in cols.values()))

    # ---------------------------------------------------------------- order

    def test_candidates_prefer_the_vendor_apps_collection(self):
        cand = I.candidates(receiver_entries())
        self.assertEqual(cand[0]["path"], STATUS_PATH)
        self.assertEqual(cand[1]["path"], VENDOR_PATH)

    def test_candidates_never_include_the_mouse_or_keyboards(self):
        usages = [(d["usage_page"], d["usage"]) for d in I.candidates(receiver_entries())]
        self.assertNotIn((0x0001, 0x0002), usages)
        self.assertNotIn((0x0001, 0x0006), usages)
        self.assertEqual(len(usages), 4)


if __name__ == "__main__":
    unittest.main()
