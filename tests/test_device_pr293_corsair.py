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


def bragi_se_reply(value=960, source=C.BRAGI_ROUTE_CHILD, cmd=C.BRAGI_CMD_GET,
                   err=0x00, report_id=False, size=C.BRAGI_MSG_SIZE):
    """The Virtuoso receivers' answer shape (probe-measured, #204): the source
    byte sits between the route and the command - `01 01 02 00 <v LE>`."""
    payload = bytearray(size)
    payload[0] = C.BRAGI_ROUTE_CHILD
    payload[1] = source
    payload[2] = cmd
    payload[3] = err
    payload[4] = value & 0xFF
    payload[5] = (value >> 8) & 0xFF
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

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)


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
                return self.dongle.write(data)

            def read(self, size, timeout_ms):
                return self.dongle.read(size, timeout_ms)

            def close(self):
                pass

        return FakeDevice


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


    def test_the_k65_plus_receiver_reads_its_level(self):
        # #154: the K65 PLUS dongle (1b1c:2b07) runs the same routed exchange as
        # the Dark Core's - level property 0x0F on the paired device's route - and
        # this test mirrors OpenLinkHub's K65 Plus dongle handler (cmdBatteryLevel
        # {0x02, 0x0F}, the value a little-endian uint16 at reply bytes 3-4). The
        # diagnostics in #154 list ff42:0001 on interface 1 and its notice
        # channel on interface 2.
        e = [entry(0x2B07, b"k65-answers", 1, 0xFF42, 0x0001,
                   "Corsair K65 PLUS Wireless Receiver"),
             entry(0x2B07, b"k65-notice", 2, 0xFF42, 0x0002,
                   "Corsair K65 PLUS Wireless Receiver")]
        out, bus = self.poll(e, {b"k65-answers": FakeDongle(bragi_reply(950)),
                                 b"k65-notice": FakeDongle(bragi_reply(0))})
        self.assertEqual(len(out), 1)
        st = out[0]
        self.assertEqual((st.key, st.level, st.kind), ("corsair:2b07", 95, "keyboard"))
        self.assertEqual(bus.opened, [b"k65-answers"])
        w = bus.dongles[b"k65-answers"].writes[0]
        self.assertEqual((w[0], w[1], w[2], w[3], len(w)),
                         (0x00, 0x09, 0x02, 0x0F, C.BRAGI_MSG_SIZE + 1))


    def test_the_headset_family_is_untouched(self):
        self.assertEqual(C.PIDS, {0x2A08: "Corsair Void v2 Wireless",
                                  0x2A02: "Corsair Virtuoso Max Wireless",
                                  0x0A97: "Corsair HS80 Max Wireless"})
        self.assertEqual(C.BRAGI_PIDS, {
            0x1B7F: "Corsair Dark Core RGB Pro SE",
            0x2B07: "Corsair K65 PLUS Wireless",
            0x1BDC: "Corsair M75 Wireless",
            0x0A40: "Corsair Virtuoso RGB Wireless SE",
            0x0A3E: "Corsair Virtuoso RGB Wireless SE",
            0x0A3D: "Corsair Virtuoso RGB Wireless SE",
            0x0A64: "Corsair Virtuoso RGB Wireless XT",
            0x0A62: "Corsair Virtuoso RGB Wireless XT",
        })
        # a headset's receiver and cable ids share one icon; the session the
        # 0A40 was measured with stays scoped to it
        self.assertEqual(C.BRAGI_KEYS[0x0A40], C.BRAGI_KEYS[0x0A3E])
        self.assertEqual(C.BRAGI_KEYS[0x0A40], C.BRAGI_KEYS[0x0A3D])
        self.assertEqual(C.BRAGI_KEYS[0x0A64], C.BRAGI_KEYS[0x0A62])
        self.assertEqual(C.BRAGI_SESSION_PIDS, {0x0A40})
        self.assertEqual(C.BRAGI_KEYS[0x2B07], "corsair:2b07")
        self.assertEqual(C.BRAGI_KINDS, {0x1B7F: "mouse", 0x2B07: "keyboard", 0x1BDC: "mouse"})
        self.assertNotIn(0x1B7F, C.PIDS)
        self.assertNotIn(0x0A40, C.PIDS)


if __name__ == "__main__":
    unittest.main()
