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


# --- the headset path (Void v2 / Virtuoso Max / HS80 Max) ------------------------------
# HeadsetControl's corsair_void_v2w exchange: firmware query, receiver heartbeat,
# headset heartbeat, then command 0x0f, with the level as response[4] | [5] << 8 in
# tenths of a percent. The probe run (#28) adds depth around it: every write's return
# value, answer timings, a listen pass, and the vendor app's own software-mode path.

VOID = "CORSAIR VOID WIRELESS v2 Gaming Receiver"


def void_reply(tenths=553):
    """A battery reply: the level as a 0..1000 value across bytes 4 and 5."""
    payload = bytearray(C.MSG_SIZE_READ)
    payload[4] = tenths & 0xFF
    payload[5] = (tenths >> 8) & 0xFF
    return list(payload)


JUNK_REPLY = [0x01, 0x01, 0x06] + [0x00] * 29      # what #28's probe got, verbatim


def void_entries():
    # the reporter's dump, in its order: interface 4 carries the protocol collection
    return [entry(0x2A08, b"void-iface4-0001", 4, 0xFF42, 0x0001, name=VOID),
            entry(0x2A08, b"void-iface4-0002", 4, 0xFF42, 0x0002, name=VOID),
            entry(0x2A08, b"void-iface3-ff13", 3, 0xFF13, 0x0001, name=VOID)]


class FakeVoidReceiver:
    """Writes are recorded; reads serve a scripted answer per phase."""

    def __init__(self, heartbeat=None, replies=(), listen=()):
        self.heartbeat = heartbeat          # the headset-heartbeat answer (None = silence)
        self.replies = list(replies)        # one frame per battery read
        self.listen = list(listen)          # anything the listen pass should catch
        self.writes = []
        self.reads = []
        self._hb_seen = False

    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms):
        self.reads.append((size, timeout_ms))
        if timeout_ms <= 250:               # the listen pass, or a drain
            return self.listen.pop(0) if self.listen else []
        if not self._hb_seen:               # the first longer read is the heartbeat
            self._hb_seen = True
            return self.heartbeat
        return self.replies.pop(0) if self.replies else []


HEARTBEAT_FRAME = [0x01, 0x02, 0x00] + [0x00] * 61


class FakeClock:
    """Each look at the clock advances it a little, so the listen pass ends."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        self.t += 0.3
        return self.t


class VoidPollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (C.hid, C.hidlist, C.time)
        self._env = os.environ.pop("HALO_PROBE", None)
        self.entries = void_entries()

    def tearDown(self):
        C.hid, C.hidlist, C.time = self._saved
        os.environ.pop("HALO_PROBE", None)
        if self._env is not None:
            os.environ["HALO_PROBE"] = self._env

    def poll(self, fakes, probe=False):
        if probe:
            os.environ["HALO_PROBE"] = "1"
        C.time = FakeClock()

        class Device:
            def open_path(self, path):
                if path not in fakes:
                    raise OSError("cannot open")
                self.impl = fakes[path]

            def write(self, data):
                return self.impl.write(data)

            def read(self, size, timeout_ms):
                return self.impl.read(size, timeout_ms)

            def close(self):
                pass

        C.hid = types.SimpleNamespace(device=Device)
        C.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.entries))
        p = C.CorsairProvider()
        return p.poll(), p.diagnostics()

    def test_the_level_is_read_and_the_tray_windows_stay_short(self):
        fake = FakeVoidReceiver(heartbeat=HEARTBEAT_FRAME, replies=[void_reply(553)])
        out, diag = self.poll({b"void-iface4-0001": fake})
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind)
                          for s in out],
                         [("corsair:2a08", C.PIDS[0x2A08], 55, False, True, "headset")])
        self.assertTrue(all(t <= C.READ_TIMEOUT_MS for _, t in fake.reads))
        self.assertFalse(any("[w]" in line or "listening" in line for line in diag))

    def test_silence_gives_no_reading_and_the_plain_note(self):
        fake = FakeVoidReceiver(heartbeat=None, replies=[void_reply(553)])
        out, diag = self.poll({b"void-iface4-0001": fake})
        self.assertEqual(out, [])
        self.assertIn("  headset heartbeat: no reply (headset off or asleep)", diag)
        # the usual path stops there; the fuller sequence is probe-only
        self.assertFalse(any("software mode" in line for line in diag))
        self.assertEqual(len(fake.writes), 3)          # firmware, both heartbeats

    def test_the_probe_logs_the_writes_the_timings_and_the_junk_frame(self):
        fake = FakeVoidReceiver(heartbeat=HEARTBEAT_FRAME,
                                replies=[JUNK_REPLY, JUNK_REPLY, JUNK_REPLY])
        out, diag = self.poll({b"void-iface4-0001": fake}, probe=True)
        self.assertEqual(out, [])
        self.assertTrue(any("listening 6 s" in line for line in diag))
        self.assertTrue(any("[w] 08/02/13 -> 65" in line for line in diag))
        self.assertTrue(any("headset heartbeat answered (" in line for line in diag))
        self.assertTrue(any(line.startswith("  attempt 1 reply (") for line in diag))
        self.assertTrue(any("01 01 06" in line for line in diag))
        self.assertTrue(any("no usable level" in line for line in diag))
        self.assertTrue(any("probe: trying the vendor app's fuller sequence"
                            in line for line in diag))
        timeouts = [t for _, t in fake.reads]
        self.assertEqual([t for t in timeouts if t > 250][0], C.PROBE_HB_TIMEOUT_MS)
        self.assertIn(C.PROBE_ATTEMPT_TIMEOUT_MS[1], timeouts)

    def test_the_probe_still_returns_a_level_when_the_device_answers(self):
        fake = FakeVoidReceiver(heartbeat=HEARTBEAT_FRAME, replies=[void_reply(120)])
        out, diag = self.poll({b"void-iface4-0001": fake}, probe=True)
        self.assertEqual([s.level for s in out], [12])
        self.assertFalse(any("sibling collection" in line for line in diag))

    def test_a_listen_frame_is_logged_with_its_clock(self):
        # the vendor app's own battery notification frame, if it is on the wire
        note = [0x03, 0x01, 0x00, 0x0F, 0x00, 553 & 0xFF, (553 >> 8) & 0xFF] + [0x00] * 57
        fake = FakeVoidReceiver(heartbeat=None, replies=(), listen=[note])
        out, diag = self.poll({b"void-iface4-0001": fake}, probe=True)
        self.assertEqual(out, [])
        self.assertTrue(any(line.lstrip().startswith("[") and "03 01 00 0f" in line
                            for line in diag))

    def test_a_silent_receiver_gets_one_ask_on_a_sibling_collection(self):
        main = FakeVoidReceiver(heartbeat=None, replies=[])
        sib = FakeVoidReceiver(heartbeat=void_reply(999))
        out, diag = self.poll({b"void-iface4-0001": main,
                               b"void-iface4-0002": sib}, probe=True)
        self.assertEqual(out, [])
        self.assertTrue(any("probe: sibling collection" in line for line in diag))
        self.assertTrue(any("    reply (" in line for line in diag))
        self.assertEqual(sib.writes[0][:5], b"\x00\x02\x09\x02\x0f")   # the battery ask


if __name__ == "__main__":
    unittest.main()
