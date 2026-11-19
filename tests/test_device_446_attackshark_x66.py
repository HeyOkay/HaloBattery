"""Tests for providers/attackshark_x66.py. No hardware is needed.

The dongle (0C45:FEFE, interface 3, ff60:0061) uses the Sonix 33-byte
output/input reports documented and tested on the AK980 V2 by
YellowHearth1/ajazz-ak980-v2-linux:

    request: report id 0 + 20 01 + zero padding, byte sum last
    reply:   20 01 00 LEVEL ... + byte sum

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import attackshark_x66 as X  # noqa: E402


def with_sum(data):
    data = list(data)
    data.append(sum(data) & 0xFF)
    return data


def reply(level=64, short=False, report_id=False):
    payload = [0x20, 0x01, 0x00, level]
    if short:
        data = with_sum(payload)
    else:
        data = with_sum(payload + [0x00] * (X.REPORT_LEN - 1 - len(payload)))
    if report_id:
        # 32 payload bytes + a report-id byte = 33, the Windows form.
        data = with_sum(payload + [0x00] * (X.REPORT_LEN - 2 - len(payload)))
        data = [0x00] + data
    return data


class ParseTest(unittest.TestCase):
    def test_the_request_has_the_documented_command_and_checksum(self):
        req = X.make_request()
        self.assertEqual(X.REPORT_LEN, len(req))
        self.assertEqual(0x00, req[0])
        self.assertEqual([0x20, 0x01], req[1:3])
        self.assertEqual(sum(req[:-1]) & 0xFF, req[-1])

    def test_the_level_is_read_from_a_full_reply(self):
        self.assertEqual(64, X.parse_reply(reply(64)))
        self.assertEqual(100, X.parse_reply(reply(100)))

    def test_a_short_reply_and_a_windows_report_id_are_both_read(self):
        self.assertEqual(64, X.parse_reply(reply(64, short=True)))
        self.assertEqual(64, X.parse_reply(reply(64, report_id=True)))

    def test_garbage_is_refused(self):
        self.assertIsNone(X.parse_reply([]))
        self.assertIsNone(X.parse_reply([0x20, 0x01, 0x00]))
        bad = reply(64)
        bad[0] = 0x21
        self.assertIsNone(X.parse_reply(bad))
        self.assertIsNone(X.parse_reply(reply(101)))



class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.reads = []
        self.opened = []

    def open_path(self, path):
        self.opened.append(path)

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms):
        self.reads.append((size, timeout_ms))
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    INFO = [{"path": b"ff60", "interface_number": 3,
             "usage_page": 0xFF60, "usage": 0x0061,
             "product_id": X.PID, "product_string": "X66 2.4G"}]

    def setUp(self):
        self._saved = (X.hid, X.hidlist, X.time)
        self.now = [1000.0]

    def tearDown(self):
        X.hid, X.hidlist, X.time = self._saved

    def make(self, replies, infos=None):
        dev = FakeDevice(replies)
        X.time = types.SimpleNamespace(time=lambda: self.now[0])
        X.hid = types.SimpleNamespace(device=lambda: dev)
        X.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO if infos is None else infos))
        return X.AttackSharkX66Provider(), dev

    def test_the_x66_is_read_without_a_charging_claim(self):
        provider, dev = self.make([reply(64)])
        out = provider.poll()
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in out],
                         [("attackshark:x66", "ATTACKSHARK X66",
                           64, False, True, "keyboard")])
        self.assertFalse(out[0].charging_known)
        self.assertEqual([bytes(X.make_request())], dev.writes)

    def test_a_sleeping_radio_gets_one_more_request(self):
        provider, dev = self.make([[]] * X.READ_ATTEMPTS + [reply(64)])
        self.assertEqual(64, provider.poll()[0].level)
        self.assertEqual(2, len(dev.writes))

    def test_no_reply_keeps_the_last_reading_greyed(self):
        provider, dev = self.make([reply(64)])
        self.assertEqual(64, provider.poll()[0].level)
        dev.replies = []
        self.now[0] += 30
        out = provider.poll()
        self.assertEqual([(64, False, False)],
                         [(s.level, s.charging, s.online) for s in out])
        self.now[0] += X.ASLEEP_KEEP + 1
        out = provider.poll()
        self.assertEqual([(None, False)], [(s.level, s.online) for s in out])

    def test_another_keyboard_on_the_same_dongle_is_not_claimed(self):
        provider, _dev = self.make([reply(64)], infos=[
            dict(self.INFO[0], product_string="OTHER 2.4G")])
        self.assertEqual([], provider.poll())

    def test_no_dongle_reports_nothing(self):
        provider, _dev = self.make([], infos=[])
        self.assertEqual([], provider.poll())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
