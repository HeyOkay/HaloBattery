"""Tests for providers/atk_bitmouse.py and its place in providers/pulsar.py. No hardware.

The fake receiver is 373b:1216 with the collections the diagnostics of #291 list. It
answers on ff05:0001 only, with the reply atk-tool's pull request #15 captured from this
id; the reply layout and the tail of old bytes are OpenMouse's (src/bitmouse).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import atk_bitmouse as A, pulsar as P  # noqa: E402

VID, PID = 0x373B, 0x1216
SHAPE = [(0, 0x0001, 0x0002), (1, 0xFFB3, 0x0001), (2, 0x0001, 0x0006),
         (2, 0x000C, 0x0001), (2, 0xFFB1, 0x0001), (2, 0xFF05, 0x0001)]


def reply(command, *payload, status=0x00, sn=0x05, tail=()):
    """A frame of 64 bytes; tail is what the device left behind the payload."""
    frame = [A.REPORT_ID, A.MARKER, status, sn, 0x00, command, len(payload)]
    frame += list(payload) + list(tail)
    return frame + [0] * (64 - len(frame))


# atk-tool's capture of this id: 90 %, with its "voltage" bytes behind the payload
CAPTURED = reply(A.CMD_BATTERY, 0x5A, tail=(0xBC, 0x10))


class FakeReceiver:
    def __init__(self, battery=(CAPTURED,), charging=(reply(A.CMD_CHARGING, 0),)):
        self.answers = {A.CMD_BATTERY: list(battery), A.CMD_CHARGING: list(charging)}
        self.written = []       # (usage page, usage, frame)
        self.opened = []
        self.pending = []

    def enumerate(self, vid):
        if vid != VID:
            return []
        return [{"path": b"col%d" % i, "interface_number": iface, "usage_page": page,
                 "usage": usage, "product_id": PID, "vendor_id": VID}
                for i, (iface, page, usage) in enumerate(SHAPE)]

    def device_class(self):
        rx = self

        class FakeDevice:
            def open_path(self, path):
                self.col = SHAPE[int(path[3:])]
                rx.opened.append(self.col)

            def write(self, frame):
                rx.written.append((self.col[1], self.col[2], list(frame)))
                rx.pending += rx.answers[frame[6]][:1]
                del rx.answers[frame[6]][:1]
                return len(frame)

            def read(self, n, timeout=0):
                # hidapi's read() with no timeout waits until a report comes
                assert timeout > 0 or rx.pending, "read() without a timeout would hang"
                return rx.pending.pop(0) if rx.pending else []

            def close(self):
                pass

        return FakeDevice


class FrameTest(unittest.TestCase):
    def test_the_battery_request_is_the_vendors(self):
        frame = A.make_request(A.CMD_BATTERY)
        self.assertEqual(len(frame), 64)
        self.assertEqual(frame[:9], [0x08, 0x7D, 0x72, 0x02, 0x00, 0x01, 0x07, 0x01, 0x00])
        self.assertEqual(set(frame[9:]), {0})

    def test_the_checksum_is_the_sum_of_the_rest(self):
        frame = A.make_request(A.CMD_CHARGING)
        self.assertEqual(frame[1], sum(frame[2:]) & 0xFF)

    def test_the_captured_reply_parses(self):
        self.assertEqual(A.parse_reply(CAPTURED, A.CMD_BATTERY), 90)

    def test_a_reply_to_another_command_or_an_error_is_refused(self):
        self.assertIsNone(A.parse_reply(CAPTURED, A.CMD_CHARGING))
        self.assertIsNone(A.parse_reply(reply(A.CMD_BATTERY, 90, status=0xFF), A.CMD_BATTERY))

    def test_a_reply_without_a_payload_does_not_read_the_old_tail(self):
        self.assertIsNone(A.parse_reply(reply(A.CMD_BATTERY, tail=(90,)), A.CMD_BATTERY))

    def test_other_frames_are_refused(self):
        self.assertIsNone(A.parse_reply(None, A.CMD_BATTERY))
        self.assertIsNone(A.parse_reply([0x08, 0x72], A.CMD_BATTERY))
        self.assertIsNone(A.parse_reply([0x09] + CAPTURED[1:], A.CMD_BATTERY))
        self.assertIsNone(A.parse_reply([0x08, 0x04] + CAPTURED[2:], A.CMD_BATTERY))


class PollTest(unittest.TestCase):
    def receiver(self, **kw):
        self.rx = FakeReceiver(**kw)
        P.hidlist = types.SimpleNamespace(enumerate=self.rx.enumerate)
        A.hid = types.SimpleNamespace(device=self.rx.device_class())
        return P.PulsarProvider()

    def setUp(self):
        saved = (P.hidlist, A.hid, A.REPLY_TIMEOUT_S)
        self.addCleanup(lambda: (setattr(P, "hidlist", saved[0]), setattr(A, "hid", saved[1]),
                                 setattr(A, "REPLY_TIMEOUT_S", saved[2])))
        A.REPLY_TIMEOUT_S = 0.01

    def test_the_diagnostics_case_reads_the_level(self):
        res = self.receiver().poll()
        self.assertEqual([(s.key, s.level, s.charging, s.online, s.kind) for s in res],
                         [("pulsar:373b1216", 90, False, True, "mouse")])

    def test_charging_comes_from_its_own_command_and_not_from_the_sequence_byte(self):
        res = self.receiver(charging=[reply(A.CMD_CHARGING, 1)]).poll()
        self.assertTrue(res[0].charging)
        res = self.receiver(battery=[reply(A.CMD_BATTERY, 90, sn=0x3B)]).poll()
        self.assertFalse(res[0].charging)

    def test_no_charging_answer_keeps_the_level(self):
        res = self.receiver(charging=[]).poll()
        self.assertEqual([(s.level, s.charging) for s in res], [(90, False)])

    def test_only_the_configuration_channel_is_written_to(self):
        self.receiver().poll()
        self.assertEqual({w[:2] for w in self.rx.written}, {(0xFF05, 0x0001)})

    def test_the_old_frame_never_reaches_this_receiver(self):
        self.receiver().poll()
        self.assertFalse([w for w in self.rx.written if w[2][1] == P.CMD_POWER])

    def test_without_the_channel_nothing_is_opened(self):
        p = self.receiver()
        self.rx.enumerate = lambda vid: [dict(e) for e in FakeReceiver().enumerate(vid)
                                         if e["usage_page"] != 0xFF05]
        P.hidlist = types.SimpleNamespace(enumerate=self.rx.enumerate)
        self.assertEqual(p.poll(), [])
        self.assertEqual(self.rx.opened, [])

    def test_a_sleeping_mouse_keeps_its_reading_greyed(self):
        p = self.receiver()
        self.assertEqual([s.online for s in p.poll()], [True])
        res = p.poll()                  # the fake has nothing left: the mouse went quiet
        self.assertEqual([(s.level, s.online, s.wake_on_input) for s in res],
                         [(90, False, True)])

    def test_an_error_status_or_a_zero_is_not_a_level(self):
        for battery in ([reply(A.CMD_BATTERY, 90, status=0xFF)], [reply(A.CMD_BATTERY, 0)],
                        [reply(A.CMD_BATTERY, 101)]):
            self.assertEqual(self.receiver(battery=battery).poll(), [])

    def test_the_pulsar_receivers_are_not_affected(self):
        self.assertNotIn((0x373B, 0x1085), A.PIDS)
        self.assertIn(0x1085, P.PIDS[VID])


if __name__ == "__main__":
    unittest.main()
