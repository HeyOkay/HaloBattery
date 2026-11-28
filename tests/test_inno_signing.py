"""Exercise the token-free external-signing handoff with a fake compiler."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh') or shutil.which('pwsh.exe')
HELPER = ROOT / 'scripts/inno_signing.ps1'

FAKE_COMPILER = r'''
if ($PSNativeCommandUseErrorActionPreference -ne $false) { throw 'Native exit handling was not scoped off' }
$cache=($args | Where-Object { $_ -like '/DSignedUninstallerCache=*' }).Substring(25)
$output=($args | Where-Object { $_ -like '/O*' }).Substring(2)
$name=($args | Where-Object { $_ -like '/F*' }).Substring(2)
$path=Join-Path $cache 'uninst-6.7.4-0123456789.e32'
if (-not (Test-Path -LiteralPath $path)) {
    [IO.File]::WriteAllText($path, 'inert unsigned compiler fixture')
    if ($env:BAD_COMPILER -eq 'true') { $global:LASTEXITCODE=1; return }
    Write-Output 'Signed uninstaller mode is enabled'
    $global:LASTEXITCODE=2
} else {
    [IO.File]::WriteAllText((Join-Path $output ($name+'.exe')), 'inert setup fixture')
    $global:LASTEXITCODE=0
}
'''


@unittest.skipUnless(os.name == 'nt' and PWSH, 'Windows PowerShell 7 required')
class InnoSigningTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='halo inno signing ')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        fixture_repo = self.root / 'isolated-repo'
        (fixture_repo / 'scripts').mkdir(parents=True)
        (fixture_repo / 'installer').mkdir()
        (fixture_repo / 'tools').mkdir()
        (fixture_repo / 'tools/inno_helper.py').write_text(
            "import argparse, hashlib, json\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser(); p.add_argument('command'); p.add_argument('--template'); "
            "p.add_argument('--signed-helper'); p.add_argument('--engine'); p.add_argument('--output'); a=p.parse_args()\n"
            "if a.command=='extract-engine':\n Path(a.output).write_bytes((Path(__file__).parents[2]/'signed-helper.exe').read_bytes()); raise SystemExit(0)\n"
            "data=Path(a.template).read_bytes()+Path(a.signed_helper).read_bytes()\n"
            "Path(a.output).write_bytes(data); print(json.dumps({'source_sha256':hashlib.sha256(Path(a.template).read_bytes()).hexdigest(),'output_sha256':hashlib.sha256(data).hexdigest()}))\n",
            encoding='utf-8')
        self.helper = fixture_repo / 'scripts/inno_signing.ps1'
        shutil.copyfile(HELPER, self.helper)
        (fixture_repo / 'installer/HaloBattery.iss').write_text('; inert compiler fixture\n', encoding='utf-8')
        (fixture_repo / 'halo.ico').write_bytes(b'inert icon binding fixture')
        self.payload = self.root / 'payload'
        self.payload.mkdir()
        (self.payload / 'HaloBattery.exe').write_bytes(b'inert payload')
        compiler_dir = self.root / 'compiler'
        compiler_dir.mkdir()
        (compiler_dir / 'Setup.e32').write_bytes(b'inert template')
        self.signed_helper = self.root / 'signed-helper.exe'
        self.signed_helper.write_bytes(b'inert signed helper')
        self.compiler = compiler_dir / 'compiler.ps1'
        self.compiler.write_text(FAKE_COMPILER, encoding='utf-8')
        self.work = self.root / 'work'
        self.output = self.root / 'output'

    def run_stage(self, stage, *, bad=False, signed=None, stub_signed=False):
        arguments = [PWSH, '-NoProfile', '-NonInteractive', '-File', str(self.helper),
                     '-Stage', stage, '-CompilerPath', str(self.compiler),
                     '-SignedHelperPath', str(self.signed_helper),
                     '-SourceDirectory', str(self.payload), '-AppVersion', '1.18.0',
                     '-WorkDirectory', str(self.work), '-OutputDirectory', str(self.output),
                     '-OutputBaseFilename', 'HaloBattery-test-setup']
        if signed:
            arguments += ['-SignedCacheDirectory', str(signed)]
        command = "$ErrorActionPreference='Stop'; $PSNativeCommandUseErrorActionPreference=$true; "
        if stub_signed:
            command += ("function global:Get-AuthenticodeSignature { "
                        "[pscustomobject]@{SignerCertificate='fixture';SignatureType='Authenticode'} }; ")
        command += '& ' + ' '.join(arg if arg.startswith('-') else "'" + arg.replace("'", "''") + "'"
                                  for arg in arguments[4:])
        command += ("; if ($PSNativeCommandUseErrorActionPreference -ne $true) { throw 'Caller preference changed' }; "
                    "if (Test-Path variable:LASTEXITCODE) { exit $LASTEXITCODE }")
        arguments = [PWSH, '-NoProfile', '-NonInteractive', '-Command', command]
        return subprocess.run(arguments, capture_output=True, text=True, timeout=30,
                              env=dict(os.environ, BAD_COMPILER=str(bad).lower()))

    def test_prepare_requires_exact_expected_compiler_stop_and_records_binding(self):
        result = self.run_stage('Prepare')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((self.work / 'internal-manifest.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(list(manifest), ['uninst-6.7.4-0123456789.e32'])
        self.assertFalse((self.output / 'HaloBattery-test-setup.exe').exists())
        binding = json.loads((self.work / 'binding.json').read_text(encoding='utf-8-sig'))
        self.assertTrue({'payload', 'originalData', 'signedHelper', 'patchedData'} <= binding.keys())

    def test_other_compiler_failure_does_not_produce_a_signing_manifest(self):
        result = self.run_stage('Prepare', bad=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / 'internal-manifest.json').exists())

    def test_complete_refuses_changed_payload_before_compiler_runs(self):
        prepared = self.run_stage('Prepare')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        (self.payload / 'HaloBattery.exe').write_bytes(b'changed')
        result = self.run_stage('Complete', signed=self.work / 'unsigned-cache')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Inno input changed: payload', result.stderr)
        self.assertFalse((self.output / 'HaloBattery-test-setup.exe').exists())

    def test_complete_refuses_changed_signed_helper(self):
        prepared = self.run_stage('Prepare')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        self.signed_helper.write_bytes(b'changed signed helper')
        result = self.run_stage('Complete', signed=self.work / 'unsigned-cache')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Inno input changed:', result.stderr)
        self.assertFalse((self.output / 'HaloBattery-test-setup.exe').exists())

    def test_complete_refuses_unsigned_cache(self):
        prepared = self.run_stage('Prepare')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        result = self.run_stage('Complete', signed=self.work / 'unsigned-cache')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('missing an embedded signature', result.stderr)
        self.assertFalse((self.output / 'HaloBattery-test-setup.exe').exists())

    def test_complete_reuses_exact_cache_without_invoking_a_signing_tool(self):
        prepared = self.run_stage('Prepare')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        result = self.run_stage('Complete', signed=self.work / 'unsigned-cache', stub_signed=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / 'HaloBattery-test-setup.exe').read_bytes(), b'inert setup fixture')

    def test_complete_rejects_extra_returned_cache_files(self):
        prepared = self.run_stage('Prepare')
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        (self.work / 'unsigned-cache' / 'unexpected.exe').write_bytes(b'extra')
        result = self.run_stage('Complete', signed=self.work / 'unsigned-cache', stub_signed=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unexpected signed cache files', result.stderr)


if __name__ == '__main__':
    unittest.main()
