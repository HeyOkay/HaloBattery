"""Keep the reviewed security toolchain consistent across local/CI/locked inputs."""
import base64
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "ui" / "HaloBattery.Settings"
SDK = "10.0.404"
RUNTIME = "10.0.15"
# Complete signed nuget.org archives, independently SHA-512 hashed after the
# verified SDK restore (2026-11-18); these are not NuGet contentHash values.
RUNTIME_ARCHIVES = {
    "Microsoft.NET.ILLink.Tasks": "q/cuZxi8XPwa4dpK+7y9t3KoL7T6qQZUtIqqN96YKx1Kcgr/bhD0K3U+OaLlrWVn6R23UrsEr8FARqKYVuE3Zg==",
    "Microsoft.AspNetCore.App.Runtime.win-x64": "GbC1BsO6jqVOj7LrAagG7x4Qi9rFt7ylOEHqXmZw+k9zeKMAlt8A4QBHt0C6XDg/cN93LBsL/WWTPEZAnEWJ7g==",
    "Microsoft.NETCore.App.Runtime.win-x64": "MEGAkRGHrAXYA2t/Lo0mDxH+/O5NLqgWTcVh70iOZJTpoOCL/jKfOgDqYoDi3W9WcnviL4DhMl+5lWC8cxttSw==",
    "Microsoft.WindowsDesktop.App.Runtime.win-x64": "vUp++3z72ONuBlzCplThiiZHdunYILGH37bfKjzzWG75nsllieq9m63DSGkf6budzAgOXQ9/+jWBeaS4qlUUEw==",
}


