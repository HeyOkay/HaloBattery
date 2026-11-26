"""Installer scope guardrails plus an opt-in compiled Pascal process fixture."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import verify_installer_safety as safety


class InstallerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.source = safety.SCRIPT.read_text(encoding="utf-8")
        self.contract = safety.source_contract(self.source)

    def test_production_source_preserves_untracked_root_data(self):
        self.assertEqual(self.contract["deletions"],
                         [("filesandordirs", r"{app}\_internal"), ("dirifempty", "{app}")])

    def test_only_main_and_packaged_settings_are_termination_targets(self):
        self.assertEqual(self.contract["templates"],
                         [r"{app}\{#AppExe}", r"{app}\_internal\settings-ui\HaloBattery.Settings.exe"])

    def test_native_contract_uses_pointer_width_handles_and_bounded_wait(self):
        self.assertEqual(self.contract["access"], 0x00101001)
        self.assertEqual(self.contract["budget_ms"], 5000)
        for signature in (
                "InstallerOpenProcess(Access: Cardinal; Inherit: BOOL; Id: Cardinal): THandle",
                "InstallerQueryImage(Process: THandle; Flags: Cardinal; Image: String;",
                "InstallerTerminateProcess(Process: THandle; ExitCode: Cardinal): BOOL",
                "InstallerWait(Process: THandle; Milliseconds: Cardinal): Cardinal",
                "InstallerCloseHandle(Process: THandle): BOOL"):
            self.assertIn(signature, self.source)
        self.assertIn("if not InstallerQueryImage(Process, 0, Image, Characters) then\n    Exit;", self.source)

    def test_cleanup_cannot_return_success_after_known_termination_failure(self):
        self.assertIn("Result := CloseApp();", self.source)
        self.assertIn("Result := ErrorText = '';", self.source)
        self.assertIn("if InstallerWait(Handles[I], Cardinal(Remaining)) <> 0 then\n          Result := ErrorText;", self.source)

    def test_broad_cleanup_image_kill_and_pointer_truncation_are_rejected(self):
        mutations = (
            self.source.replace('Type: dirifempty; Name: "{app}"', 'Type: filesandordirs; Name: "{app}"'),
            self.source + "\n; taskkill /f /im HaloBattery.exe\n",
            self.source.replace('function IsInstallProcess(Process: THandle)',
                                'function IsInstallProcess(Process: Cardinal)'),
            self.source.replace('Deadline := InstallerTicks() + 5000;', 'Deadline := InstallerTicks() + 60000;'),
            self.source.replace(r'{app}\_internal\settings-ui\HaloBattery.Settings.exe',
                                r'{app}\HaloBattery.Settings.exe'),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation[-80:]):
                with self.assertRaises(AssertionError):
                    safety.source_contract(mutation)

    @unittest.skipUnless(os.name == "nt" and os.environ.get("HALOBATTERY_ISCC"),
                         "set HALOBATTERY_ISCC to run the compiled Pascal fixture")
    def test_compiled_production_helpers_preserve_unrelated_processes_and_data(self):
        workspace = Path(os.environ.get("HALOBATTERY_INSTALLER_FIXTURE_ROOT", r"D:\Ai\Codex"))
        result = safety.run_fixture(workspace, Path(os.environ["HALOBATTERY_ISCC"]))
        self.assertTrue(result["passed"])
        self.assertIn("Production Pascal", result["execution"])
        self.assertEqual(result["targets_stopped"], 2)
        self.assertEqual(result["unrelated_processes_preserved"], 3)
        self.assertEqual(result["user_files_preserved"], 7)
        self.assertIn(result["pascal_handle_bits"], (32, 64))
        self.assertTrue(result["installer_appid_unchanged"])


if __name__ == "__main__":
    unittest.main()
