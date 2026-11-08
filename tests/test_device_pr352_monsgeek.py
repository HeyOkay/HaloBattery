"""Tests for providers/monsgeek.py - no hardware needed.

The exchange is the one MonsGeek's own Windows driver uses (MonsGeek Driver v4,
resources/app/dist/js/index.bc699543.js, ___dongleCheck24GStatusByF7): a 65-byte feature
report, report id 0, payload ``F7`` and zeros, read back on the same report id. The answer
carries the keyboard level at payload byte 1, the dongle's mouse level at byte 2 and the
device type at byte 6 - with the report id in front of it, r[2], r[3] and r[7]. Verified on
a FUN60 Pro receiver (3151:5026): 54 %, later 47 %, on repeated polls.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import monsgeek as G  # noqa: E402


def answer(level=54, mouse=100, kind=1):
    """A status reply, report id first, as hidapi returns it."""
    return [0x00, 0x00, level, mouse, 0x01, 0x01, 0x01, kind] + [0x00] * 57


class FakeDongle:
    """One fake receiver for every open_path; only `answer_path` answers."""

    def __init__(self, reply=None, answer_path=None):
        self.reply = reply
        self.answer_path = answer_path
        self.path = None
        self.sent = []

    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def send_feature_report(self, data):
        self.sent.append((self.path, bytes(data)))
        return len(data)

    def get_feature_report(self, report_id, length):
        if self.path == self.answer_path and self.reply is not None:
            return list(self.reply)
        return [0x00] * length


def entries(pid=0x5026, vid=0x3151):
    shape = [(0, 0x0001, 0x0006),      # the keyboard collection: never written to
             (1, 0x0001, 0x0006),
             (1, 0x000C, 0x0001),
             (1, 0xFFFF, 0x0001),
             (2, 0xFFFF, 0x0002)]      # the configuration collection
    return [{"vendor_id": vid, "product_id": pid, "interface_number": i,
             "usage_page": p, "usage": u,
             "path": b"mg-%d-%04x-%02x" % (i, p, u),
             "product_string": "MonsGeek 2.4G Wireless Keyboard"}
            for i, p, u in shape]


CONFIG = entries()[4]["path"]          # ffff:0002 on interface 2


class ParseTest(unittest.TestCase):
    def test_the_frame_is_the_drivers(self):
        self.assertEqual(G.make_request(), [0x00, 0xF7] + [0x00] * 63)
        self.assertEqual(len(G.make_request()), 65)

    def test_the_answer_reads_the_keyboard_level(self):
        self.assertEqual(G.parse_status(answer(54, 100, 1)), (54, 100, 1))
        self.assertEqual(G.parse_status(answer(7, 100, 3)), (7, 100, 3))

    def test_an_asleep_receiver_that_answers_zeros_is_refused(self):
        self.assertIsNone(G.parse_status([0x00] * 65))

    def test_an_unknown_device_type_is_refused(self):
        self.assertIsNone(G.parse_status(answer(54, 100, 0)))
        self.assertIsNone(G.parse_status(answer(54, 100, 9)))

    def test_a_level_above_100_is_refused(self):
        self.assertIsNone(G.parse_status(answer(200, 100, 1)))

    def test_a_short_answer_is_refused(self):
        self.assertIsNone(G.parse_status([0x00, 0x00, 0x33]))
        self.assertIsNone(G.parse_status(None))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (G.hid, G.hidlist)

    def tearDown(self):
        G.hid, G.hidlist = self._saved

    def poll(self, fake, ents):
        G.hid = types.SimpleNamespace(device=lambda: fake)
        G.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        p = G.MonsGeekProvider()
        return p.poll(), p.diagnostics()

    def test_only_the_configuration_collection_is_written_to(self):
        ents = entries()
        fake = FakeDongle(answer(54, 100, 1), answer_path=CONFIG)
        out, diag = self.poll(fake, ents)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("monsgeek:5026", "MonsGeek 2.4G Wireless Keyboard",
                           54, False, True, "monsgeek", "keyboard")])
        self.assertEqual({p for p, _ in fake.sent}, {CONFIG})
        self.assertEqual(fake.sent[0][1], bytes(G.make_request()))

    def test_a_keyboard_only_dongle_does_not_report_a_mouse(self):
        ents = entries()
        fake = FakeDongle(answer(54, 100, 1), answer_path=CONFIG)
        out, _ = self.poll(fake, ents)
        self.assertEqual([s.kind for s in out], ["keyboard"])

    def test_silence_reports_nothing_and_says_so(self):
        ents = entries()
        fake = FakeDongle(None, answer_path=CONFIG)
        out, diag = self.poll(fake, ents)
        self.assertEqual(out, [])
        self.assertTrue(any("no 0xF7 answer" in line for line in diag))

    def test_another_vendor_id_is_left_alone(self):
        ents = entries(vid=0x1234)
        fake = FakeDongle(answer(54, 100, 1), answer_path=CONFIG)
        out, _ = self.poll(fake, ents)
        self.assertEqual(out, [])
        self.assertEqual(fake.sent, [])


if __name__ == "__main__":
    unittest.main()
