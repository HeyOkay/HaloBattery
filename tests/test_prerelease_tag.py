"""Execute the rolling publisher with an offline gh/state fake, never GitHub."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_release_permissions import bash_path

ROOT = Path(__file__).resolve().parents[1]
SHA = "0123456789abcdef0123456789abcdef01234567"
ZIP = "HaloBattery-1.17.0-pre-0123456.zip"
SETUP = "HaloBattery-1.17.0-pre-0123456-setup.exe"
OLD = ["HaloBattery-1.16.0-pre-abcdef0.zip", "HaloBattery-1.16.0-pre-abcdef0-setup.exe"]


def publisher():
    text = (ROOT / ".github/workflows/prerelease.yml").read_text(encoding="utf-8")
    step = text.split("      - name: Replace the rolling pre-release\n", 1)[1]
    block = step.split("        run: |\n", 1)[1]
    return "\n".join(line[10:] for line in block.splitlines()) + "\n"


FAKE_GH = r'''
import json
import os
from pathlib import Path
import sys

sys.stdout.reconfigure(newline="\n")  # Match gh on the Ubuntu publisher runner.
path = Path("state.json")
state = json.loads(path.read_text(encoding="utf-8"))
args = sys.argv[1:]
state["calls"].append(args)
def save():
    path.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")
def option(flag):
    return args[args.index(flag) + 1]
def metadata():
    state["notes"] = Path(option("--notes-file")).read_text(encoding="utf-8")
    state["title"] = option("--title")
    state["prerelease"] = "--prerelease" in args
def upload():
    for filename in args[3:6]:
        data = Path(filename).read_text(encoding="utf-8")
        state["assets"][Path(filename).name] = data

# Unexpected commands, including tag mutations, fail instead of reaching real gh.
assert args[:1] == ["release"], args
assert args[2] == "prerelease-build", args
assert option("--repo") == "HeyOkay/HaloBattery", args
command = args[1]
if command == "view":
    save()
    if not state["release"]:
        sys.exit(1)
    assert option("--json") == "assets", args
    assert option("--jq") == ".assets[].name | @json", args
    for name in state["assets"]:
        print(json.dumps(name, ensure_ascii=True))
elif command == "upload":
    assert state["release"] and "--clobber" in args, args
    assert args[3:6] == ["release-assets/" + os.environ["NEW_ZIP"],
                         "release-assets/" + os.environ["NEW_SETUP"],
                         "release-assets/SHA256SUMS.txt"], args
    if state.get("upload_failure"):
        # Even a partially successful upload must never start stale-asset cleanup.
        state["assets"][os.environ["NEW_ZIP"]] = "partial new upload"
        save()
        sys.exit(9)
    upload()
elif command == "edit":
    assert state["release"] and "--target" not in args and "--tag" not in args, args
    if state.get("edit_failure"):
        save()
        sys.exit(8)
    metadata()
elif command == "delete-asset":
    assert state["release"] and "--yes" in args, args
    del state["assets"][args[3]]
elif command == "create":
    assert not state["release"], args
    state["release"] = True
    state["tag"] = state["tag"] or option("--target")
    assert option("--target") == os.environ["GITHUB_SHA"], args
    upload()
    metadata()
else:
    raise AssertionError(args)
save()
'''


@unittest.skipUnless(bash_path(), "Bash is required to execute the exact rolling publisher")
class PrereleaseTagTests(unittest.TestCase):
    def run_publisher(self, *, release=True, tag="old-anchor", names=(), **failures):
        initial = {"release": release, "tag": tag, "assets": {name: "old " + name for name in names},
                   "calls": [], "notes": "old notes", **failures}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = root / "release-assets"
            assets.mkdir()
            for name in (ZIP, SETUP, "SHA256SUMS.txt"):
                (assets / name).write_text("new " + name, encoding="utf-8")
            (assets / "release_notes.md").write_text("Build notes $(touch EXECUTED)\n", encoding="utf-8")
            (root / "state.json").write_text(json.dumps(initial), encoding="utf-8")
            (root / "fake_gh.py").write_text(FAKE_GH, encoding="utf-8")
            script = 'gh() { "$FAKE_PYTHON" "$FAKE_GH" "$@"; }\n' + publisher()
            (root / "publisher.sh").write_text(script, encoding="utf-8")
            environment = dict(os.environ, TAG="prerelease-build", GITHUB_REPOSITORY="HeyOkay/HaloBattery",
                               GITHUB_SHA=SHA, SHORT=SHA[:7], RUNNER_TEMP=root.as_posix(),
                               ZIP="release-assets/" + ZIP, SETUP="release-assets/" + SETUP,
                               NEW_ZIP=ZIP, NEW_SETUP=SETUP, FAKE_GH=(root / "fake_gh.py").as_posix(),
                               FAKE_PYTHON=Path(sys.executable).as_posix())
            environment.pop("BASH_ENV", None)
            environment.pop("ENV", None)
            result = subprocess.run([bash_path(), "--login", "-e", "-o", "pipefail", "publisher.sh"],
                                    cwd=root, env=environment, capture_output=True, text=True, timeout=30)
            final = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertFalse((root / "EXECUTED").exists())
            self.assertFalse((assets / "EXECUTED").exists())
            # Optional local audit evidence; no output is left in the repository.
            if destination := os.environ.get("HALOBATTERY_TEST_ARTIFACTS"):
                output = Path(destination) / self._testMethodName
                output.mkdir(parents=True, exist_ok=True)
                for name in ("state.json", "publisher.sh", "fake_gh.py"):
                    shutil.copyfile(root / name, output / name)
                (output / "result.txt").write_text(
                    f"exit={result.returncode}\n{result.stdout}\n{result.stderr}", encoding="utf-8")
            return result, initial, final

    def assert_notes(self, final):
        self.assertIn("fixed source-archive anchor", final["notes"])
        self.assertIn("may differ from these build assets", final["notes"])
        self.assertIn(f"https://github.com/HeyOkay/HaloBattery/commit/{SHA}", final["notes"])
        self.assertIn("$(touch EXECUTED)", final["notes"])
        self.assertEqual(final["title"], "Pre-release build (0123456)")
        self.assertTrue(final["prerelease"])

    def test_existing_release_keeps_tag_and_replaces_only_known_old_assets(self):
        result, _, final = self.run_publisher(names=OLD + ["SHA256SUMS.txt", "README.txt", ZIP, SETUP])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(final["tag"], "old-anchor")
        self.assertEqual(set(final["assets"]), {ZIP, SETUP, "SHA256SUMS.txt", "README.txt"})
        self.assertEqual(final["assets"][ZIP], "new " + ZIP)
        self.assertEqual([call[1] for call in final["calls"]],
                         ["view", "upload", "edit", "delete-asset", "delete-asset"])
        self.assert_notes(final)

    def test_first_release_creates_missing_tag_at_exact_build_commit(self):
        result, _, final = self.run_publisher(release=False, tag=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(final["tag"], SHA)
        self.assertEqual([call[1] for call in final["calls"]], ["view", "create"])
        self.assertEqual(set(final["assets"]), {ZIP, SETUP, "SHA256SUMS.txt"})
        self.assert_notes(final)

    def test_release_creation_preserves_a_preexisting_tag(self):
        result, _, final = self.run_publisher(release=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(final["tag"], "old-anchor")
        self.assert_notes(final)

    def test_upload_failure_keeps_old_build_assets_and_metadata(self):
        result, initial, final = self.run_publisher(names=OLD + ["SHA256SUMS.txt"], upload_failure=True)
        self.assertNotEqual(result.returncode, 0)
        for name in OLD:
            self.assertEqual(final["assets"][name], initial["assets"][name])
        self.assertEqual(final["tag"], "old-anchor")
        self.assertEqual(final["notes"], "old notes")
        self.assertEqual([call[1] for call in final["calls"]], ["view", "upload"])

    def test_edit_failure_does_not_start_old_asset_cleanup(self):
        result, initial, final = self.run_publisher(names=OLD, edit_failure=True)
        self.assertNotEqual(result.returncode, 0)
        for name in OLD:
            self.assertEqual(final["assets"][name], initial["assets"][name])
        self.assertEqual([call[1] for call in final["calls"]], ["view", "upload", "edit"])

    def test_unknown_and_hostile_asset_names_are_preserved_as_data(self):
        unknown = ["README.txt", "HaloBattery-1.16.0.zip", "HaloBattery-1.16.0-pre-abcdef0.zip.sig",
                   "HaloBattery-1.16.0-pre-ABCDEFG.zip", "HaloBattery-1.16.0-pre-abcdef01.zip",
                   "--help", "$(touch EXECUTED)", "`touch EXECUTED`", "../" + OLD[0],
                   OLD[0] + "\n", "\n" + OLD[0], OLD[0] + "\n" + OLD[1],
                   OLD[0] + '"', OLD[0] + "\t", "HaloBattery-1x16x0-pre-abcdef0.zip"]
        result, initial, final = self.run_publisher(names=unknown + OLD)
        self.assertEqual(result.returncode, 0, result.stderr)
        for name in unknown:
            self.assertEqual(final["assets"][name], initial["assets"][name])
        self.assertEqual([call[3] for call in final["calls"] if call[1] == "delete-asset"], OLD)
        self.assertEqual(final["tag"], "old-anchor")


if __name__ == "__main__":
    unittest.main()
