"""Tests for the Sony INZONE H9 serial battery provider; no hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import sony_inzone as S  # noqa: E402


def frame(level=73, charging=1):
    return S.SIGNATURE + bytes([0, 0, 0, charging, level, 0])


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakePort:
    def __init__(self, clock, answer=None):
        self.clock = clock
        self.answer = answer
        self.response = b""
        self.writes = []
        self.read_count = 0
        self.dtr = False
        self.rts = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    @property
    def in_waiting(self):
        return len(self.response)

    def write(self, command):
        self.writes.append(command)
        if command == S.QUERY and self.answer is not None:
            self.response += self.answer
        return len(command)

    def read(self, size):
        self.read_count += 1
        if not self.response:
            self.clock.now += 0.2
            return b""
        result, self.response = self.response[:size], self.response[size:]
        return result


class ParseTest(unittest.TestCase):
    def test_finds_frame_after_noise(self):
        expected = frame()
        self.assertEqual(S.find_frame(b"noise" + expected + b"tail"), expected)

    def test_rejects_bad_signature_and_invalid_values(self):
        expected = frame()
        self.assertIsNone(S.find_frame(b"noise" + b"\x00" + expected[1:]))
        self.assertIsNone(S.find_frame(S.SIGNATURE + bytes([0, 0, 0, 2, 73, 0])))
        self.assertIsNone(S.find_frame(S.SIGNATURE + bytes([0, 0, 0, 1, 101, 0])))
        self.assertIsNone(S.find_frame(S.SIGNATURE + bytes([0, 0, 0, 1])))


class PollTest(unittest.TestCase):
    def setUp(self):
        self.saved = (S.serial, S.list_ports, S.time)
        self.clock = FakeClock()
        self.port = FakePort(self.clock, b"prefix" + frame(88, 0) + b"extra")
        S.serial = types.SimpleNamespace(
            SerialException=OSError,
            Serial=lambda *args, **kwargs: self.port,
        )
        S.list_ports = types.SimpleNamespace(comports=lambda: [
            types.SimpleNamespace(vid=S.VID, pid=S.PID, device="COM7"),
            types.SimpleNamespace(vid=0x1234, pid=S.PID, device="COM8"),
        ])
        S.time = types.SimpleNamespace(monotonic=self.clock.monotonic, sleep=self.clock.sleep)

    def tearDown(self):
        S.serial, S.list_ports, S.time = self.saved

    def test_polls_matching_port_and_stops_after_frame(self):
        result = S.SonyInzoneProvider().poll()
        self.assertEqual([(r.name, r.level, r.charging, r.kind) for r in result],
                         [("Sony INZONE H9", 88, False, "headset")])
        self.assertEqual(self.port.writes, [S.INIT_1, S.INIT_2, S.QUERY])
        self.assertLess(self.clock.now, 1.5)

    def test_poll_reports_charging_state(self):
        self.port.answer = frame(88, 1)
        result = S.SonyInzoneProvider().poll()
        self.assertEqual([(r.level, r.charging) for r in result], [(88, True)])

    def test_no_response_returns_no_status_after_single_query(self):
        self.port.answer = None
        result = S.SonyInzoneProvider().poll()
        self.assertEqual(result, [])
        self.assertEqual(self.port.writes, [S.INIT_1, S.INIT_2, S.QUERY])
        self.assertLess(self.clock.now, 2.5)


if __name__ == "__main__":
    unittest.main()
