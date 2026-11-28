"""Public signing attribution must follow a verified production build."""

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from tools.update_download_badges import render_signing_badges, verified_production_run


REPOSITORY = "HeyOkay/HaloBattery"
PRODUCTION_JOBS = ["release_sign_payload", "release_sign_internal", "release_sign_installer", "signing_verify", "publish"]


def workflow_run(branch="v1.18.0"):
    return {
        "id": 42,
        "event": "push",
        "head_branch": branch,
        "head_repository": {"full_name": REPOSITORY},
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "html_url": "https://github.com/HeyOkay/HaloBattery/actions/runs/42",
    }


def job_results(names=PRODUCTION_JOBS):
    return {"jobs": [{"name": name, "conclusion": "success"} for name in names]}


class ReadmeSigningBadgeTests(unittest.TestCase):
    def test_all_readme_sources_use_gated_badges_including_light_fallback(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("README.md", "docs/readme/README.ru.md", "docs/readme/README.zh-CN.md"):
            with self.subTest(name=name):
                text = (root / name).read_text(encoding="utf-8")
                self.assertNotIn('="docs/readme/signpath', text)
                self.assertIn("docs/readme-download-stats/signpath", text)

    def test_verified_production_run_enables_attribution(self):
        for branch in ("v1.18.0", "v2.0.0"):
            with self.subTest(branch=branch), patch(
                "tools.update_download_badges.github_json",
                side_effect=[{"workflow_runs": [workflow_run(branch)]}, job_results()],
            ):
                self.assertEqual(verified_production_run(REPOSITORY, None)["run_id"], 42)

    def test_unsigned_and_test_builds_do_not_enable_attribution(self):
        for names in (
            ["build"],
            ["test_sign_payload", "test_sign_internal", "test_sign_installer", "signing_verify"],
            PRODUCTION_JOBS[:-1],
        ):
            with self.subTest(names=names), patch(
                "tools.update_download_badges.github_json",
                side_effect=[{"workflow_runs": [workflow_run()]}, job_results(names)],
            ):
                self.assertIsNone(verified_production_run(REPOSITORY, None))

    def test_failed_verification_does_not_enable_attribution(self):
        results = job_results()
        results["jobs"][-2]["conclusion"] = "failure"
        with patch("tools.update_download_badges.github_json", side_effect=[
            {"workflow_runs": [workflow_run()]}, results,
        ]):
            self.assertIsNone(verified_production_run(REPOSITORY, None))

    def test_production_dry_run_does_not_enable_attribution(self):
        run = workflow_run("main")
        run["event"] = "workflow_dispatch"
        with patch("tools.update_download_badges.github_json", return_value={"workflow_runs": [run]}) as api:
            self.assertIsNone(verified_production_run(REPOSITORY, None))
            self.assertEqual(api.call_count, 1)

    def test_failed_publication_does_not_enable_attribution(self):
        results = job_results()
        results["jobs"][-1]["conclusion"] = "failure"
        with patch("tools.update_download_badges.github_json", side_effect=[
            {"workflow_runs": [workflow_run()]}, results,
        ]):
            self.assertIsNone(verified_production_run(REPOSITORY, None))

    def test_untrusted_sources_and_incomplete_runs_are_ignored(self):
        variants = [workflow_run("docs/readme-refresh"), workflow_run(), workflow_run(), workflow_run()]
        variants[1]["head_repository"]["full_name"] = "someone/fork"
        variants[2]["conclusion"] = "failure"
        variants[3]["status"] = "in_progress"
        with patch("tools.update_download_badges.github_json", return_value={"workflow_runs": variants}) as api:
            self.assertIsNone(verified_production_run(REPOSITORY, None))
            self.assertEqual(api.call_count, 1)

    def test_release_history_is_paginated(self):
        with patch("tools.update_download_badges.github_json", side_effect=[
            {"workflow_runs": [workflow_run("docs/example")] * 100},
            {"workflow_runs": [workflow_run()]}, job_results(),
        ]) as api:
            self.assertEqual(verified_production_run(REPOSITORY, None)["run_id"], 42)
            self.assertIn("page=2", api.call_args_list[1].args[1])

    def test_all_badges_are_hidden_before_production_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            render_signing_badges(root, None)
            self.assertEqual(len(list(root.glob("*.svg"))), 8)
            for badge in root.glob("*.svg"):
                svg = ET.parse(badge).getroot()
                self.assertEqual(svg.attrib["width"], "1")
                self.assertEqual(svg.attrib["height"], "1")
                self.assertEqual(len(svg), 0)

    def test_original_localized_badges_return_after_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            render_signing_badges(root, {"run_id": 42})
            for badge in root.glob("*.svg"):
                original = Path(__file__).resolve().parents[1] / "docs/readme" / badge.name
                self.assertEqual(badge.read_text(encoding="utf-8"), original.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
