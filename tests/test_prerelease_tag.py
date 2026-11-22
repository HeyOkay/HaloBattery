"""Execute the build-specific publisher with an offline gh/state fake."""
from __future__ import annotations

import hashlib
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
VERSION = "1.17.0"
RUN_ID = "123456"
ROLLING = {"id": 10, "draft": False, "prerelease": True, "immutable": True,
           "assets": {"old.zip": {"digest": "sha256:" + "a" * 64, "state": "uploaded"}}}


def publisher():
    text = (ROOT / ".github/workflows/prerelease.yml").read_text(encoding="utf-8")
    step = text.split("      - name: Publish a build-specific pre-release\n", 1)[1]
    block = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return "\n".join(line[10:] for line in block.splitlines()) + "\n"


FAKE_GH = r'''
import hashlib
import json
import os
from pathlib import Path
import sys

sys.stdout.reconfigure(newline="\n")
args = sys.argv[1:]
path = Path("state.json")
state = json.loads(path.read_text(encoding="utf-8"))
state["calls"].append(args)
mode = state.get("mode", "")
repo = "repos/HeyOkay/HaloBattery/"
tag = "pre-" + os.environ["RELEASE_VERSION"] + "-" + os.environ["GITHUB_SHA"][:7] + "-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]

def save():
    path.write_text(json.dumps(state, ensure_ascii=True), encoding="utf-8")

def stop(message):
    save()
    print(message, file=sys.stderr)
    sys.exit(7)

def option(name):
    return args[args.index(name) + 1]

def fields():
    result = {}
    for index, value in enumerate(args[:-1]):
        if value in ("-f", "-F"):
            key, data = args[index + 1].split("=", 1)
            result[key] = (value, data)
    return result

def identity(release):
    return str(release["id"]) + "\t" + str(release["draft"]).lower() + "\t" + str(release["prerelease"]).lower() + "\t" + tag

def asset(filename):
    return {"digest": "sha256:" + hashlib.sha256(Path(filename).read_bytes()).hexdigest(), "state": "uploaded"}

save()
if args[0] == "api":
    endpoint = next(value for value in args if value.startswith(repo))
    route = endpoint[len(repo):].split("?", 1)[0]
    method = option("--method") if "--method" in args else "GET"
    data = fields()
    if route == "releases" and method == "GET":
        assert "--paginate" in args and tag in option("--jq"), args
        if mode == "release-list-error": stop("release API unavailable")
        if tag in state["releases"]: print(state["releases"][tag]["id"])
    elif route == "git/matching-refs/tags/" + tag and method == "GET":
        assert "--paginate" in args and "refs/tags/" + tag in option("--jq"), args
        if mode == "tag-list-error": stop("tag API unavailable")
        if tag in state["tags"]: print("refs/tags/" + tag)
    elif route == "git/refs" and method == "POST":
        assert data["ref"][1] == "refs/tags/" + tag and data["sha"][1] == os.environ["GITHUB_SHA"], args
        assert tag not in state["tags"], args
        state["tags"][tag] = "f" * 40 if mode == "wrong-source" else data["sha"][1]
        assert option("--jq") == ".ref", args
        print("refs/tags/" + tag)
    elif route == "commits/" + tag and method == "GET":
        assert option("--jq") == ".sha" and tag in state["tags"], args
        print(state["tags"][tag])
    elif route == "releases" and method == "POST":
        assert data["tag_name"][1] == tag and tag not in state["releases"], args
        assert data["draft"] == ("-F", "true") and data["prerelease"] == ("-F", "true"), args
        assert data["make_latest"] == ("-f", "false"), args
        assert data["body"] == ("-F", "@release-assets/release_notes.md"), args
        assert state["tags"][tag] == os.environ["GITHUB_SHA"], args
        release = {"id": 100 + len(state["releases"]), "draft": True, "prerelease": True,
                   "immutable": False, "assets": {}, "title": data["name"][1], "notes": Path("release-assets/release_notes.md").read_text(encoding="utf-8")}
        state["releases"][tag] = release
        assert option("--jq") == ".id", args
        print(release["id"])
    elif route.startswith("releases/"):
        release = state["releases"][tag]
        assert route in ("releases/" + str(release["id"]), "releases/" + str(release["id"]) + "/assets"), args
        if route.endswith("/assets"):
            assert method == "GET" and "--paginate" in args, args
            if mode == "asset-list-error": stop("asset API unavailable")
            if option("--jq") == ".[].id":
                for index, name in enumerate(release["assets"]): print(index + 1)
            else:
                assert option("--jq") == ".[] | [.name,.digest,.state] | @tsv", args
                remote = dict(release["assets"])
                if remote and mode == "bad-remote-digest": remote[os.environ["NEW_ZIP"]] = {"digest": "sha256:" + "0" * 64, "state": "uploaded"}
                if remote and mode == "extra-remote-asset": remote["unexpected.sh"] = {"digest": "sha256:" + "f" * 64, "state": "uploaded"}
                if remote and mode == "pending-remote-asset": remote[os.environ["NEW_ZIP"]] = {"digest": remote[os.environ["NEW_ZIP"]]["digest"], "state": "starter"}
                for name, item in remote.items(): print(name + "\t" + item["digest"] + "\t" + item["state"])
        elif method == "GET":
            assert option("--jq") == "[.id,.draft,.prerelease,.tag_name] | @tsv", args
            result = dict(release)
            if mode == "wrong-draft-identity": result["prerelease"] = False
            print(identity(result))
        elif method == "PATCH":
            assert data["draft"] == ("-F", "false") and data["prerelease"] == ("-F", "true"), args
            assert data["make_latest"] == ("-f", "false"), args
            assert release["draft"] and release["prerelease"], args
            assert set(release["assets"]) == {os.environ["NEW_ZIP"], os.environ["NEW_SETUP"], "SHA256SUMS.txt"}, args
            if mode == "publish-error": stop("publish failed")
            release["draft"], release["immutable"] = False, True
            assert option("--jq") == "[.id,.draft,.prerelease,.tag_name] | @tsv", args
            print(identity(release))
        else:
            raise AssertionError(args)
    else:
        raise AssertionError(args)
elif args[:2] == ["release", "upload"]:
    assert args[2] == tag and "--clobber" not in args, args
    assert option("--repo") == "HeyOkay/HaloBattery", args
    assert args[3:6] == ["release-assets/" + os.environ["NEW_ZIP"], "release-assets/" + os.environ["NEW_SETUP"], "release-assets/SHA256SUMS.txt"], args
    release = state["releases"][tag]
    assert release["draft"] and not release["assets"], args
    for filename in args[3:6]:
        release["assets"][Path(filename).name] = asset(filename)
        if mode == "partial-upload": stop("upload interrupted")
elif args[:2] in (["release", "verify"], ["release", "verify-asset"]):
    assert args[2] == tag and option("--repo") == "HeyOkay/HaloBattery", args
    release = state["releases"][tag]
    assert not release["draft"] and release["prerelease"] and release["immutable"], args
    if args[1] == "verify":
        attempts = sum(call[:2] == ["release", "verify"] for call in state["calls"])
        if mode == "delayed-attestation" and attempts < 3: stop("no attestations for tag")
        if mode == "invalid-attestation": stop("attestation verification failed")
    if args[1] == "verify-asset": assert release["assets"][Path(args[3]).name] == asset(args[3]), args
else:
    # Never accept edits/deletions of old releases or arbitrary GitHub commands.
    raise AssertionError(args)
save()
'''