class DotnetToolchainPinTests(unittest.TestCase):
    def test_local_sdk_and_publisher_require_reviewed_patch_without_roll_forward(self):
        pin = json.loads((ROOT / "global.json").read_text(encoding="utf-8"))["sdk"]
        self.assertEqual(pin, {"version": SDK, "rollForward": "disable", "allowPrerelease": False})
        publisher = (ROOT / "scripts/build_settings.ps1").read_text(encoding="utf-8")
        self.assertRegex(publisher, r"\$sdkVersion\.Trim\(\) -ne '" + re.escape(SDK) + "'")
        self.assertIn("requires .NET SDK " + SDK, publisher)

    def test_all_three_ci_workflows_select_the_same_security_sdk(self):
        for name in ("tests.yml", "prerelease.yml", "release.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
                versions = re.findall(r'^\s*dotnet-version:\s*[\"\']([^\"\']+)[\"\']\s*$', workflow, re.MULTILINE)
                expected_jobs = {
                    "tests.yml": {"tests", "compatibility"},
                    "prerelease.yml": {"build"},
                    "release.yml": {"build", "test_build"},
                }[name]
                self.assertEqual(versions, [SDK] * len(expected_jobs))
                # Pin every actual SDK-consuming job, including the isolated
                # manual TEST build; another SDK in a different job must fail.
                body = workflow.split("\njobs:\n", 1)[1]
                sections = re.split(r"(?m)^  ([A-Za-z_][A-Za-z0-9_-]*):\n", body)
                sdk_jobs = {sections[index]: sections[index + 1]
                            for index in range(1, len(sections), 2)
                            if "dotnet-version:" in sections[index + 1]}
                self.assertEqual(set(sdk_jobs), expected_jobs)
                for job_name, job in sdk_jobs.items():
                    with self.subTest(job=job_name):
                        self.assertEqual(re.findall(
                            r'^\s*dotnet-version:\s*[\"\']([^\"\']+)[\"\']\s*$',
                            job, re.MULTILINE), [SDK])

    def test_required_ci_installs_exact_sdk_before_native_unit_guards(self):
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        required = workflow.split("  tests:\n", 1)[1].split("  compatibility:\n", 1)[0]
        compatibility = workflow.split("  compatibility:\n", 1)[1].split("  lint:\n", 1)[0]
        for job in (required, compatibility):
            self.assertLess(job.index("- name: Set up .NET"), job.index("- name: Unit tests"))
        self.assertIn("actions/setup-dotnet@26b0ec14cb23fa6904739307f278c14f94c95bf1", workflow)

    def test_runtime_targeting_pin_stays_scoped_and_shipping_qa_aot_policy_is_preserved(self):
        project = ET.parse(PROJECT / "HaloBattery.Settings.csproj").getroot()
        framework = project.find(".//FrameworkReference[@Update='Microsoft.NETCore.App']")
        self.assertIsNotNone(framework)
        self.assertEqual(framework.get("RuntimeFrameworkVersion"), RUNTIME)
        self.assertEqual(framework.get("TargetingPackVersion"), RUNTIME)
        self.assertIsNone(project.find(".//PropertyGroup/RuntimeFrameworkVersion"))
        aot_policy = [(item.get("Condition", ""), item.text)
                      for item in project.findall(".//PropertyGroup/PublishAot")]
        self.assertEqual(aot_policy, [(
            "'$(PublishAot)' == '' and '$(Configuration)' == 'Release' and "
            "'$(SelfContained)' == 'true' and '$(HaloBatteryVerification)' != 'true'", "true"),
            ("'$(PublishAot)' == ''", "false")])
        guard = project.find("Target[@Name='RequireSupportedAotMode']/Error")
        self.assertIsNotNone(guard)
        self.assertEqual(guard.get("Condition"),
            "'$(Configuration)' != 'Release' or '$(SelfContained)' != 'true' or '$(HaloBatteryVerification)' == 'true'")
        exclusion = project.find("ItemGroup[@Condition=\"'$(HaloBatteryVerification)' != 'true'\"]/Compile")
        self.assertIsNotNone(exclusion)
        self.assertEqual(exclusion.get("Remove"), "**/*Verification.cs;FixtureWindowCapture.cs")

    def test_both_deployment_locks_match_archive_versions_and_complete_hash_shapes(self):
        archives = json.loads((PROJECT / "packages.archives.lock.json").read_text(encoding="utf-8"))["dependencies"]
        for mode in ("true", "false"):
            lock = json.loads((PROJECT / ("packages." + mode + ".lock.json")).read_text(encoding="utf-8"))
            self.assertEqual(lock["version"], 1)
            for framework, packages in lock["dependencies"].items():
                for name, pin in packages.items():
                    with self.subTest(mode=mode, framework=framework, package=name):
                        self.assertEqual(pin["resolved"], archives[name]["resolved"])
                        self.assertEqual(len(base64.b64decode(pin["contentHash"], validate=True)), 64)
            illink = [packages["Microsoft.NET.ILLink.Tasks"] for packages in lock["dependencies"].values()
                      if "Microsoft.NET.ILLink.Tasks" in packages]
            self.assertEqual([pin["resolved"] for pin in illink], [RUNTIME] if mode == "true" else [])

    def test_runtime_download_archives_pin_the_reviewed_complete_signed_payloads(self):
        archives = json.loads((PROJECT / "packages.archives.lock.json").read_text(encoding="utf-8"))["dependencies"]
        for name, expected_hash in RUNTIME_ARCHIVES.items():
            with self.subTest(package=name):
                self.assertEqual(archives[name], {"resolved": RUNTIME, "archiveSha512": expected_hash})
                self.assertEqual(len(base64.b64decode(expected_hash, validate=True)), 64)

    def test_windows_sdk_and_winui_dependencies_are_not_upgraded_with_dotnet(self):
        project = ET.parse(PROJECT / "HaloBattery.Settings.csproj").getroot()
        self.assertEqual(project.findtext(".//WindowsSdkPackageVersion"), "10.0.26100.57")
        declared = {item.get("Include"): item.get("Version") for item in project.findall(".//PackageReference")}
        self.assertEqual(declared, {"Microsoft.WindowsAppSDK.WinUI": "[2.3.9]",
                                    "Microsoft.WindowsAppSDK.InteractiveExperiences": "[2.1.9]",
                                    "Microsoft.WindowsAppSDK.Runtime": "[2.5.1]"})


if __name__ == "__main__":
    unittest.main()
