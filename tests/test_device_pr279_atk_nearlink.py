"""Tests for providers/atk_nearlink.py. No hardware is needed.

The fake receiver is the ATK 8k Dongle Nearlink Version (373b:108c) the way the
reporter's diagnostics list its nine collections: the mouse on interface 0, five
vendor collections with usage pages 0xffb0..0xffb4 on interface 1, and the
consumer collections behind them. ATK HUB's device table binds 0xffb3 to this
product id, so the fake answers only there - the other vendor collections stay
silent, which is what the real receiver does.

The first reply in most tests is the reporter's own hardware answering the
power query: 91% on battery, the frame this provider was written against.

The same mouse on its cable enumerates as 373b:108b ("ATK X1 Nearlink 8k"),
with only two vendor collections (0xffb1 with a 13-byte output report and
0xffb2 with a 64-byte one). ATK HUB's device table lists that id with
connectType wired8K, and its controller keeps its constructor default report
id 4 for it; confirmed on hardware, the 0xffb2 collection answers report id 4
there, charging flag set while the cable supplies power.

hidlist.enumerate() and hid.device() are replaced: the real ones ask Windows for
collections of real devices.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import atk_nearlink as A  # noqa: E402

# the reporter's 373b:108c, nine collections as the diagnostics list them
SHAPE = [
    (0, 0x0001, 0x0002),
    (1, 0xFFB0, 0x0001),
    (1, 0xFFB1, 0x0001),
    (1, 0xFFB2, 0x0001),
    (1, 0xFFB3, 0x0001),
    (1, 0xFFB4, 0x0001),
    (2, 0x0001, 0x0006),
    (2, 0x000C, 0x0001),
    (3, 0x0001, 0x0080),
]

# the reporter's hardware answering the power query on the ffb3 collection:
# report id 5, requestCode 1, commandId 0x1a, battery 0x5b, charging flag 0
REAL_FRAME = bytes([0x05, 0x01, 0x00, 0x1A] + [0x00] * 4 + [0x5B, 0x00] + [0x00] * 54)


def reply(level, power=0, command=A.CMD_POWER, report_id=0x05, code=0x0001):
    """A 64-byte frame of the shape the receiver sends, report id first."""
    frame = [report_id, code & 0xFF, (code >> 8) & 0xFF, command]
    frame += [0x00] * 4 + [level, power] + [0x00] * 54
    assert len(frame) == A.WIRE_LEN
    return bytes(frame)


def asleep():
    """The receiver's answer while the mouse behind it sleeps: commandId 0xff."""
    return reply(0, command=0xFF)


class FakeCollection:
    def __init__(self, path, iface, page, usage, replies):
        self.info = {"path": path, "interface_number": iface, "usage_page": page,
                     "usage": usage}
        self.replies = list(replies)
        self.sent = []
        self.opened = 0

    def read(self, length):
        if not self.sent:
            return b""            # nothing asked yet: no stale data either
        return self.replies.pop(0) if self.replies else b""


class FakeBus:
    """The receiver, the way Windows presents it."""

    def __init__(self, vid, pid, replies=(), answering=A.CONTROL_USAGE, shape=SHAPE):
        self.vid = vid
        self.pid = pid
        self.cols = []
        for i, (iface, page, usage) in enumerate(shape):
            path = b"\\\\?\\hid#vid_%04x&pid_%04x#col%02d" % (vid, pid, i)
            mine = replies if (page, usage) == answering else ()
            self.cols.append(FakeCollection(path, iface, page, usage, mine))

    def enumerate(self, vid):
        if vid != self.vid:
            return []
        return [dict(c.info, product_id=self.pid, vendor_id=self.vid) for c in self.cols]

    def written(self):
        """(usage page, usage) of every collection a frame was written to."""
        return [(c.info["usage_page"], c.info["usage"]) for c in self.cols if c.sent]

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.c = next(c for c in bus.cols if c.info["path"] == path)
                self.c.opened += 1

            def write(self, frame):
                self.c.sent.append(list(frame))

            def read(self, length, timeout_ms=0):
                return self.c.read(length)

            def close(self):
                pass

        return FakeDevice


