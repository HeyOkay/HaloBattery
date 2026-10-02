"""Tests for the Corsair provider: the headset family (Void v2 / Virtuoso Max /
HS80 Max, from HeadsetControl) and the Dark Core / Ironclaw "nxp" family from
ckb-next. No hardware is needed.

The nxp frames are ckb-next's: CMD_GET 0x0e plus FIELD_BATTERY 0x50 in a 64-byte
packet (src/daemon/nxp_proto.h), answered with a level index at byte 4 and a
status byte at byte 5, the index selecting from the five-step table
{0, 15, 30, 50, 100} (src/daemon/device.c, nxp_battery_lut). The report id in
front of the packet is hidapi's, not ckb-next's: it talks to the device over
libusb, which has no report ids. No capture of this dongle was available, so
the collection (ff42:0001 on its interface 1, from the reporter's dump in #56)
and the reply offsets are the reference's, and both are marked unverified in
the README and the provider docstring.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import corsair as C  # noqa: E402

DONGLE = "CORSAIR DARK CORE RGB PRO SE Gaming Dongle"


def entry(pid, path, iface, page, usage, name=DONGLE):
    return {"product_id": pid, "interface_number": iface, "usage_page": page, "usage": usage,
            "path": path, "product_string": name, "serial_number": ""}


def nxp_reply(idx, status=2, report_id=True, size=C.NXP_MSG_SIZE):
    payload = bytearray(size)
    payload[0] = C.NXP_CMD_GET
    payload[1] = C.NXP_FIELD_BATTERY
    payload[C.NXP_LEVEL_INDEX] = idx
    payload[C.NXP_STATUS_INDEX] = status
    return ([0x00] + list(payload)) if report_id else list(payload)


class FakeDongle:
    """One HID interface of the dongle. Answers the next read with a battery reply."""

    def __init__(self, reply=None, silent=False):
        self.reply = reply if reply is not None else nxp_reply(3)
        self.silent = silent
        self.writes = []

    def read(self, size, timeout_ms):
        return [] if self.silent else self.reply


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


class NxpParseTest(unittest.TestCase):
    def test_the_request_is_the_ckb_next_packet_behind_a_report_id(self):
        req = C.nxp_request()
        self.assertEqual(len(req), C.NXP_MSG_SIZE + 1)
        self.assertEqual(req[0], 0x00)
        self.assertEqual(req[1], 0x0E)          # CMD_GET
        self.assertEqual(req[2], 0x50)          # FIELD_BATTERY

    def test_every_index_maps_through_the_five_step_table(self):
        got = [C.parse_nxp(nxp_reply(i))[0] for i in range(5)]
        self.assertEqual(got, [0, 15, 30, 50, 100])

    def test_the_label_says_about_and_the_level(self):
        level, label = C.parse_nxp(nxp_reply(3))
        self.assertEqual((level, label), (50, "about 50%"))

    def test_an_index_past_the_table_is_refused(self):
        self.assertIsNone(C.parse_nxp(nxp_reply(5)))
        self.assertIsNone(C.parse_nxp(nxp_reply(255)))

    def test_a_short_reply_is_refused(self):
        self.assertIsNone(C.parse_nxp([0x00, 0x0E, 0x50, 0x00, 0x03]))
        self.assertIsNone(C.parse_nxp([0x00]))

    def test_an_empty_reply_is_refused(self):
        self.assertIsNone(C.parse_nxp([]))
        self.assertIsNone(C.parse_nxp(None))

    def test_the_report_id_is_tolerated_with_or_without(self):
        a = C.parse_nxp(nxp_reply(2, report_id=True))
        b = C.parse_nxp(nxp_reply(2, report_id=False))
        self.assertEqual(a, b)
        self.assertEqual(a[0], 30)


class NxpPollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (C.hid, C.hidlist)

    def tearDown(self):
        C.hid, C.hidlist = self._saved

    def poll(self, entries, dongles):
        bus = FakeBus(dongles)
        C.hid = types.SimpleNamespace(device=bus.device_class())
        C.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return C.CorsairProvider().poll(), bus

    def test_the_dongle_is_shown_as_a_gauge(self):
        out, bus = self.poll(
            [entry(0x1B7F, b"dongle-ff420001", 1, 0xFF42, 0x0001)],
            {b"dongle-ff420001": FakeDongle(nxp_reply(3))})
        self.assertEqual(len(out), 1)
        st = out[0]
        self.assertEqual((st.key, st.level, st.approx, st.kind), ("corsair:1b7f", 50, "about 50%", "mouse"))
        self.assertEqual((st.ui_message, st.ui_params), ("state.approx", {"level": 50}))
        self.assertFalse(st.charging)
        self.assertTrue(st.online)
        self.assertEqual(bus.opened, [b"dongle-ff420001"])

    def test_the_packet_goes_on_the_wire_unchanged(self):
        _, bus = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)], {b"d": FakeDongle()})
        w = bus.dongles[b"d"].writes[0]
        self.assertEqual((w[0], w[1], w[2], len(w)), (0x00, 0x0E, 0x50, C.NXP_MSG_SIZE + 1))

    def test_both_vendor_collections_are_tried_and_the_first_that_answers_wins(self):
        e = [entry(0x1B7F, b"c0001", 1, 0xFF42, 0x0001),
             entry(0x1B7F, b"c0002", 2, 0xFF42, 0x0002)]
        silent = FakeDongle(silent=True)
        out, bus = self.poll(e, {b"c0001": silent, b"c0002": FakeDongle(nxp_reply(4))})
        self.assertEqual(out[0].level, 100)
        self.assertEqual(bus.opened, [b"c0001", b"c0002"])

    def test_the_iface_1_collection_is_tried_first(self):
        e = [entry(0x1B7F, b"c0002", 2, 0xFF42, 0x0002),
             entry(0x1B7F, b"c0001", 1, 0xFF42, 0x0001)]
        out, bus = self.poll(e, {b"c0001": FakeDongle(nxp_reply(4)),
                                 b"c0002": FakeDongle(nxp_reply(0))})
        self.assertEqual(bus.opened, [b"c0001"])
        self.assertEqual(out[0].level, 100)

    def test_the_usage_1_collection_wins_on_one_interface(self):
        # both collections can sit on the same interface; the usage decides
        e = [entry(0x1B7F, b"u0002", 1, 0xFF42, 0x0002),
             entry(0x1B7F, b"u0001", 1, 0xFF42, 0x0001)]
        out, bus = self.poll(e, {b"u0001": FakeDongle(nxp_reply(4)),
                                 b"u0002": FakeDongle(nxp_reply(0))})
        self.assertEqual(bus.opened, [b"u0001"])
        self.assertEqual(out[0].level, 100)

    def test_a_non_ff42_collection_is_not_substituted_when_ff42_exists(self):
        # a silent ff42 collection must give no reading, not a try on the keyboard page
        e = [entry(0x1B7F, b"vendor", 1, 0xFF42, 0x0001),
             entry(0x1B7F, b"kbdpage", 0, 0x0001, 0x0002)]
        out, bus = self.poll(e, {b"vendor": FakeDongle(silent=True),
                                 b"kbdpage": FakeDongle(nxp_reply(4))})
        self.assertEqual(out, [])
        self.assertEqual(bus.opened, [b"vendor"])

    def test_an_index_past_the_table_gives_no_reading(self):
        out, _ = self.poll([entry(0x1B7F, b"d", 1, 0xFF42, 0x0001)],
                           {b"d": FakeDongle(nxp_reply(9))})
        self.assertEqual(out, [])

    def test_an_empty_dump_entry_set_is_tried_when_ff42_is_missing(self):
        # the doc's collection comes from the reporter's dump; if a firmware ever
        # reports another page, trying the remaining collections is one read
        out, bus = self.poll([entry(0x1B7F, b"d", 0, 0x0001, 0x0002)],
                             {b"d": FakeDongle(nxp_reply(1))})
        self.assertEqual(out[0].level, 15)
        self.assertEqual(bus.opened, [b"d"])

    def test_the_headset_family_is_untouched(self):
        self.assertEqual(C.PIDS, {0x2A08: "Corsair Void v2 Wireless",
                                  0x2A02: "Corsair Virtuoso Max Wireless",
                                  0x0A97: "Corsair HS80 Max Wireless"})
        self.assertNotIn(0x1B7F, C.PIDS)


if __name__ == "__main__":
    unittest.main()
