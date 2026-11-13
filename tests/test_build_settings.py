"""Exercise publishing transactions offline with a fake PowerShell dotnet command."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


@unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
class SettingsPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="halo publish [test] ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repository"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "ui" / "HaloBattery.Settings").mkdir(parents=True)
        (self.repo / "halo.ico").write_bytes(b"icon")
        self.script = self.repo / "scripts" / "build_settings.ps1"
        shutil.copyfile(REPOSITORY / "scripts" / "build_settings.ps1", self.script)
        self.output = self.repo / ".build" / "settings [release]"
        self.intermediate = self.repo / ".build" / "obj [release]"
        self.trace = self.root / "dotnet.trace"
        self.sibling = self.repo / ".build" / "unrelated [keep]"
        self.sibling.mkdir(parents=True)
        (self.sibling / "keep.txt").write_text("keep", encoding="utf-8")

    def seed_output(self):
        self.output.mkdir(parents=True)
        for name in ("HaloBattery.Settings.exe", "HaloBattery.Settings.deps.json",
                     "HaloBattery.Settings.runtimeconfig.json", "stale.dll"):
            (self.output / name).write_text("old " + name, encoding="utf-8")

    def run_publish(self, mode="success", output=None, intermediate=None):
        runner = self.root / "runner.ps1"
        runner.write_text(r'''
$ErrorActionPreference = 'Stop'
function global:dotnet {
    [IO.File]::AppendAllText($env:HALO_TEST_TRACE, "called`n")
    $global:LASTEXITCODE = 0
    if ($args[0] -eq '--version') {
        if ($env:HALO_TEST_MODE -eq 'sdkMismatch') { return '0.0.0' }
        return '10.0.401'
    }
    if ($env:HALO_TEST_MODE -eq 'fail') { $global:LASTEXITCODE = 42; return }
    $destination = $args[[Array]::IndexOf($args, '--output') + 1]
    [IO.Directory]::CreateDirectory($destination) | Out-Null
    foreach ($name in @('HaloBattery.Settings.deps.json', 'HaloBattery.Settings.runtimeconfig.json')) {
        [IO.File]::WriteAllText((Join-Path $destination $name), 'new')
    }
    $exe = Join-Path $destination 'HaloBattery.Settings.exe'
    if ($env:HALO_TEST_MODE -eq 'directoryExe') {
        [IO.Directory]::CreateDirectory($exe) | Out-Null
    } elseif ($env:HALO_TEST_MODE -ne 'missingExe') {
        [IO.File]::WriteAllText($exe, 'new executable')
    }
}
if ($env:HALO_TEST_MODE -eq 'promotionFailure') {
    function global:Rename-Item {
        [CmdletBinding()]
        param([string]$LiteralPath, [string]$NewName)
        if ((Split-Path $LiteralPath -Leaf) -like '*.stage-*') { throw 'Injected promotion failure' }
        Microsoft.PowerShell.Management\Rename-Item -LiteralPath $LiteralPath -NewName $NewName
    }
}
if ($env:HALO_TEST_MODE -eq 'collision') {
    function global:Rename-Item {
        [CmdletBinding()]
        param([string]$LiteralPath, [string]$NewName)
        Microsoft.PowerShell.Management\Rename-Item -LiteralPath $LiteralPath -NewName $NewName
        if ($LiteralPath -eq $env:HALO_TEST_OUTPUT) {
            [IO.Directory]::CreateDirectory($env:HALO_TEST_OUTPUT) | Out-Null
            [IO.File]::WriteAllText((Join-Path $env:HALO_TEST_OUTPUT 'external.txt'), 'external owner')
        }
    }
}
try {
    & $env:HALO_TEST_SCRIPT -OutputDirectory $env:HALO_TEST_OUTPUT -IntermediateDirectory $env:HALO_TEST_INTERMEDIATE
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
''', encoding="utf-8")
        env = os.environ.copy()
        env.update(HALO_TEST_TRACE=str(self.trace), HALO_TEST_MODE=mode,
                   HALO_TEST_SCRIPT=str(self.script),
                   HALO_TEST_OUTPUT=str(output if output is not None else self.output),
                   HALO_TEST_INTERMEDIATE=str(intermediate if intermediate is not None else self.intermediate))
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(runner)], env=env, capture_output=True, text=True, timeout=30)

    def assert_no_transaction_dirs(self):
        names = [p.name for p in self.output.parent.iterdir()]
        self.assertFalse(any(name.startswith(self.output.name + ".stage-") for name in names))
        self.assertFalse(any(name.startswith(self.output.name + ".backup-") for name in names))
        self.assertEqual((self.sibling / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_success_replaces_old_tree_and_preserves_literal_siblings(self):
        self.seed_output()
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.output / "HaloBattery.Settings.exe").read_text(), "new executable")
        self.assertFalse((self.output / "stale.dll").exists())
        self.assert_no_transaction_dirs()

    def test_success_creates_missing_output(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.output / "HaloBattery.Settings.exe").is_file())
        self.assert_no_transaction_dirs()

    def test_failures_preserve_previous_publication(self):
        for mode in ("fail", "missingExe", "directoryExe", "sdkMismatch", "promotionFailure"):
            with self.subTest(mode=mode):
                if self.trace.exists():
                    self.trace.unlink()
                if self.output.exists():
                    shutil.rmtree(self.output)
                self.seed_output()
                before = {p.name: p.read_bytes() for p in self.output.iterdir()}
                result = self.run_publish(mode)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(self.trace.exists(), result.stdout + result.stderr)
                self.assertEqual(self.trace.read_text().splitlines(),
                                 ["called"] * (1 if mode == "sdkMismatch" else 2))
                self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
                self.assert_no_transaction_dirs()

    def test_unsafe_paths_fail_before_dotnet(self):
        project = self.repo / "ui" / "HaloBattery.Settings"
        cases = [(Path(self.repo.anchor), self.intermediate), (self.repo, self.intermediate),
                 (self.repo.parent, self.intermediate), (project, self.intermediate),
                 (project / "publish", self.intermediate), (self.repo / "ui", self.intermediate),
                 (self.output, self.output), (self.output, self.output / "obj"),
                 (self.output, self.output.parent)]
        for output, intermediate in cases:
            with self.subTest(output=output, intermediate=intermediate):
                result = self.run_publish(output=output, intermediate=intermediate)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.trace.exists(), "guard must run before dotnet")
        self.assertTrue(self.script.is_file())
        self.assert_no_transaction_dirs()

    def test_collision_preserves_backup_and_external_destination(self):
        self.seed_output()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        result = self.run_publish("collision")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.trace.read_text().splitlines(), ["called", "called"])
        self.assertEqual({p.name: p.read_text(encoding="utf-8") for p in self.output.iterdir()},
                         {"external.txt": "external owner"})
        backups = [p for p in self.output.parent.iterdir()
                   if p.name.startswith(self.output.name + ".backup-")]
        self.assertEqual(len(backups), 1)
        self.assertEqual({p.name: p.read_bytes() for p in backups[0].iterdir()}, before)
        self.assertIn(str(backups[0]), result.stderr)
        self.assertFalse(any(p.name.startswith(self.output.name + ".stage-")
                             for p in self.output.parent.iterdir()))
        self.assertEqual((self.sibling / "keep.txt").read_text(encoding="utf-8"), "keep")

    def test_unknown_nonempty_output_is_preserved(self):
        self.output.mkdir(parents=True)
        marker = self.output / "personal.txt"
        marker.write_text("personal", encoding="utf-8")
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.trace.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "personal")
        self.assert_no_transaction_dirs()

    def test_existing_file_output_is_preserved(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text("personal file", encoding="utf-8")
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.trace.exists())
        self.assertEqual(self.output.read_text(encoding="utf-8"), "personal file")
        self.assert_no_transaction_dirs()


if __name__ == "__main__":
    unittest.main()
