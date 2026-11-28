"""Tests for providers/waizowl.py - no hardware needed.

Frames are the ones Waizowl Hub (owlnest.waizowl.com) sends to the OGM 8K receiver
(1915:0728), with the replies captured on an OGM Pro V2 (see the provider docstring).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import waizowl as W  # noqa: E402


def framed(head):
    body = list(head) + [0] * (63 - len(head))
    return body + [(0xA1 - sum(body)) & 0xFF]


# captured on hardware
DONGLE_AWAKE = framed([0x01, 0x00, 0x8C, 0x01, 0x0A, 0x15, 0x19, 0x28, 0x07, 0x06, 0x01, 0xF5, 0x07])
NOTIFY_46 = framed([0x41, 0x00, 0x8C, 0x01, 0x00, 0x00, 0x2E, 0x00, 0x01, 0x03, 0x06])


def notify(level):
    return framed([0x41, 0x00, 0x8C, 0x01, 0x00, 0x00, level, 0x00, 0x01, 0x03, 0x06])


def wired(level):
    return framed([0x01, 0x00, 0x8C, 0x01, 0x00, 0x01, level, 0x00, 0x01, 0x03, 0x03])


# captured on hardware, mouse on its USB cable (1915:07A2)
WIRED_100 = wired(0x64)


def dongle(awake):
    return framed([0x01, 0x00, 0x8C, 0x01, 0x0A if awake else 0x02, 0x15])


class FakeReceiver:
    """Answers each written command with the next queued reply for that command byte."""

    def __init__(self, replies=None, fail_open=False, fail_read=False):
        self.replies = {k: list(v) for k, v in (replies or {}).items()}
        self.pending = []
        self.sent = []
        self.path = None
        self.fail_open = fail_open
        self.fail_read = fail_read

    def open_path(self, path):
        if self.fail_open:
            raise OSError("gone")
        self.path = path

    def close(self):
        pass

    def set_nonblocking(self, value):
        pass

    def write(self, data):
        data = bytes(data)
        self.sent.append((self.path, data))
        queue = self.replies.get(data[1], [])
        if queue:
            self.pending.append(queue.pop(0))
        return len(data)

    def read(self, size, timeout_ms=0):
        if self.fail_read:
            raise OSError("device unplugged")
        return self.pending.pop(0) if self.pending else []


def entries(vid=0x1915, pid=0x0728):
    shape = [(3, 0xFF0A, 0x0001),     # the command collection
             (2, 0x000C, 0x0001),
             (2, 0x0001, 0x0080),
             (1, 0x0001, 0x0006),
             (0, 0x0001, 0x0002)]
    return [{"vendor_id": vid, "product_id": pid, "interface_number": i,
             "usage_page": p, "usage": u, "path": b"ogm-%d-%04x" % (i, p),
             "product_string": "OGM 8K"} for i, p, u in shape]


CMD_PATH = entries()[0]["path"]


class FrameTest(unittest.TestCase):
    def test_notify_frame_matches_the_hub_checksum(self):
        f = W.make_frame(W.CMD_NOTIFY)
        self.assertEqual(len(f), 64)
        self.assertEqual(f[:4], bytes([0x41, 0x00, 0x81, 0x01]))
        self.assertEqual(f[63], 0xDE)
        self.assertEqual(sum(f) & 0xFF, 0xA1)

    def test_dongle_frame(self):
        f = W.make_frame(W.CMD_DONGLE)
        self.assertEqual(f[63], 0x1E)


class ParseTest(unittest.TestCase):
    def test_captured_notify_reads_46(self):
        self.assertEqual(W.parse_notify(NOTIFY_46), 46)

    def test_levels_0_and_100(self):
        self.assertEqual(W.parse_notify(notify(0)), 0)
        self.assertEqual(W.parse_notify(notify(100)), 100)

    def test_level_above_100_is_refused(self):
        self.assertIsNone(W.parse_notify(notify(101)))
        self.assertIsNone(W.parse_notify(notify(0xFF)))

    def test_captured_29_percent_reply_with_a_different_last_byte_is_read(self):
        # captured at 29 %: the last byte (0x2C) does not follow the request checksum rule
        r = [0x41, 0x00, 0x8C, 0x01, 0x00, 0x00, 0x1D, 0x00, 0x01, 0x03, 0x06] + [0] * 52 + [0x2C]
        self.assertEqual(len(r), 64)
        self.assertEqual(W.parse_notify(r), 29)

    def test_last_byte_is_not_used_to_refuse_a_reply(self):
        odd = list(NOTIFY_46)
        odd[63] ^= 0x80
        self.assertEqual(W.parse_notify(odd), 46)

    def test_short_and_garbage_replies_are_refused(self):
        self.assertIsNone(W.parse_notify(None))
        self.assertIsNone(W.parse_notify([]))
        self.assertIsNone(W.parse_notify(NOTIFY_46[:20]))
        self.assertIsNone(W.parse_notify(framed([0x42, 0, 0, 1, 0, 0, 50])))
        self.assertIsNone(W.parse_notify(framed([0x41, 0, 0, 2, 0, 0, 50])))

    def test_dongle_status(self):
        self.assertIs(W.parse_dongle(DONGLE_AWAKE), True)
        self.assertIs(W.parse_dongle(dongle(False)), False)
        self.assertIsNone(W.parse_dongle(NOTIFY_46))
        self.assertIsNone(W.parse_dongle([1, 0, 0x8C]))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (W.hid, W.hidlist, W.REPLY_WAIT)
        W.REPLY_WAIT = 0.01

    def tearDown(self):
        W.hid, W.hidlist, W.REPLY_WAIT = self._saved

    def provider(self, fake, ents):
        W.hid = types.SimpleNamespace(device=lambda: fake)
        W.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        return W.WaizowlProvider()

    def test_awake_mouse_is_read_on_the_command_collection(self):
        fake = FakeReceiver({0x01: [DONGLE_AWAKE], 0x41: [NOTIFY_46]})
        out = self.provider(fake, entries()).poll()
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("waizowl:0728", "Waizowl OGM 8K", 46, False, True, "waizowl", "mouse")])
        self.assertEqual({p for p, _ in fake.sent}, {CMD_PATH})
        self.assertTrue(all(d[0] == 0x00 and len(d) == 65 for _, d in fake.sent))

    def test_sleeping_mouse_keeps_its_last_level_greyed(self):
        fake = FakeReceiver({0x01: [DONGLE_AWAKE, dongle(False)], 0x41: [notify(80)]})
        p = self.provider(fake, entries())
        self.assertEqual(p.poll()[0].level, 80)
        out = p.poll()
        self.assertEqual((out[0].level, out[0].online), (80, False))
        # asleep: the notify request is not sent
        self.assertEqual([d[1] for _, d in fake.sent], [0x01, 0x41, 0x01])

    def test_silent_receiver_gives_an_empty_offline_icon(self):
        out = self.provider(FakeReceiver(), entries()).poll()
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)

    def test_device_disappears_mid_poll(self):
        out = self.provider(FakeReceiver(fail_read=True), entries()).poll()
        self.assertEqual(len(out), 1)
        self.assertFalse(out[0].online)
        out = self.provider(FakeReceiver(fail_open=True), entries()).poll()
        self.assertFalse(out[0].online)

    def use(self, fake, ents):
        W.hid = types.SimpleNamespace(device=lambda: fake)
        W.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])

    def test_cable_without_a_known_level_says_charging_level_unknown(self):
        fake = FakeReceiver({0x01: [WIRED_100]})
        out = self.provider(fake, entries(pid=0x07A2)).poll()
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind, s.ui_message)
                          for s in out],
                         [("waizowl:0728", "Waizowl OGM Pro V2", None, True, True, "mouse",
                           "state.charging_level_unknown")])
        self.assertEqual([d[1:5] for _, d in fake.sent], [bytes([0x01, 0x00, 0x81, 0x01])])

    def test_cable_100_is_not_shown_the_receiver_level_is(self):
        # the real sequence: 29 % on the receiver, 100 on the cable, 35 % back on the receiver
        p = self.provider(FakeReceiver({0x01: [DONGLE_AWAKE], 0x41: [notify(29)]}), entries())
        self.assertEqual(p.poll()[0].level, 29)
        self.use(FakeReceiver({0x01: [WIRED_100]}), entries(pid=0x07A2))
        st = p.poll()[0]
        self.assertEqual((st.key, st.level, st.charging, st.online, st.ui_message, st.ui_params),
                         ("waizowl:0728", 29, True, True, "state.charging_was", {"level": 29}))
        self.use(FakeReceiver({0x01: [DONGLE_AWAKE], 0x41: [notify(35)]}), entries())
        st = p.poll()[0]
        self.assertEqual((st.key, st.level, st.charging, st.ui_message), ("waizowl:0728", 35, False, ""))

    def test_cable_and_receiver_give_one_icon_and_the_cable_wins(self):
        fake = FakeReceiver({0x01: [WIRED_100]})
        out = self.provider(fake, entries(pid=0x0728) + entries(pid=0x07A2)).poll()
        self.assertEqual([(s.key, s.charging) for s in out], [("waizowl:0728", True)])
        self.assertEqual(len(fake.sent), 1)   # the receiver is not asked

    def test_silent_cable_keeps_the_last_level_greyed(self):
        p = self.provider(FakeReceiver({0x01: [DONGLE_AWAKE], 0x41: [notify(70)]}), entries())
        p.poll()
        self.use(FakeReceiver(), entries(pid=0x07A2))
        out = p.poll()
        self.assertEqual([(s.level, s.online) for s in out], [(70, False)])

    def test_wired_parser(self):
        self.assertEqual(W.parse_wired_notify(WIRED_100), 100)
        self.assertIsNone(W.parse_wired_notify(wired(101)))
        self.assertIsNone(W.parse_wired_notify(NOTIFY_46))
        self.assertIsNone(W.parse_wired_notify(WIRED_100[:20]))

    def test_only_the_validated_collection_is_written(self):
        for changes in ({"usage_page": 0xFF00}, {"usage": 2}, {"usage_page": None}):
            with self.subTest(changes=changes):
                fake = FakeReceiver({0x01: [DONGLE_AWAKE], 0x41: [NOTIFY_46]})
                ents = [dict(e, **changes) for e in entries()]
                self.assertEqual(self.provider(fake, ents).poll(), [])
                self.assertEqual(fake.sent, [])

    def test_a_refused_or_short_write_reads_nothing(self):
        class Refused(FakeReceiver):
            def __init__(self, result):
                super().__init__({0x01: [DONGLE_AWAKE], 0x41: [NOTIFY_46]})
                self.result, self.reads = result, 0

            def write(self, data):
                super().write(data)
                return self.result

            def read(self, size, timeout_ms=0):
                self.reads += 1
                return super().read(size, timeout_ms)

        for result in (-1, 0, 64, None):
            with self.subTest(result=result):
                fake = Refused(result)
                out = self.provider(fake, entries()).poll()
                self.assertEqual([(s.level, s.online) for s in out], [(None, False)])
                self.assertEqual(fake.reads, 0)

    def test_other_ids_are_left_alone(self):
        fake = FakeReceiver({0x01: [DONGLE_AWAKE], 0x41: [NOTIFY_46]})
        self.assertEqual(self.provider(fake, entries(pid=0x0729)).poll(), [])
        self.assertEqual(self.provider(fake, entries(vid=0x1916)).poll(), [])
        self.assertEqual(fake.sent, [])


if __name__ == "__main__":
    unittest.main()
