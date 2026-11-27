"""Execute activation/source gates with fake Git: no network, keys or devices."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh') or shutil.which('pwsh.exe')
TEST_PIN = '17F9F411C424674855A077F9A120A7A434990AF1'
SOURCE = '0123456789abcdef0123456789abcdef01234567'


@unittest.skipUnless(PWSH, 'PowerShell 7 is required')
class SigningActivationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='halo signing gates ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'scripts').mkdir()
        (self.root / '.github/signpath').mkdir(parents=True)
        shutil.copyfile(ROOT / 'scripts/signing_pipeline.ps1', self.root / 'scripts/signing_pipeline.ps1')
        (self.root / 'halo_battery.pyw').write_text('VERSION = "1.18.0"\n', encoding='utf-8')
        self.pin('B' * 40)
        self.runner = self.root / 'runner.ps1'
        self.runner.write_text(r"""
$ErrorActionPreference='Stop'
function global:git {
    [IO.File]::AppendAllText($env:GIT_RECEIPT, ($args -join ' ') + "`n")
    $global:LASTEXITCODE = if ($env:BAD_ANCESTRY -eq 'true' -and $args[0] -eq 'merge-base') {1} else {0}
}
try { & $env:PIPELINE -Stage Initialize; exit 0 }
catch { [Console]::Error.WriteLine($_.Exception.Message); exit 1 }
""", encoding='utf-8')

    def pin(self, value):
        (self.root / '.github/signpath/production.json').write_text(
            json.dumps({'certificate_thumbprint': value}), encoding='utf-8')

    def run_gate(self, **changes):
        output = self.root / 'outputs'
        receipt = self.root / 'git-calls'
        output.write_text('', encoding='utf-8')
        receipt.write_text('', encoding='utf-8')
        environment = dict(os.environ, GITHUB_REPOSITORY='HeyOkay/HaloBattery', GITHUB_SHA=SOURCE,
            GITHUB_EVENT_NAME='workflow_dispatch', GITHUB_REF='refs/heads/pre-release',
            GITHUB_REF_TYPE='branch', GITHUB_REF_NAME='pre-release', CONFIRMED_TEST='true',
            CONFIRMED_RELEASE='false', TEST_ENABLED='true', RELEASE_ENABLED='false',
            PIPELINE=str(self.root / 'scripts/signing_pipeline.ps1'), GITHUB_OUTPUT=str(output),
            GIT_RECEIPT=str(receipt), BAD_ANCESTRY='false', PSExecutionPolicyPreference='Bypass')
        environment.update(changes)
        result = subprocess.run([PWSH, '-NoProfile', '-NonInteractive', '-File', str(self.runner)],
                                env=environment, capture_output=True, text=True, timeout=30)
        return result, output.read_text(encoding='utf-8-sig'), receipt.read_text(encoding='utf-8')

    def production(self, **changes):
        return self.run_gate(**dict(CONFIRMED_TEST='false', CONFIRMED_RELEASE='true', RELEASE_ENABLED='true',
                                   GITHUB_REF='refs/heads/main', GITHUB_REF_NAME='main') | changes)

    def test_explicit_test_pre_release_uses_only_test_pin_without_git(self):
        result, output, git = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mode=Test', output)
        self.assertIn('certificate-thumbprint=' + TEST_PIN, output)
        self.assertEqual(git, '')

    def test_test_mode_rejects_other_source_disabled_or_conflicting_inputs(self):
        for change in ({'GITHUB_REF': 'refs/heads/main'}, {'TEST_ENABLED': 'false'},
                       {'GITHUB_REPOSITORY': 'attacker/HaloBattery'}, {'CONFIRMED_RELEASE': 'true'},
                       {'GITHUB_SHA': 'bad'}, {'CONFIRMED_TEST': 'false'}):
            with self.subTest(change=change):
                result, output, git = self.run_gate(**change)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')
                self.assertEqual(git, '')

    def test_production_dry_run_requires_reviewed_pin_enablement_main_and_ancestry(self):
        result, output, git = self.production()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mode=Production', output)
        self.assertIn('certificate-thumbprint=' + 'B' * 40, output)
        self.assertEqual(git.splitlines(), ['fetch origin main', 'merge-base --is-ancestor ' + SOURCE + ' origin/main'])
        for change in ({'RELEASE_ENABLED': 'false'}, {'GITHUB_REF': 'refs/heads/pre-release'},
                       {'BAD_ANCESTRY': 'true'}, {'CONFIRMED_TEST': 'true'}):
            with self.subTest(change=change):
                result, output, _ = self.production(**change)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')

    def test_missing_malformed_and_test_production_pins_fail_before_git(self):
        for pin in ('', TEST_PIN, 'B' * 39, None, 12):
            with self.subTest(pin=pin):
                self.pin(pin)
                result, output, git = self.production()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')
                self.assertEqual(git, '')

    def test_stable_tag_must_match_version_and_main_ancestry(self):
        changes = dict(GITHUB_EVENT_NAME='push', GITHUB_REF_TYPE='tag', GITHUB_REF='refs/tags/v1.18.0',
                       GITHUB_REF_NAME='v1.18.0', CONFIRMED_TEST='false', CONFIRMED_RELEASE='false', RELEASE_ENABLED='true')
        result, output, _ = self.run_gate(**changes)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mode=Production', output)
        for tag in ('v1.17.0', 'v1.18.0-extra', 'vv1.18.0', 'v1.18.0;bad'):
            with self.subTest(tag=tag):
                result, output, git = self.run_gate(**(changes | {'GITHUB_REF_NAME': tag}))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output, '')
                self.assertEqual(git, '')


if __name__ == '__main__':
    unittest.main()
