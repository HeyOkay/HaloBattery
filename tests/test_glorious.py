"""Tests for providers/glorious.py. No hardware is needed.

The frames are the Sinowealth battery query documented by
Rodrigo-200/GloriousBatteryMonitor-Go for the Model O Wireless (258a:2022):

    request  [id=0x00][00 00 02 02 00 83] ...
    reply    [0] id [1] status (A1 awake / A2 waking / A4 asleep)
             [2..5] echo [6] 0x83 [7] charging (0 = no) [8] level %

The collection shape is #281's diagnostics: vendor collections on `ffa0:0001`,
`ffff:0001` and `ffff:0000`.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import glorious as G  # noqa: E402


def reply(level, charging=0x00, status=0xA1):
    f = [0x00, status, 0x00, 0x00, 0x00, 0x00, 0x83, charging, level] + [0x00] * 56
    return f


CAPTURED_75 = reply(0x4B)
CAPTURED_75_CHARGING = reply(0x4B, charging=0x01)


class ParseTest(unittest.TestCase):
    def test_the_request_starts_with_the_documented_command(self):
        req = G.make_request(65)
        self.assertEqual(65, len(req))
        self.assertEqual(0x00, req[0])
        self.assertEqual(G.BATTERY_COMMAND, req[1:7])
        self.assertEqual([0] * 58, req[7:])

    def test_the_documented_reply(self):
        self.assertEqual((75, False), G.parse_reply(CAPTURED_75))
        self.assertEqual((75, True), G.parse_reply(CAPTURED_75_CHARGING))

    def test_any_non_zero_charge_byte_counts_as_charging(self):
        two = G.parse_reply(reply(50, charging=0x02))
        three = G.parse_reply(reply(50, charging=0x03))
        self.assertIsNotNone(two)
        self.assertIsNotNone(three)
        self.assertTrue(two[1])
        self.assertTrue(three[1])

    def test_a_waking_mouse_is_read(self):
        self.assertEqual((80, False), G.parse_reply(reply(80, status=0xA2)))

    def test_an_asleep_mouse_is_not_a_reading(self):
        self.assertIsNone(G.parse_reply(reply(80, status=G.STATUS_ASLEEP)))

    def test_other_frames_are_refused(self):
        bad = list(CAPTURED_75)
        bad[G.FUNC_INDEX] = 0x81
        self.assertIsNone(G.parse_reply(bad))
        self.assertIsNone(G.parse_reply(reply(101)))
        self.assertIsNone(G.parse_reply([0x00, 0xA1, 0x00]))
        self.assertIsNone(G.parse_reply([]))


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.opened = []

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def get_feature_report(self, rid, length):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    INFO = [{"path": b"ffa0", "interface_number": 1,
             "usage_page": 0xFFA0, "usage": 0x0001,
             "product_id": G.PID, "product_string": "Model O Wireless"}]

    def setUp(self):
        self._saved = (G.hid, G.hidlist, G.time, G.feature_length)
        self.now = [1000.0]

    def tearDown(self):
        G.hid, G.hidlist, G.time, G.feature_length = self._saved

    def poll(self, replies, infos=None):
        dev = FakeDevice(replies)
        G.feature_length = lambda path: 65
        G.time = types.SimpleNamespace(
            time=lambda: self.now[0],
            sleep=lambda s: None)
        G.hid = types.SimpleNamespace(device=lambda: dev)
        G.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO if infos is None else infos))
        return G.GloriousProvider().poll(), dev

    def test_the_model_o_is_read_and_the_query_is_the_documented_one(self):
        out, dev = self.poll([CAPTURED_75])
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in out],
                         [("glorious:2022", "Glorious Model O Wireless", 75, False, True,
                           "mouse")])
        self.assertEqual([bytes(G.make_request(65))], dev.sent)

    def test_charging_is_reported(self):
        out, _dev = self.poll([CAPTURED_75_CHARGING])
        self.assertTrue(out[0].charging)

    WIRED = {"path": b"wired", "interface_number": 0, "product_id": 0x2011,
             "product_string": "Model O"}

    def test_a_plugged_cable_counts_as_charging(self):
        # the wired id's presence is how GloriousBatteryMonitor-Go reads "on the charger"
        out, _dev = self.poll([CAPTURED_75], infos=self.INFO + [self.WIRED])
        self.assertEqual((75, True, True), (out[0].level, out[0].charging, out[0].online))

    def test_the_cable_alone_keeps_the_last_level_and_says_charging(self):
        provider = G.GloriousProvider()
        dev = FakeDevice([CAPTURED_75])
        G.feature_length = lambda path: 65
        G.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        G.hid = types.SimpleNamespace(device=lambda: dev)
        G.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.INFO))
        self.assertEqual(75, provider.poll()[0].level)
        # on the cable: the receiver goes quiet, the wired id appears
        dev.replies = []
        G.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(self.INFO + [self.WIRED]))
        out = provider.poll()
        self.assertEqual((75, True, True), (out[0].level, out[0].charging, out[0].online))

    def test_the_cable_with_no_history_is_charging_without_a_level(self):
        out, _dev = self.poll([], infos=self.INFO + [self.WIRED])
        self.assertEqual((None, True, True), (out[0].level, out[0].charging, out[0].online))

    def test_no_answer_keeps_the_last_reading_greyed(self):
        provider = G.GloriousProvider()
        dev = FakeDevice([CAPTURED_75])
        G.feature_length = lambda path: 65
        G.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        G.hid = types.SimpleNamespace(device=lambda: dev)
        G.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.INFO))
        self.assertEqual(75, provider.poll()[0].level)
        # the next answer is an asleep status: no new level, the icon greys out
        dev.replies = [reply(75, status=G.STATUS_ASLEEP)]
        self.now[0] += 30
        out = provider.poll()
        self.assertEqual([(75, False, False)], [(s.level, s.charging, s.online) for s in out])
        self.now[0] += G.ASLEEP_KEEP + 1
        out = provider.poll()
        self.assertEqual([(None, False)], [(s.level, s.online) for s in out])

    def test_no_receiver_reports_nothing(self):
        out, _dev = self.poll([], infos=[])
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
