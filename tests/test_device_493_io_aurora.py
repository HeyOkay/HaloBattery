"""Tests for providers/io_aurora.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import io_aurora as A  # noqa: E402
from providers import compx  # noqa: E402


def reply(level, power=0, *, valid=True):
    frame = [0x08, 0x04] + [0] * 4 + [level, power] + [0] * 8
    frame.append(compx.checksum(frame) if valid else 0)
    return frame


AURORA = {
    "path": b"aurora",
    "product_id": A.PID,
    "product_string": "IO Aurora Receiver",
    "interface_number": 1,
    "usage_page": 0xFF02,
    "usage": 0x0002,
}


class CandidateTest(unittest.TestCase):
    def test_the_control_collection_is_chosen(self):
        info = dict(AURORA, usage_page=0xFF05, usage=0x0000)
        self.assertIs(A.candidate([info, AURORA]), AURORA)

    def test_other_products_with_the_same_vid_are_refused(self):
        other = dict(AURORA, product_id=0xF50D, product_string="LAMZU")
        self.assertIsNone(A.candidate([other]))

    def test_a_different_product_string_is_refused(self):
        other = dict(AURORA, product_string="USB Receiver")
        self.assertIsNone(A.candidate([other]))


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.opened = []
        self.armed = False

    def open_path(self, path):
        self.opened.append(path)

    def write(self, data):
        self.sent.append(bytes(data))
        self.armed = True
        return len(data)

    def read(self, size, timeout):
        if not self.armed:
            return []
        return self.replies.pop(0) if self.replies else []

    def get_feature_report(self, rid, size):
        return []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    def setUp(self):
        self.saved = (A.hid, A.hidlist, A.time)
        self.now = [1000.0]
        A.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)

    def tearDown(self):
        A.hid, A.hidlist, A.time = self.saved

    def poll(self, replies, infos=None):
        dev = FakeDevice(replies)
        A.hid = types.SimpleNamespace(device=lambda: dev)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [AURORA] if infos is None else infos)
        return A.IoAuroraProvider().poll(), dev

    def test_the_read_only_power_query_and_level(self):
        out, dev = self.poll([reply(76)])
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in out],
                         [(A.KEY, "IO Aurora", 76, False, True, "mouse")])
        self.assertEqual([bytes(compx.make_request())], dev.sent)

    def test_charging_is_the_power_byte(self):
        out, _ = self.poll([reply(76, power=1)])
        self.assertTrue(out[0].charging)

    def test_bad_checksum_is_not_a_reading(self):
        out, _ = self.poll([reply(76, valid=False)])
        self.assertFalse(out[0].online)
        self.assertIsNone(out[0].level)

    def test_silence_keeps_the_last_reading_greyed(self):
        provider = A.IoAuroraProvider()
        dev = FakeDevice([reply(76)])
        A.hid = types.SimpleNamespace(device=lambda: dev)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [AURORA])
        self.assertEqual(76, provider.poll()[0].level)
        dev.replies = []
        out = provider.poll()
        self.assertEqual([(76, False, False)], [(s.level, s.charging, s.online) for s in out])
        self.assertTrue(out[0].wake_on_input)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
