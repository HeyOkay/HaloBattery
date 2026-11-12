"""Tests for providers/blackshark_v3.py and its place in providers/razer.py. No hardware.

The frames are the Synapse capture of a 1532:057A dongle posted in #292, checksums
included, and the collections are the ones the diagnostics of that issue list. The fake
dongle answers the way the capture shows; the clock is the test's own, so the rule that
matters most can be checked exactly: this headset's radio link resets when it is polled
all the time, so a query goes out only at connect, after a link-up push, or after
REFRESH_S of silence.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import blackshark_v3 as V, razer as R  # noqa: E402

PATH = b"\\\\?\\hid#vid_1532&pid_057a&mi_05&col04#x"
SHAPE = [(0x000C, 0x0001), (0xFF13, 0x0001), (0x000B, 0x0005), (0xFF14, 0x0001)]
ENTRIES = [{"path": PATH if page == 0xFF14 else b"other%d" % i, "interface_number": 5,
            "usage_page": page, "usage": usage, "product_id": V.PID, "vendor_id": R.RAZER_VID,
            "serial_number": "", "product_string": "BlackShark V3 HID"}
           for i, (page, usage) in enumerate(SHAPE)]


def captured(head, crc):
    """A frame of the capture: its first bytes, zeros, and the checksum it had."""
    b = bytearray(64)
    first = bytes.fromhex(head)
    b[:len(first)] = first
    b[62] = crc
    return list(b)


BATTERY_97 = captured("02 02 60 00 00 00 05 00 00 80 21 01 01 61", 0xA5)
NOT_CHARGING = captured("02 02 60 00 00 00 05 00 00 80 2a 01 01 00", 0xCF)
LINK_UP = captured("02 02 60 00 00 00 06 00 00 00 20 02 02 01", 0x47)


def frame(cmd_class, kind, value):
    """A frame of the same shape as the capture, with the checksum worked out."""
    b = [0] * 64
    b[0], b[1], b[2], b[6], b[9] = 2, 2, 0x60, 5, 0x80
    b[10], b[11], b[12], b[13] = cmd_class, kind, 1, value
    b[62] = V.checksum(b)
    return b


class FakeDongle:
    def __init__(self):
        self.now = 1000.0
        self.sent = []
        self.inbox = []
        self.battery = BATTERY_97    # what a battery query gets; None = headset off
        self.opened = self.closed = 0
        self.fail = None             # raised by read(): the dongle was pulled out

    def queries(self):
        return sum(1 for f in self.sent if f[10] == V.CLASS_BATTERY)

    def device_class(self):
        dongle = self

        class FakeDevice:
            def open_path(self, path):
                dongle.opened += 1

            def write(self, data):
                dongle.sent.append(bytes(data))
                if data[10] == V.CLASS_BATTERY and data[9] == V.DIR_QUERY and dongle.battery:
                    dongle.inbox.append(dongle.battery)
                if data[10] == V.CLASS_CHARGING and data[9] == V.DIR_QUERY:
                    dongle.inbox.append(NOT_CHARGING)
                return len(data)

            def read(self, n, timeout=0):
                if dongle.fail:
                    raise dongle.fail
                # hidapi's read() with no timeout waits until a report comes, which on a
                # quiet headset is forever: it froze the whole app, so no icon appeared
                assert timeout > 0 or dongle.inbox, "read() without a timeout would hang"
                dongle.now += timeout / 1000
                return dongle.inbox.pop(0) if dongle.inbox else []

            def close(self):
                dongle.closed += 1

        return FakeDevice


class Base(unittest.TestCase):
    def setUp(self):
        self.d = FakeDongle()
        self.entries = list(ENTRIES)
        saved = (V.hid, V.time, R.hidlist)
        self.addCleanup(lambda: (setattr(V, "hid", saved[0]), setattr(V, "time", saved[1]),
                                 setattr(R, "hidlist", saved[2])))
        V.hid = types.SimpleNamespace(device=self.d.device_class())
        V.time = types.SimpleNamespace(monotonic=lambda: self.d.now, sleep=self.sleep)
        R.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.entries))
        self.p = R.RazerProvider()

    def sleep(self, seconds):
        self.d.now += seconds


class FrameTest(unittest.TestCase):
    def test_requests_match_the_capture_byte_for_byte(self):
        self.assertEqual(list(V.make_frame(V.CLASS_INIT, V.DIR_WAKE)),
                         captured("02 00 60 00 00 00 04 00 00 00 02", 0x64))
        self.assertEqual(list(V.make_frame(V.CLASS_CHARGING, V.DIR_WAKE)),
                         captured("02 00 60 00 00 00 04 00 00 00 2a", 0x4C))
        self.assertEqual(list(V.make_frame(V.CLASS_CHARGING)),
                         captured("02 00 60 00 00 00 04 00 00 80 2a", 0xCC))
        self.assertEqual(list(V.make_frame(V.CLASS_BATTERY)),
                         captured("02 00 60 00 00 00 04 00 00 80 21", 0xC7))

    def test_replies_of_the_capture_parse(self):
        self.assertEqual(V.parse_frame(BATTERY_97), (V.CLASS_BATTERY, 97))
        self.assertEqual(V.parse_frame(NOT_CHARGING), (V.CLASS_CHARGING, 0))
        self.assertEqual(V.parse_frame(LINK_UP), (V.CLASS_LINK, 1))

    def test_a_bad_checksum_is_refused(self):
        bad = list(BATTERY_97)
        bad[13] = 50
        self.assertIsNone(V.parse_frame(bad))

    def test_a_request_is_not_a_reply(self):
        self.assertIsNone(V.parse_frame(V.make_frame(V.CLASS_BATTERY)))

    def test_short_and_empty_reads_are_refused(self):
        self.assertIsNone(V.parse_frame(None))
        self.assertIsNone(V.parse_frame([2, 2, 0x60]))


class PollTest(Base):
    def test_the_diagnostics_case_reads_the_level(self):
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online) for s in self.p.poll()],
                         [("razer:057a:", "Razer BlackShark V3", 97, False, True)])

    def test_only_the_battery_collection_is_used_and_nothing_else_is_sent(self):
        self.p.poll()
        self.assertEqual(self.d.opened, 1)
        # the wake frames in Synapse's order, then charging and battery: no PA frame
        self.assertEqual([(f[9], f[10]) for f in self.d.sent],
                         [(0x00, 0x02), (0x00, 0x2A), (0x80, 0x2A), (0x80, 0x21)])

    def test_polls_do_not_query_and_the_collection_stays_open(self):
        self.p.poll()
        for _ in range(50):
            self.d.now += 2            # far faster than any setting of the app
            self.assertEqual([s.level for s in self.p.poll()], [97])
        self.assertEqual(self.d.queries(), 1)
        self.assertEqual((self.d.opened, self.d.closed), (1, 0))

    def test_a_query_goes_out_after_the_silence_and_without_wake_frames(self):
        self.p.poll()
        self.d.sent.clear()
        self.d.now += V.REFRESH_S
        self.p.poll()
        self.assertEqual([(f[9], f[10]) for f in self.d.sent], [(0x80, 0x2A), (0x80, 0x21)])

    def test_a_push_replaces_the_level_and_postpones_the_query(self):
        self.p.poll()
        self.d.now += V.REFRESH_S - 1
        self.d.inbox.append(frame(V.CLASS_BATTERY, V.KIND_PUSH, 96))
        self.assertEqual([s.level for s in self.p.poll()], [96])
        self.d.now += V.REFRESH_S - 1
        self.assertEqual([s.level for s in self.p.poll()], [96])
        self.assertEqual(self.d.queries(), 1)

    def test_a_charging_push_is_shown(self):
        self.p.poll()
        self.d.inbox.append(frame(V.CLASS_CHARGING, V.KIND_PUSH, 1))
        self.assertTrue(self.p.poll()[0].charging)

    def test_link_up_drops_the_cache_and_wakes_again(self):
        self.p.poll()
        self.d.sent.clear()
        self.d.battery = frame(V.CLASS_BATTERY, V.KIND_REPLY, 80)
        self.d.inbox.append(LINK_UP)
        self.assertEqual([s.level for s in self.p.poll()], [80])
        self.assertEqual(self.d.sent[0][10], V.CLASS_INIT)

    def test_a_not_ready_answer_is_never_shown_as_a_level(self):
        self.d.battery = frame(V.CLASS_BATTERY, V.KIND_REPLY, V.NOT_READY)
        self.assertEqual(self.p.poll(), [])
        self.assertEqual(self.d.queries(), V.ATTEMPTS)

    def test_a_switched_off_headset_costs_one_ask_per_refresh(self):
        self.d.battery = None
        for _ in range(10):
            self.assertEqual(self.p.poll(), [])
            self.d.now += 2
        self.assertEqual(self.d.queries(), V.ATTEMPTS)

    def test_the_headset_leaves_the_tray_when_the_refresh_goes_unanswered(self):
        self.assertEqual(len(self.p.poll()), 1)
        self.d.battery = None
        self.d.now += V.REFRESH_S
        self.assertEqual(self.p.poll(), [])          # like every switched-off Razer headset

    def test_a_headset_switched_on_later_is_read_at_its_link_up_push(self):
        self.d.battery = None
        self.assertEqual(self.p.poll(), [])
        self.d.battery = BATTERY_97
        self.d.inbox.append(LINK_UP)
        self.assertEqual([s.level for s in self.p.poll()], [97])

    def test_a_dead_handle_is_replaced(self):
        self.p.poll()
        self.d.fail = OSError("device disconnected")
        self.assertEqual(self.p.poll(), [])
        self.assertEqual(self.d.closed, 1)
        self.d.fail = None
        self.assertEqual([s.level for s in self.p.poll()], [97])
        self.assertEqual(self.d.opened, 2)

    def test_an_unplugged_dongle_closes_the_collection(self):
        self.p.poll()
        self.entries = []
        self.assertEqual(self.p.poll(), [])
        self.assertEqual((self.d.closed, self.p._v3), (1, {}))

    def test_no_battery_collection_is_said_and_nothing_is_opened(self):
        self.entries = [e for e in ENTRIES if e["usage_page"] != 0xFF14]
        self.assertEqual(self.p.poll(), [])
        self.assertEqual(self.d.opened, 0)
        self.assertIn("  no ff14:0001 collection among 3", self.p.diagnostics())


if __name__ == "__main__":
    unittest.main()
