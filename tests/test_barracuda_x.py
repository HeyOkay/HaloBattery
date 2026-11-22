"""Tests for providers/barracuda_x.py - the Barracuda X (2022) dongle (1532:0552).

The frames mirror mehdibouchami/razer-barracuda-x-battery (MIT), whose Macronix-bridge
framing comes from KhromotozzDevOut/razer-barracuda-battery-tray:

    app mode   > 01 40                 < 01 40 01 01
    request    > 01 80 <len> 50 41 <group> <seq> <body...>
    reply      < 01 80 <len> 50 49 .... [payload: group, 0x80|seq, status, mvLo, mvHi]

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import barracuda_x as B  # noqa: E402


def race_reply(group, seq, payload):
    """One RACE frame as the dongle packs it: PI + 6 bytes + length + payload."""
    frame = [0x50, 0x49, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, len(payload), 0x00]
    return frame + list(payload)


def report(frames, report_id=B.REPORT_ID):
    body = []
    for f in frames:
        body.extend(f)
    return [report_id, 0x80, len(body)] + body


def battery_reply(mv, seq, group=0x06):
    return race_reply(group, seq, [group, 0x80 | seq, 0x00, mv & 0xFF, mv >> 8])


class FrameTest(unittest.TestCase):
    def test_the_request_frame_is_the_bridge_header_then_race(self):
        req = B.wrap(B.REPORT_ID, B.race_request(0x06, 5, (0x01, 0x00, 0x31)))
        self.assertEqual([0x01, 0x80, 7, 0x50, 0x41, 0x06, 0x05, 0x01, 0x00, 0x31], req)

    def test_the_answer_is_found_behind_an_unsolicited_event(self):
        event = race_reply(0x11, 1, [0x11, 0x81, 0x00, 0xFF])
        answer = battery_reply(3975, 9)
        payload = B.find_frame(report([event, answer]), 3, len(report([event, answer])) - 3, 0x06, 9)
        self.assertEqual([0x06, 0x89, 0x00, 0x87, 0x0F], payload)

    def test_a_frame_for_another_sequence_is_not_taken(self):
        answer = battery_reply(3975, 9)
        self.assertIsNone(B.find_frame(report([answer]), 3, len(report([answer])) - 3, 0x06, 10))

    def test_the_voltage_comes_from_the_payload(self):
        self.assertEqual(3975, B.battery_mv([0x06, 0x89, 0x00, 0x87, 0x0F]))
        self.assertIsNone(B.battery_mv([0x06, 0x89, 0x05, 0x87, 0x0F]))   # status != 0
        self.assertIsNone(B.battery_mv([0x06, 0x89, 0x00, 0x00, 0x00]))   # 0 mV

    def test_the_curve_matches_the_reference_points(self):
        self.assertEqual(0, B.to_percent(3300))
        self.assertEqual(72, B.to_percent(3975))     # 15 mV into the 70-80 % step
        self.assertEqual(100, B.to_percent(4150))
        self.assertEqual(100, B.to_percent(4300))
        self.assertEqual(50, B.to_percent(B.CURVE[5]))


class ChargingTest(unittest.TestCase):
    def test_a_steady_voltage_is_not_charging(self):
        self.assertFalse(B.is_charging([3950, 3951, 3949, 3950, 3951, 3950, 3950]))

    def test_a_spread_of_twelve_millivolts_is_charging(self):
        self.assertTrue(B.is_charging([4100, 4115, 4105, 4112, 4100, 4114, 4102]))

    def test_a_voltage_at_the_top_is_charging_even_without_jitter(self):
        self.assertTrue(B.is_charging([4190] * 7))


class FakeDevice:
    def __init__(self, frames):
        self.frames = list(frames)
        self.written = []

    def open_path(self, path):
        pass

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        return self.frames.pop(0) if self.frames else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    INFO = [{"path": b"macronix", "interface_number": 3, "usage_page": 0xFF00,
             "usage": 0x0000, "product_id": B.PID,
             "product_string": "Razer Barracuda X 2.4"}]

    def setUp(self):
        self._saved = (B.hid, B.hidlist, B.time)
        B._sequence = 0

    def tearDown(self):
        B.hid, B.hidlist, B.time = self._saved

    def poll(self, frames, infos=None):
        dev = FakeDevice(frames)
        now = [1000.0]

        def tick():                      # a clock that advances, so the read loop ends
            now[0] += 0.1
            return now[0]

        B.time = types.SimpleNamespace(time=tick, sleep=lambda s: None)
        B.hid = types.SimpleNamespace(device=lambda: dev)
        B.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO if infos is None else infos))
        return B.BarracudaXProvider().poll(), dev

    def script(self, mv=3975):
        seq = 1
        frames = [[0x01, 0x40, 0x01, 0x01]]                 # app mode
        frames.append(report([race_reply(0x0E, 1, [0x0E, 0x81, 0x00])]))   # remote mode
        for i in range(B.SAMPLES):
            frames.append(report([battery_reply(mv, 2 + i)]))
        frames.append(report([race_reply(0x0E, 9, [0x0E, 0x89, 0x00])]))   # local mode
        return frames

    def test_the_headset_is_read_and_the_level_comes_from_the_voltage(self):
        out, dev = self.poll(self.script(3975))
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in out],
                         [("barracuda-x:0552", "Razer Barracuda X (2022)", 72, False, True,
                           "headset")])
        self.assertEqual(bytes([0x01, 0x40]), dev.written[0])
        self.assertEqual(0x50, dev.written[1][3])          # the RACE request follows
        self.assertEqual(0x0E, dev.written[1][5])          # remote mode first

    def test_charging_is_reported_from_the_samples(self):
        frames = self.script()
        # replace the battery samples with a jittery, high set
        frames[2:2 + B.SAMPLES] = [report([battery_reply(mv, 2 + i)])
                                   for i, mv in enumerate([4130, 4100, 4140, 4105, 4135, 4102, 4138])]
        out, _dev = self.poll(frames)
        self.assertTrue(out[0].charging)

    def test_no_answer_is_an_unknown_reading(self):
        out, _dev = self.poll([])
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)

    def test_no_dongle_reports_nothing(self):
        out, _dev = self.poll([], infos=[])
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