@unittest.skipUnless(bash_path(), "Bash is required to execute the exact rolling publisher")
class PrereleaseTagTests(unittest.TestCase):
    def run_publisher(self, *, state=None, mode="", attempt="1", extra_env=None):
        initial = json.loads(json.dumps(state)) if state else {
            "tags": {"prerelease-build": "a" * 40}, "releases": {"prerelease-build": ROLLING}, "calls": []}
        initial["mode"] = mode
        with tempfile.TemporaryDirectory(prefix="halo prerelease publisher ") as directory:
            root = Path(directory)
            assets = root / "release-assets"
            assets.mkdir()
            payloads = {ZIP: b"new ZIP bytes", SETUP: b"new installer bytes"}
            for name, data in payloads.items(): (assets / name).write_bytes(data)
            sums = "".join(hashlib.sha256(data).hexdigest() + " *" + name + "\r\n" for name, data in payloads.items())
            (assets / "SHA256SUMS.txt").write_bytes(sums.encode("ascii"))
            (assets / "release_notes.md").write_text("Build notes $(touch EXECUTED) `touch EXECUTED`\nExact source: https://github.com/HeyOkay/HaloBattery/commit/" + SHA + "\n", encoding="utf-8")
            (root / "state.json").write_text(json.dumps(initial), encoding="utf-8")
            (root / "fake_gh.py").write_text(FAKE_GH, encoding="utf-8")
            (root / "publisher.sh").write_text(
                'gh() { "$FAKE_PYTHON" "$FAKE_GH" "$@"; }\n'
                'sleep() { [[ "$*" == "5" ]] || return 1; }\n' + publisher(), encoding="utf-8")
            environment = dict(os.environ, RELEASE_VERSION=VERSION, GITHUB_REPOSITORY="HeyOkay/HaloBattery",
                               GITHUB_SHA=SHA, GITHUB_RUN_ID=RUN_ID, GITHUB_RUN_ATTEMPT=attempt,
                               RUNNER_TEMP=root.as_posix(), GITHUB_OUTPUT=(root / "output").as_posix(),
                               GITHUB_STEP_SUMMARY=(root / "summary").as_posix(), STEP_SUMMARY=(root / "summary").as_posix(),
                               ZIP="release-assets/" + ZIP, SETUP="release-assets/" + SETUP,
                               ZIP_SHA256=hashlib.sha256(payloads[ZIP]).hexdigest(),
                               SETUP_SHA256=hashlib.sha256(payloads[SETUP]).hexdigest(),
                               SUMS_SHA256=hashlib.sha256(sums.encode("ascii")).hexdigest(),
                               NEW_ZIP=ZIP, NEW_SETUP=SETUP, FAKE_GH=(root / "fake_gh.py").as_posix(),
                               FAKE_PYTHON=Path(sys.executable).as_posix())
            environment.update(extra_env or {})
            environment.pop("BASH_ENV", None)
            environment.pop("ENV", None)
            result = subprocess.run([bash_path(), "--login", "-e", "-o", "pipefail", "publisher.sh"],
                                    cwd=root, env=environment, capture_output=True, text=True, timeout=30)
            final = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertFalse((root / "EXECUTED").exists())
            self.assertFalse((assets / "EXECUTED").exists())
            self.assertEqual(final["releases"]["prerelease-build"], initial["releases"]["prerelease-build"])
            self.assertEqual(final["tags"]["prerelease-build"], initial["tags"]["prerelease-build"])
            self.assertFalse(any(call[:2] == ["release", action] for call in final["calls"]
                                 for action in ("delete", "delete-asset", "edit")))
            # Optional local audit evidence; no output is left in the repository.
            if destination := os.environ.get("HALOBATTERY_TEST_ARTIFACTS"):
                output = Path(destination) / self._testMethodName
                output.mkdir(parents=True, exist_ok=True)
                for name in ("state.json", "publisher.sh", "fake_gh.py"):
                    shutil.copyfile(root / name, output / name)
                (output / "result.txt").write_text(
                    f"exit={result.returncode}\n{result.stdout}\n{result.stderr}", encoding="utf-8")
            return result, final

    @staticmethod
    def tag(attempt="1"):
        return f"pre-{VERSION}-{SHA[:7]}-{RUN_ID}-{attempt}"

    def assert_notes(self, final):
        release = final["releases"][self.tag()]
        self.assertIn(f"https://github.com/HeyOkay/HaloBattery/commit/{SHA}", release["notes"])
        self.assertIn("$(touch EXECUTED)", release["notes"])
        self.assertEqual(release["title"], "Pre-release 1.17.0 (0123456)")
        self.assertTrue(release["prerelease"])

    def test_existing_release_keeps_tag_and_replaces_only_known_old_assets(self):
        for draft in (False, True):
            with self.subTest(draft=draft):
                initial = {"tags": {"prerelease-build": "a" * 40, self.tag(): SHA},
                           "releases": {"prerelease-build": ROLLING, self.tag(): dict(ROLLING, id=11, draft=draft)}, "calls": []}
                result, final = self.run_publisher(state=initial)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(final["tags"], initial["tags"])
                self.assertEqual(final["releases"], initial["releases"])

    def test_first_release_creates_missing_tag_at_exact_build_commit(self):
        result, final = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(final["tags"][self.tag()], SHA)
        release = final["releases"][self.tag()]
        self.assertFalse(release["draft"])
        self.assertTrue(release["prerelease"] and release["immutable"])
        self.assertEqual(set(release["assets"]), {ZIP, SETUP, "SHA256SUMS.txt"})
        self.assertIn("$(touch EXECUTED)", release["notes"])
        self.assertIn("`touch EXECUTED`", release["notes"])
        self.assertEqual(sum(call[:2] == ["release", "verify-asset"] for call in final["calls"]), 3)
        self.assert_notes(final)

    def test_attestation_readiness_retries_without_republishing(self):
        result, final = self.run_publisher(mode="delayed-attestation")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(call[:2] == ["release", "verify"] for call in final["calls"]), 3)
        self.assertEqual(sum(call[:2] == ["release", "upload"] for call in final["calls"]), 1)
        self.assertEqual(sum("PATCH" in call for call in final["calls"]), 1)
        self.assertEqual(sum(call[:2] == ["release", "verify-asset"] for call in final["calls"]), 3)

    def test_persistent_attestation_failure_remains_a_failure(self):
        result, final = self.run_publisher(mode="invalid-attestation")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(call[:2] == ["release", "verify"] for call in final["calls"]), 12)
        self.assertFalse(any(call[:2] == ["release", "verify-asset"] for call in final["calls"]))
        self.assertEqual(sum(call[:2] == ["release", "upload"] for call in final["calls"]), 1)
        self.assertFalse(final["releases"][self.tag()]["draft"])

    def test_release_creation_preserves_a_preexisting_tag(self):
        for source in (SHA, "f" * 40):
            with self.subTest(source=source):
                initial = {"tags": {"prerelease-build": "a" * 40, self.tag(): source},
                           "releases": {"prerelease-build": ROLLING}, "calls": []}
                result, final = self.run_publisher(state=initial)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(final["tags"], initial["tags"])
                self.assertEqual(final["releases"], initial["releases"])

    def test_upload_failure_keeps_old_build_assets_and_metadata(self):
        for mode in ("partial-upload", "bad-remote-digest", "extra-remote-asset", "pending-remote-asset",
                     "wrong-draft-identity", "asset-list-error", "publish-error"):
            with self.subTest(mode=mode):
                result, final = self.run_publisher(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(final["releases"][self.tag()]["draft"])
                self.assertFalse(any(call[:2] == ["release", "verify"] for call in final["calls"]))

    def test_edit_failure_does_not_start_old_asset_cleanup(self):
        first, before = self.run_publisher(mode="partial-upload")
        self.assertNotEqual(first.returncode, 0)
        result, final = self.run_publisher(state=before, attempt="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(final["releases"][self.tag()], before["releases"][self.tag()])
        self.assertTrue(final["releases"][self.tag()]["draft"])
        self.assertFalse(final["releases"][self.tag("2")]["draft"])

    def test_unknown_and_hostile_asset_names_are_preserved_as_data(self):
        unknown = ["README.txt", "HaloBattery-1.16.0.zip", "HaloBattery-1.16.0-pre-abcdef0.zip.sig",
                   "HaloBattery-1.16.0-pre-ABCDEFG.zip", "HaloBattery-1.16.0-pre-abcdef01.zip",
                   "--help", "$(touch EXECUTED)", "`touch EXECUTED`", "../" + OLD[0],
                   OLD[0] + "\n", "\n" + OLD[0], OLD[0] + "\n" + OLD[1],
                   OLD[0] + '"', OLD[0] + "\t", "HaloBattery-1x16x0-pre-abcdef0.zip"]
        assets = {name: {"digest": "sha256:" + hashlib.sha256(name.encode()).hexdigest(), "state": "uploaded"}
                  for name in unknown + OLD}
        initial = {"tags": {"prerelease-build": "a" * 40},
                   "releases": {"prerelease-build": dict(ROLLING, assets=assets)}, "calls": []}
        result, final = self.run_publisher(state=initial)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(final["releases"]["prerelease-build"]["assets"], assets)
        self.assert_notes(final)

    def test_api_failures_are_not_treated_as_absence(self):
        for mode in ("release-list-error", "tag-list-error"):
            with self.subTest(mode=mode):
                result, final = self.run_publisher(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(set(final["releases"]), {"prerelease-build"})
                self.assertEqual(set(final["tags"]), {"prerelease-build"})

    def test_wrong_tag_target_prevents_draft_creation(self):
        result, final = self.run_publisher(mode="wrong-source")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.tag(), final["releases"])

    def test_rerun_gets_a_new_tag_and_preserves_the_previous_immutable_release(self):
        first, before = self.run_publisher()
        self.assertEqual(first.returncode, 0, first.stderr)
        result, final = self.run_publisher(state=before, attempt="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(final["releases"][self.tag()], before["releases"][self.tag()])
        self.assertEqual(final["tags"][self.tag("2")], SHA)
        self.assertFalse(final["releases"][self.tag("2")]["draft"])

    def test_untrusted_identity_and_paths_cannot_reach_github(self):
        for override in ({"RELEASE_VERSION": "../1.17.0"}, {"GITHUB_SHA": "f" * 39},
                         {"GITHUB_RUN_ID": "123;touch EXECUTED"}, {"GITHUB_RUN_ID": "0"},
                         {"GITHUB_RUN_ATTEMPT": "../2"}, {"GITHUB_RUN_ATTEMPT": "0"},
                         {"ZIP": "../../outside.zip"}, {"SETUP": "release-assets/other.exe"}):
            with self.subTest(override=override):
                result, final = self.run_publisher(extra_env=override)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(final["calls"], [])


if __name__ == "__main__":
    unittest.main()
