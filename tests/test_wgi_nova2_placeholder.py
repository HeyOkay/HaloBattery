"""Nova 2 Lite's captured WGI placeholder, without native I/O or network.

Exact owner report: HeyOkay/HaloBattery#142 comment5885746260, supported by
comment5879245780's Windows100% versus maker69% comparison. The literal report
below is independent of provider constants. Other capacity/status variations
are synthetic boundary checks, not new hardware claims.
"""
import copy
import json
import unittest
from unittest import mock

import test_xinput_identity as identity
from providers import wgi, xinput


CAPTURED = dict(name='Xbox 360 Controller for Windows', vid=0x3537, pid=0x1098,
                status='Discharging', remain=1000, full=1000, rate=None)


class Nova2ReportTests(unittest.TestCase):
    def report(self, **changes):
        raw = dict(CAPTURED, **changes)
        return wgi.WgiController(raw)

    def test_exact_captured_object_is_unknown_without_losing_raw_fields(self):
        raw = copy.deepcopy(CAPTURED)
        report = wgi.WgiController(raw)
        self.assertIsNone(report.level)
        self.assertFalse(report.charging)
        self.assertTrue(report.placeholder)
        self.assertEqual(report.raw, CAPTURED)
        self.assertEqual(raw, CAPTURED)
        self.assertIn('remain=1000 full=1000 rate=None', report.describe())

    def test_other_ids_with_identical_report_keep_full_capacity(self):
        for vid, pid in ((0x3537, 0x105E), (0x3537, 0x1099), (0x045E, 0x028E),
                         (0x37D7, 0x2401), (0x1234, 0x1098)):
            with self.subTest(vid=vid, pid=pid):
                self.assertEqual(self.report(vid=vid, pid=pid).level, 100)

    def test_name_alone_never_establishes_placeholder_identity(self):
        for vid, pid in ((0x1234, 0x1234), (0, 0), ('0x3537', 0x1098), (0x3537, '0x1098')):
            report = self.report(name='GameSir Nova 2 Lite', vid=vid, pid=pid)
            self.assertEqual(report.level, 100)

    def test_real_zero_intermediate_and_full_ratios_are_kept(self):
        for remain, full, expected in ((0, 1000, 0), (690, 1000, 69), (999, 1000, 100),
                                       (100, 100, 100), (690, 690, 100),
                                       (0x7FFFFFFF, 0x7FFFFFFF, 100)):
            with self.subTest(remain=remain, full=full):
                self.assertEqual(self.report(remain=remain, full=full).level, expected)

    def test_charging_idle_unknown_and_reported_rates_keep_full(self):
        for status, rate in (('Charging', None), ('Idle', None), (None, None),
                             ('Discharging', 0), ('Discharging', -100)):
            with self.subTest(status=status, rate=rate):
                report = self.report(status=status, rate=rate)
                self.assertEqual(report.level, 100)
                self.assertEqual(report.charging, status == 'Charging')
        report = self.report(status='discharging')
        self.assertIsNone(report.level)

    def test_notpresent_and_invalid_capacities_remain_refused(self):
        missing = self.report(status='NotPresent')
        self.assertIsNone(missing.level)
        self.assertFalse(missing.placeholder)
        for remain, full in ((None, 1000), (1000, None), (1001, 1000), (-1, 1000),
                             (1000, 0), (True, 1000), (1000, True), (1000., 1000),
                             (1000, '1000'), (2**64, 2**64)):
            with self.subTest(remain=remain, full=full):
                report = self.report(remain=remain, full=full)
                self.assertIsNone(report.level)
                self.assertFalse(report.placeholder)

    def test_parse_keeps_identity_row_and_explains_no_level_with_raw_values(self):
        diagnostics = []
        [report] = wgi.parse(json.dumps(dict(controllers=[CAPTURED])), diagnostics)
        self.assertEqual((report.vid, report.pid, report.level), (0x3537, 0x1098, None))
        self.assertIn('3537:1098 status=Discharging remain=1000 full=1000 rate=None -> no level',
                      diagnostics[0])


class Nova2FusionTests(unittest.TestCase):
    def setUp(self):
        identity.ControllerIdentityTests.setUp(self)
        self.dll.slots = {0: (1, 3)}  # literal XInput wired; level byte is not a battery gauge
        for module in (xinput.flydigi, xinput.beitong):
            patcher = mock.patch.object(module, 'read_connected', side_effect=AssertionError('device I/O'))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_known_placeholder_uses_unknown_instead_of_wired_full_or_charging(self):
        self.reports = [dict(CAPTURED)]
        [reading] = xinput.XInputProvider().poll()
        self.assertEqual((reading.level, reading.charging, reading.ui_message),
                         (None, False, 'state.unreported'))

    def test_real_nova_level_and_g7_full_are_kept(self):
        for changes, expected in ((dict(remain=690), 69), (dict(remain=0), 0),
                                   (dict(remain=100, full=100), 100), (dict(pid=0x105E), 100)):
            with self.subTest(changes=changes):
                self.reports = [dict(CAPTURED, **changes)]
                [reading] = xinput.XInputProvider().poll()
                self.assertEqual((reading.level, reading.charging), (expected, False))

    def test_known_placeholder_can_fall_back_to_actual_coarse_reading(self):
        self.dll.slots = {0: (3, 1)}  # NiMH, low
        self.reports = [dict(CAPTURED)]
        [reading] = xinput.XInputProvider().poll()
        self.assertEqual((reading.level, reading.charging), (20, False))
        self.assertTrue(reading.approx)

    def test_multiple_reports_do_not_gain_a_slot_assignment_when_one_level_is_refused(self):
        self.dll.slots = {0: (3, 1)}
        self.reports = [dict(CAPTURED), dict(CAPTURED, pid=0x105E, remain=690)]
        for reports in (self.reports, list(reversed(self.reports))):
            self.reports = reports
            [reading] = xinput.XInputProvider().poll()
            self.assertEqual(reading.level, 20)
            self.assertTrue(reading.approx)

    def test_multiple_slots_keep_their_own_coarse_levels(self):
        self.dll.slots = {0: (3, 1), 1: (3, 2)}
        self.reports = [dict(CAPTURED)]
        readings = xinput.XInputProvider().poll()
        self.assertEqual([(r.key, r.level) for r in readings], [('xinput:0', 20), ('xinput:1', 55)])

    def test_existing_8bitdo_discharging_dongle_already_has_no_false_full(self):
        # #24/#110 are additional confirmed placeholders. They are outside the
        # eligible WGI level vendors; their status already suppresses wired100.
        for pid in (0x310A, 0x3106):
            with self.subTest(pid=pid):
                self.reports = [dict(CAPTURED, vid=0x2DC8, pid=pid)]
                [reading] = xinput.XInputProvider().poll()
                self.assertEqual((reading.level, reading.charging), (None, False))


if __name__ == '__main__':
    unittest.main()