class AtkNearlinkTest(unittest.TestCase):
    def setUp(self):
        self._hid, self._hidlist, self._outlen = A.hid, A.hidlist, A._output_length
        self.one_receiver()

    def tearDown(self):
        A.hid, A.hidlist, A._output_length = self._hid, self._hidlist, self._outlen

    def one_receiver(self, vid=0x373B, pid=0x108C, replies=(), lengths=None, **kw):
        """lengths: {(usage_page, usage): OutputReportByteLength}. Unknown stays None,
        which is what Windows gives for a collection it will not classify."""
        lengths = dict(lengths or {})
        self.bus = FakeBus(vid, pid, replies, **kw)
        bus = self.bus

        def output_length(path):
            for c in bus.cols:
                if c.info["path"] == path:
                    return lengths.get((c.info["usage_page"], c.info["usage"]))
            return None

        A._output_length = output_length
        self.lengths = lengths
        A.hidlist = types.SimpleNamespace(enumerate=self.bus.enumerate)
        A.hid = types.SimpleNamespace(device=self.bus.device_class())
        return self.bus

    def test_the_8k_nearlink_receiver_is_read(self):
        self.one_receiver(replies=[REAL_FRAME])
        provider = A.AtkNearlinkProvider()
        found = provider.poll()
        self.assertEqual(1, len(found))
        d = found[0]
        self.assertEqual("atk_nearlink:108c", d.key)
        self.assertEqual("ATK X1 Ultimate (2.4 GHz)", d.name)
        self.assertEqual(91, d.level)
        self.assertFalse(d.charging)
        self.assertTrue(d.online)
        self.assertEqual("mouse", d.kind)
        diag = "\n".join(provider.diagnostics())
        self.assertIn("[ATK Nearlink] pid=373b:108c 'ATK X1 Ultimate (2.4 GHz)'", diag)
        self.assertIn("ffb3:0001", diag)
        self.assertIn("report=05", diag)

    def test_the_control_collection_is_the_one_that_gets_the_request(self):
        bus = self.one_receiver(replies=[reply(64)])
        A.AtkNearlinkProvider().poll()
        self.assertEqual([(0xFFB3, 0x0001)], bus.written(),
                         "the request must go to the collection ATK HUB binds 108c to")

    def test_the_request_is_the_64_byte_power_query(self):
        bus = self.one_receiver(replies=[reply(64)])
        A.AtkNearlinkProvider().poll()
        c = next(c for c in bus.cols if c.sent)
        sent, = c.sent
        self.assertEqual(A.make_request(1, 0x05), sent)
        self.assertEqual(64, len(sent))
        self.assertEqual(0x05, sent[0], "report id first, as on the wire")
        self.assertEqual(1, sent[1], "requestCode starts at 1")
        self.assertEqual(0, sent[2], "requestCode is little-endian")
        self.assertEqual(A.CMD_POWER, sent[3])
        self.assertEqual([0x00] * 60, sent[4:])

    def test_the_request_code_increments_with_every_query(self):
        self.one_receiver(replies=[reply(64), reply(65)])
        provider = A.AtkNearlinkProvider()
        provider.poll()
        provider.poll()
        c = next(c for c in self.bus.cols if c.sent)
        self.assertEqual(2, len(c.sent))
        self.assertEqual([0x05, 0x02, 0x00], c.sent[1][:3])

    def test_the_power_flag_means_charging(self):
        self.one_receiver(replies=[reply(55, 1)])
        found = A.AtkNearlinkProvider().poll()
        self.assertTrue(found[0].charging)

    def test_the_captured_frame_parses(self):
        self.assertEqual((91, False), A.parse_power(REAL_FRAME, 0x05))

    def test_a_level_above_100_is_refused(self):
        self.one_receiver(replies=[reply(200)])
        self.assertEqual([], A.AtkNearlinkProvider().poll())

    def test_a_sleeping_mouse_reply_is_not_an_answer(self):
        # commandId 0xff is the receiver saying the mouse is asleep, not a level
        self.one_receiver(replies=[asleep()])
        provider = A.AtkNearlinkProvider()
        self.assertEqual([], provider.poll())
        self.assertIn("no power reply", "\n".join(provider.diagnostics()))

    def test_an_event_frame_is_skipped_and_the_reply_after_it_is_used(self):
        self.one_receiver(replies=[asleep(), reply(41)])
        found = A.AtkNearlinkProvider().poll()
        self.assertEqual(41, found[0].level)

    def test_a_short_reply_is_ignored(self):
        bus = self.one_receiver()
        bus.cols[4].replies = [b"\x05\x01\x00"]
        self.assertEqual([], A.AtkNearlinkProvider().poll())

    def test_a_reply_on_the_wrong_report_id_is_refused(self):
        self.one_receiver(replies=[reply(64, report_id=0x04)])
        self.assertEqual([], A.AtkNearlinkProvider().poll())

    def test_a_device_that_is_not_in_the_table_is_not_queried(self):
        bus = self.one_receiver(vid=0x373B, pid=0x1234, replies=[reply(78)])
        self.assertEqual([], A.AtkNearlinkProvider().poll())
        self.assertEqual([], bus.written())

    def test_every_claimed_id_answers_the_same_request(self):
        for pid, (name, report_id, usage_page) in A.PIDS.items():
            with self.subTest(pid=pid):
                shape = [(1, usage_page, 0x0001), (1, 0x0001, 0x0080)]
                bus = self.one_receiver(pid=pid, replies=[reply(33, report_id=report_id)],
                                        answering=(usage_page, 0x0001), shape=shape)
                found = A.AtkNearlinkProvider().poll()
                self.assertEqual(1, len(found), "no reading for 373b:%04x" % pid)
                self.assertEqual(33, found[0].level)
                c = next(c for c in bus.cols if c.sent)
                self.assertEqual(A.make_request(1, report_id), c.sent[0])
                self.assertEqual(report_id, c.sent[0][0])

    def test_the_wired_id_is_read_on_the_ffb2_collection_with_report_4(self):
        # the wired mouse's only frame-capable vendor collection, answering the
        # driver's default report id 4 - the frame captured on a real cable:
        # requestCode 1, commandId 0x1a, battery 0x61 (97%), charging flag 1
        bus = self.one_receiver(pid=0x108B, replies=[reply(97, 1, report_id=0x04)],
                                answering=(0xFFB2, 0x0001))
        found = A.AtkNearlinkProvider().poll()
        self.assertEqual(1, len(found))
        self.assertEqual(("atk_nearlink:108b", "ATK X1 Ultimate (wired)", 97, True),
                         (found[0].key, found[0].name, found[0].level, found[0].charging))
        self.assertEqual([(0xFFB2, 0x0001)], bus.written())

    def test_the_control_collection_is_used_when_its_output_report_fits(self):
        self.one_receiver(replies=[reply(64)], lengths={(0xFFB3, 0x0001): 64})
        bus = self.bus
        A.AtkNearlinkProvider().poll()
        self.assertEqual([(0xFFB3, 0x0001)], bus.written())

    def test_a_control_collection_that_cannot_take_the_frame_is_not_queried(self):
        # the driver's table binds 108c to ffb3; when that collection declares an
        # output report too small for the 64-byte frame, the device is not
        # queried at all rather than the frame going to some other collection
        bus = self.one_receiver(replies=[reply(72)], answering=(0xFFB2, 0x0001),
                                lengths={(0xFFB0, 0x0001): 64, (0xFFB1, 0x0001): 64,
                                         (0xFFB3, 0x0001): 13, (0xFFB4, 0x0001): 64})
        provider = A.AtkNearlinkProvider()
        self.assertEqual([], provider.poll())
        self.assertEqual([], bus.written())
        diag = "\n".join(provider.diagnostics())
        self.assertIn("ffb3:0001 output=13 cannot take a 64-byte frame; not queried", diag)

    def test_a_missing_control_collection_means_no_query(self):
        # a receiver without the ffb3 collection is not this protocol's device:
        # no frame is written to any other collection
        shape = [(1, 0xFFB0, 0x0001), (1, 0xFFB2, 0x0001), (1, 0x0001, 0x0080)]
        bus = self.one_receiver(replies=[reply(48)], shape=shape)
        provider = A.AtkNearlinkProvider()
        self.assertEqual([], provider.poll())
        self.assertEqual([], bus.written())
        self.assertIn("no ffb3:0001 collection; not queried",
                      "\n".join(provider.diagnostics()))

    def test_an_unknown_output_length_never_disqualifies_a_collection(self):
        # on a system where the caps query says nothing, the usage rule still decides
        self.one_receiver(replies=[reply(64)])
        A.AtkNearlinkProvider().poll()
        self.assertEqual([(0xFFB3, 0x0001)], self.bus.written())

    def test_the_diagnostic_names_the_output_length(self):
        self.one_receiver(replies=[reply(64)], lengths={(0xFFB3, 0x0001): 64})
        provider = A.AtkNearlinkProvider()
        provider.poll()
        self.assertIn("ffb3:0001", "\n".join(provider.diagnostics()))

    # ------------------------------------------------- the idle mouse

    def test_a_mouse_that_stops_answering_keeps_its_reading_greyed(self):
        self.one_receiver(replies=[REAL_FRAME])
        provider = A.AtkNearlinkProvider()
        with mock.patch.object(A.time, "time", return_value=1000.0):
            first = provider.poll()
        self.assertEqual(1, len(first))
        self.assertTrue(first[0].online)
        self.assertFalse(first[0].wake_on_input, "a fresh reading is not a wake candidate")
        with mock.patch.object(A.time, "time", return_value=1060.0):
            kept = provider.poll()          # idle now: the receiver goes quiet
        self.assertEqual(1, len(kept), "the reading stays while the receiver is enumerated")
        self.assertFalse(kept[0].online, "a held reading is greyed out")
        self.assertEqual((91, False), (kept[0].level, kept[0].charging))
        self.assertEqual("atk_nearlink:108c", kept[0].key)
        self.assertTrue(kept[0].wake_on_input, "the kept reading can be woken by input")

    def test_the_kept_reading_does_not_expire_while_the_receiver_is_present(self):
        self.one_receiver(replies=[REAL_FRAME])
        provider = A.AtkNearlinkProvider()
        with mock.patch.object(A.time, "time", return_value=1000.0):
            self.assertEqual(1, len(provider.poll()))
        with mock.patch.object(A.time, "time", return_value=1000.0 + 3600.0):
            kept = provider.poll()
        self.assertEqual(1, len(kept), "the receiver is still plugged in")
        self.assertFalse(kept[0].online)
        self.assertEqual(91, kept[0].level)

    def test_an_unplugged_receiver_is_not_kept(self):
        self.one_receiver(replies=[REAL_FRAME])
        provider = A.AtkNearlinkProvider()
        with mock.patch.object(A.time, "time", return_value=1000.0):
            self.assertEqual(1, len(provider.poll()))
        self.bus.cols.clear()               # the receiver is pulled out of the machine
        with mock.patch.object(A.time, "time", return_value=1005.0):
            self.assertEqual([], provider.poll())

    def test_the_diagnostic_says_the_reading_is_kept(self):
        self.one_receiver(replies=[REAL_FRAME])
        provider = A.AtkNearlinkProvider()
        with mock.patch.object(A.time, "time", return_value=1000.0):
            provider.poll()
        with mock.patch.object(A.time, "time", return_value=1060.0):
            provider.poll()
        diag = "\n".join(provider.diagnostics())
        self.assertIn("did not answer", diag)
        self.assertIn("greyed", diag)


if __name__ == "__main__":
    unittest.main()
