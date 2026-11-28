"""One-version owner-authorized publication, without signing or network in tests."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from test_release_permissions import jobs
import test_stable_release_safety as stable

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/unsigned-release-1.18.0.yml"
TEXT = (ROOT / WORKFLOW).read_text(encoding="utf-8")
PWSH = shutil.which("pwsh") or shutil.which("pwsh.exe")


class UnsignedReleasePolicyTests(unittest.TestCase):
    def test_only_the_exact_release_tag_can_run(self):
        trigger = TEXT.split("on:\n", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertEqual(trigger.strip(), "push:\n    tags: ['v1.18.0']")
        self.assertNotIn("workflow_dispatch", TEXT)
        self.assertNotIn("workflow_call", TEXT)
        for body in jobs(TEXT).values():
            gate = re.search(r"(?m)^    if: (.+)$", body)[1]
            self.assertIn("github.repository == 'HeyOkay/HaloBattery'", gate)
            self.assertIn("github.event_name == 'push'", gate)
            self.assertIn("github.ref == 'refs/tags/v1.18.0'", gate)
        original = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn('tags: ["v*", "!v1.18.0"]', original)
        self.assertIn("needs.signing_verify.outputs.mode == 'Production'", jobs(original)["publish"])

    def test_unsigned_publisher_is_isolated_and_attests_the_correct_workflow(self):
        sections = jobs(TEXT)
        self.assertEqual(set(sections), {"build", "publish"})
        self.assertNotIn("secrets.", TEXT)
        self.assertNotIn("SignPath/signpath", TEXT)
        self.assertNotIn("contents: write", sections["build"])
        self.assertIn("contents: read", sections["build"])
        self.assertIn("Get-AuthenticodeSignature", sections["build"])
        self.assertIn("$signature.Status -ne 'NotSigned'", sections["build"])
        self.assertIn("_internal/settings-ui/HaloBattery.Settings.exe", sections["build"])
        self.assertIn('git merge-base --is-ancestor "$GITHUB_SHA" origin/main', sections["build"])
        publish = sections["publish"]
        self.assertIn("needs.build.result == 'success'", publish)
        self.assertNotRegex(publish, r"actions/checkout|pip install|dotnet |python |\./tools/|\./scripts/")
        self.assertIn("--signer-workflow \"$GITHUB_REPOSITORY/" + WORKFLOW + '\"', publish)
        self.assertNotIn("--clobber", publish)
        self.assertIn("gh release verify-asset", publish)
        for action in re.findall(r"(?m)^\s+uses: (\S+)", TEXT):
            self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")

    @unittest.skipUnless(PWSH, "PowerShell required for the exact version/ref gate")
    def test_version_repository_event_and_ref_gate_executes_offline(self):
        body = jobs(TEXT)["build"].split("      - name: Require exactly the approved unsigned version\n", 1)[1]
        script = body.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
        script = "\n".join(line[10:] for line in script.splitlines() if line.strip())
        with tempfile.TemporaryDirectory(prefix="halo unsigned version gate ") as directory:
            root = Path(directory)
            (root / "gate.ps1").write_text(script, encoding="utf-8")
            base_env = dict(os.environ, GITHUB_REPOSITORY="HeyOkay/HaloBattery", GITHUB_EVENT_NAME="push",
                            GITHUB_REF="refs/tags/v1.18.0", PSExecutionPolicyPreference="Bypass")
            variants = [("1.18.0", {}, True), ("1.18.1", {}, False),
                        ("1.18.0", {"GITHUB_REF": "refs/heads/main"}, False),
                        ("1.18.0", {"GITHUB_EVENT_NAME": "workflow_dispatch"}, False),
                        ("1.18.0", {"GITHUB_REPOSITORY": "someone/fork"}, False)]
            for version, changes, allowed in variants:
                with self.subTest(version=version, changes=changes):
                    (root / "halo_battery.pyw").write_text(f'VERSION = "{version}"\n', encoding="utf-8")
                    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(root / "gate.ps1")],
                                            cwd=root, env=base_env | changes, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode == 0, allowed, result.stderr)


@unittest.skipUnless(stable.bash_path(), "Git Bash required for offline publisher execution")
class UnsignedPublisherExecutionTests(unittest.TestCase):
    def run_publisher(self, **kwargs):
        runner = stable.StablePublisherExecutionTests()
        return runner.run_publisher(workflow_text=TEXT, version="1.18.0", signer_workflow=WORKFLOW, **kwargs)

    def test_success_publishes_only_the_118_assets_with_matching_provenance(self):
        result, state, calls, _ = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state["phase"], "published")
        self.assertEqual(set(state["assets"]), {"HaloBattery-1.18.0.zip", "HaloBattery-1.18.0-setup.exe", "SHA256SUMS.txt"})
        self.assertEqual(sum(call[:2] == ["attestation", "verify"] for call in calls), 2)
        self.assertEqual(sum(call[:2] == ["release", "verify-asset"] for call in calls), 3)

    def test_another_tag_or_missing_ack_never_creates_a_draft(self):
        for values in ({"release_tag": "v1.18.1"}, {"release_tag": "v1.18.0-preview"}, {"enabled": ""}):
            with self.subTest(values=values):
                result, state, calls, _ = self.run_publisher(**values)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["phase"], "absent")
                self.assertEqual(calls, [])

    def test_old_release_or_draft_and_partial_rerun_are_never_overwritten(self):
        for phase in ("published", "draft"):
            result, state, _, initial = self.run_publisher(phase=phase)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state, initial)
        result, state, calls, _ = self.run_publisher(rerun=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["phase"], "draft")
        self.assertEqual(sum(call[:2] == ["release", "upload"] for call in calls), 1)

    def test_bad_source_upload_or_attestation_blocks_publication(self):
        for mode in ("wrong-source", "upload-fail", "attestation-fail", "remote-digest-mismatch", "remote-extra-asset"):
            with self.subTest(mode=mode):
                result, state, calls, _ = self.run_publisher(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(state["phase"], "published")
                self.assertFalse(any("PATCH" in call for call in calls))

    def test_corrupt_or_extra_files_never_reach_gh(self):
        for mutation in (lambda assets: (assets / "HaloBattery-1.18.0.zip").write_bytes(b"changed"),
                         lambda assets: (assets / "extra.exe").write_bytes(b"not reviewed")):
            result, state, calls, _ = self.run_publisher(mutation=mutation)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state["phase"], "absent")
            self.assertEqual(calls, [])
