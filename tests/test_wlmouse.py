"""Tests for providers/wlmouse.py. No hardware is needed.

The HID layer is replaced: a fake receiver whose 0xFFFF collections answer or stay
silent, and a fake clock, so a walk runs without real waiting. The backoff of #62
item 5 - a switched-off mouse is not walked on every poll - is pinned by counting
how often the silent receiver is walked.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import wlmouse as W  # noqa: E402

RECEIVER_PID = 0xA880                    # Beast X Max, the confirmed one
CABLE_PID = 0xA881                       # Beast X Max cable, owner's diagnostic
UNKNOWN_PID = 0xA882                     # must never receive the feature query


def path_for(pid, iface):
    return (f"\\\\?\\hid#vid_36a7&pid_{pid:04x}&mi_{iface:02x}"
            f"#7&8f0a1b2c&0&0000#{{4d1e55b2-f16f-11cf-88cb-001111000030}}").encode()


def entry(pid, iface, usage_page=0xFFFF, usage=0x0000, product="WLmouse Beast X Max"):
    return {"product_id": pid, "interface_number": iface, "usage_page": usage_page,
            "usage": usage, "path": path_for(pid, iface), "product_string": product}


def feature_reply(batt=90, chg=1):
    return [0xA1, 0x00, 0x02, 0x02, 0x00, 0x83, chg, batt] + [0] * 57


def heartbeat(batt=90, chg=0):
    return [0x03, 0x00, batt, chg] + [0] * 60


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakePuck:
    """One fake HID device behind a path: feature answers and heartbeat frames."""

    def __init__(self, feature=None, beats=None):
        self.feature = feature          # what get_feature_report returns
        self.beats = list(beats or [])  # frames handed out by read()
        self.sends = 0
        self.sent_frames = []
        self.feature_reads = 0
        self.opens = 0


class FakeBus:
    def __init__(self, pucks, clock):
        self.pucks = pucks              # {path: FakePuck}
        self.clock = clock

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.pucks:
                    raise OSError("cannot open")
                self.puck = bus.pucks[path]
                self.puck.opens += 1

            def set_nonblocking(self, on):
                pass

            def send_feature_report(self, data):
                self.puck.sends += 1
                self.puck.sent_frames.append(list(data))
                return len(data)

            def get_feature_report(self, rid, length):
                self.puck.feature_reads += 1
                return self.puck.feature if self.puck.feature else []

            def read(self, n):
                if self.puck.beats:
                    frame = self.puck.beats.pop(0)
                    bus.clock.now += 0.02
                    return frame
                return []

            def close(self):
                pass

        return FakeDevice


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (W.hid, W.hidlist, W.time)
        self.clock = Clock()
        W.time = types.SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep)
        self.provider = W.WLmouseProvider()

    def tearDown(self):
        W.hid, W.hidlist, W.time = self._saved

    def poll(self, entries, pucks):
        bus = FakeBus(pucks, self.clock)
        W.hid = types.SimpleNamespace(device=bus.device_class())
        W.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()


# ------------------------------------------------------------------ parse
class ParseTest(unittest.TestCase):
    def test_captured_beast_x_max_cable_reply(self):
        # Literal diagnostic bytes from the owner's 2026-11-22 A881 capture,
        # interface 2 FFFF:0000, independent of feature_reply() and W.QUERY.
        reply = bytes.fromhex("00 a1 00 02 02 00 83 01 60 00 00 00")
        self.assertEqual(W.parse_feature(reply), (96, True))

    def test_feature_reply(self):
        self.assertEqual(W.parse_feature(feature_reply(90, 1)), (90, True))
        self.assertEqual(W.parse_feature(feature_reply(0, 0)), (0, False))
        self.assertEqual(W.parse_feature(feature_reply(101, 1)), (None, None))
        self.assertEqual(W.parse_feature([0] * 65), (None, None))
        self.assertEqual(W.parse_feature([]), (None, None))

    def test_heartbeat(self):
        self.assertEqual(W.parse_heartbeat(heartbeat(90, 0)), (90, False))
        self.assertEqual(W.parse_heartbeat(heartbeat(100, 1)), (100, True))
        self.assertEqual(W.parse_heartbeat([0x02, 0x00, 90, 0]), (None, None))
        self.assertEqual(W.parse_heartbeat([]), (None, None))


# ------------------------------------------------------------------ polling
class PollTest(ProviderTest):
    def test_each_listed_pid_receives_only_the_documented_query(self):
        # Literal identities/bytes from devices.py and the owner's cable
        # diagnostic, not W.KNOWN/QUERY.
        for pid in (0xA887, 0xA868, 0xA880, 0xA881):
            with self.subTest(pid=pid):
                puck = FakePuck(feature=feature_reply(85, 0))
                res = self.poll([entry(pid, 2)], {path_for(pid, 2): puck})
                self.assertEqual([(r.level, r.online) for r in res], [(85, True)])
                self.assertEqual(puck.sent_frames,
                                 [[0x00, 0x00, 0x00, 0x02, 0x02, 0x00, 0x83] + [0] * 58])

    def test_an_unknown_pid_with_a_convincing_name_receives_no_feature_request(self):
        puck = FakePuck(feature=feature_reply(85, 0))
        res = self.poll([entry(UNKNOWN_PID, 2, product="WLMOUSE BEAST MAX 8K RECEIVER")],
                        {path_for(UNKNOWN_PID, 2): puck})
        self.assertEqual(res, [])
        self.assertEqual(puck.sends, 0)
        self.assertEqual(puck.feature_reads, 0)

    def test_a_known_pid_on_another_collection_receives_no_feature_request(self):
        for pid in (RECEIVER_PID, CABLE_PID):
            for page, usage in ((0xFFFF, 0x0001), (0xFFA0, 0x0001), (0xFF00, 0x0000),
                                (0xFFFF, None), (0x0001, 0x0002), (0x0001, 0x0006)):
                with self.subTest(pid=pid, page=page, usage=usage):
                    puck = FakePuck(feature=feature_reply(85, 0))
                    res = self.poll([entry(pid, 1, usage_page=page, usage=usage)],
                                    {path_for(pid, 1): puck})
                    self.assertEqual(res, [])
                    self.assertEqual(puck.sends, 0)
                    self.assertEqual(puck.feature_reads, 0)

    def test_cable_feature_reply_shows_charging_without_a_heartbeat(self):
        cable = FakePuck(feature=feature_reply(95, 1))
        [res] = self.poll([entry(CABLE_PID, 2, product="WLMOUSE BEAST MAX")],
                          {path_for(CABLE_PID, 2): cable})
        self.assertEqual((res.key, res.name, res.level, res.charging, res.online),
                         ("wlmouse", "WLmouse Beast X Max", 95, True, True))
        self.assertEqual(cable.sends, 1)

    def test_charging_cable_wins_and_unplug_returns_to_the_receiver_with_one_icon(self):
        receiver = FakePuck(feature=feature_reply(93, 0))
        cable = FakePuck(feature=feature_reply(95, 1))
        receiver_info = entry(RECEIVER_PID, 2, product="WLMOUSE BEAST MAX 8K RECEIVER")
        cable_info = entry(CABLE_PID, 2, product="WLMOUSE BEAST MAX")
        pucks = {receiver_info["path"]: receiver, cable_info["path"]: cable}
        [wireless] = self.poll([receiver_info], pucks)
        self.assertEqual((wireless.level, wireless.charging, wireless.online), (93, False, True))
        [charging] = self.poll([receiver_info, cable_info], pucks)
        self.assertEqual((charging.level, charging.charging, charging.online), (95, True, True))
        self.assertEqual(receiver.sends, 1)  # charging cable skips the receiver query
        self.assertEqual(cable.sends, 1)
        [unplugged] = self.poll([receiver_info], {receiver_info["path"]: receiver})
        self.assertEqual((unplugged.level, unplugged.charging, unplugged.online), (93, False, True))
        self.assertEqual(receiver.sends, 2)
        self.assertEqual([(r.key, r.name) for r in (wireless, charging, unplugged)],
                         [("wlmouse", "WLmouse Beast X Max")] * 3)

    def test_an_excluded_feature_collection_can_still_supply_a_passive_heartbeat(self):
        puck = FakePuck(beats=[heartbeat(47, 0)])
        res = self.poll([entry(0xA880, 1, usage=0x0001)], {path_for(0xA880, 1): puck})
        self.assertEqual([(r.level, r.online) for r in res], [(47, True)])
        self.assertEqual(puck.sends, 0)
        self.assertEqual(puck.feature_reads, 0)

    def test_an_unknown_pid_can_still_supply_a_passive_heartbeat_without_queries(self):
        puck = FakePuck(beats=[heartbeat(47, 1)])
        res = self.poll([entry(UNKNOWN_PID, 2)], {path_for(UNKNOWN_PID, 2): puck})
        self.assertEqual([(r.level, r.charging, r.online) for r in res], [(47, True, True)])
        self.assertEqual(puck.sends, 0)
        self.assertEqual(puck.feature_reads, 0)

    def test_no_receiver_costs_nothing(self):
        t0 = self.clock.now
        self.assertEqual(self.poll([], {}), [])
        self.assertEqual(self.clock.now, t0)

    def test_a_silent_receiver_is_walked_every_fifth_poll(self):
        # #62 item 5: with the mouse off, each walk costs ~2.75 s of fake time
        # (0.75 s of refusals + the 2 s listen); polls 1-3 walk, 4-7 are skipped,
        # poll 8 walks again.
        puck = FakePuck()
        pucks = {path_for(RECEIVER_PID, 0): puck}
        entries = [entry(RECEIVER_PID, 0)]
        for i in range(8):
            t0 = self.clock.now
            res = self.poll(entries, pucks)
            self.assertEqual(res, [], f"poll {i + 1}")
            if i < 3 or i == 7:
                self.assertGreaterEqual(self.clock.now - t0, 2.0, f"poll {i + 1} walked")
            else:
                self.assertLess(self.clock.now - t0, 0.01, f"poll {i + 1} skipped")
        self.assertEqual(puck.sends, 4)          # one send per walk: polls 1-3 and 8

    def test_a_reading_keeps_the_rate_and_a_stale_one_backs_off(self):
        puck = FakePuck(feature=feature_reply(85, 0))
        pucks = {path_for(RECEIVER_PID, 0): puck}
        entries = [entry(RECEIVER_PID, 0)]
        for _ in range(2):                        # a reading arrives: every poll walks
            res = self.poll(entries, pucks)
            self.assertEqual([(r.level, r.online) for r in res], [(85, True)])
        self.assertEqual(puck.sends, 2)
        self.provider._last["wlmouse"] = (85, False, self.clock.now)
        # the mouse goes quiet: the greyed value shows and every poll still walks
        puck.feature = None
        walked = puck.sends
        for i in range(4):
            self.clock.now += 60                  # a poll a minute while it is fresh
            res = self.poll(entries, pucks)
            self.assertEqual([(r.level, r.online) for r in res], [(85, False)], f"poll {i}")
        self.assertEqual(puck.sends, walked + 4)
        # past ASLEEP_KEEP the icon is gone and the walk backs off
        self.clock.now += 60
        t0 = self.clock.now
        self.assertEqual(self.poll(entries, pucks), [])
        self.assertLess(self.clock.now - t0, 0.01)
        t0 = self.clock.now
        self.assertEqual(self.poll(entries, pucks), [])
        self.assertLess(self.clock.now - t0, 0.01)

    def test_the_backoff_ends_when_the_mouse_answers_again(self):
        puck = FakePuck()
        pucks = {path_for(RECEIVER_PID, 0): puck}
        entries = [entry(RECEIVER_PID, 0)]
        for _ in range(4):                        # enter the skip regime
            self.poll(entries, pucks)
        t0 = self.clock.now
        self.assertEqual(self.poll(entries, pucks), [])
        self.assertLess(self.clock.now - t0, 0.01)
        puck.feature = feature_reply(77, 1)       # it answers on the next walk
        res = []
        for _ in range(5):                        # within one backoff period
            res = self.poll(entries, pucks)
            if res:
                break
        self.assertEqual([(r.level, r.charging, r.online) for r in res], [(77, True, True)])
        res = self.poll(entries, pucks)           # and keeps the full rate again
        self.assertEqual([(r.level, r.online) for r in res], [(77, True)])

    def test_a_new_device_is_walked_at_once(self):
        puck = FakePuck()
        pucks = {path_for(RECEIVER_PID, 0): puck}
        entries = [entry(RECEIVER_PID, 0)]
        for _ in range(4):                        # enter the skip regime
            self.poll(entries, pucks)
        t0 = self.clock.now
        self.assertEqual(self.poll(entries, pucks), [])
        self.assertLess(self.clock.now - t0, 0.01)
        # The confirmed cable appears during silent backoff and only answers
        # feature reports: discover its charging reading on the very next poll.
        cable = FakePuck(feature=feature_reply(91, 1))
        pucks[path_for(CABLE_PID, 0)] = cable
        previous_sends = puck.sends
        res = self.poll(entries + [entry(CABLE_PID, 0)], pucks)
        self.assertEqual([(r.level, r.charging, r.online) for r in res], [(91, True, True)])
        self.assertEqual(cable.sends, 1)
        self.assertEqual(cable.feature_reads, 1)
        self.assertEqual(puck.sends, previous_sends)  # the charging cable wins; no receiver query


if __name__ == "__main__":
    unittest.main()
