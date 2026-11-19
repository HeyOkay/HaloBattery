"""Run production Settings wire contracts without WinUI, devices or network."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / 'tests/nativeconsole'
SDK = '10.0.404'
RUNTIME = '10.0.15'


class NativeSettingsWireContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = os.environ.get('GITHUB_ACTIONS') == 'true'
        cls.dotnet = shutil.which('dotnet')
        if cls.dotnet:
            sdks = subprocess.run([cls.dotnet, '--list-sdks'], capture_output=True,
                                  encoding='utf-8', timeout=30, check=False)
            match = re.search(r'^' + re.escape(SDK) + r' \[(.+)\]$', sdks.stdout, re.MULTILINE)
            runtimes = subprocess.run([cls.dotnet, '--list-runtimes'], capture_output=True,
                                      encoding='utf-8', timeout=30, check=False)
            installed = sdks.returncode == 0 and runtimes.returncode == 0 and match and re.search(
                r'^Microsoft.NETCore.App ' + re.escape(RUNTIME) + r' \[', runtimes.stdout, re.MULTILINE)
        else:
            installed = False
        if not installed:
            reason = f'Settings wire contracts require SDK {SDK} and runtime {RUNTIME}; CI setup must precede tests'
            if required:
                raise AssertionError(reason)
            raise unittest.SkipTest(reason)
        cls.sdk = str(Path(match.group(1)) / SDK / 'dotnet.dll')
        cls.temporary = tempfile.TemporaryDirectory(prefix='halo-wire-contract-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.work = Path(cls.temporary.name)
        cls.output = cls.work / 'bin'
        cls.environment = {**os.environ, 'DOTNET_NOLOGO': '1', 'DOTNET_CLI_TELEMETRY_OPTOUT': '1',
                           'DOTNET_SKIP_FIRST_TIME_EXPERIENCE': '1', 'DOTNET_GENERATE_ASPNET_CERTIFICATE': 'false',
                           'DOTNET_ADD_GLOBAL_TOOLS_TO_PATH': 'false',
                           'DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE': 'true', 'DOTNET_CLI_HOME': str(cls.work / 'home'),
                           'NUGET_PACKAGES': str(cls.work / 'packages'),
                           'NUGET_HTTP_CACHE_PATH': str(cls.work / 'http-cache')}
        common = [f'-p:BaseIntermediateOutputPath={cls.work / "obj"}{os.sep}',
                  f'-p:MSBuildProjectExtensionsPath={cls.work / "obj"}{os.sep}',
                  f'-p:RestorePackagesPath={cls.work / "packages"}', '-p:RestoreFallbackFolders=',
                  '-p:RestoreAdditionalProjectSources=', '-p:NuGetAudit=false', '-p:RestoreLockedMode=true',
                  '--disable-build-servers']
        project = str(CONSOLE / 'SettingsWireContract.csproj')
        lock = CONSOLE / 'packages.lock.json'
        before = lock.read_bytes()
        cls.run_checked([cls.dotnet, cls.sdk, 'restore', project, '--configfile',
                         str(CONSOLE / 'NuGet.Config'), *common])
        if lock.read_bytes() != before:
            raise AssertionError('Console restore rewrote its committed lock')
        cls.run_checked([cls.dotnet, cls.sdk, 'build', project, '--configuration', 'Release',
                         '--no-restore', '--output', str(cls.output), *common])
        cls.executable = [cls.dotnet, str(cls.output / 'SettingsWireContract.dll')]

    @classmethod
    def run_checked(cls, command, *, input=None):
        result = subprocess.run(command, cwd=cls.work, env=cls.environment, input=input,
                                capture_output=True, encoding='utf-8', timeout=90, check=False)
        if result.returncode:
            raise AssertionError(f'Console contract process failed ({result.returncode}):\n'
                                 + result.stdout + result.stderr)
        return result

    def test_73_independent_baseline_vectors_with_json_reflection_disabled(self):
        data = self.work / 'vectors'
        data.mkdir()
        result = self.run_checked([*self.executable, '--test-root', str(data),
                                   '--fixture-state', str(data / 'state.json')])
        self.assertIn('PASS: 73 byte-exact baseline vectors; reflection disabled', result.stdout)
        self.assertEqual(len(json.loads((CONSOLE / 'wire-goldens.json').read_text(encoding='utf-8'))), 73)
        self.assertIn('.NET ' + RUNTIME, result.stderr)

    def test_real_stdin_stdout_bridge_flushes_seven_exact_baseline_records(self):
        welcome = '{"type":"welcome","protocol":1,"sessionId":"pipe-test"}\n'
        state = '{"type":"state","protocol":1,"revision":7,"sessionId":"pipe-test"}\n'
        result = self.run_checked([*self.executable, '--test-root', str(self.work / 'pipe'), '--pipe-test'],
                                  input=welcome + state)
        lines = result.stdout.splitlines()
        expected = json.loads((CONSOLE / 'pipe-goldens.json').read_text(encoding='utf-8'))
        self.assertEqual(len(lines), 7)
        self.assertEqual(lines, expected)
        records = [json.loads(line) for line in lines]
        self.assertIn('requestId', records[1])
        self.assertNotIn('requestId', records[2])
        self.assertIsNone(records[3]['args']['value'])
        self.assertIn('.NET ' + RUNTIME, result.stderr)


if __name__ == '__main__':
    unittest.main()
