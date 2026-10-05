"""Tests for providers/steam.py. No hardware is needed.

The tests replace the HID layer with a fake puck that sends the reports SDL's
Steam Triton driver describes: 0x43 with the charge state and the level, 0x79 /
0x46 for connect and disconnect, and 0x42 state reports. A fake clock replaces
time, so a listen window runs without real waiting.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import steam as S  # noqa: E402

PUCK = 0x1304        # Proteus dongle, as in issue #58
NEREID = 0x1305


def path_for(pid, iface):
    return (f"\\\\?\\hid#vid_28de&pid_{pid:04x}&mi_{iface:02x}"
            f"#7&8f0a1b2c&0&0000#{{4d1e55b2-f16f-11cf-88cb-001111000030}}").encode()


def battery(state, level, pad=46):
    return [0x43, state, level] + [0] * pad


def wireless(state):
    return [0x79, state] + [0] * 60


def state_report():
    return [0x42] + [0] * 63


# ------------------------------------------------------------------ fakes
class Clock:
    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class FakePuck:
    """frames: what one listen returns, in order, then nothing."""

    def __init__(self, frames=()):
        self.queue = [list(f) for f in frames]
        self.writes = []
        self.opened = 0

    def on_read(self):
        return self.queue.pop(0) if self.queue else []


class FakeBus:
    def __init__(self, pucks, clock=None):
        self.pucks = pucks          # {path: FakePuck}
        self.clock = clock          # set = a frame read advances it (a ~250 Hz stream)

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.pucks:
                    raise OSError("cannot open")
                self.puck = bus.pucks[path]
                self.puck.opened += 1

            def set_nonblocking(self, on):
                pass

            def write(self, data):
                self.puck.writes.append(list(data))
                return len(data)

            def read(self, n):
                data = self.puck.on_read()
                if data and bus.clock is not None:
                    bus.clock.now += 0.004
                return data

            def close(self):
                pass

        return FakeDevice


def entry(pid, iface, usage_page=0xFF00, usage=0x0001):
    # the shape of the issue #58 diagnostics for the puck's collections
    return {"product_id": pid, "interface_number": iface, "usage_page": usage_page,
            "usage": usage, "path": path_for(pid, iface),
            "product_string": "Steam Controller Puck", "serial_number": "3d78b3a8716f4e02"}


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (S.hid, S.hidlist, S.time)
        self.clock = Clock()
        S.time = types.SimpleNamespace(time=self.clock.time, sleep=self.clock.sleep)
        self.provider = S.SteamProvider()

    def tearDown(self):
        S.hid, S.hidlist, S.time = self._saved

    def poll(self, entries, pucks, advance=False):
        bus = FakeBus(pucks, clock=self.clock if advance else None)
        S.hid = types.SimpleNamespace(device=bus.device_class())
        S.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()


# ------------------------------------------------------------------ parse
class ParseTest(unittest.TestCase):
    def test_charge_states(self):
        self.assertIs(S.parse_charge(1), False)
        self.assertIs(S.parse_charge(2), True)
        self.assertIs(S.parse_charge(4), True)
        for byte in (0, 3, 5, 0xFF):
            self.assertIsNone(S.parse_charge(byte), byte)

    def test_battery_frame(self):
        self.assertEqual(S.parse_battery(battery(1, 75)), (75, False))
        self.assertEqual(S.parse_battery(battery(2, 40)), (40, True))
        self.assertEqual(S.parse_battery(battery(4, 100)), (100, True))
        self.assertEqual(S.parse_battery(battery(1, 0)), (0, False))

    def test_frames_without_a_usable_level_are_refused(self):
        self.assertIsNone(S.parse_battery(battery(1, 101)))     # above 100
        self.assertIsNone(S.parse_battery(battery(0, 50)))      # reset
        self.assertIsNone(S.parse_battery(battery(3, 50)))      # source validate
        self.assertIsNone(S.parse_battery(battery(1, 50)[:14]))  # shorter than the struct
        self.assertIsNone(S.parse_battery(state_report()))      # not a battery report
        self.assertIsNone(S.parse_battery([]))


# ------------------------------------------------------------------ poll
class PollTest(ProviderTest):
    def test_battery_gives_an_icon_and_writes_nothing(self):
        puck = FakePuck([battery(1, 75)])
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck})
        self.assertEqual(len(res), 1)
        st = res[0]
        self.assertEqual((st.name, st.level, st.charging, st.online),
                         ("Steam Controller", 75, False, True))
        self.assertEqual((st.key, st.kind, st.source),
                         ("steam:1304:2", "gamepad", "steam"))
        self.assertEqual((puck.opened, puck.writes), (1, []))

    def test_the_battery_is_waited_for_past_the_short_window(self):
        # #58: ~266 Hz state stream, one 0x43 every ~2.5 s - 1.6 s of stream
        # then the battery; the old 0.4 s window stopped before it.
        puck = FakePuck([state_report()] * 400 + [battery(2, 80)])
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck}, advance=True)
        self.assertEqual([(r.level, r.charging, r.online) for r in res],
                         [(80, True, True)])

    def test_a_live_slot_without_a_battery_waits_the_battery_window(self):
        puck = FakePuck([state_report()] * 4)
        start = self.clock.now
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck}, advance=True)
        self.assertEqual([(r.level, r.online, r.approx) for r in res],
                         [(None, True, S.NOT_REPORTED)])
        self.assertGreaterEqual(self.clock.now - start, S.BATTERY_WINDOW - 0.05)

    def test_a_quiet_slot_does_not_wait_the_battery_window(self):
        start = self.clock.now
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): FakePuck()},
                        advance=True)
        self.assertEqual(res, [])
        self.assertLessEqual(self.clock.now - start, S.WINDOW + 0.1)

    def test_a_disconnect_ends_the_read_before_the_battery_window(self):
        puck = FakePuck([wireless(2), wireless(1)])
        start = self.clock.now
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck}, advance=True)
        self.assertEqual(res, [])
        self.assertLess(self.clock.now - start, S.WINDOW)

    def test_the_battery_window_covers_twice_the_measured_cadence(self):
        self.assertGreaterEqual(S.BATTERY_WINDOW, 2 * 2.5)

    def test_charging_and_charging_done(self):
        a, b = FakePuck([battery(2, 40)]), FakePuck([battery(4, 100)])
        res = self.poll([entry(PUCK, 2), entry(PUCK, 3)],
                        {path_for(PUCK, 2): a, path_for(PUCK, 3): b})
        self.assertEqual(sorted((r.level, r.charging) for r in res),
                         [(40, True), (100, True)])

    def test_the_confirming_frames_from_58(self):
        # @Kosminaut13's run of the test build caught the battery report the 5 s
        # window was for: `43 01 5f` = 95 % awake (after a state stream, the
        # original 0.4 s window's blind spot), and on the cable `43 04 64` =
        # 100 %, charging done - shown as charging, as the tray did.
        puck = FakePuck([state_report()] * 200 + [battery(1, 0x5F)])
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck}, advance=True)
        self.assertEqual([(r.level, r.charging, r.online) for r in res],
                         [(95, False, True)])
        puck = FakePuck([battery(4, 0x64)])
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck})
        self.assertEqual([(r.level, r.charging, r.online) for r in res],
                         [(100, True, True)])

    def test_state_report_alone_shows_the_controller_without_a_level(self):
        puck = FakePuck([state_report(), state_report()])
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck})
        self.assertEqual([(r.level, r.online, r.approx) for r in res],
                         [(None, True, S.NOT_REPORTED)])

    def test_wireless_connect_alone_shows_the_controller(self):
        puck = FakePuck([wireless(2)])
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck})
        self.assertEqual([(r.level, r.online) for r in res], [(None, True)])

    def test_disconnect_removes_the_icon_and_clears_the_value(self):
        puck = FakePuck([battery(1, 80)])
        e = [entry(PUCK, 2)]
        self.assertEqual([r.level for r in self.poll(e, {path_for(PUCK, 2): puck})], [80])
        puck.queue = [wireless(1)]
        self.assertEqual(self.poll(e, {path_for(PUCK, 2): puck}), [])
        # and the value is gone too: a silent poll shows nothing, not a greyed icon
        self.assertEqual(self.poll(e, {path_for(PUCK, 2): puck}), [])

    def test_silent_slot_keeps_the_last_value_greyed_without_a_timer(self):
        # an icon removed and re-created comes back at a new tray position (#87,
        # #202 review): the greyed value stays while the puck is there
        puck = FakePuck([battery(1, 80)])
        e = [entry(PUCK, 2)]
        self.poll(e, {path_for(PUCK, 2): puck})
        res = self.poll(e, {path_for(PUCK, 2): puck})
        self.assertEqual([(r.level, r.charging, r.online) for r in res], [(80, False, False)])
        self.clock.now += 6 * 3600
        res = self.poll(e, {path_for(PUCK, 2): puck})
        self.assertEqual([(r.level, r.charging, r.online) for r in res], [(80, False, False)])

    def test_a_puck_that_leaves_clears_the_kept_value(self):
        puck = FakePuck([battery(1, 80)])
        e = [entry(PUCK, 2)]
        self.assertEqual([r.level for r in self.poll(e, {path_for(PUCK, 2): puck})], [80])
        self.assertEqual(self.poll([], {}), [])          # the puck is unplugged
        self.assertEqual(self.provider._last, {})        # and nothing is kept
        # plugging it back shows nothing until a report arrives again
        puck.queue = []
        self.assertEqual(self.poll(e, {path_for(PUCK, 2): puck}), [])

    def test_refused_level_shows_the_controller_without_a_level(self):
        puck = FakePuck([battery(1, 150)])
        res = self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck})
        self.assertEqual([(r.level, r.online, r.approx) for r in res],
                         [(None, True, S.NOT_REPORTED)])

    def test_a_vendor_collection_with_another_usage_still_listens(self):
        # no ff00:0001 on the interface: SDL matches the interface alone, so a
        # vendor page is the next pick and the choice is logged (#202 review)
        puck = FakePuck([battery(1, 66)])
        res = self.poll([entry(PUCK, 2, usage_page=0xFF00, usage=0x0002)],
                        {path_for(PUCK, 2): puck})
        self.assertEqual([r.level for r in res], [66])
        self.assertIn("picked vendor page ff00", "\n".join(self.provider.diagnostics()))

    def test_the_gamepad_face_is_the_last_resort(self):
        puck = FakePuck([battery(1, 42)])
        res = self.poll([entry(PUCK, 2, usage_page=0x0001, usage=0x0005)],
                        {path_for(PUCK, 2): puck})
        self.assertEqual([r.level for r in res], [42])

    def test_a_puck_without_a_usable_collection_lists_what_it_has(self):
        cases = [entry(PUCK, 3, usage_page=0x0001, usage=0x0002),
                 entry(PUCK, 3, usage_page=0x0001, usage=0x0006)]
        pucks = {d["path"]: FakePuck([battery(1, 50)]) for d in cases}
        res = self.poll(cases, pucks)
        self.assertEqual(res, [])
        self.assertTrue(all(p.opened == 0 for p in pucks.values()))
        diag = "\n".join(self.provider.diagnostics())
        self.assertIn("no slot collection", diag)
        self.assertIn("usage=0001:0002", diag)
        self.assertIn("usage=0001:0006", diag)

    def test_only_the_slot_collections_are_opened(self):
        # old dongle, other interfaces, the mouse / keyboard collections and the
        # dongle's own ff00:0002 collection: none of them is opened
        cases = [entry(0x1142, 2),                    # the 2015 dongle
                 entry(PUCK, 1), entry(PUCK, 6),      # not a slot interface
                 entry(PUCK, 3, usage_page=0x0001, usage=0x0002),   # mouse
                 entry(PUCK, 3, usage_page=0x0001, usage=0x0006),   # keyboard
                 entry(PUCK, 6, usage_page=0xFF00, usage=0x0002)]
        pucks = {d["path"]: FakePuck([battery(1, 50)]) for d in cases}
        res = self.poll(cases, pucks)
        self.assertEqual(res, [])
        self.assertTrue(all(p.opened == 0 and not p.writes for p in pucks.values()))

    def test_two_controllers_are_two_icons(self):
        a, b = FakePuck([battery(1, 80)]), FakePuck([state_report(), battery(2, 30)])
        res = self.poll([entry(PUCK, 2), entry(NEREID, 4)],
                        {path_for(PUCK, 2): a, path_for(NEREID, 4): b})
        self.assertEqual(sorted((r.key, r.level, r.charging) for r in res),
                         [("steam:1304:2", 80, False), ("steam:1305:4", 30, True)])

    def test_diagnostics_count_the_reports(self):
        puck = FakePuck([state_report(), state_report(), battery(1, 60)])
        self.poll([entry(PUCK, 2)], {path_for(PUCK, 2): puck})
        diag = "\n".join(self.provider.diagnostics())
        self.assertIn("report 0x42 x2", diag)
        self.assertIn("report 0x43 x1", diag)
        self.assertIn("-> 60%", diag)

    def test_unopenable_slot_is_tolerated(self):
        puck = FakePuck([battery(1, 50)])
        res = self.poll([entry(PUCK, 2), entry(PUCK, 3)], {path_for(PUCK, 2): puck})
        self.assertEqual([r.key for r in res], ["steam:1304:2"])

    def test_no_hid_module_is_no_crash(self):
        self._saved_hid = S.hid
        S.hid = None
        try:
            self.assertEqual(self.provider.poll(), [])
        finally:
            S.hid = self._saved_hid


if __name__ == "__main__":
    unittest.main()
