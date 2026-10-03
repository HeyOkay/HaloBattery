"""Tests for the Corsair provider: the headset family (Void v2 / Virtuoso Max /
HS80 Max, from HeadsetControl) and the Dark Core RGB Pro SE dongle, whose
routed exchange ckb-next calls "bragi" and OpenLinkHub "slipstream". No
hardware is needed.

The frames are the reporter's, from the probe output in #56: a 64-byte routed
frame behind report id 0 - route 0x09 asks the mouse behind the receiver for
property 0x0F (battery level) - answered with `01 02 00 26 02 ...`: the mouse's
route, the get command, err 0 and the value little-endian in tenths of a
percent (550 = 55 %). The dongle sends other frames on the same channel
(device-list records like `00 12 00 ...`, receiver answers like `00 02 00 7f
1b ...`) and notices on its sibling collection; only the shape above is a
reading - parsing the others was the 0 % flash the 1.13.0 build showed.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import corsair as C  # noqa: E402

DONGLE = "CORSAIR DARK CORE RGB PRO SE Gaming Dongle"


def entry(pid, path, iface, page, usage, name=DONGLE):
    return {"product_id": pid, "interface_number": iface, "usage_page": page, "usage": usage,
            "path": path, "product_string": name, "serial_number": ""}


def bragi_reply(value=550, route=C.BRAGI_ROUTE_CHILD, cmd=C.BRAGI_CMD_GET, err=0x00,
                report_id=False, size=C.BRAGI_MSG_SIZE):
    """A reply frame of the shape the dongle sends; value is in tenths of a percent."""
    payload = bytearray(size)
    payload[0] = route
    payload[1] = cmd
    payload[2] = err
    payload[3] = value & 0xFF
    payload[4] = (value >> 8) & 0xFF
    return ([0x00] + list(payload)) if report_id else list(payload)


class FakeDongle:
    """One HID interface of the dongle. Each read returns the next queued reply;
    a single reply repeats."""

    def __init__(self, replies=None, silent=False):
        if replies is None:
            replies = [bragi_reply()]
        elif isinstance(replies, list) and replies and isinstance(replies[0], list):
            replies = list(replies)                  # a queue of reply frames
        else:
            replies = [replies]                      # one reply frame
        self.replies = [list(r) for r in replies]
        self.silent = silent
        self.writes = []
        self.reads = 0

    def read(self, size, timeout_ms):
        self.reads += 1
        if self.silent or not self.replies:
            return []
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class FakeBus:
    def __init__(self, dongles):
        self.dongles = dongles            # {path: FakeDongle}
        self.opened = []

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.dongles:
                    raise OSError("cannot open")
                self.path = path
                self.dongle = bus.dongles[path]
                bus.opened.append(path)

            def write(self, data):
                self.dongle.writes.append(bytes(data))
                return len(data)

            def read(self, size, timeout_ms):
                return self.dongle.read(size, timeout_ms)

            def close(self):
                pass

        return FakeDevice


class BragiParseTest(unittest.TestCase):
    def test_the_request_is_the_routed_get_behind_report_id_0(self):
        req = C.bragi_request()
        self.assertEqual(len(req), C.BRAGI_MSG_SIZE + 1)
        self.assertEqual((req[0], req[1], req[2], req[3]), (0x00, 0x09, 0x02, 0x0F))

    def test_the_reporter_s_answer_parses_to_55_percent(self):
        # the frame from the probe output in #56: 01 02 00 26 02 ...
        self.assertEqual(C.parse_bragi(bragi_reply(550)), 55)

    def test_the_value_is_tenths_of_a_percent(self):
        self.assertEqual(C.parse_bragi(bragi_reply(765)), 76)
        self.assertEqual(C.parse_bragi(bragi_reply(1000)), 100)
        self.assertEqual(C.parse_bragi(bragi_reply(9)), 0)

    def test_a_value_past_the_max_is_refused(self):
        self.assertIsNone(C.parse_bragi(bragi_reply(1001)))

    def test_zero_is_not_a_reading(self):
        # the dongle's notices carry value 0 - the 1.13.0 build showed that as 0 %
        self.assertIsNone(C.parse_bragi(bragi_reply(0)))

    def test_the_receiver_s_own_answers_are_not_the_mouse_s(self):
        # route 0x00 is the receiver itself: `00 02 00 7f 1b ...` is its product id
        self.assertIsNone(C.parse_bragi(bragi_reply(550, route=0x00)))

    def test_a_record_frame_is_not_a_reading(self):
        # `00 12 00 04 ...` (the device list) rides the same channel
        self.assertIsNone(C.parse_bragi(bragi_reply(550, cmd=0x12)))

    def test_an_error_answer_is_not_a_reading(self):
        for err in (1, 2, 3):
            self.assertIsNone(C.parse_bragi(bragi_reply(550, err=err)))

    def test_a_short_or_empty_reply_is_refused(self):
        self.assertIsNone(C.parse_bragi([]))
        self.assertIsNone(C.parse_bragi(None))
        self.assertIsNone(C.parse_bragi([0x01, 0x02, 0x00, 0x26]))

    def test_the_report_id_is_tolerated_with_or_without(self):
        a = C.parse_bragi(bragi_reply(550, report_id=True))
        b = C.parse_bragi(bragi_reply(550, report_id=False))
        self.assertEqual(a, b)
        self.assertEqual(a, 55)


class BragiPollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (C.hid, C.hidlist)

    def tearDown(self):
        C.hid, C.hidlist = self._saved

    def poll(self, entries, dongles):
        bus = FakeBus(dongles)
        C.hid = types.SimpleNamespace(device=bus.device_class())
        C.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return C.CorsairProvider().poll(), bus

    def test_the_dongle_shows_the_reporter_s_55_percent(self):
        out, bus = self.poll(
            [entry(0x1B7F, b"dongle-ff420001", 1, 0xFF42, 0x0001)],
            {b"dongle-ff420001": FakeDongle(bragi_reply(550))})
        self.assertEqual(len(out), 1)
        st = out[0]
        self.assertEqual((st.key, st.level, st.approx, st.kind),
                         ("corsair:1b7f", 55, "", "mouse"))
        self.assertFalse(st.charging)
        self.assertTrue(st.online)
        self.assertEqual(bus.opened, [b"dongle-ff420001"])

    def test_the_question_goes_on_the_wire_as_the_probe_sent_it(self):
        _, bus = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)], {b"d": FakeDongle()})
        w = bus.dongles[b"d"].writes[0]
        self.assertEqual((w[0], w[1], w[2], w[3], len(w)),
                         (0x00, 0x09, 0x02, 0x0F, C.BRAGI_MSG_SIZE + 1))

    def test_the_usage_1_collection_is_asked_first(self):
        # the 0x0002 collection is the dongle's notice channel and stays untouched
        e = [entry(0x1B7F, b"notice", 2, 0xFF42, 0x0002),
             entry(0x1B7F, b"answers", 1, 0xFF42, 0x0001)]
        out, bus = self.poll(e, {b"answers": FakeDongle(bragi_reply(550)),
                                 b"notice": FakeDongle(bragi_reply(0))})
        self.assertEqual(bus.opened, [b"answers"])
        self.assertEqual(out[0].level, 55)

    def test_the_iface_1_collection_is_tried_first(self):
        # with no usage 0x0001 collection, the interface-1 one goes first
        e = [entry(0x1B7F, b"c0002", 2, 0xFF42, 0x0002),
             entry(0x1B7F, b"c0001", 1, 0xFF42, 0x0003)]
        out, bus = self.poll(e, {b"c0001": FakeDongle(bragi_reply(765)),
                                 b"c0002": FakeDongle(bragi_reply(0))})
        self.assertEqual(bus.opened, [b"c0001"])
        self.assertEqual(out[0].level, 76)

    def test_both_vendor_collections_are_tried_and_the_first_that_answers_wins(self):
        e = [entry(0x1B7F, b"c0001", 1, 0xFF42, 0x0001),
             entry(0x1B7F, b"c0002", 2, 0xFF42, 0x0002)]
        silent = FakeDongle(silent=True)
        out, bus = self.poll(e, {b"c0001": silent, b"c0002": FakeDongle(bragi_reply(765))})
        self.assertEqual(out[0].level, 76)
        self.assertEqual(bus.opened, [b"c0001", b"c0002"])

    def test_a_sleeping_or_unanswering_dongle_gives_no_icon(self):
        out, _ = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)],
                           {b"d": FakeDongle(silent=True)})
        self.assertEqual(out, [])

    def test_a_notice_frame_is_not_a_level(self):
        # `01 02 00 00 ...` is the dongle answering something that is not the
        # battery; the 1.13.0 build showed it as 0 % briefly
        out, _ = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)],
                           {b"d": FakeDongle(bragi_reply(0))})
        self.assertEqual(out, [])

    def test_a_receiver_record_before_the_answer_is_ignored(self):
        # the device-list records (`00 12 00 ...`) ride the same channel
        out, _ = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)],
                           {b"d": FakeDongle([bragi_reply(550, route=0x00, cmd=0x12),
                                              bragi_reply(550)])})
        self.assertEqual(out[0].level, 55)

    def test_the_two_asks_must_agree(self):
        # the answer is not property-attributed; a foreign answer (another app
        # asking the dongle for something else) would otherwise pass as a level
        # (review by @ahmedkhursheed23)
        class TwoAnswers(FakeDongle):
            """The first ask is answered with 55 %, the second with 90 %."""

            def read(self, size, timeout_ms):
                self.reads += 1
                return list(bragi_reply(550 if len(self.writes) < 2 else 900))

        dongle = TwoAnswers()
        out, _ = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)], {b"d": dongle})
        self.assertEqual(out, [])
        self.assertEqual(len(dongle.writes), 2)   # both asks went out

    def test_a_busy_channel_does_not_end_the_wait(self):
        # 12 traffic frames used to eat the old 10-read budget before the answer
        traffic = [bragi_reply(0, route=0x00, cmd=0x12)] * 12
        out, _ = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)],
                           {b"d": FakeDongle(traffic + [bragi_reply(550)])})
        self.assertEqual(out[0].level, 55)

    def test_the_answer_window_is_a_time_window(self):
        # frames arriving early used to eat a read-count budget; the window is
        # the clock now (review by @ahmedkhursheed23) - here the clock passes
        # it after a single read
        record = bragi_reply(0, route=0x00, cmd=0x12)
        st = {"t": 0.0}

        def clock():
            st["t"] += 2.0
            return st["t"]

        with mock.patch.object(C.time, "monotonic", clock):
            out, bus = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)],
                                 {b"d": FakeDongle(record)})
        self.assertEqual(out, [])
        # 4 drain reads + one read inside the window; a read-count budget would
        # have kept reading up to the attempt cap
        self.assertEqual(bus.dongles[b"d"].reads, 5)
        self.assertEqual(len(bus.dongles[b"d"].writes), 1)

    def test_a_spent_budget_skips_the_remaining_collections(self):
        # the per-dongle budget bounds the try-every-collection fallback
        # (review by @ahmedkhursheed23); the clock jumps so it is spent at once
        e = [entry(0x1B7F, b"c1", 1, 0x0001, 0x0002),
             entry(0x1B7F, b"c2", 2, 0x0001, 0x0003)]
        st = {"t": 0.0}

        def clock():
            st["t"] += 10.0
            return st["t"]

        with mock.patch.object(C.time, "monotonic", clock):
            out, bus = self.poll(e, {b"c1": FakeDongle(silent=True),
                                     b"c2": FakeDongle(bragi_reply(765))})
        self.assertEqual(out, [])
        self.assertEqual(bus.opened, [])
        self.assertEqual(bus.dongles[b"c1"].reads, 0)

    def test_a_non_ff42_collection_is_not_substituted_when_ff42_exists(self):
        # a silent ff42 collection must give no reading, not a try on the keyboard page
        e = [entry(0x1B7F, b"vendor", 1, 0xFF42, 0x0001),
             entry(0x1B7F, b"kbdpage", 0, 0x0001, 0x0002)]
        out, bus = self.poll(e, {b"vendor": FakeDongle(silent=True),
                                 b"kbdpage": FakeDongle(bragi_reply(550))})
        self.assertEqual(out, [])
        self.assertEqual(bus.opened, [b"vendor"])

    def test_an_empty_dump_entry_set_is_tried_when_ff42_is_missing(self):
        # the doc's collection comes from the reporter's dump; if a firmware ever
        # reports another page, trying the remaining collections is one read window
        out, bus = self.poll([entry(0x1B7F, b"d", 0, 0x0001, 0x0002)],
                             {b"d": FakeDongle(bragi_reply(765))})
        self.assertEqual(out[0].level, 76)
        self.assertEqual(bus.opened, [b"d"])

    def test_the_headset_family_is_untouched(self):
        self.assertEqual(C.PIDS, {0x2A08: "Corsair Void v2 Wireless",
                                  0x2A02: "Corsair Virtuoso Max Wireless",
                                  0x0A97: "Corsair HS80 Max Wireless"})
        self.assertEqual(C.BRAGI_PIDS, {0x1B7F: "Corsair Dark Core RGB Pro SE"})
        self.assertNotIn(0x1B7F, C.PIDS)


if __name__ == "__main__":
    unittest.main()
