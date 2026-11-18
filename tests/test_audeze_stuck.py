"""Tests for the stuck Audeze dongle: it answers every packet with an empty echo of the
request, so the battery never arrives although the headset is on. No hardware: the
dongle is a fake that answers like the real one did.

Run from the repository root:

    python -m unittest discover -s tests
"""

# Independent wire fixtures: HeadsetControl lib/devices/audeze_maxwell.hpp, RACE battery marker d6 0c 00 00.
# Keep reply bytes/offsets independent of the provider constants under test.
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from providers import audeze  # noqa: E402

PATH = b"\\\\?\\hid#vid_3329&pid_4b18&mi_00&col02#x"


def echo(req):
    """What the stuck 4B18 dongle answered: 07 00 <byte 2 of the request> 00 ..."""
    return [0x07, 0x00, req[2]] + [0] * (62 - 3)


def battery(level):
    return ([0x07, 0x05, 0x5D, 0x07, 0x00] + list(bytes.fromhex("d6 0c 00 00"))
            + [level] + [0] * (62 - 10))


class FakeDongle:
    """Answers each request with echo() while stuck, else with the battery."""

    def __init__(self, stuck, level=80):
        self.stuck, self.level, self.last = stuck, level, None
        self.opened, self.writes = [], []

    def open_path(self, path):
        self.opened.append(path)

    def write(self, data):
        self.writes.append(bytes(data))
        self.last = list(data)
        return len(data)

    def get_input_report(self, report_id, size):
        if self.last is None:
            return []
        req, self.last = self.last, None
        return echo(req) if self.stuck else battery(self.level)

    def close(self):
        pass


def ifaces(product="Audeze Maxwell HID", pid=0x4B18):
    return [{"product_id": pid, "serial_number": "0000000000000000", "path": PATH,
             "usage_page": 0xFF13, "usage": 0x0001, "interface_number": 0,
             "product_string": product}]


