"""Literal WinRT reports must not turn corrupt capacities into a full battery."""
import json
import unittest
from unittest import mock

from providers import wgi, xinput
import test_xinput_identity as identity_tests


class WgiCapacityTests(unittest.TestCase):
    def test_valid_int32_capacities_include_real_zero_and_full(self):
        for remaining, full, expected in ((0, 1000, 0), (1000, 1000, 100),
                                           (647, 1000, 65), (0x7FFFFFFF, 0x7FFFFFFF, 100)):
            with self.subTest(remaining=remaining, full=full):
                self.assertEqual(wgi.capacity_level(remaining, full), expected)

    def test_invalid_capacities_are_refused_before_rounding_or_clamping(self):
        for remaining, full in ((1001, 1000), (-1, 1000), (1, 0), (1, -1),
                                (None, 1000), (1000, None), (True, 1000), (1, True),
                                (1000.0, 1000), (1, "1000"), (float("inf"), 1000),
                                (float("nan"), 1000), (2**64, 1000), (1, 2**64)):
            with self.subTest(remaining=remaining, full=full):
                self.assertIsNone(wgi.capacity_level(remaining, full))

    def test_no_battery_does_not_reuse_capacity_fields(self):
        report = wgi.WgiController(dict(status="NotPresent", remain=1000, full=1000))
        self.assertIsNone(report.level)
        self.assertFalse(report.placeholder)

    def test_malformed_row_does_not_hide_another_valid_controller(self):
        good = dict(name="GameSir G7 PRO", vid=0x3537, pid=0x105E,
                    status="Discharging", remain=647, full=1000)
        diagnostics = []
        reports = wgi.parse(json.dumps(dict(controllers=[None, "bad", 4, good])), diagnostics)
        self.assertEqual([(r.name, r.level) for r in reports], [("GameSir G7 PRO", 65)])
        self.assertEqual(sum("invalid controller entry" in line for line in diagnostics), 3)

    def test_malformed_envelopes_fail_without_attribute_errors(self):
        for raw in ("[]", "false", "null", '{"controllers":42}', '{"controllers":"bad"}'):
            with self.subTest(raw=raw):
                self.assertIsNone(wgi.parse(raw, []))

    def test_invalid_metadata_cannot_raise_in_diagnostics(self):
        [report] = wgi.parse(json.dumps(dict(controllers=[dict(
            name=42, vid="bad", pid={}, status=[], remain=0, full=1000)])), [])
        self.assertEqual((report.vid, report.pid, report.level, report.status), (0, 0, 0, ""))


class WgiFallbackTests(unittest.TestCase):
    def setUp(self):
        identity_tests.ControllerIdentityTests.setUp(self)

    def test_invalid_wgi_capacity_uses_actual_coarse_reading(self):
        self.dll.slots = {0: (3, 1)}
        for remaining, full in ((1001, 1000), (-1, 1000), (1000, 0)):
            with self.subTest(remaining=remaining, full=full):
                self.reports = [dict(name="GameSir G7 PRO", vid=0x3537, pid=0x105E,
                                     remain=remaining, full=full, status="Discharging")]
                [reading] = xinput.XInputProvider().poll()
                self.assertEqual(reading.level, 20)
                self.assertTrue(reading.approx)

    def test_unreported_charging_is_not_misrepresented_as_known_discharging(self):
        self.dll.slots = {0: (0, 0)}
        self.reports = [dict(name="GameSir G7 PRO", vid=0x3537, pid=0x105E,
                             remain=647, full=1000, status=None)]
        [reading] = xinput.XInputProvider().poll()
        self.assertEqual(reading.level, 65)
        self.assertFalse(reading.charging_known)

    def test_confirmed_flydigi_placeholder_is_not_shown_as_a_full_battery(self):
        self.dll.slots = {0: (3, 1)}
        self.reports = [dict(name="FlyDigi Vader 5 Pro", vid=0x37D7, pid=0x2401,
                             remain=1000, full=1000, rate=None, status="Discharging")]
        with mock.patch.object(xinput.flydigi, "read_connected", return_value=None):
            [reading] = xinput.XInputProvider().poll()
        self.assertEqual(reading.level, 20)
        self.assertTrue(reading.approx)
