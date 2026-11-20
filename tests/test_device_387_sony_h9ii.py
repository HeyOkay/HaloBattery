"""Tests for providers/sony_h9ii.py - no hardware needed.

Frames from marcinjakubowski/zoneout (MIT), which reads the same audio-status
command on the H9 II; the test only fixes the shape this provider sends.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import sony_h9ii as S  # noqa: E402


def reply(level=60, charging=0x00):
    r = [0x02, 0x0C, 0x01, 0x00, 0xFC, 0x08, 0x96, 0xC3, 0x41, 0x06,
         0x01, 0x01, 0x00, 0x9C] + [0x00] * 50
    r[14] = charging
    r[15] = level
    return r


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.path = None
        self.flushed = False

    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def write(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        if not self.flushed:
            self.flushed = True
            return []           # the pre-query drain
        return self.replies.pop(0) if self.replies else []


def entries(vid=S.VID, pid=S.PID):
    return [{"vendor_id": vid, "product_id": pid, "interface_number": 5,
             "usage_page": S.PAGE, "usage": S.USAGE,
             "path": b"sony-h9ii", "product_string": "INZONE H9 II - Chat"}]


class ParseTest(unittest.TestCase):
    def test_the_request_shape(self):
        req = S.request()
        self.assertEqual(len(req), 64)
        self.assertEqual(req[:10], [0x02, 0x0C, 0x01, 0x00, 0xFC, 0x08,
                                    0x96, 0xC3, 0x41, 0x06])
        self.assertEqual(req[13], (0x06 + 0x9C) & 0xFF)

    def test_level_and_charge(self):
        self.assertEqual(S.parse_reply(reply(60, 0)), (60, False))
        self.assertEqual(S.parse_reply(reply(42, 1)), (42, True))

    def test_other_commands_and_bad_levels_are_refused(self):
        self.assertIsNone(S.parse_reply(reply()[0:9] + [0x07] + reply()[10:]))
        self.assertIsNone(S.parse_reply(reply(101)))
        self.assertIsNone(S.parse_reply([]))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (S.hid, S.hidlist)

    def tearDown(self):
        S.hid, S.hidlist = self._saved

    def poll(self, fake, ents):
        S.hid = types.SimpleNamespace(device=lambda: fake)
        S.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        p = S.SonyH9IIProvider()
        return p.poll(), p.diagnostics()

    def test_the_status_reply_becomes_a_headset(self):
        fake = FakeDevice([reply(60, 0x01)])
        out, _ = self.poll(fake, entries())
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("sony_h9ii", "Sony INZONE H9 II", 60, True, True,
                           "sony_h9ii", "headset")])
        self.assertEqual(fake.sent, [bytes(S.request())])

    def test_a_wrong_collection_is_ignored(self):
        ents = entries()
        ents[0]["usage_page"] = 0xFF13
        out, _ = self.poll(FakeDevice([reply()]), ents)
        self.assertEqual(out, [])

    def test_no_reply_reports_nothing(self):
        out, _ = self.poll(FakeDevice([]), entries())
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
