"""Independent Audeze headsets, proven endpoint preferences and safe identities."""
import unittest
from unittest import mock

from providers import audeze as A


def endpoint(path, serial="UNIT-A", pid=0x4b18):
    return dict(path=path, serial_number=serial, product_id=pid, vendor_id=0x3329,
                product_string="Audeze Maxwell HID", interface_number=0,
                usage_page=0xff13, usage=1)


class MultipleHeadsetTests(unittest.TestCase):
    def setUp(self):
        self.provider = A.AudezeProvider()
        self.infos, self.levels, self.reads = [], {}, []
        self.clock = 1000.
        def read(path, state_key):
            self.reads.append(path)
            value = self.levels.get(path)
            if isinstance(value, Exception):
                raise value
            return value, False
        for patch in (mock.patch.object(A.hidlist, "enumerate", lambda vid: self.infos),
                      mock.patch.object(self.provider, "_read_battery", side_effect=read),
                      mock.patch.object(A.time, "time", lambda: self.clock)):
            patch.start()
            self.addCleanup(patch.stop)

    def states(self):
        return {s.key: (s.level, s.charging, s.ui_message) for s in self.provider.poll()}

    def test_two_trusted_serials_both_report_and_keep_levels_on_reorder(self):
        self.infos = [endpoint(b"one", "UNIT-A"), endpoint(b"two", "UNIT-B")]
        self.levels = {b"one": 72, b"two": 31}
        self.assertEqual(self.states(), {"audeze:UNIT-A": (72, False, ""),
                                         "audeze:UNIT-B": (31, False, "")})
        self.infos.reverse()
        self.assertEqual(self.states(), {"audeze:UNIT-A": (72, False, ""),
                                         "audeze:UNIT-B": (31, False, "")})
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_one_failed_headset_does_not_discard_the_other(self):
        self.infos = [endpoint(b"one", "UNIT-A"), endpoint(b"two", "UNIT-B")]
        for failure in (None, OSError("first disconnected"), ValueError("bad first reply")):
            with self.subTest(failure=failure):
                self.levels = {b"one": failure, b"two": 88}
                self.assertEqual(self.states(), {"audeze:UNIT-B": (88, False, "")})

    def test_cable_wins_only_for_its_own_trusted_headset(self):
        self.infos = [endpoint(b"one-radio", "UNIT-A"), endpoint(b"two", "UNIT-B"),
                      endpoint(b"one-cable", "UNIT-A", 0x4b1a)]
        self.levels = {b"one-radio": 99, b"one-cable": 42, b"two": 67}
        self.assertEqual(self.states(), {"audeze:UNIT-A": (42, True, ""),
                                         "audeze:UNIT-B": (67, False, "")})
        self.assertEqual(self.reads, [b"one-cable", b"two"])

    def test_failed_cable_can_fall_back_to_same_headsets_receiver(self):
        self.infos = [endpoint(b"one-cable", "UNIT-A", 0x4b1a),
                      endpoint(b"one-radio", "UNIT-A"), endpoint(b"two", "UNIT-B")]
        self.levels = {b"one-cable": None, b"one-radio": 31, b"two": 62}
        self.assertEqual(self.states(), {"audeze:UNIT-A": (31, False, ""),
                                         "audeze:UNIT-B": (62, False, "")})

    def test_power_on_zero_grace_does_not_stop_another_headsets_reading(self):
        self.infos = [endpoint(b"one", "UNIT-A"), endpoint(b"two", "UNIT-B")]
        self.levels = {b"one": 0, b"two": 54}
        self.assertEqual(self.states(), {"audeze:UNIT-A": (None, False, "state.pending"),
                                         "audeze:UNIT-B": (54, False, "")})
        self.assertTrue(self.provider.pending)
        self.clock += A.ZERO_GRACE + 1
        self.assertEqual(self.states()["audeze:UNIT-A"], (0, False, ""))

    def test_placeholder_serials_cannot_merge_unrelated_control_paths(self):
        self.infos = [endpoint(b"one", "0000000000000000"), endpoint(b"two", "0000000000000000")]
        self.levels = {b"one": 72, b"two": 31}
        first = self.states()
        self.assertEqual(len(first), 2)
        self.assertEqual({v[0] for v in first.values()}, {72, 31})
        self.assertTrue(all(key.startswith("audeze:hid-") for key in first))
        self.assertEqual(self.provider.legacy_key_aliases(), {})
        self.infos.reverse()
        self.assertEqual(self.states(), first)
        other = A.AudezeProvider()
        with mock.patch.object(other, "_read_battery", side_effect=lambda p, k: (self.levels[p], False)):
            self.assertEqual({s.key: s.level for s in other.poll()}, {k:v[0] for k,v in first.items()})

    def test_missing_serial_has_only_unambiguous_cached_alias(self):
        self.infos = [endpoint(b"one", None)]
        self.levels = {b"one": 76}
        state = self.states()
        key = next(iter(state))
        self.assertNotEqual(key, "audeze:")
        self.assertEqual(self.provider.legacy_key_aliases(), {"audeze:": key})
        copied = self.provider.legacy_key_aliases()
        copied.clear()
        self.assertEqual(self.provider.legacy_key_aliases(), {"audeze:": key})
        self.infos += [endpoint(b"two", None)]
        self.levels[b"two"] = 31
        self.assertEqual(len(self.states()), 2)
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_unknown_cable_and_dongle_are_not_guessed_to_be_one_headset(self):
        self.infos = [endpoint(b"radio", "0000"), endpoint(b"cable", "0000", 0x4b1a)]
        self.levels = {b"radio": 21, b"cable": 87}
        state = self.states()
        self.assertEqual(len(state), 2)
        self.assertEqual({(v[0], v[1]) for v in state.values()}, {(21, False), (87, True)})
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_unknown_identity_never_uses_unrelated_collections(self):
        info = endpoint(b"wrong", None)
        info["usage"] = 2
        self.infos = [info]
        self.levels = {b"wrong": 90}
        self.assertEqual(self.states(), {})
        self.assertEqual(self.reads, [])

    def test_stuck_hint_is_per_headset_and_does_not_hide_a_healthy_one(self):
        self.infos = [endpoint(b"one", "UNIT-A"), endpoint(b"two", "UNIT-B")]
        def read(path, key):
            self.provider._last_echo_only = path == b"one"
            return (None if path == b"one" else 66), False
        with mock.patch.object(self.provider, "_read_battery", side_effect=read):
            self.states()
            state = self.states()
        self.assertEqual(state["audeze:UNIT-B"], (66, False, ""))
        self.assertEqual(state["audeze:UNIT-A"][2], "state.audeze_stuck")

    def test_failed_cable_does_not_restart_receivers_zero_grace_each_poll(self):
        self.infos = [endpoint(b"cable", "UNIT-A", 0x4b1a), endpoint(b"radio", "UNIT-A")]
        self.levels = {b"cable": None, b"radio": 0}
        self.assertEqual(self.states()["audeze:UNIT-A"][0], None)
        self.clock += A.ZERO_GRACE + 1
        self.assertEqual(self.states()["audeze:UNIT-A"][0], 0)
