"""Tests for providers/urx.py - the URX Core75 on its 2.4 GHz dongle (#245).

Frames are the ones the reporter's probe captured:

    request  00 | 08 00 07 00 ...      report id 0, VIA custom get, channel 0, value 7
    reply    08 00 07 4c 00 ...        echoed header, level 0x4c = 76 %
    refused  ff 07 00 ...              what comes back without the report-id byte

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import urx as U  # noqa: E402

CAPTURED_76 = [0x08, 0x00, 0x07, 0x4C] + [0x00] * 28
CAPTURED_76_PREFIXED = [0x00] + CAPTURED_76
REFUSED = [0xFF, 0x07] + [0x00] * 30


class FrameTest(unittest.TestCase):
    def test_the_request_is_report_id_then_via_custom_get(self):
        req = U.make_request()
        self.assertEqual(U.REPORT_SIZE, len(req))
        self.assertEqual([0x00, 0x08, 0x00, 0x07], req[:4])
        self.assertEqual([0] * (U.REPORT_SIZE - 4), req[4:])

    def test_the_captured_reply_reads_76(self):
        self.assertEqual((76, False), U.parse_reply(CAPTURED_76))

    def test_a_reply_that_keeps_the_report_id_in_front_is_read_too(self):
        self.assertEqual((76, False), U.parse_reply(CAPTURED_76_PREFIXED))

    def test_the_refused_answer_is_not_a_reading(self):
        self.assertIsNone(U.parse_reply(REFUSED))

    def test_another_channel_or_value_is_not_the_battery(self):
        self.assertIsNone(U.parse_reply([0x08, 0x01, 0x07, 0x4C] + [0] * 28))
        self.assertIsNone(U.parse_reply([0x08, 0x00, 0x06, 0x4C] + [0] * 28))

    def test_a_zero_or_oversized_level_is_refused(self):
        self.assertIsNone(U.parse_reply([0x08, 0x00, 0x07, 0x00] + [0] * 28))
        self.assertIsNone(U.parse_reply([0x08, 0x00, 0x07, 0x65] + [0] * 28))

    def test_a_short_frame_is_ignored(self):
        self.assertIsNone(U.parse_reply([0x08, 0x00]))


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
    INFO = [{"path": b"via", "interface_number": 1, "usage_page": 0xFF60, "usage": 0x0061,
             "product_id": U.PID, "product_string": "URX Core75 Wireless"}]

    def setUp(self):
        self._saved = (U.hid, U.hidlist, U.time)
        self.now = [1000.0]

    def tearDown(self):
        U.hid, U.hidlist, U.time = self._saved

    def poll(self, frames, infos=None):
        dev = FakeDevice(frames)
        U.time = types.SimpleNamespace(time=lambda: self.now[0])
        U.hid = types.SimpleNamespace(device=lambda: dev)
        U.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO if infos is None else infos))
        return U.UrxProvider().poll(), dev

    def test_the_keyboard_is_read_from_its_via_channel(self):
        out, dev = self.poll([CAPTURED_76])
        self.assertEqual([(s.key, s.name, s.level, s.online, s.kind) for s in out],
                         [("urx:3002", "URX Core75", 76, True, "keyboard")])
        self.assertFalse(out[0].charging_known)      # no charging byte on this board
        self.assertEqual([b"via"], dev.opened)
        self.assertEqual([bytes(U.make_request())], dev.written)

    def test_another_channel_is_not_opened(self):
        # if only the vendor page (0xFF00) is present there is no VIA channel at all
        infos = [{"path": b"vendor", "interface_number": 2, "usage_page": 0xFF00,
                  "usage": 0x0001, "product_id": U.PID}]
        out, dev = self.poll([], infos=infos)
        self.assertEqual([], dev.opened)
        self.assertIsNone(out[0].level)

    def test_silence_keeps_the_last_level_greyed(self):
        U.time = types.SimpleNamespace(time=lambda: self.now[0])
        provider = U.UrxProvider()
        dev = FakeDevice([CAPTURED_76])
        U.hid = types.SimpleNamespace(device=lambda: dev)
        U.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.INFO))
        self.assertEqual(76, provider.poll()[0].level)
        dev.frames = []
        self.now[0] += 30
        out = provider.poll()
        self.assertEqual((76, False), (out[0].level, out[0].online))
        self.now[0] += U.ASLEEP_KEEP + 1
        out = provider.poll()
        self.assertIsNone(out[0].level)

    def test_no_receiver_reports_nothing(self):
        out, _dev = self.poll([], infos=[])
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
