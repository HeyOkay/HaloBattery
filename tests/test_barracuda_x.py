"""Tests for providers/barracuda_x.py - the Barracuda X (2022) dongle (1532:0552).

The frames mirror mehdibouchami/razer-barracuda-x-battery (MIT), whose Macronix-bridge
framing comes from KhromotozzDevOut/razer-barracuda-battery-tray:

    app mode   > 01 40                 < 01 40 01 01
    request    > 01 80 <len> 50 41 <group> <seq> <body...>
    reply      < 01 80 <len> 50 49 .... [payload: group, 0x80|seq, status, mvLo, mvHi]

Run from the repository root:

    python -m unittest discover -s tests
"""

# Independent wire fixtures: mehdibouchami/razer-barracuda-x-battery src/Protocol.cs, Macronix report 01.
# Keep reply bytes/offsets independent of the provider constants under test.
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import barracuda_x as B  # noqa: E402


def race_reply(group, seq, payload):
    """One RACE frame as the dongle packs it: PI + 6 bytes + length + payload."""
    frame = [0x50, 0x49, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, len(payload), 0x00]
    return frame + list(payload)


def report(frames, report_id=0x01):
    body = []
    for f in frames:
        body.extend(f)
    return [report_id, 0x80, len(body)] + body


def battery_reply(mv, seq, group=0x06):
    return race_reply(group, seq, [group, 0x80 | seq, 0x00, mv & 0xFF, mv >> 8])


class FrameTest(unittest.TestCase):
    def test_the_request_frame_is_the_bridge_header_then_race(self):
        req = B.wrap(0x01, B.race_request(0x06, 5, (0x01, 0x00, 0x31)))
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
        self.assertEqual(len(dev.written), 10)  # handshake, remote, seven samples, local
        self.assertEqual(dev.written[-1], bytes([
            1, 0x80, 7, 0x50, 0x41, 0x0e, 9, 2, 0xe1, 0]))

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


class ModeRestorationTest(unittest.TestCase):
    """Remote mode may take effect even when its acknowledgment is lost."""
    def run_ask(self, remote, *, battery=None, local=None, handshake=True):
        provider = B.BarracudaXProvider()
        device = mock.Mock()
        calls = []
        def race(dev, group, body):
            self.assertIs(dev, device)
            command = (group, body)
            calls.append(command)
            value = remote if command == (0x0E, (0x02, 0xE1, 0x01)) else (
                local if command == (0x0E, (0x02, 0xE1, 0x00)) else battery)
            if isinstance(value, Exception):
                raise value
            return value
        with mock.patch.object(B.hid, "device", return_value=device), \
                mock.patch.object(provider, "_enter_app_mode", return_value=handshake), \
                mock.patch.object(provider, "_race", side_effect=race), \
                mock.patch.object(B.time, "sleep", return_value=None):
            result = provider._ask({"path": b"source-confirmed-macronix"})
        device.close.assert_called_once()
        return result, calls, provider.diagnostics()

    def test_missing_short_or_refused_remote_ack_still_restores_local_mode(self):
        for ack in (None, [], [0x0e], [0x0e, 0x81], [0x0e, 0x81, 2]):
            with self.subTest(ack=ack):
                result, calls, lines = self.run_ask(ack, local=[0x0e, 0x82, 0])
                self.assertIsNone(result)
                self.assertEqual(calls, [(0x0E, (0x02, 0xE1, 0x01)), (0x0E, (0x02, 0xE1, 0x00))])
                self.assertTrue(any("remote mode refused" in line for line in lines))

    def test_remote_exchange_exception_does_not_skip_restore(self):
        result, calls, lines = self.run_ask(OSError("remote ACK read failed"),
                                             local=[0x0e, 0x82, 0])
        self.assertIsNone(result)
        self.assertEqual(calls, [(0x0E, (0x02, 0xE1, 0x01)), (0x0E, (0x02, 0xE1, 0x00))])
        self.assertTrue(any("remote ACK read failed" in line for line in lines))

    def test_sampling_failure_restores_and_preserves_original_error(self):
        result, calls, lines = self.run_ask([0x0e, 0x81, 0],
            battery=OSError("battery disconnected"), local=OSError("local disconnected"))
        self.assertIsNone(result)
        self.assertEqual(calls, [(0x0E, (0x02, 0xE1, 0x01)), (0x06, (0x01, 0x00, 0x31)), (0x0E, (0x02, 0xE1, 0x00))])
        self.assertTrue(any("battery disconnected" in line for line in lines))
        self.assertTrue(any("local mode restoration failed" in line for line in lines))

    def test_unconfirmed_restore_is_visible_in_diagnostics(self):
        for ack in (None, [], [0x0e, 0x82], [0x0e, 0x82, 2]):
            with self.subTest(ack=ack):
                result, calls, lines = self.run_ask(None, local=ack)
                self.assertIsNone(result)
                self.assertEqual(calls, [(0x0E, (0x02, 0xE1, 0x01)), (0x0E, (0x02, 0xE1, 0x00))])
                self.assertTrue(any("local mode restoration unconfirmed" in line for line in lines))

    def test_failed_app_handshake_does_not_issue_remote_or_local_commands(self):
        result, calls, _lines = self.run_ask(None, handshake=False)
        self.assertIsNone(result)
        self.assertEqual(calls, [])

    def test_lost_ack_uses_only_existing_exact_macronix_frames(self):
        provider = B.BarracudaXProvider()
        class LostAckDevice(FakeDevice):
            def __init__(self):
                super().__init__([[1, 0x40, 1, 1]])
                self.closed = False
            def write(self, data):
                value = super().write(data)
                if list(data)[-3:] == [2, 0xe1, 0]:
                    self.frames.append(report([race_reply(0x0e, 2, [0x0e, 0x82, 0])]))
                return value
            def close(self):
                self.closed = True
        device = LostAckDevice()
        now = [0.0]
        def tick():
            now[0] += .1
            return now[0]
        with mock.patch.object(B.hid, "device", return_value=device), \
                mock.patch.object(B, "_sequence", 0), \
                mock.patch.object(B, "time", types.SimpleNamespace(time=tick, sleep=lambda s: None)):
            self.assertIsNone(provider._ask({"path": b"macronix"}))
        self.assertEqual(device.written, [
            bytes([1, 0x40]),
            bytes([1, 0x80, 7, 0x50, 0x41, 0x0e, 1, 2, 0xe1, 1]),
            bytes([1, 0x80, 7, 0x50, 0x41, 0x0e, 2, 2, 0xe1, 0])])
        self.assertTrue(device.closed)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
