"""Exercise publishing transactions offline with a fake PowerShell dotnet command."""
import json
import base64
import hashlib
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
        self.temp = tempfile.TemporaryDirectory(prefix="halo publish [test] owner's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repository"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "ui" / "HaloBattery.Settings").mkdir(parents=True)
        (self.repo / "halo.ico").write_bytes(b"icon")
        (self.repo / "halo_battery.pyw").write_text('VERSION = "1.17.0"\n', encoding="utf-8")
        for name in ("global.json", "NuGet.Config"):
            shutil.copyfile(REPOSITORY / "ui" / name, self.repo / "ui" / name)
        self.packages = self.root / "packages"
        pins = {}
        for name in ("Fake.Main", "Fake.SDK"):
            content = (name + " archive").encode()
            archive = self.packages / name.lower() / "1.0.0" / (name.lower() + ".1.0.0.nupkg")
            archive.parent.mkdir(parents=True)
            archive.write_bytes(content)
            pins[name] = {"resolved": "1.0.0", "contentHash": base64.b64encode(hashlib.sha512(content).digest()).decode()}
        self.project = self.repo / "ui" / "HaloBattery.Settings"
        for contained in ("true", "false"):
            (self.project / ("packages." + contained + ".lock.json")).write_text(
                json.dumps({"version": 1, "dependencies": {"net10.0": {"Fake.Main": pins["Fake.Main"]}}}), encoding="utf-8")
        (self.project / "packages.archives.lock.json").write_text(
            json.dumps({"version": 1, "dependencies": {name: {"resolved": pin["resolved"],
                       "archiveSha512": pin["contentHash"]} for name, pin in pins.items()}}), encoding="utf-8")
        for source, destination in (("packages.true.lock.json", "packages.aot.true.lock.json"),
                                    ("packages.archives.lock.json", "packages.aot.archives.lock.json")):
            shutil.copyfile(self.project / source, self.project / destination)
        self.assets = self.root / "fake-assets.json"
        self.assets.write_text(json.dumps({"packageFolders": {str(self.packages): {}}, "project": {
            "frameworks": {"net10.0": {"downloadDependencies": [{"name": "Fake.SDK", "version": "[1.0.0, 1.0.0]"}]}}}}), encoding="utf-8")
        self.script = self.repo / "scripts" / "build_settings.ps1"
        shutil.copyfile(REPOSITORY / "scripts" / "build_settings.ps1", self.script)
        self.output = self.repo / ".build" / "settings [release]"
        self.intermediate = self.repo / ".build" / "obj [release]"
        self.trace = self.root / "dotnet.trace"
        self.arguments = self.root / "dotnet-arguments.json"
        self.toolchain_arguments = self.root / "toolchain-arguments.json"
        self.msvc = self.root / "fake pinned msvc [owner's]"
        self.sdk = self.root / "fake pinned sdk [owner's]"
        self.sibling = self.repo / ".build" / "unrelated [keep]"
        self.sibling.mkdir(parents=True)
        (self.sibling / "keep.txt").write_text("keep", encoding="utf-8")

    def seed_output(self):
        self.output.mkdir(parents=True)
        for name in ("HaloBattery.Settings.exe", "HaloBattery.Settings.deps.json",
                     "HaloBattery.Settings.runtimeconfig.json", "stale.dll"):
            (self.output / name).write_text("old " + name, encoding="utf-8")

    def run_publish(self, mode="success", output=None, intermediate=None, enable_verification=False,
                    publish_mode="Jit", configuration="Release", self_contained=True, toolchain_cache=None):
        runner = self.root / "runner.ps1"
        runner.write_text(r'''
$ErrorActionPreference = 'Stop'
function global:python {
    [IO.File]::WriteAllText($env:HALO_TEST_TOOLCHAIN_ARGUMENTS, (ConvertTo-Json -InputObject @($args)))
    if ($env:HALO_TEST_MODE -eq 'toolchainFailure') { $global:LASTEXITCODE = 17; return }
    $global:LASTEXITCODE = 0
    return (@{msvc=$env:HALO_TEST_MSVC; sdk=$env:HALO_TEST_SDK} | ConvertTo-Json)
}
function global:dotnet {
    [IO.File]::AppendAllText($env:HALO_TEST_TRACE, "called`n")
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'publish' -and $args -contains '-p:PublishAot=true') {
        $expectedBin = Join-Path $env:HALO_TEST_MSVC 'bin/Hostx64/x64'
        $expectedLib = (Join-Path $env:HALO_TEST_MSVC 'lib/x64') + ';' +
            (Join-Path $env:HALO_TEST_SDK 'c/um/x64') + ';' + (Join-Path $env:HALO_TEST_SDK 'c/ucrt/x64')
        if (-not $env:PATH.StartsWith($expectedBin + ';') -or $env:LIB -cne $expectedLib -or
            $env:INCLUDE -cne (Join-Path $env:HALO_TEST_MSVC 'include')) {
            throw 'AOT toolchain paths did not match the literal fixture roots'
        }
    }
    if ($args[0] -eq '--version') {
        if ($env:HALO_TEST_MODE -eq 'sdkMismatch') { return '0.0.0' }
        return '10.0.404'
    }
    [IO.File]::WriteAllText($env:HALO_TEST_ARGUMENTS, (ConvertTo-Json -InputObject @($args)))
    if ($env:HALO_TEST_MODE -eq 'fail') { $global:LASTEXITCODE = 42; return }
    if ($args[0] -eq 'restore') {
        [IO.Directory]::CreateDirectory($env:HALO_TEST_INTERMEDIATE) | Out-Null
        Copy-Item -LiteralPath $env:HALO_TEST_ASSETS -Destination (Join-Path $env:HALO_TEST_INTERMEDIATE 'project.assets.json')
        if ($env:HALO_TEST_MODE -eq 'relock') {
            [IO.File]::AppendAllText($env:HALO_TEST_LOCK, ' ')
        }
        return
    }
    if ($env:HALO_TEST_MODE -eq 'publishFail') { $global:LASTEXITCODE = 42; return }
    $destination = $args[[Array]::IndexOf($args, '--output') + 1]
    [IO.Directory]::CreateDirectory($destination) | Out-Null
    $jitNames = @('HaloBattery.Settings.deps.json', 'HaloBattery.Settings.runtimeconfig.json')
    if ($args -contains '-p:PublishAot=true') { $jitNames = @() }
    foreach ($name in $jitNames) {
        [IO.File]::WriteAllText((Join-Path $destination $name), 'new')
    }
    $exe = Join-Path $destination 'HaloBattery.Settings.exe'
    if ($env:HALO_TEST_MODE -eq 'directoryExe') {
        [IO.Directory]::CreateDirectory($exe) | Out-Null
    } elseif ($env:HALO_TEST_MODE -eq 'emptyExe') {
        [IO.File]::WriteAllText($exe, '')
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
    $options = @{}
    if ($env:HALO_TEST_VERIFICATION -eq 'true') { $options.EnableVerification = $true }
    if ($env:HALO_TEST_PUBLISH_MODE) { $options.PublishMode = $env:HALO_TEST_PUBLISH_MODE }
    if ($env:HALO_TEST_TOOLCHAIN_CACHE) { $options.ToolchainCache = $env:HALO_TEST_TOOLCHAIN_CACHE }
    $beforeEnvironment = @($env:PATH, $env:LIB, $env:INCLUDE) | ConvertTo-Json
    try {
        & $env:HALO_TEST_SCRIPT -OutputDirectory $env:HALO_TEST_OUTPUT -IntermediateDirectory $env:HALO_TEST_INTERMEDIATE -Configuration $env:HALO_TEST_CONFIGURATION -SelfContained ($env:HALO_TEST_CONTAINED -eq 'true') @options
    } finally {
        $afterEnvironment = @($env:PATH, $env:LIB, $env:INCLUDE) | ConvertTo-Json
        if ($beforeEnvironment -cne $afterEnvironment) { throw 'Build leaked process environment changes' }
    }
    exit 0
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
''', encoding="utf-8")
        env = os.environ.copy()
        env.update(HALO_TEST_TRACE=str(self.trace), HALO_TEST_MODE=mode,
                   HALO_TEST_VERIFICATION=str(enable_verification).lower(),
                   HALO_TEST_PUBLISH_MODE=publish_mode or "", HALO_TEST_CONFIGURATION=configuration,
                   HALO_TEST_CONTAINED=str(self_contained).lower(),
                   HALO_TEST_ARGUMENTS=str(self.arguments),
                   HALO_TEST_TOOLCHAIN_ARGUMENTS=str(self.toolchain_arguments),
                   HALO_TEST_MSVC=str(self.msvc), HALO_TEST_SDK=str(self.sdk),
                   HALO_TEST_TOOLCHAIN_CACHE=toolchain_cache or "",
                   HALO_TEST_ASSETS=str(self.assets),
                   HALO_TEST_LOCK=str(self.project / ("packages.aot.true.lock.json" if
                       publish_mode == "Aot" or (publish_mode in (None, "Auto") and configuration == "Release"
                       and self_contained and not enable_verification) else
                       "packages." + str(self_contained).lower() + ".lock.json")),
                   HALO_TEST_SCRIPT=str(self.script),
                   HALO_TEST_OUTPUT=str(output if output is not None else self.output),
                   HALO_TEST_INTERMEDIATE=str(intermediate if intermediate is not None else self.intermediate))
        return subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                               "-File", str(runner)], env=env, capture_output=True, text=True, timeout=90)

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

    def test_native_product_and_version_follow_python_application(self):
        (self.repo / "halo_battery.pyw").write_bytes(b'VERSION = "2.34.5"\r\n')
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        arguments = json.loads(self.arguments.read_text(encoding="utf-8-sig"))
        self.assertIn("-p:Version=2.34.5", arguments)
        self.assertIn("-p:FileVersion=2.34.5.0", arguments)
        self.assertIn("-p:Product=HaloBattery", arguments)
        self.assertIn("-p:IncludeSourceRevisionInInformationalVersion=false", arguments)

    def test_verification_is_an_explicit_publish_opt_in(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                result = self.run_publish(enable_verification=enabled)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                arguments = json.loads(self.arguments.read_text(encoding="utf-8-sig"))
                self.assertEqual("-p:HaloBatteryVerification=true" in arguments, enabled)

    def test_publish_uses_locked_restore_and_never_restores_again(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        arguments = json.loads(self.arguments.read_text(encoding="utf-8-sig"))
        self.assertIn("--no-restore", arguments)
        self.assertIn("-p:RestoreLockedMode=true", arguments)
        self.assertIn("-p:NuGetLockFilePath=" + str(self.project / "packages.true.lock.json"), arguments)

    def test_missing_lock_fails_before_dotnet_and_preserves_publication(self):
        self.seed_output()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        (self.project / "packages.true.lock.json").unlink()
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing committed dependency lock", result.stderr)
        self.assertFalse(self.trace.exists())
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
        self.assert_no_transaction_dirs()

    def test_archive_hash_mismatch_and_silent_relock_never_publish(self):
        self.seed_output()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        lock = self.project / "packages.true.lock.json"
        original = lock.read_text(encoding="utf-8")
        main = self.packages / "fake.main" / "1.0.0" / "fake.main.1.0.0.nupkg"
        sdk = self.packages / "fake.sdk" / "1.0.0" / "fake.sdk.1.0.0.nupkg"
        for mode, corrupted in (("relock", None), ("success", main), ("success", sdk)):
            with self.subTest(mode=mode, corrupted=corrupted):
                lock.write_text(original, encoding="utf-8")
                if self.trace.exists(): self.trace.unlink()
                content = corrupted.read_bytes() if corrupted else None
                if corrupted: corrupted.write_bytes(b"altered archive")
                result = self.run_publish(mode)
                if corrupted: corrupted.write_bytes(content)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.trace.read_text().splitlines(), ["called", "called"])
                self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
                self.assert_no_transaction_dirs()

    def test_invalid_or_ambiguous_version_preserves_previous_publication(self):
        self.seed_output()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        for text in ('VERSION = "not-a-version"\n', 'VERSION = "65535.0.0"\n',
                     'VERSION = "999999999999999999999.0.0"\n',
                     'VERSION = "1.2.3"\nVERSION = "2.3.4"\n'):
            with self.subTest(text=text):
                (self.repo / "halo_battery.pyw").write_text(text, encoding="utf-8")
                result = self.run_publish()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(self.trace.exists(), "invalid version must fail before dotnet")
                self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
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
                                 ["called"] * (1 if mode == "sdkMismatch" else 2 if mode == "fail" else 3))
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
        self.assertEqual(self.trace.read_text().splitlines(), ["called", "called", "called"])
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


    def test_default_release_selects_aot_exact_lock_and_invocation_relative_cache(self):
        cache = "relative [toolchain cache]"
        result = self.run_publish(publish_mode=None, toolchain_cache=cache)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads(self.arguments.read_text(encoding="utf-8-sig"))
        self.assertIn("-p:PublishAot=true", args)
        self.assertIn("-p:NuGetLockFilePath=" + str(self.project / "packages.aot.true.lock.json"), args)
        toolchain = json.loads(self.toolchain_arguments.read_text(encoding="utf-8-sig"))
        self.assertEqual(toolchain[toolchain.index("--cache") + 1], str((Path.cwd() / cache).resolve()))

    def test_aot_raw_archive_hash_failure_preserves_previous_publication(self):
        self.seed_output()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        pin = self.project / "packages.aot.archives.lock.json"
        data = json.loads(pin.read_text())
        data["dependencies"]["Fake.Main"]["archiveSha512"] = base64.b64encode(b"x" * 64).decode()
        pin.write_text(json.dumps(data), encoding="utf-8")
        result = self.run_publish(publish_mode="Aot")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archive hash mismatch", result.stderr)
        self.assertEqual(self.trace.read_text().splitlines(), ["called", "called"])
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
        self.assert_no_transaction_dirs()

    def test_auto_mode_uses_only_supported_aot_and_exact_lock_variant(self):
        cases = [("Release", True, False, True), ("Debug", True, False, False),
                 ("Release", True, True, False), ("Release", False, False, False)]
        for configuration, contained, verification, aot in cases:
            with self.subTest(configuration=configuration, contained=contained, verification=verification):
                result = self.run_publish(publish_mode="Auto", configuration=configuration,
                                          self_contained=contained, enable_verification=verification)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                args = json.loads(self.arguments.read_text(encoding="utf-8-sig"))
                self.assertIn("-p:PublishAot=" + str(aot).lower(), args)
                expected = "packages.aot.true.lock.json" if aot else "packages." + str(contained).lower() + ".lock.json"
                self.assertIn("-p:NuGetLockFilePath=" + str(self.project / expected), args)
                self.assertEqual("-p:IlcUseEnvironmentalTools=true" in args, aot)
                self.assertEqual("-p:CsWinRTUseEnvironmentalTools=true" in args, aot)

    def test_explicit_invalid_aot_modes_fail_before_dotnet(self):
        self.seed_output()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        for configuration, contained, verification in (("Debug", True, False),
                ("Release", False, False), ("Release", True, True)):
            result = self.run_publish(publish_mode="Aot", configuration=configuration,
                                     self_contained=contained, enable_verification=verification)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.trace.exists())
            self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
        self.assert_no_transaction_dirs()

    def test_aot_marker_allows_repeat_publication_without_jit_files(self):
        self.seed_output()
        for _ in range(2):
            result = self.run_publish(publish_mode="Aot")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            marker = json.loads((self.output / "HaloBattery.Settings.publish.json").read_text())
            self.assertEqual(marker, {"schema": 1, "project": "HaloBattery.Settings", "mode": "Aot"})
            self.assertFalse((self.output / "HaloBattery.Settings.deps.json").exists())
            self.assertFalse((self.output / "stale.dll").exists())
        self.assert_no_transaction_dirs()

    def test_aot_missing_pins_and_toolchain_or_native_publish_failure_preserve_old_output(self):
        self.seed_output()
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        for mode in ("toolchainFailure", "publishFail", "missingExe", "emptyExe", "relock", "promotionFailure"):
            lock = self.project / "packages.aot.true.lock.json"
            original = lock.read_bytes()
            result = self.run_publish(mode, publish_mode="Aot")
            lock.write_bytes(original)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)
            self.assert_no_transaction_dirs()
        for name in ("packages.aot.true.lock.json", "packages.aot.archives.lock.json"):
            pin = self.project / name
            original = pin.read_bytes()
            pin.unlink()
            result = self.run_publish(publish_mode="Aot")
            pin.write_bytes(original)
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, before)

    def test_aot_marker_cannot_authorize_other_or_empty_output(self):
        self.output.mkdir(parents=True)
        (self.output / "HaloBattery.Settings.exe").write_bytes(b"exe")
        marker = self.output / "HaloBattery.Settings.publish.json"
        for text in ('{}', '{"schema":1,"project":"Other","mode":"Aot"}',
                     '{"schema":2,"project":"HaloBattery.Settings","mode":"Aot"}',
                     '{"schema":1,"project":"HaloBattery.Settings","mode":"Jit"}', 'bad-json'):
            marker.write_text(text, encoding="utf-8")
            result = self.run_publish(publish_mode="Aot")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(self.trace.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), text)
        marker.write_text('{"schema":1,"project":"HaloBattery.Settings","mode":"Aot"}')
        (self.output / "HaloBattery.Settings.exe").write_bytes(b"")
        self.assertNotEqual(self.run_publish(publish_mode="Aot").returncode, 0)


if __name__ == "__main__":
    unittest.main()
