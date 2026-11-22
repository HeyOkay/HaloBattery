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
    "vendor_id": 0x3554,
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

    def test_the_cable_id_is_the_same_mouse(self):
        # #493's charging diagnostics: the receiver goes away and 3554:fb09 "IO Aurora" appears
        wired = dict(AURORA, path=b"wired", product_id=A.WIRED_PID, product_string="IO Aurora")
        self.assertTrue(A.is_aurora(wired))
        self.assertIs(A.candidate([AURORA, wired], A.WIRED_PID), wired)
        self.assertIs(A.candidate([AURORA, wired], A.PID), AURORA)

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

class WiredPollTest(unittest.TestCase):
    """The charging identity: the receiver is gone, 3554:fb09 answers."""

    class PathDevice:
        def __init__(self, by_path):
            self.by_path = {k: list(v) for k, v in by_path.items()}
            self.path = None
            self.sent = []

        def open_path(self, path):
            self.path = path

        def write(self, data):
            self.sent.append((self.path, bytes(data)))
            return len(data)

        def read(self, size, timeout):
            q = self.by_path.get(self.path, [])
            return q.pop(0) if q else []

        def get_feature_report(self, rid, size):
            return []

        def close(self):
            pass

    def test_the_cable_reading_keeps_the_same_icon_and_shows_charging(self):
        wired = dict(AURORA, path=b"wired", product_id=A.WIRED_PID, product_string="IO Aurora")
        dev = self.PathDevice({b"aurora": [], b"wired": [[], reply(64, power=1)]})
        saved = (A.hid, A.hidlist, A.time)
        A.time = types.SimpleNamespace(time=lambda: 1000.0, sleep=lambda s: None)
        A.hid = types.SimpleNamespace(device=lambda: dev)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [AURORA, wired])
        try:
            out = A.IoAuroraProvider().poll()
        finally:
            A.hid, A.hidlist, A.time = saved
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online) for s in out],
                         [(A.KEY, "IO Aurora", 64, True, True)])
        self.assertEqual(b"wired", dev.sent[-1][0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
