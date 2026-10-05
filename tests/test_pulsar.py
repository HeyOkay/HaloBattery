"""Tests for providers/pulsar.py. No hardware is needed.

The fake dongle is the VXE R1 Pro Max 1 kHz receiver from issue #87: 3554:f58a,
whose report lists two collections on interface 0 and 2 and six on interface 1,
with the ATK/VXE panel of the OpenMouse project (@openmouse/protocol, drivers/atk)
opening the one with usage page 0xFF02 and usage 0x0002. That panel reads the battery with command
0x04 and takes the level, the flag and the millivolts from the same three places
providers/pulsar.py does, so the fake answers only on that collection - the first
interface-1 collection stays silent, which is what the real dongle does.

The same mouse on its cable (3554:f58c) is the reporter's second report in that issue: it
lists the same eight collections, so the same rule picks ff02:0002 and the same frame
applies.

The Hitscan Hyperlight (3770:0200 on its receiver, 3770:0100 on the cable, #105) is
faked with its own collection list and the frames sopparus/hitscan-battery captured
from the vendor application; the same rule picks its ff02:0002 collection.

hidlist.enumerate() and hid.device() are replaced: the real ones ask Windows for
collections of real devices.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import pulsar as P  # noqa: E402

# (interface, usage page, usage) exactly as the reporter's diagnostics.txt lists them
SHAPE = [
    (0, 0x0001, 0x0006),
    (1, 0xFF05, 0x0000),
    (1, 0xFF03, 0x0000),
    (1, 0x000C, 0x0001),
    (1, 0x0001, 0x0080),
    (1, 0xFF02, 0x0002),
    (1, 0xFF04, 0x0002),
    (2, 0x0001, 0x0002),
]

# the Hitscan Hyperlight receiver (3770:0200) as #105's diagnostics list its collections
HITSCAN_SHAPE = [
    (1, 0xFF04, 0x0002),
    (1, 0xFF05, 0x0000),
    (1, 0xFF03, 0x0000),
    (1, 0x000C, 0x0001),
    (1, 0x0001, 0x0080),
    (1, 0xFF02, 0x0002),
    (2, 0x0001, 0x0002),
]

# the two frames the Pulsar X2 CrazyLight Mini answered on its reporter's hardware
# (#164, both checksums valid): 3710:5406 the dongle, 3710:3414 the cable
CRAZYLIGHT_DONGLE = bytes.fromhex("08 04 00 00 00 02 14 00 0d ea 00 00 00 00 00 00 3c")
CRAZYLIGHT_CABLE = bytes.fromhex("08 04 00 00 00 02 19 01 0e 52 00 00 00 00 00 00 cd")


def reply(level, power=0, mv=0, command=P.CMD_POWER, header=P.PAYLOAD_HEADER,
          break_checksum=False):
    """A 17-byte frame of the shape the dongle sends."""
    frame = [header, command] + [0x00] * 4 + [level, power] + list(mv.to_bytes(2, "big"))
    frame += [0x00] * 6
    assert len(frame) == P.PAYLOAD_LEN - 1
    ck = P.checksum(frame)
    if break_checksum:
        ck ^= 0xFF
    return bytes(frame + [ck])


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
    """The dongle, the way Windows presents it."""

    def __init__(self, vid, pid, replies=(), answering=(0xFF02, 0x0002), shape=SHAPE):
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


class PulsarTest(unittest.TestCase):
    def setUp(self):
        self._hid, self._hidlist, self._outlen = P.hid, P.hidlist, P.output_length
        self.bus = self.one_receiver()
        P.hidlist = types.SimpleNamespace(enumerate=self.bus.enumerate)
        P.hid = types.SimpleNamespace(device=self.bus.device_class())

    def tearDown(self):
        P.hid, P.hidlist, P.output_length = self._hid, self._hidlist, self._outlen

    def one_receiver(self, vid=0x3554, pid=0xF58A, replies=(), lengths=None, **kw):
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

        P.output_length = output_length
        self.lengths = lengths
        P.hidlist = types.SimpleNamespace(enumerate=self.bus.enumerate)
        P.hid = types.SimpleNamespace(device=self.bus.device_class())
        return self.bus

    def test_the_r1_pro_max_receiver_is_read(self):
        bus = self.one_receiver(replies=[reply(78, 0, 3900)])
        provider = P.PulsarProvider()
        found = provider.poll()
        self.assertEqual(1, len(found))
        d = found[0]
        self.assertEqual("pulsar:3554f58a", d.key)
        self.assertEqual("VXE R1 Pro Max (2.4 GHz)", d.name)
        self.assertEqual(78, d.level)
        self.assertFalse(d.charging)
        self.assertTrue(d.online)
        self.assertEqual("mouse", d.kind)
        diag = "\n".join(provider.diagnostics())
        self.assertIn("[Pulsar] pid=3554:f58a 'VXE R1 Pro Max (2.4 GHz)'", diag)
        self.assertIn("3900 mV", diag)

    def test_the_r1_pro_max_on_its_cable_is_read(self):
        # the reporter's second report: the wired mouse is 3554:f58c, product string
        # 'VXE R1 PRO MAX', with the same eight collections as the receiver
        bus = self.one_receiver(vid=0x3554, pid=0xF58C, replies=[reply(54, 1, 4020)])
        found = P.PulsarProvider().poll()
        self.assertEqual(1, len(found))
        d = found[0]
        self.assertEqual("pulsar:3554f58c", d.key)
        self.assertEqual("VXE R1 Pro Max (wired)", d.name)
        self.assertEqual(54, d.level)
        self.assertTrue(d.charging)
        self.assertEqual([(0xFF02, 0x0002)], bus.written())

    def test_the_vendor_control_collection_is_the_one_that_gets_the_request(self):
        bus = self.one_receiver(replies=[reply(64)])
        P.PulsarProvider().poll()
        self.assertEqual([(0xFF02, 0x0002)], bus.written(),
                         "the request must go to the collection the OpenMouse ATK/VXE panel opens")

    def test_the_request_is_the_17_byte_power_query(self):
        bus = self.one_receiver(replies=[reply(64)])
        P.PulsarProvider().poll()
        c = next(c for c in bus.cols if c.sent)
        sent, = c.sent
        self.assertEqual(P.make_request(), sent)
        self.assertEqual(17, len(sent))
        self.assertEqual(P.PAYLOAD_HEADER, sent[0])
        self.assertEqual(P.CMD_POWER, sent[1])
        self.assertEqual(P.CHECKSUM_BASE, sum(sent) % 256)

    def test_the_power_flag_means_charging(self):
        self.one_receiver(replies=[reply(55, 1)])
        found = P.PulsarProvider().poll()
        self.assertTrue(found[0].charging)

    def test_a_wrong_checksum_is_refused(self):
        self.one_receiver(replies=[reply(78, 0, 3900, break_checksum=True)])
        provider = P.PulsarProvider()
        self.assertEqual([], provider.poll())
        self.assertIn("no power reply", "\n".join(provider.diagnostics()))

    def test_a_level_above_100_is_refused(self):
        self.one_receiver(replies=[reply(200)])
        self.assertEqual([], P.PulsarProvider().poll())

    def test_an_event_frame_is_skipped_and_the_reply_after_it_is_used(self):
        # the same endpoint pushes events (command 0x0a); they are not a battery reply
        self.one_receiver(replies=[reply(78, command=0x0A),
                                   reply(41),
                                   reply(78, 0, 3900)])
        found = P.PulsarProvider().poll()
        self.assertEqual(41, found[0].level)

    def test_a_short_reply_is_ignored(self):
        bus = self.one_receiver()
        bus.cols[5].replies = [b"\x08\x04\x00"]
        self.assertEqual([], P.PulsarProvider().poll())

    def test_a_sleeping_dongle_gives_no_device(self):
        self.one_receiver(replies=[])
        self.assertEqual([], P.PulsarProvider().poll())

    def test_the_captured_crazylight_frames_parse(self):
        self.assertEqual((20, False), P.parse_power(CRAZYLIGHT_DONGLE))
        self.assertEqual(3562, P.voltage_mv(CRAZYLIGHT_DONGLE))
        self.assertEqual((25, True), P.parse_power(CRAZYLIGHT_CABLE))
        self.assertEqual(3666, P.voltage_mv(CRAZYLIGHT_CABLE))

    def test_the_crazylight_mini_is_read_on_both_transports(self):
        self.one_receiver(vid=0x3710, pid=0x5406, replies=[CRAZYLIGHT_DONGLE])
        found = P.PulsarProvider().poll()
        self.assertEqual(1, len(found))
        self.assertEqual(("pulsar:37105406", "Pulsar X2 CrazyLight Mini (2.4 GHz)", 20, False),
                         (found[0].key, found[0].name, found[0].level, found[0].charging))
        self.one_receiver(vid=0x3710, pid=0x3414, replies=[CRAZYLIGHT_CABLE])
        found = P.PulsarProvider().poll()
        self.assertEqual(("pulsar:37103414", "Pulsar X2 CrazyLight Mini (wired)", 25, True),
                         (found[0].key, found[0].name, found[0].level, found[0].charging))

    def test_every_claimed_id_answers_the_same_request(self):
        for vid, pids in P.PIDS.items():
            for pid in pids:
                with self.subTest(vid=vid, pid=pid):
                    bus = FakeBus(vid, pid, [reply(33)])
                    P.output_length = lambda path: None
                    P.hidlist = types.SimpleNamespace(enumerate=bus.enumerate)
                    P.hid = types.SimpleNamespace(device=bus.device_class())
                    found = P.PulsarProvider().poll()
                    self.assertEqual(1, len(found), "no reading for %04x:%04x" % (vid, pid))
                    self.assertEqual(33, found[0].level)
                    c = next(c for c in bus.cols if c.sent)
                    self.assertEqual(P.make_request(), c.sent[0])

    def test_the_control_collection_is_used_when_its_output_report_fits(self):
        bus = self.one_receiver(replies=[reply(64)], lengths={(0xFF02, 0x0002): 17})
        P.PulsarProvider().poll()
        self.assertEqual([(0xFF02, 0x0002)], bus.written())

    def test_a_collection_that_cannot_take_the_frame_is_skipped(self):
        # Windows refuses a 17-byte write to a collection that has no such report:
        # the keepalive report on FF02 is 64 bytes, so the frame goes to FF04 instead
        bus = self.one_receiver(replies=[reply(72)], shape=[(1, 0xFF05, 0x0000),
                                                            (1, 0xFF02, 0x0002),
                                                            (1, 0xFF04, 0x0002)],
                                answering=(0xFF04, 0x0002),
                                lengths={(0xFF05, 0x0000): 0, (0xFF02, 0x0002): 64,
                                         (0xFF04, 0x0002): 17})
        provider = P.PulsarProvider()
        found = provider.poll()
        self.assertEqual(72, found[0].level)
        self.assertEqual([(0xFF04, 0x0002)], bus.written())
        diag = "\n".join(provider.diagnostics())
        self.assertIn("ff02:0002 output=64 cannot take a 17-byte frame; skipped", diag)
        self.assertIn("output=17", diag)

    def test_the_request_goes_to_the_first_collection_that_fits_when_there_is_no_control_one(self):
        bus = self.one_receiver(replies=[reply(48)], shape=[(1, 0xFF05, 0x0000),
                                                            (1, 0xFF03, 0x0000)],
                                answering=(0xFF03, 0x0000),
                                lengths={(0xFF05, 0x0000): 8, (0xFF03, 0x0000): 17})
        found = P.PulsarProvider().poll()
        self.assertEqual(48, found[0].level)
        self.assertEqual([(0xFF03, 0x0000)], bus.written())

    def test_an_unknown_output_length_never_disqualifies_a_collection(self):
        # on a system where the caps query says nothing, the usage rule still decides
        self.one_receiver(replies=[reply(64)])
        P.PulsarProvider().poll()
        self.assertEqual([(0xFF02, 0x0002)], self.bus.written())

    def test_an_unknown_length_still_qualifies_when_the_control_collection_is_wrong(self):
        # the control collection says 8 (no 17-byte report); the next one says nothing.
        # Unknown must not disqualify it, or the frame is written to a collection that
        # refuses it and the device looks switched off
        bus = self.one_receiver(replies=[reply(66)],
                                shape=[(1, 0xFF02, 0x0002), (1, 0xFF05, 0x0000)],
                                answering=(0xFF05, 0x0000),
                                lengths={(0xFF02, 0x0002): 8})
        found = P.PulsarProvider().poll()
        self.assertEqual(66, found[0].level)
        self.assertEqual([(0xFF05, 0x0000)], bus.written())

    def test_the_diagnostic_names_the_output_length(self):
        self.one_receiver(replies=[reply(64)], lengths={(0xFF02, 0x0002): 17})
        provider = P.PulsarProvider()
        provider.poll()
        self.assertIn("ff02:0002 output=17", "\n".join(provider.diagnostics()))

    def test_an_interface_1_collection_is_used_when_the_control_one_is_absent(self):
        shape = [(1, 0xFF05, 0x0000), (1, 0x0001, 0x0080)]
        bus = self.one_receiver(replies=[reply(51)], shape=shape,
                                answering=(0xFF05, 0x0000))
        found = P.PulsarProvider().poll()
        self.assertEqual(51, found[0].level)
        self.assertEqual([(0xFF05, 0x0000)], bus.written())

    def test_a_device_that_is_not_in_the_table_is_not_queried(self):
        bus = self.one_receiver(vid=0x3554, pid=0x1234, replies=[reply(78)])
        self.assertEqual([], P.PulsarProvider().poll())
        self.assertEqual([], bus.written())

    def test_the_hitscan_receiver_is_read(self):
        bus = self.one_receiver(vid=0x3770, pid=0x0200, replies=[reply(100, 0, 4393)],
                                shape=HITSCAN_SHAPE)
        found = P.PulsarProvider().poll()
        self.assertEqual(1, len(found))
        self.assertEqual("Hitscan Hyperlight (2.4 GHz)", found[0].name)
        self.assertEqual(100, found[0].level)
        self.assertEqual("pulsar:37700200", found[0].key)
        self.assertEqual([(0xFF02, 0x0002)], bus.written())

    def test_the_hitscan_on_its_cable_is_read(self):
        self.one_receiver(vid=0x3770, pid=0x0100, replies=[reply(74, 1, 4000)],
                          shape=HITSCAN_SHAPE)
        found = P.PulsarProvider().poll()
        self.assertEqual("Hitscan Hyperlight (wired)", found[0].name)
        self.assertEqual(74, found[0].level)

    def test_the_captured_hitscan_frames_parse(self):
        """The literal frames in sopparus/hitscan-battery's NOTES.md, byte 5 being
        0x02 there - which this provider does not check, and must not. The second is
        the one that disproved the vendor app's indicator: the device answered 75
        while Hitscan Utility displayed 100."""
        for raw, level, mv in (("08040000000264001129000000000000a9", 100, 4393),
                               ("0804000000024b00107300000000000079", 75, 4211),
                               ("0804000000026400112d000000000000a5", 100, 4397)):
            frame = bytes.fromhex(raw)
            self.assertEqual((level, False), P.parse_power(frame))
            self.assertEqual(mv, P.voltage_mv(frame))


if __name__ == "__main__":
    unittest.main()
