import unittest
import test_endgame_4k as baseline
from test_endgame_4k import reply, ident


class DiagnosticSamples(unittest.TestCase):
    setUp = baseline.Endgame4kTests.setUp
    run_fake = baseline.Endgame4kTests.run_fake

    def test_alternating_values_rejected_but_sequence_available(self):
        result, dev, provider = self.run_fake(
            [ident(), reply(bytes([30])), reply(bytes([35])), reply(bytes([30]))])
        self.assertEqual(result, [])
        diag = '\n'.join(provider.diagnostics())
        self.assertIn('validated_battery_samples=[30, 35, 30]', diag)
        self.assertIn('phase=battery', diag)
        self.assertIn('poll_elapsed_ms=', diag)
        self.assertNotIn('read_elapsed_ms=', diag)
        self.assertEqual([r[1] for r in dev.writes], [14, 180, 180, 180])

    def test_failed_identity_has_no_battery_values(self):
        result, dev, provider = self.run_fake([reply(bytes([95]), status=8)])
        self.assertEqual(result, [])
        diag = '\n'.join(provider.diagnostics())
        self.assertIn('phase=identity', diag)
        self.assertIn('validated_battery_samples=[]', diag)
        self.assertNotIn('95', diag)
        self.assertEqual(len(dev.writes), 1)

    def test_later_status_failure_preserves_only_previous_valid_sample(self):
        _, _, provider = self.run_fake([ident(), reply(bytes([30])), reply(bytes([95]), status=8)])
        diag = '\n'.join(provider.diagnostics())
        self.assertIn('validated_battery_samples=[30]', diag)
        self.assertNotIn('95', diag)

    def test_invalid_percentage_never_enters_validated_samples(self):
        _, _, provider = self.run_fake([ident(), reply(bytes([255]))])
        self.assertIn('validated_battery_samples=[]', '\n'.join(provider.diagnostics()))

    def test_device_exception_details_are_not_logged(self):
        with self.assertLogs('providers.endgame_4k', level='INFO') as captured:
            self.run_fake([OSError('PRIVATE-HID-PATH-AND-SERIAL')])
        text = '\n'.join(captured.output)
        self.assertNotIn('PRIVATE-HID-PATH-AND-SERIAL', text)
        self.assertIn('phase=identity', text)

    def test_agreement_failure_keeps_prior_value_offline(self):
        _, _, provider = self.run_fake([ident(), reply(bytes([45])), reply(bytes([45]))])
        result, _, provider = self.run_fake(
            [ident(), reply(bytes([30])), reply(bytes([35])), reply(bytes([30]))], provider=provider)
        self.assertEqual(result[0].level, 45)
        self.assertFalse(result[0].online)
        self.assertIn('[30, 35, 30]', '\n'.join(provider.diagnostics()))
