"""Tests for providers/jbl_quantum350.py. No hardware is needed.

The collection shape is copied from the #454 diagnostics: 0ecb:206b on
interface 3, generic desktop usage 0x0001:0x0000. The feature exchange is the
one in krisgfx9/Wireless-Device-Battery-Monitor's JBL driver.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import jbl_quantum350 as J  # noqa: E402


def entry(pid=J.PID, iface=J.INTERFACE, page=J.PAGE, usage=J.USAGE):
    return {"vendor_id": J.VID, "product_id": pid, "interface_number": iface,
            "usage_page": page, "usage": usage, "path": b"jbl-350",
            "product_string": "JBL Quantum350 Wireless", "feature_report_byte_length": 2}


class FakeDevice:
    def __init__(self, replies):
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls = []
        self.closed = 0

    def open_path(self, path):
        self.path = path

    def get_feature_report(self, report_id, length):
        self.calls.append((report_id, length))
        queue = self.replies.get(report_id, [])
        if not queue:
            return []
        value = queue.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed += 1


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (J.hid, J.hidlist)
        self.provider = J.JblQuantum350Provider()

    def tearDown(self):
        J.hid, J.hidlist = self._saved

    def poll(self, infos, dev):
        J.hid = fake_hid(dev)
        J.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_reads_link_then_level(self):
        dev = FakeDevice({0x68: [[0x68, 1]], 0x49: [[0x49, 55]]})
        res = self.poll([entry()], dev)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.online, r.kind) for r in res],
                         [("jbl:206b", "JBL Quantum 350 Wireless", 55, False, True, "headset")])
        self.assertEqual(dev.calls, [(0x68, 2), (0x49, 2)])
        self.assertFalse(res[0].charging_known)
        self.assertEqual(dev.closed, 1)

    def test_zero_and_hundred_are_real_levels(self):
        for level in (0, 100):
            dev = FakeDevice({0x68: [[0x68, 1]], 0x49: [[0x49, level]]})
            res = self.poll([entry()], dev)
            self.assertEqual(res[0].level, level)
            self.assertTrue(res[0].online)

    def test_headset_off_does_not_read_the_level(self):
        dev = FakeDevice({0x68: [[0x68, 0]]})
        res = self.poll([entry()], dev)
        self.assertEqual([(r.level, r.online) for r in res], [(None, False)])
        self.assertEqual(dev.calls, [(0x68, 2)])

    def test_headset_off_keeps_the_last_level_offline(self):
        dev = FakeDevice({0x68: [[0x68, 1]], 0x49: [[0x49, 60]]})
        self.poll([entry()], dev)
        off = FakeDevice({0x68: [[0x68, 0]]})
        res = self.poll([entry()], off)
        self.assertEqual([(r.level, r.online) for r in res], [(60, False)])

    def test_only_the_interface_3_generic_desktop_collection_is_opened(self):
        # the dongle also exposes a consumer-control collection; it must not be read
        dev = FakeDevice({0x68: [[0x68, 1]], 0x49: [[0x49, 50]]})
        res = self.poll([entry(iface=0, usage=0x01, page=0x000C), entry(iface=3)], dev)
        self.assertEqual(res[0].level, 50)
        self.assertEqual(len(dev.calls), 2)

    def test_wrong_pid_is_not_claimed(self):
        dev = FakeDevice({0x68: [[0x68, 1]], 0x49: [[0x49, 50]]})
        self.assertEqual(self.poll([entry(pid=0x2088)], dev), [])
        self.assertEqual(dev.calls, [])

    def test_mismatched_or_short_feature_reply_is_not_a_level(self):
        for reply in ([], [0x49], [0x68, 60], [0x49, 60, 0]):
            dev = FakeDevice({0x68: [[0x68, 1]], 0x49: [reply, reply]})
            res = self.poll([entry()], dev)
            self.assertIsNone(res[0].level, reply)
            self.assertFalse(res[0].online)

    def test_level_above_100_is_refused(self):
        dev = FakeDevice({0x68: [[0x68, 1], [0x68, 1]], 0x49: [[0x49, 101], [0x49, 101]]})
        res = self.poll([entry()], dev)
        self.assertIsNone(res[0].level)
        self.assertFalse(res[0].online)

    def test_read_errors_do_not_escape(self):
        dev = FakeDevice({0x68: [OSError("unplugged")], 0x49: []})
        res = self.poll([entry()], dev)
        self.assertEqual(len(res), 1)
        self.assertFalse(res[0].online)
        self.assertTrue(any("unplugged" in line for line in self.provider.diagnostics()))

    def test_no_answer_then_retry(self):
        dev = FakeDevice({0x68: [[0x68, 1], [0x68, 1]], 0x49: [[], [0x49, 42]]})
        res = self.poll([entry()], dev)
        self.assertEqual(res[0].level, 42)
        self.assertEqual(dev.calls, [(0x68, 2), (0x49, 2), (0x68, 2), (0x49, 2)])


if __name__ == "__main__":
    unittest.main()
