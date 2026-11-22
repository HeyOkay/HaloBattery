"""Tests for providers/attackshark_r11.py (#525). No hardware is needed.

The fake dongle has the collections from the reporter's diagnostics
(3554:fb44, CompX: ff02:0002 next to ff03/ff04/ff05 vendor pages) and answers
the CompX report-8 power query.

Frames are the documented CompX layout, built once and pasted here as bytes:

    request  08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49
    reply    08 04 00 00 00 00 55 01 00 00 00 00 00 00 00 00 f3   -> (85, charging)
    reply    08 04 00 00 00 00 2a 00 00 00 00 00 00 00 00 00 1f   -> (42, on battery)

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import attackshark_r11 as A  # noqa: E402

REQUEST = bytes.fromhex("08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49")
REPLY_85_CHARGING = bytes.fromhex("08 04 00 00 00 00 55 01 00 00 00 00 00 00 00 00 f3")
REPLY_42_BATTERY = bytes.fromhex("08 04 00 00 00 00 2a 00 00 00 00 00 00 00 00 00 1f")
NOT_A_REPLY = bytes.fromhex("08 05 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00")


class FakeDevice:
    """Answers `reads` in order; an empty item means "nothing arrived"."""

    def __init__(self, replies, path_log=None, fail_open=False):
        self.queue = list(replies)
        self.sent = []
        self.opened = []
        self.closed = 0
        self.fail_open = fail_open
        self.path_log = path_log
        self.asked = False          # a real dongle only answers after the question

    def open_path(self, path):
        if self.fail_open:
            raise OSError("Access is denied")
        self.opened.append(path)

    def read(self, n, timeout_ms=0):
        if not self.asked or not self.queue:
            return []
        item = self.queue.pop(0)
        return list(item)

    def write(self, data):
        self.sent.append(bytes(data))
        self.asked = True
        return len(data)

    def close(self):
        self.closed += 1


def entries(pid=A.PID):
    """The R11 Ultra's collections, exactly as in the issue's diagnostics."""
    shape = [(0, 0x0001, 0x0002), (1, 0xFF02, 0x0002), (1, 0xFF04, 0x0002),
             (1, 0xFF05, 0x0000), (1, 0x0001, 0x0002), (1, 0xFF03, 0x0000),
             (1, 0x000C, 0x0001), (1, 0x0001, 0x0080), (2, 0x0001, 0x0006)]
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": b"\\\\?\\hid#vid_3554&pid_fb44&mi_%02x#%04x_%04x" % (i, p, u),
             "product_string": "ATTACK SHARK Mouse"}
            for i, p, u in shape]


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist, A.time)
        A.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)
        self.provider = A.AttackSharkR11Provider()
        self.device = FakeDevice([])

    def tearDown(self):
        A.hid, A.hidlist, A.time = self._saved

    def poll(self, rows, replies, **kwargs):
        self.device = FakeDevice(replies, **kwargs)
        A.hid = types.SimpleNamespace(device=lambda: self.device)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(rows))
        return self.provider.poll()

    def test_no_dongle_no_icon(self):
        self.assertEqual(self.poll([], []), [])
        self.assertEqual(self.device.opened, [])
        self.assertEqual(self.device.sent, [])

    def test_a_charging_mouse_reports_its_level_and_the_cable(self):
        out = self.poll(entries(), [REPLY_85_CHARGING])
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s.key, s.name, s.level, s.charging, s.online),
                         ("attackshark:fb44", "Attack Shark R11 Ultra", 85, True, True))
        self.assertEqual((s.source, s.kind), ("attackshark_r11", "mouse"))

    def test_on_battery_is_not_charging(self):
        out = self.poll(entries(), [REPLY_42_BATTERY])
        self.assertEqual((out[0].level, out[0].charging), (42, False))

    def test_the_question_is_the_compX_power_frame_and_only_that(self):
        self.poll(entries(), [REPLY_85_CHARGING])
        self.assertEqual(self.device.sent, [REQUEST])

    def test_only_the_ff02_control_collection_is_opened(self):
        self.poll(entries(), [REPLY_85_CHARGING])
        self.assertEqual(len(self.device.opened), 1)
        self.assertIn(b"ff02_0002", self.device.opened[0].lower())
        self.assertEqual(self.device.closed, 1)

    def test_noise_before_the_answer_is_tolerated(self):
        out = self.poll(entries(), [NOT_A_REPLY, REPLY_85_CHARGING])
        self.assertEqual(out[0].level, 85)
        self.assertTrue(any("reply 1" in line for line in self.provider.diagnostics()))

    def test_a_silent_dongle_keeps_the_last_level_greyed(self):
        self.poll(entries(), [REPLY_85_CHARGING])
        out = self.poll(entries(), [], )
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].level, out[0].online, out[0].wake_on_input),
                         (85, False, True))

    def test_a_silent_dongle_with_no_history_shows_nothing(self):
        self.assertEqual(self.poll(entries(), []), [])

    def test_a_dongle_that_cannot_be_opened_does_not_crash(self):
        self.assertEqual(self.poll(entries(), [], fail_open=True), [])
        self.assertTrue(any("open:" in line for line in self.provider.diagnostics()))

    def test_the_greyed_level_expires(self):
        A.time = types.SimpleNamespace(time=lambda: 1000.0, sleep=lambda s: None)
        self.poll(entries(), [REPLY_85_CHARGING])
        A.time = types.SimpleNamespace(time=lambda: 1000.0 + A.ASLEEP_KEEP + 1,
                                       sleep=lambda s: None)
        self.assertEqual(self.poll(entries(), []), [])

    def test_another_compx_pid_is_not_claimed(self):
        self.assertEqual(self.poll(entries(pid=0xFB09), [REPLY_85_CHARGING]), [])
        self.assertEqual(self.device.opened, [])

    def test_no_ff02_collection_is_reported_not_guessed(self):
        rows = [d for d in entries() if d["usage_page"] != 0xFF02]
        self.assertEqual(self.poll(rows, [REPLY_85_CHARGING]), [])
        self.assertTrue(any("no ff02:0002" in line for line in self.provider.diagnostics()))


if __name__ == "__main__":
    unittest.main()
