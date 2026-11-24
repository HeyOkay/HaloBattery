"""Tests for providers/royuan.py: the ROYUAN 2.4 GHz receiver (3151:4011).

No hardware. The frames follow dniminenn/sharkfin's PROTOCOL.md, whose status
reply was read on a 3151:4011 receiver with a paired keyboard:
`01 60 00 00 01 01 01 00 00 ...` - battery 0x60 = 96, keyboard online (byte 3
is 0), device type 1.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import royuan as R  # noqa: E402


def payload(level=96, online=True, kind=1):
    p = bytearray(R.LENGTH)
    p[0] = 1
    p[1] = level
    p[3] = 0 if online else 1
    p[5] = 1
    p[6] = kind
    return p


def status(level=96, online=True, kind=1):
    """What hidapi returns: the report id, then the 64 payload bytes."""
    return [R.REPORT_ID] + list(payload(level, online, kind))


class FakeDevice:
    def __init__(self, replies=(), send_error=None, open_error=None):
        self.replies = list(replies)
        self.sent = []
        self.opened = []
        self.closed = 0
        self.send_error = send_error
        self.open_error = open_error

    def open_path(self, path):
        self.opened.append(path)
        if self.open_error is not None:
            raise self.open_error

    def send_feature_report(self, data):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(list(data))
        return len(data)

    def get_feature_report(self, report_id, length):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        self.closed += 1


def entry(pid=R.PID, page=0xFFFF, usage=0x0002, iface=2, path=b"royuan"):
    return {"vendor_id": R.VID, "product_id": pid, "usage_page": page, "usage": usage,
            "interface_number": iface, "path": path,
            "product_string": "2.4G Wireless Keyboard"}


class ParseTests(unittest.TestCase):
    def test_the_captured_status_frame_reads(self):
        self.assertEqual((96, True), R.parse_status(status(96)))

    def test_the_payload_without_a_report_id_reads(self):
        self.assertEqual((88, True), R.parse_status(list(payload(88))))

    def test_offline_is_reported_as_such(self):
        self.assertEqual((96, False), R.parse_status(status(96, online=False)))

    def test_the_kind_must_carry_a_keyboard(self):
        self.assertIsNone(R.parse_status(status(kind=2)))
        self.assertIsNotNone(R.parse_status(status(kind=3)))

    def test_zero_and_out_of_range_levels_are_not_levels(self):
        self.assertIsNone(R.parse_status(status(level=0)))
        self.assertIsNone(R.parse_status(status(level=101)))
        self.assertIsNone(R.parse_status(status(level=255)))
        self.assertIsNone(R.parse_status([]))
        self.assertIsNone(R.parse_status([R.REPORT_ID] + [0] * 12))     # short

    def test_the_request_is_the_documented_frame(self):
        req = R.status_request()
        self.assertEqual(R.REPORT_LEN, len(req))
        self.assertEqual([R.REPORT_ID, R.STATUS_CMD], req[:2])
        self.assertEqual(0x08, req[1 + R.CHECKSUM_INDEX])              # 0xFF - 0xF7
        self.assertEqual(0, any(req[2:8]))
        self.assertEqual(0, any(req[9:]))


class PollTests(unittest.TestCase):
    def setUp(self):
        self._saved = (R.hid, R.hidlist, R.time)
        self.now = [1000.0]
        R.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        self.dev = FakeDevice()
        self.provider = R.RoyuanProvider()

    def tearDown(self):
        R.hid, R.hidlist, R.time = self._saved

    def poll(self, infos, dev=None):
        dev = dev or self.dev
        R.hid = types.SimpleNamespace(device=lambda: dev)
        R.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_the_keyboard_is_read_from_the_receiver_status(self):
        dev = FakeDevice([status(96)])
        out = self.poll([entry()], dev)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online,
                           s.charging_known, s.kind, s.source) for s in out],
                         [("royuan:4011", "ROYUAN 2.4G keyboard", 96, False, True,
                           False, "keyboard", "royuan")])
        self.assertEqual([R.status_request()], dev.sent)
        self.assertEqual([b"royuan"], dev.opened)
        self.assertIn("-> 96%", "\n".join(self.provider.diagnostics()))

    def test_the_interface_two_collection_is_tried_first(self):
        first = entry(iface=1, path=b"first")
        second = entry(iface=2, path=b"second")
        dev = FakeDevice([status(77)])
        out = self.poll([first, second], dev)
        self.assertEqual(77, out[0].level)
        self.assertEqual([b"second"], dev.opened)

    def test_another_collection_is_not_written(self):
        dev = FakeDevice([status(96)])
        self.assertEqual([], self.poll([entry(page=0xFFFF, usage=0x0001)], dev))
        self.assertEqual([], dev.sent)
        self.assertEqual(0, dev.closed)

    def test_another_product_is_ignored(self):
        self.assertEqual([], self.poll([entry(pid=0x4012)], FakeDevice([status(96)])))

    def test_an_offline_keyboard_keeps_the_last_level_greyed(self):
        self.poll([entry()], FakeDevice([status(88)]))
        self.now[0] += 60
        out = self.poll([entry()], FakeDevice([status(88, online=False)]))
        self.assertEqual([(s.level, s.online) for s in out], [(88, False)])
        self.assertIn("keyboard offline", "\n".join(self.provider.diagnostics()))

    def test_the_greyed_level_expires(self):
        self.poll([entry()], FakeDevice([status(88)]))
        self.now[0] += R.ASLEEP_KEEP + 1
        out = self.poll([entry()], FakeDevice())
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])

    def test_a_silent_receiver_is_offline_without_a_level(self):
        out = self.poll([entry()], FakeDevice())
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])
        self.assertIn("no 0xF7 answer", "\n".join(self.provider.diagnostics()))

    def test_a_send_error_does_not_raise(self):
        dev = FakeDevice(send_error=OSError("busy"))
        out = self.poll([entry()], dev)
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])
        self.assertIn("send: busy", "\n".join(self.provider.diagnostics()))

    def test_an_open_error_does_not_raise(self):
        dev = FakeDevice(open_error=OSError("busy"))
        out = self.poll([entry()], dev)
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])
        self.assertIn("open: busy", "\n".join(self.provider.diagnostics()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
