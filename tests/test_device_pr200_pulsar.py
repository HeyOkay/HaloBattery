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
applies. The keep-window tests at the end cover the reporter's later report: after 10-15
idle minutes his mouse deep-sleeps, the receiver goes quiet, and the reading used to
vanish from the tray until long after the mouse was used again.

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
from unittest import mock

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

# the Redragon M991 receiver (3554:f5d5) as #183's diagnostics list its collections
REDRAGON_SHAPE = [
    (0, 0x0001, 0x0006),
    (1, 0x0001, 0x0002),
    (1, 0xFF03, 0x0000),
    (1, 0x000C, 0x0001),
    (1, 0x0001, 0x0080),
    (1, 0xFF02, 0x0002),
    (1, 0xFF04, 0x0002),
]

# the same mouse on its cable (3554:f55e, '3-mode mouse') as #183's charging report
# lists it - the receiver's collections plus ff05:0000
REDRAGON_WIRED_SHAPE = [
    (0, 0x0001, 0x0006),
    (1, 0xFF05, 0x0000),
    (1, 0xFF03, 0x0000),
    (1, 0x000C, 0x0001),
    (1, 0x0001, 0x0080),
    (1, 0xFF02, 0x0002),
    (1, 0xFF04, 0x0002),
    (2, 0x0001, 0x0002),
]


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


    def test_the_redragon_m991_receiver_is_read(self):
        # #183: the receiver's own dump lists this collection shape, and the
        # family's command 0x04 read answers on ff02:0002
        bus = self.one_receiver(vid=0x3554, pid=0xF5D5, replies=[reply(57, 1, 4010)],
                                shape=REDRAGON_SHAPE)
        provider = P.PulsarProvider()
        found = provider.poll()
        self.assertEqual(1, len(found))
        d = found[0]
        self.assertEqual("pulsar:3554f5d5", d.key)
        # the row covers both models on this receiver: #183 (M991), #197 (K1NG Max)
        self.assertEqual("Redragon M991 / K1NG Max (2.4 GHz)", d.name)
        self.assertEqual(57, d.level)
        self.assertTrue(d.charging)
        self.assertEqual([(0xFF02, 0x0002)], bus.written(),
                         "the request goes to the vendor collection, as for the other ids")
        self.assertIn("'Redragon M991 / K1NG Max (2.4 GHz)'", "\n".join(provider.diagnostics()))

    def test_the_redragon_m991_reply_needs_the_checksum(self):
        self.one_receiver(vid=0x3554, pid=0xF5D5, shape=REDRAGON_SHAPE,
                          replies=[reply(57, 1, 4010, break_checksum=True)])
        self.assertEqual([], P.PulsarProvider().poll())

    def test_the_redragon_m991_on_its_cable_is_read(self):
        # #183's charging report: the wired mouse is 3554:f55e with the same vendor
        # collections as the receiver, and answers the same command 0x04 frame
        bus = self.one_receiver(vid=0x3554, pid=0xF55E, shape=REDRAGON_WIRED_SHAPE,
                                replies=[reply(95, 1, 4020)])
        found = P.PulsarProvider().poll()
        self.assertEqual(1, len(found))
        d = found[0]
        self.assertEqual("pulsar:3554f55e", d.key)
        self.assertEqual("Redragon M991 (wired)", d.name)
        self.assertEqual(95, d.level)
        self.assertTrue(d.charging)
        self.assertEqual([(0xFF02, 0x0002)], bus.written(),
                         "the request goes to the vendor collection, as for the other ids")

    def test_while_charging_only_the_wired_mouse_is_shown(self):
        # on the cable the mouse leaves the 2.4 GHz link: the receiver answers
        # nothing, so the wired id is the one that keeps the icon - with charging
        dongle = FakeBus(0x3554, 0xF5D5, [], shape=REDRAGON_SHAPE)
        wired = FakeBus(0x3554, 0xF55E, [reply(95, 1, 4020)], shape=REDRAGON_WIRED_SHAPE)
        cols = dongle.cols + wired.cols

        class FakeDevice:
            def open_path(self, path):
                self.c = next(c for c in cols if c.info["path"] == path)
                self.c.opened += 1

            def write(self, frame):
                self.c.sent.append(list(frame))

            def read(self, length, timeout_ms=0):
                return self.c.read(length)

            def close(self):
                pass

        def enumerate_(vid=0):
            if vid != 0x3554:
                return []
            out = []
            for c in cols:
                pid = dongle.pid if any(c is d for d in dongle.cols) else wired.pid
                out.append(dict(c.info, product_id=pid, vendor_id=vid))
            return out

        P.output_length = lambda path: None
        P.hidlist = types.SimpleNamespace(enumerate=enumerate_)
        P.hid = types.SimpleNamespace(device=FakeDevice)
        found = P.PulsarProvider().poll()
        self.assertEqual(1, len(found), "only one icon: the wired mouse, since it charges")
        self.assertEqual("pulsar:3554f55e", found[0].key)
        self.assertEqual(95, found[0].level)
        self.assertTrue(found[0].charging)


    # ------------------------------------------------- the idle mouse (#87, later report)


if __name__ == "__main__":
    unittest.main()