class TargetTests(unittest.TestCase):
    def setUp(self):
        self.dongle = FakeDongle(stuck=False)
        self.infos = []
        for p in (mock.patch.object(audeze.hid, "device", lambda: self.dongle),
                  mock.patch.object(audeze.hidlist, "enumerate", lambda vid: self.infos),
                  mock.patch.object(audeze.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)

    def test_all_sourced_identities_use_the_exact_collection(self):
        # Maxwell 2's interface 5 must not be rejected by an interface-0 rule.
        for pid, interface in ((0x4B19, 0), (0x4B18, 0), (0x4B1A, 0),
                               (0x4B29, 5), (0x4B28, 5)):
            with self.subTest(pid=pid):
                self.dongle.opened.clear()
                self.dongle.writes.clear()
                self.infos = [dict(ifaces(pid=pid)[0], interface_number=interface)]
                out = audeze.AudezeProvider().poll()
                self.assertEqual([(s.level, s.charging) for s in out],
                                 [(80, pid == 0x4B1A)])
                self.assertEqual(self.dongle.opened, [PATH])
                self.assertEqual(self.dongle.writes,
                                 [bytes([0x06, 0x07, 0x80, 0x05, 0x5A, 0x03,
                                         0x00, 0xD6, 0x0C]) + bytes(53)])

    def test_the_inferred_xbox_cable_identity_is_not_opened_or_written(self):
        self.infos = ifaces("Audeze Maxwell Headset", pid=0x4B1E)
        self.assertEqual(audeze.AudezeProvider().poll(), [])
        self.assertEqual(self.dongle.opened, [])
        self.assertEqual(self.dongle.writes, [])

    def test_other_vendor_collections_are_not_opened_or_written(self):
        for page, usage in ((0xFF13, 0x0002), (0xFF00, 0x0001), (0x000C, 0x0001),
                            (0xFF13, None)):
            with self.subTest(page=page, usage=usage):
                info = dict(ifaces(pid=0x4B19)[0], usage_page=page, usage=usage)
                if usage is None:
                    del info["usage"]
                self.infos = [info]
                out = audeze.AudezeProvider().poll()
                self.assertTrue(all(s.level is None for s in out))
                self.assertEqual(self.dongle.opened, [])
                self.assertEqual(self.dongle.writes, [])

    def test_excluded_siblings_do_not_hide_the_allowed_collection(self):
        allowed = dict(ifaces(pid=0x4B19)[0], path=b"allowed")
        self.infos = [dict(allowed, path=b"wrong-usage", usage=0x0002),
                      dict(allowed, path=b"wrong-page", usage_page=0xFF00), allowed]
        self.assertEqual([s.level for s in audeze.AudezeProvider().poll()], [80])
        self.assertEqual(self.dongle.opened, [b"allowed"])
        self.assertEqual(len(self.dongle.writes), 1)


class StuckDongleTests(unittest.TestCase):
    def setUp(self):
        self.dongle = FakeDongle(stuck=True)
        self.infos = ifaces()
        for p in (mock.patch.object(audeze.hid, "device", lambda: self.dongle),
                  mock.patch.object(audeze.hidlist, "enumerate", lambda vid: self.infos),
                  mock.patch.object(audeze.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.p = audeze.AudezeProvider()

    def test_echo_only(self):
        self.assertTrue(audeze.echo_only([bytes(echo([6, 8, 0x80]))] * 5))
        self.assertTrue(audeze.echo_only([bytes(echo([6, 8, 0x00]))] + [bytes(echo([6, 8, 0x80]))] * 4))
        self.assertFalse(audeze.echo_only([bytes(echo([6, 8, 0x80]))] * 4))          # too few
        self.assertFalse(audeze.echo_only([bytes(echo([6, 8, 0x80]))] * 4 + [bytes(battery(80))]))
        self.assertFalse(audeze.echo_only([]))

    def test_first_stuck_poll_shows_nothing(self):
        self.assertEqual(self.p.poll(), [])
        self.assertTrue(any("empty echo" in line for line in self.p.diagnostics()))

    def test_second_stuck_poll_shows_the_hint(self):
        self.p.poll()
        res = self.p.poll()
        self.assertEqual(len(res), 1)
        st = res[0]
        self.assertEqual((st.key, st.name, st.level, st.online, st.approx),
                         ("audeze:hid-c71f8d5fa49911f7d921e1ea19a8b56827f8c98396a4854b8379e5cff3d55978", "Audeze Maxwell", None, True, audeze.STUCK_TEXT))

    def test_replug_brings_the_level_back_under_the_same_key(self):
        self.p.poll()
        stuck_key = self.p.poll()[0].key
        self.dongle.stuck = False
        res = self.p.poll()
        self.assertEqual([(s.key, s.level, s.approx) for s in res], [(stuck_key, 80, "")])
        # and the count starts over: one stuck poll after that shows nothing again
        self.dongle.stuck = True
        self.assertEqual(self.p.poll(), [])

    def test_switched_off_headset_is_not_called_stuck(self):
        self.infos = ifaces("Audeze Maxwell Dongle", pid=0x4B19)
        self.p.poll()
        self.assertEqual(self.p.poll(), [])

    def test_a_headset_that_answers_is_not_stuck(self):
        self.dongle.stuck = False
        for _ in range(3):
            res = self.p.poll()
        self.assertEqual([s.level for s in res], [80])


class ZeroAtPowerOnTests(unittest.TestCase):
    """Right after power-on the headset reports 0% for a moment (measured on a 4B18)."""

    def setUp(self):
        self.dongle = FakeDongle(stuck=False, level=0)
        self.infos = ifaces()
        self.now = 1000.0
        for p in (mock.patch.object(audeze.hid, "device", lambda: self.dongle),
                  mock.patch.object(audeze.hidlist, "enumerate", lambda vid: self.infos),
                  mock.patch.object(audeze.time, "sleep", lambda s: None),
                  mock.patch.object(audeze.time, "time", lambda: self.now)):
            p.start()
            self.addCleanup(p.stop)
        self.p = audeze.AudezeProvider()

    def test_zero_right_after_power_on_is_not_a_reading(self):
        res = self.p.poll()
        self.assertEqual([(s.level, s.approx) for s in res], [(None, audeze.WAKING_TEXT)])
        self.assertTrue(self.p.pending)             # the app re-checks in 3 s, not 60
        self.now += 3
        self.dongle.level = 80
        res = self.p.poll()
        self.assertEqual([(s.level, s.approx) for s in res], [(80, "")])
        self.assertFalse(self.p.pending)

    def test_zero_after_the_grace_period_is_believed(self):
        self.p.poll()
        self.now += audeze.ZERO_GRACE + 1
        self.assertEqual([s.level for s in self.p.poll()], [0])
        self.assertFalse(self.p.pending)

    def test_switching_off_and_on_starts_a_new_grace_period(self):
        self.dongle.level = 80
        self.p.poll()
        self.now += 600
        self.infos = []                             # off: no answer (here: not enumerated)
        self.p.poll()
        self.infos = ifaces()
        self.dongle.level = 0
        self.now += 60
        self.assertEqual([s.level for s in self.p.poll()], [None])

    def test_a_real_level_is_shown_at_once(self):
        self.dongle.level = 55
        self.assertEqual([s.level for s in self.p.poll()], [55])


if __name__ == "__main__":
    unittest.main()
