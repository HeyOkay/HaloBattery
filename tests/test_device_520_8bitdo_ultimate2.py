"""Tests for the 8BitDo Ultimate 2 Wireless dongle query, #520; no hardware.

On its 2.4 GHz dongle the controller is an XInput pad (2dc8:310b) whose Windows
level is a placeholder. Its vendor collection 0xFF7A:0x0001 is a request/answer
channel: the fixed query 81 04 21 01 00 00 06 is answered 02 04 04 00 21 01 with
the controller's battery % at byte 18 (MiguelSanzBr/8bitdo-ultimate2-linux).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import eightbitdo_ultimate2 as U  # noqa: E402
from providers import wgi  # noqa: E402
from providers import xinput as X  # noqa: E402

DONGLE_PID, CABLE_PID = 0x6013, 0x6012


def pad_answer(byte18=83, firmware=110, edition=3):
    r = [0] * 64
    r[:6] = U.ANSWER_PAD
    r[U.LEVEL_BYTE] = byte18
    r[U.FIRMWARE_LO], r[U.FIRMWARE_HI] = firmware & 0xFF, firmware >> 8
    r[U.EDITION_BYTE] = edition
    return r


def info_answer(pid=DONGLE_PID, firmware=106):
    r = [0] * 64
    r[:6] = U.ANSWER_INFO
    r[18], r[19] = firmware & 0xFF, firmware >> 8
    r[U.INFO_PID_LO], r[U.INFO_PID_HI] = pid & 0xFF, pid >> 8
    return r


VENDOR = {"vendor_id": 0x2DC8, "product_id": 0x310B, "interface_number": 2,
          "usage_page": 0xFF7A, "usage": 0x0001, "path": b"vendor",
          "product_string": "8BitDo Ultimate 2 Wireless Controller for PC"}
XINPUT_HID = {"vendor_id": 0x2DC8, "product_id": 0x310B, "interface_number": 0,
              "usage_page": 0x0001, "usage": 0x0005, "path": b"xinput",
              "product_string": "Controller (8BitDo Ultimate 2 Wireless Controller for PC)"}


class FakeDev:
    """One open collection: answers the requests it knows, from `answers` by request prefix."""

    def __init__(self, world):
        self.world = world
        self.path = None
        self.queue = []

    def open_path(self, path):
        if path in self.world.refuse_open:
            raise OSError("access denied")
        self.path = path
        self.world.opened.append(path)
        self.queue = list(self.world.stale.get(path, []))

    def write(self, data):
        data = bytes(data)
        self.world.sent.append((self.path, data))
        if self.path in self.world.refuse_write:
            return -1
        for prefix, replies in self.world.answers.get(self.path, {}).items():
            if data.startswith(prefix):
                self.queue += replies
        return len(data)

    def read(self, size, timeout_ms=0):
        self.world.read_timeouts.append(timeout_ms)
        return self.queue.pop(0) if self.queue else []

    def close(self):
        self.world.closed.append(self.path)


class World:
    def __init__(self, answers=None, stale=None, refuse_open=(), refuse_write=()):
        self.answers = answers or {}
        self.stale = stale or {}
        self.refuse_open = set(refuse_open)
        self.refuse_write = set(refuse_write)
        self.opened, self.closed, self.sent, self.read_timeouts = [], [], [], []


def dongle(level=83, pid=DONGLE_PID, path=b"vendor"):
    replies = {U.QUERY_INFO: [info_answer(pid)]}
    if level is not None:
        replies[U.QUERY_PAD] = [pad_answer(level)]
    return {path: replies}


class ParseTests(unittest.TestCase):
    def test_byte_18_is_the_level(self):
        got = U.parse_pad_answer(pad_answer(83))
        self.assertEqual((got.name, got.level, got.charging, got.charging_known),
                         ("8BitDo Ultimate 2", 83, False, False))

    def test_bit_7_reads_as_charging(self):
        got = U.parse_pad_answer(pad_answer(0x80 | 64))
        self.assertEqual((got.level, got.charging, got.charging_known), (64, True, True))

    def test_zero_and_out_of_range_bytes_are_no_level(self):
        for byte in (0, 101, 0x7F, 0x80, 0xFF):
            with self.subTest(byte=byte):
                self.assertIsNone(U.parse_pad_answer(pad_answer(byte)))

    def test_the_level_sits_at_byte_18_of_the_answer(self):
        # written out by hand, so the offset does not come from the module
        answer = bytes.fromhex("020404002101" + "00" * 11 + "11" + "53" + "6e00" + "00" + "00" + "03")
        answer += bytes(64 - len(answer))
        self.assertEqual(U.parse_pad_answer(list(answer)).level, 0x53)

    def test_only_the_controller_answer_is_read(self):
        # the info answer keeps the firmware at byte 18: 0x50 would read as 80%
        self.assertIsNone(U.parse_pad_answer(info_answer(firmware=0x0150)))
        other = pad_answer(83)
        other[4] = 0x22
        self.assertIsNone(U.parse_pad_answer(other))
        self.assertIsNone(U.parse_pad_answer(pad_answer(83)[:U.EDITION_BYTE]))
        self.assertIsNone(U.parse_pad_answer([]))
        self.assertIsNone(U.parse_pad_answer(None))

    def test_the_requests_are_the_documented_frames(self):
        self.assertEqual(U.frame(U.QUERY_PAD),
                         bytes([0x81, 0x04, 0x21, 0x01, 0x00, 0x00, 0x06]) + bytes(57))
        self.assertEqual(U.frame(U.QUERY_INFO), bytes([0x81, 0x04, 0x00, 0x01]) + bytes(60))
        self.assertEqual(U.ANSWER_PAD, bytes([0x02, 0x04, 0x04, 0x00, 0x21, 0x01]))

    def test_the_info_answer_names_who_answered(self):
        self.assertEqual(U.parse_info_pid(info_answer(DONGLE_PID)), DONGLE_PID)
        self.assertEqual(U.parse_info_pid(info_answer(CABLE_PID)), CABLE_PID)
        self.assertIsNone(U.parse_info_pid(pad_answer(83)))


class ReadTests(unittest.TestCase):
    def setUp(self):
        self._saved = (U.hid, U.hidlist.enumerate)

    def tearDown(self):
        U.hid, U.hidlist.enumerate = self._saved

    def read(self, world, collections=(XINPUT_HID, VENDOR)):
        U.hid = types.SimpleNamespace(device=lambda: FakeDev(world))
        U.hidlist.enumerate = lambda vid=0: [c for c in collections if c["vendor_id"] == vid]
        diag = []
        return U.read_connected(diag), diag

    def test_the_dongle_answer_becomes_the_reading(self):
        world = World(dongle(83))
        got, diag = self.read(world)
        self.assertEqual((got.level, got.charging, got.charging_known), (83, False, False))
        text = "\n".join(diag)
        self.assertIn("answered by 0x6013 (dongle)", text)
        self.assertIn("-> 83%", text)
        self.assertIn("controller firmware 1.10", text)
        self.assertEqual(world.closed, [b"vendor"])

    def test_only_the_two_queries_are_sent_as_full_frames(self):
        world = World(dongle(83))
        self.read(world)
        self.assertEqual([d for _p, d in world.sent], [U.frame(U.QUERY_INFO), U.frame(U.QUERY_PAD)])
        self.assertTrue(all(len(d) == 64 and d[0] == 0x81 for _p, d in world.sent))

    def test_the_xinput_collection_is_never_opened(self):
        # reading 0001:0005 made XInput fail and the pad disappear (test build 3, #461)
        world = World(dongle(83))
        self.read(world)
        self.assertEqual(world.opened, [b"vendor"])
        self.assertNotIn(b"xinput", [p for p, _d in world.sent])

    def test_a_cable_answers_no_controller_query_and_nothing_is_invented(self):
        world = World(dongle(None, pid=CABLE_PID))
        got, diag = self.read(world)
        self.assertIsNone(got)
        text = "\n".join(diag)
        self.assertIn("answered by 0x6012 (the controller itself, on its cable)", text)
        self.assertIn("the controller did not answer", text)

    def test_silence_is_reported_and_not_invented(self):
        got, diag = self.read(World())
        self.assertIsNone(got)
        self.assertIn("device info: no answer", "\n".join(diag))

    def test_queued_frames_are_drained_and_unrelated_ones_skipped(self):
        # an old 10% answer is still queued behind the info answer when the controller
        # query is written; the fresh answer is preceded by an unrelated frame
        noise = [0x02, 0x05] + [0] * 62
        world = World({b"vendor": {U.QUERY_INFO: [info_answer(), pad_answer(10)],
                                   U.QUERY_PAD: [noise, pad_answer(83)]}},
                      stale={b"vendor": [pad_answer(12)]})
        got, diag = self.read(world)
        self.assertEqual(got.level, 83)
        self.assertIn("other frame skipped", "\n".join(diag))

    def test_reads_always_wait_a_bounded_time(self):
        # hidapi's read with timeout 0 blocks until a report arrives; the vendor
        # collection sends nothing by itself, so that would hang the poll
        world = World(dongle(83))
        self.read(world)
        self.assertTrue(world.read_timeouts)
        self.assertTrue(all(0 < t <= U.READ_TIMEOUT_MS for t in world.read_timeouts))

    def test_a_refused_write_is_logged(self):
        world = World(dongle(83), refuse_write=[b"vendor"])
        got, diag = self.read(world)
        self.assertIsNone(got)
        self.assertIn("write refused (-1)", "\n".join(diag))

    def test_an_open_failure_is_logged(self):
        got, diag = self.read(World(dongle(83), refuse_open=[b"vendor"]))
        self.assertIsNone(got)
        self.assertIn("open: access denied", "\n".join(diag))

    def test_no_vendor_collection(self):
        got, diag = self.read(World(), collections=(XINPUT_HID,))
        self.assertIsNone(got)
        self.assertIn("no 0xFF7A:0x0001 collection", "\n".join(diag))

    def test_the_dongle_is_found_next_to_the_cable(self):
        cable = dict(VENDOR, path=b"cable")
        world = World({**dongle(None, pid=CABLE_PID, path=b"cable"), **dongle(71)})
        got, _diag = self.read(world, collections=(cable, VENDOR))
        self.assertEqual(got.level, 71)
        self.assertEqual(world.opened, [b"cable", b"vendor"])


class FakeDll:
    def __init__(self, slots):
        self.slots = slots

    def XInputGetState(self, slot, state_ref):
        return X.ERROR_SUCCESS if slot in self.slots else 1167

    def XInputGetBatteryInformation(self, slot, devtype, info_ref):
        info = info_ref._obj
        info.BatteryType, info.BatteryLevel = self.slots[slot]
        return X.ERROR_SUCCESS


def wgi_report(vid, pid, remain=1000, full=1000, status="Discharging"):
    return wgi.WgiController({"vid": vid, "pid": pid, "status": status, "remain": remain,
                              "full": full, "name": "Xbox 360 Controller for Windows"})


class XInputTests(unittest.TestCase):
    U2 = (0x2DC8, 0x310B)

    def setUp(self):
        self._saved = (X.load_xinput, X.hidlist, X.wgi.query, X.eightbitdo_ultimate2.read_connected)

    def tearDown(self):
        (X.load_xinput, X.hidlist, X.wgi.query, X.eightbitdo_ultimate2.read_connected) = self._saved

    def poll(self, slots, reports, reading=None, hid_devices=()):
        calls = []

        def read_connected(diag):
            calls.append(True)
            return reading

        X.eightbitdo_ultimate2.read_connected = read_connected
        X.load_xinput = lambda: FakeDll(slots)
        X.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [d for d in hid_devices if d["vendor_id"] == vid],
            interface_paths=lambda: frozenset())
        X.wgi.query = lambda diag: list(reports)
        return X.XInputProvider().poll(), calls

    def test_the_dongle_level_reaches_the_pad(self):
        res, calls = self.poll({0: (X.TYPE_WIRED, 0)}, [wgi_report(*self.U2)],
                               reading=U.Reading(U.NAME, 83))
        self.assertEqual(calls, [True])
        [st] = res
        self.assertEqual((st.name, st.level, st.charging, st.charging_known),
                         ("8BitDo Ultimate 2", 83, False, False))

    def test_bit_7_charging_reaches_the_pad(self):
        res, _calls = self.poll({0: (X.TYPE_WIRED, 0)}, [wgi_report(*self.U2)],
                                reading=U.Reading(U.NAME, 64, charging=True, charging_known=True))
        [st] = res
        self.assertEqual((st.level, st.charging, st.charging_known), (64, True, True))

    def test_no_answer_keeps_the_pad_without_a_level(self):
        res, calls = self.poll({0: (X.TYPE_WIRED, 0)}, [wgi_report(*self.U2)], reading=None)
        self.assertEqual(calls, [True])
        [st] = res
        self.assertEqual((st.name, st.level), ("8BitDo Ultimate 2", None))

    def test_an_xbox_pad_next_to_an_idle_dongle_is_never_asked(self):
        receiver = dict(VENDOR)
        xbox = {"vendor_id": 0x045E, "product_id": 0x02FD,
                "product_string": "Controller (Xbox One For Windows)"}
        _res, calls = self.poll({0: (X.TYPE_NIMH, 2)}, [], hid_devices=[xbox, receiver],
                                reading=U.Reading(U.NAME, 83))
        self.assertEqual(calls, [])

    def test_two_slots_are_never_asked(self):
        _res, calls = self.poll({0: (X.TYPE_NIMH, 3), 1: (X.TYPE_WIRED, 0)},
                                [wgi_report(*self.U2), wgi_report(0x045E, 0x0B12, 800)],
                                reading=U.Reading(U.NAME, 83))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
