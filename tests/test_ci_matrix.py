"""The required packaging check must not turn failed prerequisites into skips."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe")


class CiMatrixTests(unittest.TestCase):
    def test_lint_and_compatibility_are_prerequisites_of_the_same_required_check(self):
        text = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        required = text.split("  tests:\n", 1)[1].split("  compatibility:\n", 1)[0]
        self.assertIn("needs: [compatibility, lint]", required)
        self.assertIn("if: always()", required)
        self.assertIn("./scripts/build_settings.ps1", required)
        self.assertIn('python: ["3.13.17", "3.14.10"]', text)
        self.assertIn("python -m ruff check halo_battery.pyw halo providers tools tests", text)
        self.assertIn("--require-hashes -r requirements-lint.txt", text)

    @unittest.skipUnless(os.name == "nt" and POWERSHELL, "Windows PowerShell required")
    def test_actual_required_check_guard_rejects_failed_skipped_and_cancelled_inputs(self):
        text = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
        step = text.split("      - name: Require compatibility and lint checks\n", 1)[1].split("      - name:", 1)[0]
        block = step.split("        run: |\n", 1)[1]
        script = "\n".join(line[10:] for line in block.splitlines())
        for compatibility, lint in (("success", "success"), ("failure", "success"),
                                     ("success", "failure"), ("skipped", "success"),
                                     ("success", "cancelled"), ("", "")):
            with self.subTest(compatibility=compatibility, lint=lint):
                result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                    env=dict(os.environ, COMPATIBILITY_RESULT=compatibility, LINT_RESULT=lint),
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode == 0, compatibility == lint == "success")
