"""Tests for providers/razer.py: another app talks to the same collection (issue #108).

Razer mice answer a feature report request in a single reply buffer. RGB software
(Synapse, Chroma apps) sends lighting frames (class 0x0f) to the same collection many
times a second, so the buffer mostly holds *its* replies, with status 02 and a new
transaction id each time. The fake device below does that. A fake clock replaces time,
so the provider's 1 s waits take no real time.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import razer as R  # noqa: E402


class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


def foreign_packet(n):
    """A reply to someone else's lighting frame: status 02, class 0f id 03."""
    out = bytearray(90)
    out[0], out[1], out[5], out[6], out[7] = R.STATUS_OK, (n * 7) % 0x20, 0x08, 0x0F, 0x03
    return out


class BusyMouse:
    """answer_on: the n-th request (1-based) that the mouse answers; 0 = none.
    Every other read returns another app's packet."""

    def __init__(self, answer_on=0, raw_level=0xB5, charging=0):
        self.answer_on = answer_on
        self.values = {0x80: raw_level, 0x84: charging}
        self.requests = []          # (transaction id, class, id) of every request
        self.pending = None
        self.reads = 0
        self.opened = 0

    def on_request(self, req):
        self.requests.append((req[1], req[6], req[7]))
        n = sum(1 for r in self.requests if r[1:] == (req[6], req[7]))
        self.pending = req if self.answer_on and n >= self.answer_on else None

    def reply(self):
        self.reads += 1
        if self.pending is not None:
            req, self.pending = self.pending, None
            out = bytearray(90)
            out[0] = R.STATUS_OK
            out[1:8] = req[1:8]
            out[9] = self.values[req[7]]
        else:
            out = foreign_packet(self.reads)
        return [0x00] + list(out)


class FakeBus:
    def __init__(self, mice):
        self.mice = mice

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                self.m = bus.mice[path]
                self.m.opened += 1

            def send_feature_report(self, data):
                self.m.on_request(bytes(data[1:]))
                return len(data)

            def get_feature_report(self, report_id, size):
                return self.m.reply()

            def close(self):
                pass

        return FakeDevice


def issue_108_entries():
    # the Basilisk V3 X HyperSpeed receiver in the #108 diagnostics
    shape = [(-1, 0x000C, 1), (-1, 0x0001, 0x80), (0, 0x0001, 2), (1, 0x0001, 0), (1, 0x0001, 0),
             (1, 0x0001, 2), (1, 0x0001, 0), (1, 0x000C, 1), (1, 0x0001, 0x80), (1, 0x0001, 0),
             (1, 0x0001, 6), (2, 0x0001, 6)]
    return [{"product_id": 0x00B9, "interface_number": i, "usage_page": p, "usage": u,
             "path": b"c%d" % n, "product_string": "Razer Basilisk V3 X HyperSpeed",
             "serial_number": "000000000000"} for n, (i, p, u) in enumerate(shape)]


class ForeignRepliesTest(unittest.TestCase):
    def setUp(self):
        self._saved = (R.hid, R.hidlist, R.time)
        self.clock = Clock()
        R.time = types.SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep)
        self.provider = R.RazerProvider()

    def tearDown(self):
        R.hid, R.hidlist, R.time = self._saved

    def poll(self, mice):
        entries = issue_108_entries()
        R.hid = types.SimpleNamespace(device=FakeBus(mice).device_class())
        R.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()

    def mice(self, answer_on=0, **kw):
        return {e["path"]: BusyMouse(answer_on, **kw) for e in issue_108_entries()}

    def test_issue_108_is_not_an_online_mouse_without_a_level(self):
        # 1.12.0 showed "no link (off or asleep)": an online icon with no level
        res = self.poll(self.mice())
        self.assertEqual(res, [])
        self.assertTrue(any("another app" in line for line in self.provider.diagnostics()))

    def test_one_collection_one_transaction_id_three_sends(self):
        mice = self.mice()
        self.poll(mice)
        used = [m for m in mice.values() if m.requests]
        self.assertEqual(len(used), 1)                    # no second (1 s) collection
        self.assertEqual(used[0].requests, [(0x1F, 0x07, 0x80)] * R.SENDS)

    def test_the_collection_is_kept_for_the_next_poll(self):
        mice = self.mice()
        self.poll(mice)
        first = [p for p, m in mice.items() if m.requests]
        for m in mice.values():
            m.answer_on = 1                               # the other app has closed
        res = self.poll(mice)
        self.assertEqual([(r.level, r.online) for r in res], [(71, True)])
        self.assertEqual([p for p, m in mice.items() if m.requests], first)

    def test_a_second_busy_poll_costs_one_request_round_not_two(self):
        mice = self.mice()
        self.poll(mice)
        start = self.clock.now
        self.poll(mice)
        self.assertEqual(sum(len(m.requests) for m in mice.values()), 2 * R.SENDS)
        self.assertLess(self.clock.now - start, 1.5)      # one ~1 s wait, not a re-probe

    def test_an_answer_to_a_repeated_request_is_read(self):
        # the other app overwrote the first request; the second one gets through
        res = self.poll(self.mice(answer_on=2, raw_level=0x80, charging=1))
        self.assertEqual([(r.level, r.charging, r.online) for r in res], [(50, True, True)])

    def test_last_level_stays_greyed_while_another_app_holds_the_mouse(self):
        mice = self.mice(answer_on=1)
        self.assertEqual([r.level for r in self.poll(mice)], [71])
        for m in mice.values():
            m.answer_on = 0
        res = self.poll(mice)
        self.assertEqual([(r.level, r.online) for r in res], [(71, False)])
        self.clock.now += R.ASLEEP_KEEP
        self.assertEqual(self.poll(mice), [])

    def test_a_normal_mouse_is_asked_once(self):
        mice = self.mice(answer_on=1)
        self.poll(mice)
        used = [m for m in mice.values() if m.requests]
        self.assertEqual(used[0].requests, [(0x1F, 0x07, 0x80), (0x1F, 0x07, 0x84)])


if __name__ == "__main__":
    unittest.main()
