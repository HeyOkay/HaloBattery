"""Tests for providers/redsquare.py - Red Square Alumix TKL (RSQ-20055), #460.

The frames come from Red Square's web configurator: output report 7 carries
``05 01 01 0A 07 8B`` and the reply is ``05 A1 01 0A 07 8B <sleep> <debounce>
<battery> <module>``.  The battery byte is 0x4B = 75 in these fixtures.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import redsquare as R  # noqa: E402

REPLY_75 = [0x07, 0x05, 0xA1, 0x01, 0x0A, 0x07, 0x8B, 0x00, 0x00, 0x4B, 0x01]
REPLY_75_NO_ID = REPLY_75[1:]
REPLY_WRONG_CMD = [0x07, 0x05, 0xA1, 0x01, 0x0A, 0x07, 0x8A, 0x00, 0x00, 0x4B, 0x01]


class FrameTest(unittest.TestCase):
    def test_the_request_is_report_id_then_the_settings_read(self):
        req = R.make_request()
        self.assertEqual(R.REPORT_SIZE + 1, len(req))
        self.assertEqual([0x07, 0x05, 0x01, 0x01, 0x0A, 0x07, 0x8B], req[:7])
        self.assertEqual([0] * (R.REPORT_SIZE - 6), req[7:])

    def test_the_captured_reply_reads_75(self):
        self.assertEqual(75, R.parse_reply(REPLY_75))

    def test_a_reply_without_the_report_id_is_read_too(self):
        self.assertEqual(75, R.parse_reply(REPLY_75_NO_ID))

    def test_a_reply_to_another_command_is_not_the_battery(self):
        self.assertIsNone(R.parse_reply(REPLY_WRONG_CMD))

    def test_a_zero_or_oversized_level_is_refused(self):
        self.assertIsNone(R.parse_reply(REPLY_75[:9] + [0] + REPLY_75[10:]))
        self.assertIsNone(R.parse_reply(REPLY_75[:9] + [0x65] + REPLY_75[10:]))

    def test_a_short_reply_is_ignored(self):
        self.assertIsNone(R.parse_reply([0x05, 0xA1, 0x01, 0x0A]))


class FakeDevice:
    def __init__(self, frames):
        self.frames = list(frames)
        self.written = []
        self.opened = []

    def open_path(self, path):
        self.opened.append(path)

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        return self.frames.pop(0) if self.frames else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    INFO = [{"path": b"config", "interface_number": 1,
             "usage_page": R.USAGE_PAGE, "usage": R.USAGE,
             "product_id": R.PID, "product_string": R.PRODUCT}]

    def setUp(self):
        self._saved = (R.hid, R.hidlist, R.time)
        self.now = [1000.0]

    def tearDown(self):
        R.hid, R.hidlist, R.time = self._saved

    def poll(self, frames, infos=None, provider=None):
        dev = FakeDevice(frames)
        R.time = types.SimpleNamespace(time=lambda: self.now[0])
        R.hid = types.SimpleNamespace(device=lambda: dev)
        R.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO if infos is None else infos))
        provider = provider or R.RedSquareProvider()
        return provider.poll(), dev

    def test_the_keyboard_is_read_from_the_config_channel(self):
        out, dev = self.poll([REPLY_75])
        self.assertEqual([(s.key, s.name, s.level, s.online, s.kind) for s in out],
                         [("redsquare:20055", "Red Square Alumix TKL", 75, True, "keyboard")])
        self.assertFalse(out[0].charging_known)
        self.assertTrue(out[0].wake_on_input)
        self.assertEqual([b"config"], dev.opened)
        self.assertEqual([bytes(R.make_request())], dev.written)

    def test_another_product_string_is_not_queried(self):
        infos = [dict(self.INFO[0], product_string="RSQ_20056_2.4G")]
        out, dev = self.poll([], infos=infos)
        self.assertEqual([], out)
        self.assertEqual([], dev.opened)

    def test_a_receiver_without_the_config_collection_is_not_opened(self):
        infos = [dict(self.INFO[0], usage_page=0xFF00, usage=0x0001)]
        out, dev = self.poll([], infos=infos)
        self.assertEqual([], dev.opened)
        self.assertEqual(1, len(out))
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)

    def test_silence_keeps_the_last_level_greyed_for_a_while(self):
        provider = R.RedSquareProvider()
        out, _dev = self.poll([REPLY_75], provider=provider)
        self.assertEqual((75, True), (out[0].level, out[0].online))
        out, _dev = self.poll([], provider=provider)
        self.assertEqual((75, False), (out[0].level, out[0].online))
        self.now[0] += R.ASLEEP_KEEP + 1
        out, _dev = self.poll([], provider=provider)
        self.assertIsNone(out[0].level)

    def test_no_receiver_reports_nothing(self):
        out, _dev = self.poll([], infos=[])
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
