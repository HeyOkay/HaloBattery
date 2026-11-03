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


class FakeRefusingReceiver(FakeVoidReceiver):
    """Like the reporter's #28 dongle: every write refused at the USB layer (hidapi -1)."""

    def write(self, data):
        self.writes.append(bytes(data))
        return -1


class FakeRidOnlyReceiver(FakeVoidReceiver):
    """A receiver that still takes only the old 65-byte rid-prefixed shape."""

    def write(self, data):
        self.writes.append(bytes(data))
        return -1 if len(data) == 64 else len(data)


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
        self._saved = (C.hid, C.hidlist, C.time, C.caps_for)
        self._env = os.environ.pop("HALO_PROBE", None)
        self.entries = void_entries()

    def tearDown(self):
        C.hid, C.hidlist, C.time, C.caps_for = self._saved
        os.environ.pop("HALO_PROBE", None)
        if self._env is not None:
            os.environ["HALO_PROBE"] = self._env

    def poll(self, fakes, probe=False, caps=None):
        if probe:
            os.environ["HALO_PROBE"] = "1"
            C.caps_for = lambda path: (64, 65, 65) if caps is None else caps.get(path)
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
        self.assertTrue(any("[w] 08/02/13 -> 64" in line for line in diag))
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
        self.assertFalse(any("write shapes" in line for line in diag))

    def test_a_listen_frame_is_logged_with_its_clock(self):
        # the vendor app's own battery notification frame, if it is on the wire
        note = [0x03, 0x01, 0x00, 0x0F, 0x00, 553 & 0xFF, (553 >> 8) & 0xFF] + [0x00] * 57
        fake = FakeVoidReceiver(heartbeat=None, replies=(), listen=[note])
        out, diag = self.poll({b"void-iface4-0001": fake}, probe=True)
        self.assertEqual(out, [])
        self.assertTrue(any(line.lstrip().startswith("[") and "03 01 00 0f" in line
                            for line in diag))

    def test_the_probe_logs_caps_for_every_collection(self):
        fake = FakeVoidReceiver(heartbeat=None, replies=[])
        caps = {b"void-iface4-0001": (64, 65, 65),
                b"void-iface4-0002": None,
                b"void-iface3-ff13": (64, 0, 32)}
        out, diag = self.poll({b"void-iface4-0001": fake}, probe=True, caps=caps)
        self.assertEqual(out, [])
        self.assertTrue(any("caps: iface=4 ff42:0001 in/out/feat=64/65/65" in line
                            for line in diag))
        self.assertTrue(any("caps: iface=4 ff42:0002 ?" in line for line in diag))
        self.assertTrue(any("caps: iface=3 ff13:0001 in/out/feat=64/0/32" in line
                            for line in diag))

    def test_the_probe_matrix_finds_the_collection_that_takes_the_write(self):
        # the #28 shape: every write on the protocol collection refused with -1,
        # while another collection of the same receiver accepts the same frame
        main = FakeRefusingReceiver(heartbeat=None, replies=[])
        sib = FakeVoidReceiver(heartbeat=HEARTBEAT_FRAME)
        caps = {b"void-iface4-0001": (64, 63, 65),   # declares a 63-byte output report
                b"void-iface4-0002": (64, 65, 65)}
        out, diag = self.poll({b"void-iface4-0001": main,
                               b"void-iface4-0002": sib}, probe=True, caps=caps)
        self.assertEqual(out, [])
        self.assertTrue(any("probe: write shapes, collection by collection" in line
                            for line in diag))
        self.assertTrue(any(line.startswith("  probe: write65-rid0 iface=4 ff42:0001 -> -1")
                            for line in diag))
        self.assertTrue(any(line.startswith("  probe: write65-rid0 iface=4 ff42:0002 -> 65")
                            for line in diag))
        self.assertTrue(any(line.startswith("  probe: write63-rid0 iface=4 ff42:0001 -> -1")
                            for line in diag))
        self.assertTrue(any("probe: first answer (" in line for line in diag))
        self.assertTrue(any("takes the write" in line for line in diag))
        self.assertEqual(len(sib.writes), 2)          # the accepted shape, then the re-ask
        self.assertEqual(sib.writes[0], sib.writes[1])   # the re-ask reuses the accepted shape

    def test_the_normal_poll_never_asks_for_caps(self):
        called = []
        C.caps_for = lambda path: called.append(path) or (64, 65, 65)
        fake = FakeVoidReceiver(heartbeat=HEARTBEAT_FRAME, replies=[void_reply(553)])
        out, _ = self.poll({b"void-iface4-0001": fake}, caps={})
        self.assertEqual([s.level for s in out], [55])
        self.assertEqual(called, [])

    def test_the_frames_go_out_in_the_shape_windows_accepts(self):
        # 64 bytes with no leading report id - the form the #28 receiver took
        fake = FakeVoidReceiver(heartbeat=HEARTBEAT_FRAME, replies=[void_reply(553)])
        out, _ = self.poll({b"void-iface4-0001": fake})
        self.assertEqual([s.level for s in out], [55])
        self.assertTrue(all(len(w) == 64 for w in fake.writes))
        self.assertTrue(any(w[:4] == b"\x02\x09\x02\x0f" for w in fake.writes))

    def test_a_receiver_that_wants_the_65_byte_shape_falls_back(self):
        fake = FakeRidOnlyReceiver(heartbeat=HEARTBEAT_FRAME, replies=[void_reply(553)])
        out, _ = self.poll({b"void-iface4-0001": fake})
        self.assertEqual([s.level for s in out], [55])
        self.assertTrue(any(len(w) == 64 for w in fake.writes))   # tried first
        self.assertTrue(any(len(w) == 65 for w in fake.writes))   # then accepted

    def test_a_refused_heartbeat_write_is_said_to_be_refused(self):
        fake = FakeRefusingReceiver(heartbeat=None, replies=[])
        out, diag = self.poll({b"void-iface4-0001": fake}, probe=True)
        self.assertEqual(out, [])
        self.assertTrue(any("the write was refused before it left" in line
                            for line in diag))

    def test_the_reported_reply_from_28_reads_81(self):
        # the exact frame the reporter's probe caught after the accepted write
        frame = [0x01, 0x01, 0x02, 0x00, 0x2A, 0x03] + [0x00] * 58
        self.assertEqual(C.parse_level(frame), 81)


if __name__ == "__main__":
    unittest.main()
