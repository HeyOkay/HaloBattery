"""Evaluate native compile inputs offline, without restoring or opening WinUI."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[1]
PROJECT = REPOSITORY / "ui" / "HaloBattery.Settings" / "HaloBattery.Settings.csproj"
DOTNET = shutil.which("dotnet")
SYMBOL = "HALO_BATTERY_VERIFICATION"


@unittest.skipUnless(os.name == "nt" and DOTNET, "Windows .NET SDK required")
class NativeVerificationBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli_home = tempfile.TemporaryDirectory(prefix="halo native SDK home ")
        cls.addClassCleanup(cls.cli_home.cleanup)
        cls.environment = {**os.environ, "DOTNET_CLI_HOME": cls.cli_home.name,
                           "DOTNET_NOLOGO": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                           "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
                           "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
                           "DOTNET_ADD_GLOBAL_TOOLS_TO_PATH": "false",
                           "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true"}
        result = subprocess.run([DOTNET, "--list-sdks"], capture_output=True,
                                text=True, encoding="utf-8", timeout=30, env=cls.environment)
        match = re.search(r"^10\.0\.404 \[(.+)\]$", result.stdout, re.MULTILINE)
        if result.returncode or match is None:
            raise unittest.SkipTest("The pinned .NET SDK 10.0.404 is not installed")
        cls.sdk = str(Path(match.group(1)) / "10.0.404" / "dotnet.dll")
        # First-use text belongs to SDK initialization, never the JSON result.
        # This safe standalone invocation initializes a test-owned profile once.
        initialized = subprocess.run([DOTNET, cls.sdk, "--version"],
                                     cwd=cls.cli_home.name, capture_output=True, text=True,
                                     encoding="utf-8", timeout=30, env=cls.environment)
        if initialized.returncode or initialized.stdout.strip() != "10.0.404":
            raise AssertionError(initialized.stdout + initialized.stderr)

    def evaluate(self, configuration, verification=None, self_contained=None, publish_aot=None, reject=False):
        # Evaluation alone cannot restore packages, run the compiler, connect to
        # hardware, or write build output into the source checkout.
        with tempfile.TemporaryDirectory(prefix="halo native evaluation ") as temp:
            args = [DOTNET, self.sdk, "msbuild", str(PROJECT), "-nologo",
                    "-getItem:Compile,TrimmerRootAssembly,TrimmerRootDescriptor",
                    "-getProperty:DefineConstants,HaloBatteryVerification,PublishAot,NuGetLockFilePath,SelfContained",
                    "-p:Configuration=" + configuration,
                    "-p:BaseIntermediateOutputPath=" + temp + os.sep,
                    "-p:MSBuildProjectExtensionsPath=" + temp + os.sep]
            if verification is not None:
                args.append("-p:HaloBatteryVerification=" + verification)
            if self_contained is not None:
                args.append("-p:SelfContained=" + self_contained)
            if publish_aot is not None:
                args.append("-p:PublishAot=" + publish_aot)
            if reject:
                args.append("-target:RequireSupportedAotMode")
            result = subprocess.run(args, cwd=temp, capture_output=True, text=True,
                                    encoding="utf-8", timeout=30, env=self.environment)
            if reject:
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("AOT requires self-contained Release without verification", result.stdout + result.stderr)
                return None
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)

    def assert_harness(self, data, enabled):
        sources = {Path(item["FullPath"]).name for item in data["Items"]["Compile"]}
        harness = {p.name for p in PROJECT.parent.glob("*Verification.cs")}
        harness.add("FixtureWindowCapture.cs")
        self.assertGreater(len(harness), 1)
        if enabled:
            self.assertLessEqual(harness, sources)
        else:
            self.assertFalse(harness & sources)
        self.assertEqual(SYMBOL in data["Properties"]["DefineConstants"].split(";"), enabled)
        self.assertEqual(data["Properties"]["HaloBatteryVerification"], str(enabled).lower())
        self.assertLessEqual({"App.xaml.cs", "MainWindow.cs", "SettingsProtocol.cs",
                             "MainWindow.Splash.cs", "MainWindow.Spotlight.cs"}, sources)
        # Preserve app activation and explicit dynamic-XAML metadata while QA
        # code is excluded before compilation.
        roots = {item["Identity"] for item in data["Items"]["TrimmerRootAssembly"]}
        self.assertIn("HaloBattery.Settings", roots)
        self.assertNotIn("Microsoft.WinUI", roots)
        descriptors = {Path(item["Identity"]).resolve() for item in data["Items"]["TrimmerRootDescriptor"]}
        self.assertIn((PROJECT.parent / "DynamicXaml.Descriptors.xml").resolve(), descriptors)
        import xml.etree.ElementTree as ET
        descriptor = ET.parse(PROJECT.parent / "DynamicXaml.Descriptors.xml").getroot()
        assembly = descriptor.find("assembly[@fullname='Microsoft.WinUI']")
        self.assertIsNotNone(assembly)
        types = assembly.findall("type")
        self.assertEqual(len(types), 24)
        self.assertTrue(all(item.get("preserve") == "all" for item in types))
        names = {item.get("fullname") for item in types}
        self.assertLessEqual({"Microsoft.UI.Xaml.Controls.ControlTemplate", "Microsoft.UI.Xaml.DataTemplate",
                             "Microsoft.UI.Xaml.Controls.Primitives.ToggleButton",
                             "Microsoft.UI.Xaml.Media.Animation.Storyboard"}, names)

    def test_normal_release_excludes_verification_sources_and_symbol(self):
        self.assert_harness(self.evaluate("Release"), False)

    def test_explicit_release_opt_in_retains_complete_harness(self):
        self.assert_harness(self.evaluate("Release", "true"), True)

    def test_debug_retains_complete_harness_by_default(self):
        self.assert_harness(self.evaluate("Debug"), True)

    def test_explicit_debug_opt_out_excludes_harness(self):
        self.assert_harness(self.evaluate("Debug", "false"), False)


    def test_evaluated_mode_and_lock_follow_release_debug_qa_and_framework_policy(self):
        cases = [("Release", None, None, None, "true", "packages.aot.true.lock.json"),
                 ("Release", "true", None, None, "false", "packages.true.lock.json"),
                 ("Debug", None, None, None, "false", "packages.true.lock.json"),
                 ("Debug", "false", None, None, "false", "packages.true.lock.json"),
                 ("Release", None, "false", None, "false", "packages.false.lock.json"),
                 ("Release", None, None, "false", "false", "packages.true.lock.json"),
                 ("Release", "false", "true", "true", "true", "packages.aot.true.lock.json")]
        for configuration, verification, contained, aot, expected_mode, expected_lock in cases:
            with self.subTest(configuration=configuration, verification=verification,
                              contained=contained, aot=aot):
                data = self.evaluate(configuration, verification, contained, aot)["Properties"]
                self.assertEqual(data["PublishAot"], expected_mode)
                self.assertEqual(Path(data["NuGetLockFilePath"]).resolve(),
                                 (PROJECT.parent / expected_lock).resolve())


    def test_project_rejects_explicit_aot_debug_qa_or_framework_dependent(self):
        for configuration, verification, contained in (("Debug", "false", "true"),
                ("Release", "true", "true"), ("Release", "false", "false")):
            with self.subTest(configuration=configuration, verification=verification, contained=contained):
                self.evaluate(configuration, verification, contained, "true", reject=True)


if __name__ == "__main__":
    unittest.main()
