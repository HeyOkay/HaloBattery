"""Tests for providers/hyperx_ngenuity.py. No hardware is needed.

The fake dongle is the Pulsefire Haste 2 Wireless receiver from #304 (03f0:0d97): the
reporter's diagnostics lists the mouse, keyboard, consumer, vendor and physical
collections, exactly as below, with the vendor collection on interface 2.

The protocol is PMarinov1994/ngenuity-linux-cli's: `50 02` asks for the battery and
`51 02` answers with the level in byte 2 and the charge status in byte 3. The reply
builder below writes those two frames in the layout that project confirmed on a real
Haste 2 Wireless.
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import hyperx_ngenuity as H  # noqa: E402

# (interface, usage page, usage) exactly as #304's diagnostics lists them
SHAPE = [
    (0, 0x0001, 0x0002),
    (1, 0x0001, 0x0006),
    (2, 0xFF00, 0x0001),
    (3, 0x000C, 0x0001),
    (4, 0x0059, 0x0001),
]

LINK = bytes([0x53, 0x01, 0x01]) + bytes(H.REPORT_LEN - 3)


def battery(level=62, status=0, mv=3900, temp=27):
    return (bytes([0x51, 0x02, level, status])
            + int(temp).to_bytes(2, "little", signed=True)
            + int(mv).to_bytes(2, "little")
            + bytes([0x02, 0x01])
            + bytes(H.REPORT_LEN - 10))


class FakeCollection:
    def __init__(self, path, iface, page, usage, replies):
        self.info = {"path": path, "interface_number": iface, "usage_page": page,
                     "usage": usage}
        self.replies = list(replies)
        self.sent = []


class FakeBus:
    def __init__(self, vid, pid, replies=(), shape=SHAPE):
        self.vid = vid
        self.pid = pid
        self.cols = []
        for i, (iface, page, usage) in enumerate(shape):
            path = b"\\\\?\\hid#vid_%04x&pid_%04x#col%02d" % (vid, pid, i)
            mine = replies if (page, usage) == (H.VENDOR_USAGE_PAGE, H.VENDOR_USAGE) else ()
            self.cols.append(FakeCollection(path, iface, page, usage, mine))

    def enumerate(self, vid):
        if vid != self.vid:
            return []
        return [dict(c.info, product_id=self.pid, vendor_id=self.vid) for c in self.cols]

    def written(self):
        return [(c.info["usage_page"], c.info["usage"], c.sent) for c in self.cols if c.sent]

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.c = next(c for c in bus.cols if c.info["path"] == path)

            def write(self, frame):
                self.c.sent.append(list(frame))

            def read(self, length, timeout_ms=0):
                return self.c.replies.pop(0) if self.c.replies else b""

            def close(self):
                pass

        return FakeDevice


class HyperXNgenuityTest(unittest.TestCase):
    def setUp(self):
        self._hid, self._hidlist = H.hid, H.hidlist
        self._timeout = H.TIMEOUT_S
        H.TIMEOUT_S = 0.05          # the fakes never make us wait
        self.bus = self.one_dongle()

    def tearDown(self):
        H.hid, H.hidlist = self._hid, self._hidlist
        H.TIMEOUT_S = self._timeout

    def one_dongle(self, pid=0x0D97, replies=(), **kw):
        self.bus = FakeBus(H.HYPERX_VID, pid, replies, **kw)
        bus = self.bus
        H.hidlist = types.SimpleNamespace(enumerate=bus.enumerate)
        H.hid = types.SimpleNamespace(device=bus.device_class())
        return bus

    def test_the_haste_2_wireless_is_read(self):
        bus = self.one_dongle(replies=[LINK, battery(62, 0, 3900, 27)])
        found = H.HyperXNgenuityProvider().poll()
        self.assertEqual(1, len(found))
        d = found[0]
        self.assertEqual("hyperx_ngenuity:0d97", d.key)
        self.assertEqual("HyperX Pulsefire Haste 2 Wireless", d.name)
        self.assertEqual(62, d.level)
        self.assertFalse(d.charging)
        self.assertTrue(d.charging_known)
        self.assertTrue(d.online)
        self.assertEqual("mouse", d.kind)
        self.assertEqual("hyperx_ngenuity", d.source)
        writes = bus.written()
        self.assertEqual([(0xFF00, 0x0001)], [(p, u) for p, u, _ in writes])
        self.assertEqual([0x52, 0x01], writes[0][2][0][:2])
        self.assertEqual([0x50, 0x02], writes[0][2][1][:2])
        self.assertEqual(H.REPORT_LEN, len(writes[0][2][1]))

    def test_the_millivolts_are_reported_in_the_diagnostic(self):
        self.one_dongle(replies=[LINK, battery(62, 0, 3900, 27)])
        provider = H.HyperXNgenuityProvider()
        provider.poll()
        diag = "\n".join(provider.diagnostics())
        self.assertIn("3900 mV", diag)
        self.assertIn("27 C", diag)
        self.assertIn("51 02 3e", diag)

    def test_charging_and_full_both_mean_on_the_charger(self):
        for status in (1, 2):
            with self.subTest(status=status):
                self.one_dongle(replies=[LINK, battery(80, status)])
                d = H.HyperXNgenuityProvider().poll()[0]
                self.assertTrue(d.charging)
                self.assertTrue(d.charging_known)

    def test_a_charger_error_shows_the_level_without_a_charging_state(self):
        self.one_dongle(replies=[LINK, battery(70, 3)])
        d = H.HyperXNgenuityProvider().poll()[0]
        self.assertEqual(70, d.level)
        self.assertFalse(d.charging)
        self.assertFalse(d.charging_known)

    def test_a_level_above_100_is_refused(self):
        self.one_dongle(replies=[LINK, battery(150)])
        self.assertEqual([], H.HyperXNgenuityProvider().poll())

    def test_an_unrelated_input_frame_is_skipped(self):
        stale = bytes([0x45, 0x02, 0x00]) + bytes(H.REPORT_LEN - 3)
        self.one_dongle(replies=[LINK, stale, battery(42)])
        d = H.HyperXNgenuityProvider().poll()[0]
        self.assertEqual(42, d.level)

    def test_no_reply_gives_no_device(self):
        provider = H.HyperXNgenuityProvider()
        self.assertEqual([], provider.poll())
        self.assertTrue(any("no reply" in line for line in provider.diagnostics()))

    def test_a_sleeping_mouse_keeps_its_reading_greyed(self):
        bus = self.one_dongle(replies=[LINK, battery(55)])
        provider = H.HyperXNgenuityProvider()
        self.assertEqual(55, provider.poll()[0].level)
        bus.cols[2].replies = []          # the mouse went to sleep
        kept = provider.poll()
        self.assertEqual(1, len(kept))
        self.assertEqual(55, kept[0].level)
        self.assertFalse(kept[0].online)
        self.assertTrue(kept[0].wake_on_input)
        self.assertTrue(any("keeping" not in line.lower() for line in provider.diagnostics()))

    def test_the_other_pid_is_claimed_too(self):
        self.one_dongle(pid=0x0F98, replies=[LINK, battery(33)])
        d = H.HyperXNgenuityProvider().poll()[0]
        self.assertEqual("hyperx_ngenuity:0f98", d.key)
        self.assertEqual(33, d.level)

    def test_a_dongle_without_the_vendor_collection_is_reported_not_queried(self):
        shape = [(0, 0x0001, 0x0002), (1, 0x0001, 0x0006)]
        bus = self.one_dongle(shape=shape)
        provider = H.HyperXNgenuityProvider()
        self.assertEqual([], provider.poll())
        self.assertEqual([], bus.written())
        self.assertTrue(any("no usage page ff00" in line for line in provider.diagnostics()))
