"""Tests for providers/dareu.py. No hardware is needed.

The fake receiver is the DAREU A950's Compx dongle (260d:1074) with the collections a
real one lists. The request goes to ff02:0002 as a feature report and the reply comes
back on ff01:0000, as on the real receiver, whose reply to command 0x04 was
09 04 00 00 00 02 5a 00 .. ec (90 %, on battery).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import dareu as D  # noqa: E402

REAL_REPLY = bytes.fromhex("09 04 00 00 00 02 5a 00 00 00 00 00 00 00 00 00 ec")

# (interface, usage page, usage) as the real receiver lists them
SHAPE = [
    (0, 0x0001, 0x0002), (1, 0xFF03, 0x0000), (1, 0x000C, 0x0001), (1, 0x0001, 0x0080),
    (1, 0xFF01, 0x0000), (1, 0xFF04, 0x0002), (1, 0x0001, 0x0006), (1, 0xFF02, 0x0002),
]


def reply(level, charging=0, command=0x04, status=0x00, break_checksum=False):
    frame = [0x09, command, status, 0, 0, 2, level, charging] + [0] * 8
    ck = D.checksum(frame) ^ (0xFF if break_checksum else 0)
    return bytes(frame + [ck])


class FakeReceiver:
    def __init__(self, replies, pid=0x1074):
        self.pid = pid
        self.replies = list(replies)
        self.features = []          # (usage, frame) of every feature report sent
        self.paths = {b"col%d" % i: (page, usage) for i, (_, page, usage) in enumerate(SHAPE)}

    def enumerate(self, vid):
        if vid != D.VID:
            return []
        return [{"path": p, "product_id": self.pid, "usage_page": u[0], "usage": u[1]}
                for p, u in self.paths.items()]

    def device(self):
        rx = self

        class Dev:
            def open_path(self, path):
                self.usage = rx.paths[path]

            def send_feature_report(self, frame):
                rx.features.append((self.usage, list(frame)))
                return len(frame)

            def read(self, length, timeout_ms=0):
                if self.usage != D.REPLY_USAGE or not rx.features or not rx.replies:
                    return b""
                return rx.replies.pop(0)

            def close(self):
                pass

        return Dev()


class DareuTest(unittest.TestCase):
    def setUp(self):
        self._hid, self._hidlist = D.hid, D.hidlist

    def tearDown(self):
        D.hid, D.hidlist = self._hid, self._hidlist

    def poll(self, replies, pid=0x1074):
        rx = FakeReceiver(replies, pid)
        D.hidlist = types.SimpleNamespace(enumerate=rx.enumerate)
        D.hid = types.SimpleNamespace(device=rx.device)
        return D.DareuProvider().poll(), rx

    def test_real_reply_reads_90_percent(self):
        self.assertEqual(REAL_REPLY, reply(90))
        out, rx = self.poll([REAL_REPLY])
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s.key, s.name, s.level, s.charging, s.kind),
                         ("dareu:260d1074", "DAREU A950 (2.4 GHz)", 90, False, "mouse"))
        # one feature report, on ff02:0002, with the checksum making the frame sum to 0x55
        self.assertEqual(len(rx.features), 1)
        usage, frame = rx.features[0]
        self.assertEqual(usage, D.COMMAND_USAGE)
        self.assertEqual(frame[:2], [0x08, 0x04])
        self.assertEqual(len(frame), 17)
        self.assertEqual(sum(frame) % 256, 0x55)

    def test_charging_flag(self):
        out, _ = self.poll([reply(55, charging=1)])
        self.assertEqual((out[0].level, out[0].charging), (55, True))

    def test_skips_ack_event_and_bad_checksum(self):
        out, _ = self.poll([reply(0, status=0x01), reply(10, command=0x0A),
                            reply(30, break_checksum=True), reply(77)])
        self.assertEqual(out[0].level, 77)

    def test_no_valid_reply_no_device(self):
        out, _ = self.poll([reply(0, status=0x01), reply(101)])
        self.assertEqual(out, [])

    def test_other_pid_from_mousepower(self):
        out, _ = self.poll([reply(40)], pid=0x1084)
        self.assertEqual(out[0].key, "dareu:260d1084")


if __name__ == "__main__":
    unittest.main()
