"""Execute both publishers against delayed/invalid attestations without network or sleep."""
from pathlib import Path
import unittest

import test_stable_release_safety as stable

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(stable.bash_path(), "Git Bash required for offline publisher execution")
class ReleaseVerificationRetryTests(unittest.TestCase):
    def run_publishers(self, mode):
        for workflow, version in (("release.yml", "1.17.0"), ("unsigned-release-1.18.0.yml", "1.18.0")):
            with self.subTest(workflow=workflow, mode=mode):
                text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
                result, state, calls, _ = stable.StablePublisherExecutionTests().run_publisher(
                    mode=mode, workflow_text=text, version=version, signer_workflow=".github/workflows/" + workflow)
                # Verification retries must never recreate, reupload or republish.
                self.assertEqual(sum("POST" in call for call in calls), 1)
                self.assertEqual(sum("PATCH" in call for call in calls), 1)
                self.assertEqual(sum(call[:2] == ["release", "upload"] for call in calls), 1)
                self.assertEqual(state["phase"], "published")
                yield result, state, calls

    def test_delayed_release_attestation_succeeds_without_republishing(self):
        for result, state, calls in self.run_publishers("delayed-attestation"):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state["release_verify_attempts"], 3)
            self.assertEqual(sum(call[0] == "fake-sleep" for call in calls), 2)
            self.assertEqual(len(state["asset_verify_attempts"]), 3)

    def test_each_delayed_asset_succeeds_independently(self):
        for result, state, calls in self.run_publishers("delayed-asset"):
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state["release_verify_attempts"], 1)
            self.assertEqual(list(state["asset_verify_attempts"].values()), [3, 3, 3])
            self.assertEqual(sum(call[0] == "fake-sleep" for call in calls), 6)

    def test_persistent_invalid_attestation_fails_after_bounded_retries(self):
        for result, state, calls in self.run_publishers("invalid-attestation"):
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state["release_verify_attempts"], 12)
            self.assertEqual(sum(call[0] == "fake-sleep" for call in calls), 11)
            self.assertNotIn("asset_verify_attempts", state)

    def test_persistent_invalid_asset_fails_and_does_not_verify_later_assets(self):
        for result, state, calls in self.run_publishers("invalid-asset"):
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(list(state["asset_verify_attempts"].values()), [12])
            self.assertEqual(sum(call[0] == "fake-sleep" for call in calls), 11)
