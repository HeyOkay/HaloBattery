"""Release privilege boundaries and actual publisher validation, no network."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ("release.yml", "prerelease.yml")


def jobs(text):
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"(?m)^  ([a-z_]+):\n", body))
    return {match[1]: body[match.start():matches[index + 1].start() if index + 1 < len(matches) else None]
            for index, match in enumerate(matches)}


def validator(text):
    step = text.split("      - name: Validate release assets\n", 1)[1]
    block = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return "\n".join(line[10:] for line in block.splitlines() if line.strip()) + "\n"


def bash_path():
    if os.name == "nt":
        # Prefer Git Bash to the unrelated Windows System32 WSL launcher.
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash")


class ReleasePermissionsTests(unittest.TestCase):
    def test_dependencies_tests_and_packaging_have_no_release_write_token(self):
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                sections = jobs(text)
                self.assertEqual(set(sections), {"build", "publish", "signpath_test"} if name == "release.yml" else {"build", "publish"})
                self.assertRegex(text.split("\njobs:", 1)[0], r"permissions:\n  contents: read")
                self.assertRegex(sections["build"], r"permissions:\n      contents: read")
                self.assertNotIn("contents: write", sections["build"])
                self.assertNotRegex(sections["build"], r"(?m)^\s+(GH_TOKEN|GITHUB_TOKEN):")
                self.assertIn("persist-credentials: false", sections["build"])
                for command in ("pip install --require-hashes", "unittest discover", "tools/build_exe.py", "./scripts/get_inno.ps1", "& $iscc /Q"):
                    self.assertIn(command, sections["build"])
                self.assertEqual(text.count("contents: write"), 1)

    def test_publisher_is_a_fresh_data_only_job_after_successful_build(self):
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
                publish = jobs(text)["publish"]
                self.assertIn("needs: build", publish)
                self.assertIn("runs-on: ubuntu-latest", publish)
                self.assertIn("artifact-ids: ${{ needs.build.outputs.release-artifact }}", publish)
                self.assertNotRegex(publish, r"actions/(checkout|setup-python|setup-dotnet)|pip install|dotnet |python |git push|run-id:")
                self.assertNotIn("./tools/", publish)
                self.assertNotIn("./scripts/", publish)
                self.assertLess(publish.index("- name: Download publish assets"), publish.index("- name: Validate release assets"))
                last_step = "- name: Attach to release" if name == "release.yml" else "- name: Replace the rolling pre-release"
                self.assertLess(publish.index("- name: Validate release assets"), publish.index(last_step))
                self.assertIn("if-no-files-found: error", jobs(text)["build"])

    def test_manual_runs_cannot_publish(self):
        expected = {
            "release.yml": "github.event_name == 'push' && github.ref_type == 'tag'",
            "prerelease.yml": "github.event_name == 'push' && github.ref == 'refs/heads/pre-release'",
        }
        for name, condition in expected.items():
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            publish = jobs(text)["publish"]
            actual = re.search(r"(?m)^    if: (.+)$", publish)[1]
            self.assertEqual(actual, condition)
            self.assertIn("  workflow_dispatch:", text)

    def test_every_downloaded_action_is_pinned(self):
        for name in WORKFLOWS:
            text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
            for action in re.findall(r"(?m)^\s+uses: (\S+)", text):
                if action.startswith("./"):
                    self.assertEqual(name, "release.yml")
                    self.assertEqual(action, "./.github/workflows/signpath-test.yml")
                else:
                    self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


@unittest.skipUnless(bash_path(), "Bash is required to execute the exact publisher validator")
class ReleaseAssetValidationTests(unittest.TestCase):
    def run_validator(self, name, *, mutation=None, version="1.17.0"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "release-assets"
            assets.mkdir()
            short = "0123456"
            stem = "HaloBattery-1.17.0" + ("-pre-" + short if name == "prerelease.yml" else "")
            zip_name, setup_name = stem + ".zip", stem + "-setup.exe"
            payloads = {zip_name: b"test zip bytes", setup_name: b"test executable bytes"}
            for filename, payload in payloads.items():
                (assets / filename).write_bytes(payload)
            # Shell-looking release notes must remain text, never code.
            (assets / "release_notes.md").write_text("$(touch EXECUTED)\n", encoding="utf-8")
            sums = "".join(hashlib.sha256(payload).hexdigest() + " *" + filename + "\r\n"
                           for filename, payload in payloads.items())
            (assets / "SHA256SUMS.txt").write_bytes(sums.encode("ascii"))
            if mutation is not None:
                mutation(root, assets, zip_name)
            environment = dict(os.environ, RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root / "outputs"),
                               GITHUB_SHA="0123456789abcdef0123456789abcdef01234567",
                               RELEASE_TAG="v" + version, RELEASE_VERSION=version)
            environment.pop("BASH_ENV", None)
            environment.pop("ENV", None)
            # Login initializes Git Bash's coreutils PATH on Windows runners.
            result = subprocess.run([bash_path(), "--login", "-e", "-o", "pipefail", "-c", validator(text)],
                cwd=root, env=environment, capture_output=True, text=True, timeout=20)
            output = (root / "outputs").read_text(encoding="utf-8") if (root / "outputs").exists() else ""
            self.assertFalse((root / "EXECUTED").exists())
            self.assertFalse((assets / "EXECUTED").exists())
            return result, output

    def test_matching_windows_checksums_and_notes_publish_only_expected_paths(self):
        for name in WORKFLOWS:
            with self.subTest(workflow=name):
                result, output = self.run_validator(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("zip=release-assets/HaloBattery-1.17.0", output)
                self.assertIn("setup=release-assets/HaloBattery-1.17.0", output)

    def test_corrupt_missing_extra_and_path_manifest_are_refused(self):
        def tamper(root, assets, zip_name):
            (assets / zip_name).write_bytes(b"changed after hashing")
        def remove(root, assets, zip_name):
            (assets / zip_name).unlink()
        def extra(root, assets, zip_name):
            (assets / "extra.sh").write_text("touch EXECUTED", encoding="utf-8")
        def path_manifest(root, assets, zip_name):
            with (assets / "SHA256SUMS.txt").open("a", encoding="ascii") as stream:
                stream.write("0" * 64 + " *../../outside\n")
        for name in WORKFLOWS:
            for mutation in (tamper, remove, extra, path_manifest):
                with self.subTest(workflow=name, mutation=mutation.__name__):
                    result, output = self.run_validator(name, mutation=mutation)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")

    def test_untrusted_version_cannot_be_shell_code_or_a_path(self):
        for name in WORKFLOWS:
            for version in ("1.17.0; touch EXECUTED", "$(touch EXECUTED)", "../../outside"):
                with self.subTest(workflow=name, version=version):
                    result, output = self.run_validator(name, version=version)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(output, "")
