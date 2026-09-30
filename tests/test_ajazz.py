"""Tests for providers/ajazz.py. No hardware is needed.

The frames are the ones the reporter's AJAZZ AJ179 V2 MAX receiver answered
in the USBPcap captures in issue #74, recorded while AJAZZ's own app
(AJAZZ Driver 1.0.7.3) talked to it: `10 00 01 0b 4e 36 32 35 00 00 11 01 00
4b 01 ... 49` = 75 % after a charge (13 % before, `0d` instead of `4b`), and
the receiver's own announcements `c0 01 4b` / `c0 01 4a`. The collection
shape is the reporter's diagnostics dump (249a:5c2f: the mouse collection on
interface 0, consumer control and a keyboard collection on interface 1, and
the 33-byte vendor channel mi_02 on interface 2, whose usage the dump does
not carry).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import ajazz as A  # noqa: E402

# the exact 32-byte replies of the #74 captures
CAPTURED75 = ([0x10, 0x00, 0x01, 0x0B, 0x4E, 0x36, 0x32, 0x35, 0x00, 0x00,
               0x11, 0x01, 0x00, 0x4B, 0x01] + [0x00] * 16 + [0x49])
CAPTURED13 = ([0x10, 0x00, 0x01, 0x0B, 0x4E, 0x36, 0x32, 0x35, 0x00, 0x00,
               0x11, 0x01, 0x00, 0x0D, 0x01] + [0x00] * 16 + [0x0B])
ANNOUNCED75 = [0xC0, 0x01, 0x4B] + [0x00] * 29
ANNOUNCED74 = [0xC0, 0x01, 0x4A] + [0x00] * 29


def info(level):
    """A well-formed info reply with the right checksum, for boundary tests."""
    f = [0x10, 0x00, 0x01, 0x0B, 0x4E, 0x36, 0x32, 0x35, 0x00, 0x00,
         0x11, 0x01, 0x00, level, 0x01]
    f += [0x00] * (31 - len(f))
    f.append(sum(f[4:31]) & 0xFF)
    return f


class FakeCollection:
    """replies: frames the collection answers with, in order; accept_write:
    False means the collection has no output report (hidapi returns -1)."""

    def __init__(self, replies=(), accept_write=True):
        self.replies = list(replies)
        self.accept_write = accept_write
        self.sent = []
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

        def write(self, data):
            self.c.sent.append(list(data))
            return len(data) if self.c.accept_write else -1

        def read(self, n, timeout):
            return self.c.read()

        def close(self):
            pass
    return FakeDevice


def receiver_entries(pid=A.PID, prefix=b"rx"):
    # the collections of the reporter's receiver in the #74 dump order; mi_02
    # carries no usage there, so the fake has none either
    shape = [(0, 0x0001, 0x0002),      # the mouse: never opened
             (1, 0x000C, 0x0001),      # consumer control: opens, no output report
             (2, 0x0000, 0x0000),      # the vendor channel (mi_02)
             (1, 0x0001, 0x0006)]      # the keyboard collection
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": prefix + b"-%d-%04x-%02x" % (i, p, u),
             "product_string": "Wireless-Receiver"}
            for i, p, u in shape]


VENDOR_PATH = receiver_entries()[2]["path"]
CONSUMER_PATH = receiver_entries()[1]["path"]


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist, A.time)
        self.now = [1000.0]
        A.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        self.provider = A.AjazzProvider()
        self.order = []

    def tearDown(self):
        A.hid, A.hidlist, A.time = self._saved

    def poll(self, entries, cols):
        A.hid = types.SimpleNamespace(device=fake_device_class(cols, self.order))
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()

    def cols(self, entries, replies=(CAPTURED75,), answer=VENDOR_PATH):
        # the vendor channel answers and takes the write; the others refuse it
        return {e["path"]: FakeCollection(replies if e["path"] == answer else (),
                                          accept_write=(e["path"] == answer))
                for e in entries}

    # ---------------------------------------------------------------- parsing

    def test_the_captured_after_charge_reply_reads_75(self):
        self.assertEqual(A.parse_reply(CAPTURED75), (75, "info"))

    def test_the_captured_before_charge_reply_reads_13(self):
        self.assertEqual(A.parse_reply(CAPTURED13), (13, "info"))

    def test_the_captured_announcements_read_75_and_74(self):
        self.assertEqual(A.parse_reply(ANNOUNCED75), (75, "announcement"))
        self.assertEqual(A.parse_reply(ANNOUNCED74), (74, "announcement"))

    def test_a_windows_read_keeps_the_report_id(self):
        self.assertEqual(A.parse_reply([0x00] + CAPTURED75), (75, "info"))

    def test_an_announcement_needs_the_live_flag(self):
        self.assertIsNone(A.parse_reply([0xC0, 0x00, 0x4B] + [0x00] * 29))

    def test_a_changed_level_without_the_checksum_is_refused(self):
        f = info(75)
        f[13] = 0x50                     # 80, but the checksum still says 75
        self.assertIsNone(A.parse_reply(f))

    def test_a_level_above_100_is_refused(self):
        self.assertIsNone(A.parse_reply(info(101)))

    def test_a_zero_level_is_refused(self):
        self.assertIsNone(A.parse_reply(info(0)))
        self.assertIsNone(A.parse_reply([0xC0, 0x01, 0x00] + [0x00] * 29))

    def test_another_command_is_not_the_info_block(self):
        cmd20 = [0x20, 0x00, 0x01, 0x02, 0x19, 0x03] + [0x00] * 25 + [0x1C]
        self.assertIsNone(A.parse_reply(cmd20))

    def test_a_short_frame_is_refused(self):
        self.assertIsNone(A.parse_reply([0xBB, 0xB0, 0x42, 0x20, 0x03, 0x01, 0x64]))

    # ---------------------------------------------------------------- polling

    def test_the_captured_level_is_read_and_the_apps_own_read_is_sent(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        out = self.poll(entries, cols)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("ajazz:5c2f", "AJAZZ AJ179 V2 MAX", 75, False, True,
                           "ajazz", "mouse")])
        self.assertEqual(cols[VENDOR_PATH].sent, [list(A.make_request())])
        self.assertEqual(list(A.make_request())[:2], [0x00, 0x10])   # report id + the app's read
        self.assertEqual(len(A.make_request()), A.REPORT_SIZE + 1)

    def test_the_collection_that_takes_the_write_is_remembered(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.assertEqual(self.provider._chosen, VENDOR_PATH)
        self.order.clear()
        self.poll(entries, cols)
        self.assertEqual(self.order[0], VENDOR_PATH)
        self.assertEqual(cols[CONSUMER_PATH].sent, [list(A.make_request())])  # tried once, refused
        self.assertEqual(cols[CONSUMER_PATH].opened, 1)                       # never opened again

    def test_an_announcement_alone_is_enough(self):
        entries = receiver_entries()
        cols = self.cols(entries, replies=(ANNOUNCED75,))
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.online) for s in out], [(75, True)])

    def test_junk_before_the_answer_is_skipped(self):
        entries = receiver_entries()
        cols = self.cols(entries, replies=(ANNOUNCED74, [0xC0, 0x00, 0x4B] + [0x00] * 29,
                                           CAPTURED75))
        out = self.poll(entries, cols)
        self.assertEqual([s.level for s in out], [74])   # the first valid frame wins

    def test_silence_keeps_the_last_level_greyed_out(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.now[0] += 60
        for c in cols.values():
            c.replies = []
            c.sent.clear()
        out = self.poll(entries, cols)
        self.assertEqual([(s.level, s.online) for s in out], [(75, False)])

    def test_silence_after_the_keep_window_drops_the_device(self):
        entries = receiver_entries()
        cols = self.cols(entries)
        self.poll(entries, cols)
        self.now[0] += A.ASLEEP_KEEP + 1
        for c in cols.values():
            c.replies = []
        self.assertEqual(self.poll(entries, cols), [])

    def test_silence_on_the_first_poll_gives_nothing(self):
        entries = receiver_entries()
        cols = self.cols(entries, replies=())
        out = self.poll(entries, cols)
        self.assertEqual(out, [])
        self.assertEqual(self.provider._chosen, VENDOR_PATH)   # the write was taken
        self.assertTrue(any("silence" in line for line in self.provider.diagnostics()))

    def test_no_receiver_gives_nothing(self):
        self.assertEqual(self.poll([], {}), [])

    def test_other_pids_are_ignored(self):
        entries = [dict(e, product_id=0x5C30) for e in receiver_entries()]
        cols = self.cols(entries)
        self.assertEqual(self.poll(entries, cols), [])
        self.assertTrue(all(not c.opened for c in cols.values()))

    # ---------------------------------------------------------------- order

    def test_candidates_never_include_the_mouse_collection(self):
        cand = A.candidates(receiver_entries())
        usages = [(d["usage_page"], d["usage"]) for d in cand]
        self.assertNotIn((0x0001, 0x0002), usages)
        self.assertEqual(len({d["path"] for d in cand}), len(cand))

    def test_vendor_pages_rank_first(self):
        entries = receiver_entries() + [{"product_id": A.PID, "interface_number": 1,
                                         "usage_page": 0xFF00, "usage": 0x0002,
                                         "path": b"rx-ff00", "product_string": "Wireless-Receiver"}]
        self.assertEqual(A.candidates(entries)[0]["path"], b"rx-ff00")


if __name__ == "__main__":
    unittest.main()
