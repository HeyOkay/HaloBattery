"""Execute the stable publisher shell offline, with a fake gh only."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
FAKE_GH_SOURCE = r'''
"""OFFLINE fixture only: never imports GitHub clients or accesses the network."""
import hashlib
import json
import os
from pathlib import Path
import sys

# Match Linux gh output exactly when this offline fixture runs on Windows.
sys.stdout.reconfigure(newline="\n")

args = sys.argv[1:]
state_path = Path(os.environ["FAKE_STATE"])
state = json.loads(state_path.read_text(encoding="utf-8"))
mode = os.environ.get("FAKE_MODE", "")
tag = os.environ["RELEASE_TAG"]
repo = os.environ["GITHUB_REPOSITORY"]
source = os.environ["GITHUB_SHA"]
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")


def stop(message):
    print("OFFLINE FAKE GH: " + message, file=sys.stderr)
    sys.exit(1)


def save():
    state_path.write_text(json.dumps(state), encoding="utf-8")


def option(name):
    return args[args.index(name) + 1]


if args[0] == "fake-attest":
    if mode == "attestation-fail": stop("injected attestation failure")
    if state["phase"] != "draft" or len(state["assets"]) != 3: stop("attestation before validated upload")
    subjects = {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in args[1:]}
    if set(subjects) != {os.environ["ZIP"], os.environ["SETUP"]}: stop("unexpected attestation subject")
    Path(os.environ["ATTESTATION_BUNDLE"]).write_text(json.dumps(subjects), encoding="utf-8")
    state["attested"] = True
    save()
elif args[:2] == ["release", "upload"]:
    if state["phase"] != "draft": stop("upload to a published release")
    expected = [tag, os.environ["ZIP"], os.environ["SETUP"], "release-assets/SHA256SUMS.txt", "--repo", repo]
    if args[2:] != expected: stop("unvalidated upload argument or overwrite option")
    for name in expected[1:4]:
        if Path(name).name in state["assets"]: stop("asset overwrite attempted")
        digest = "sha256:" + hashlib.sha256(Path(name).read_bytes()).hexdigest()
        state["assets"][Path(name).name] = {"digest": digest, "state": "uploaded"}
        if mode == "upload-fail":
            save()
            stop("injected partial upload failure")
    if mode == "remote-digest-mismatch": state["assets"][Path(expected[1]).name]["digest"] = "sha256:" + "0" * 64
    if mode == "remote-extra-asset": state["assets"]["unverified.exe"] = {"digest": "sha256:" + "0" * 64, "state": "uploaded"}
    save()
elif args[:2] == ["attestation", "verify"]:
    if not state.get("attested"): stop("no attestation exists")
    if option("--repo") != repo or option("--signer-workflow") != repo + "/" + os.environ["EXPECTED_SIGNER_WORKFLOW"]: stop("wrong signer identity policy")
    if option("--source-ref") != "refs/tags/" + tag or option("--source-digest") != source: stop("wrong source policy")
    bundle = json.loads(Path(option("--bundle")).read_text(encoding="utf-8"))
    if bundle.get(args[2]) != hashlib.sha256(Path(args[2]).read_bytes()).hexdigest(): stop("subject digest mismatch")
elif args[:2] == ["release", "verify"]:
    if state["phase"] != "published": stop("release not published")
    if mode == "immutability-disabled": stop("no GitHub immutable release attestation")
elif args[:2] == ["release", "verify-asset"]:
    if state["phase"] != "published": stop("release not published")
    digest = "sha256:" + hashlib.sha256(Path(args[3]).read_bytes()).hexdigest()
    if state["assets"].get(Path(args[3]).name, {}).get("digest") != digest: stop("release asset mismatch")
elif args[0] == "api":
    endpoint = next(arg for arg in args if arg.startswith("repos/"))
    query = option("--jq")
    method = option("--method") if "--method" in args else "GET"
    if endpoint == f"repos/{repo}/releases?per_page=100":
        if mode == "api-fail": stop("injected API failure")
        if "--paginate" not in args or tag not in query: stop("incomplete existing release lookup")
        if state["phase"] != "absent": print("123")
    elif endpoint == f"repos/{repo}/git/ref/tags/{tag}":
        print("refs/tags/" + tag)
    elif endpoint == f"repos/{repo}/commits/{tag}":
        print("0" * 40 if mode == "wrong-source" else source)
    elif endpoint == f"repos/{repo}/releases" and method == "POST":
        if state["phase"] != "absent": stop("duplicate release creation")
        for value in ("draft=true", "tag_name=" + tag, "target_commitish=" + source, "body=@release-assets/release_notes.md"):
            if value not in args: stop("unsafe release creation")
        state["phase"] = "draft"
        state["body"] = Path("release-assets/release_notes.md").read_text(encoding="utf-8")
        save()
        print("123")
    elif endpoint == f"repos/{repo}/releases/123" and method == "GET":
        print("123\t" + ("true" if state["phase"] == "draft" else "false") + "\t" + tag)
    elif endpoint == f"repos/{repo}/releases/123/assets?per_page=100":
        if "--paginate" not in args: stop("incomplete asset lookup")
        if query == ".[].id":
            if mode == "initial-assets-api-fail": stop("injected initial asset lookup failure")
            for index, _ in enumerate(state["assets"]): print(index + 1)
        elif query == ".[] | [.name,.digest,.state] | @tsv":
            for name, asset in state["assets"].items(): print(name + "\t" + asset["digest"] + "\t" + asset["state"])
        else: stop("unexpected asset projection")
    elif endpoint == f"repos/{repo}/releases/123" and method == "PATCH":
        if state["phase"] != "draft" or not state.get("attested"): stop("publish before draft attestation")
        if "draft=false" not in args: stop("unexpected release edit")
        state["phase"] = "published"
        save()
        print("false")
    else:
        stop("unhandled API call: " + repr(args))
else:
    stop("unhandled command: " + repr(args))
'''


def publisher(text):
    """Select one two-space-indented job, never the remainder of the file."""
    body = text.split("\njobs:\n", 1)[1]
    match = re.search(r"(?m)^  publish:\n", body)
    if match is None:
        raise ValueError("Stable publisher job is missing")
    tail = body[match.end():]
    boundary = re.search(r"(?m)^(?:  [A-Za-z_][A-Za-z0-9_-]*:|[^ #\s])", tail)
    return "  publish:\n" + (tail[:boundary.start()] if boundary else tail)


def step(name, text=TEXT):
    body = publisher(text).split("      - name: " + name + "\n", 1)[1]
    body = re.split(r"(?m)^      - ", body, maxsplit=1)[0]
    script = body.split("        run: |\n", 1)[1]
    # Literal run content ends when a nonempty line leaves its indentation.
    lines = []
    for line in script.splitlines():
        if line.strip() and not line.startswith("          "):
            break
        if line.strip():
            lines.append(line[10:])
    return "\n".join(lines) + "\n"


def bash_path():
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file(): return str(candidate)
    return shutil.which("bash")


class StablePublisherPolicyTests(unittest.TestCase):
    def test_later_jobs_cannot_enter_publisher_policy_or_executable_steps(self):
        # A following job intentionally contains commands forbidden to the
        # publisher and a duplicate step name. Neither belongs to its policy
        # or offline executable fixture, regardless of job ordering.
        stable_only = TEXT.split("\n  publish:\n", 1)[0] + "\n" + publisher(TEXT)
        appended = stable_only + """
  unrelated_job:
    runs-on: ubuntu-latest
    steps:
      - name: Publish attested draft
        run: |
          python unrelated.py
          exit 99
"""
        self.assertEqual(publisher(appended).rstrip(), publisher(stable_only).rstrip())
        self.assertNotIn("python unrelated.py", publisher(appended))
        for name in ("Validate release assets", "Attach to release", "Publish attested draft"):
            with self.subTest(step=name):
                self.assertEqual(step(name, appended), step(name, stable_only))
                self.assertEqual(step(name, TEXT), step(name, stable_only))

    def test_only_fresh_publisher_gets_attestation_permissions(self):
        publish = publisher(TEXT)
        # Signing jobs now follow the publisher in the document. Check every
        # other job, rather than only the old build-before-publish prefix.
        jobs = re.split(r"(?m)(?=^  [A-Za-z_][A-Za-z0-9_-]*:\n)", TEXT.split("\njobs:\n", 1)[1])
        for job in filter(str.strip, jobs):
            if job.startswith("  publish:\n"):
                continue
            with self.subTest(job=job.splitlines()[0].strip()):
                self.assertNotIn("id-token: write", job)
                self.assertNotIn("attestations: write", job)
                self.assertNotIn("contents: write", job)
                self.assertIn("      contents: read", job)
        self.assertIn("      id-token: write", publish)
        self.assertIn("      attestations: write", publish)
        self.assertNotIn("artifact-metadata: write", publish)
        self.assertNotRegex(publish, r"actions/checkout|pip install|dotnet |python |./tools/|./scripts/")
        gate = re.search(r"(?m)^    if: (.+)$", publish).group(1)
        self.assertEqual(gate, "always() && !cancelled() && github.event_name == 'push' && "
                         "github.ref_type == 'tag' && needs.signing_verify.result == 'success' && "
                         "needs.signing_verify.outputs.mode == 'Production'")
        self.assertIn("    needs: signing_verify\n", publish)
        self.assertIn("          artifact-ids: ${{ needs.signing_verify.outputs.release-artifact }}\n", publish)
        self.assertEqual(publish.count("actions/download-artifact@"), 1)
        self.assertIn("      cancel-in-progress: false", publish)

    def test_exact_pinned_action_attests_only_validated_zip_and_installer(self):
        action = TEXT.split("      - name: Attest validated release assets\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6", action)
        self.assertIn("          create-storage-record: false", action)
        subjects = action.split("          subject-path: |\n", 1)[1].split("          create-storage-record:", 1)[0]
        self.assertEqual(subjects.splitlines(), ["            ${{ steps.assets.outputs.zip }}", "            ${{ steps.assets.outputs.setup }}"])
        self.assertNotIn("--clobber", step("Attach to release"))


@unittest.skipUnless(bash_path(), "Bash required for offline publisher execution")
class StablePublisherExecutionTests(unittest.TestCase):
    def run_publisher(self, *, phase="absent", mode="", mutation=None, enabled="true", rerun=False,
                      workflow_text=TEXT, version="1.17.0", signer_workflow=".github/workflows/release.yml",
                      release_tag=None):
        with tempfile.TemporaryDirectory(prefix="halo stable publisher ") as temp:
            root = Path(temp)
            fake_gh = root / "fake_gh.py"
            fake_gh.write_text(FAKE_GH_SOURCE, encoding="utf-8")
            assets = root / "release-assets"
            assets.mkdir()
            payloads = {f"HaloBattery-{version}.zip": b"fixture ZIP bytes", f"HaloBattery-{version}-setup.exe": b"fixture installer bytes"}
            for name, data in payloads.items(): (assets / name).write_bytes(data)
            (assets / "release_notes.md").write_text("$(touch EXECUTED)\n", encoding="utf-8")
            (assets / "SHA256SUMS.txt").write_bytes("".join(hashlib.sha256(data).hexdigest() + " *" + name + "\r\n" for name, data in payloads.items()).encode())
            if mutation: mutation(assets)
            state = root / "state.json"
            initial = {"phase": phase, "assets": {}}
            if phase == "draft": initial["assets"]["previous.zip"] = {"digest": "sha256:" + "0" * 64, "state": "uploaded"}
            state.write_text(json.dumps(initial), encoding="utf-8")
            log = root / "gh.jsonl"
            env = dict(os.environ, FAKE_STATE=str(state), FAKE_LOG=str(log), FAKE_MODE=mode,
                       RELEASE_TAG=release_tag or "v" + version, GITHUB_SHA="1" * 40, GITHUB_REPOSITORY="HeyOkay/HaloBattery",
                       EXPECTED_SIGNER_WORKFLOW=signer_workflow,
                       RUNNER_TEMP=str(root), GITHUB_OUTPUT=str(root / "outputs"), IMMUTABILITY_ENABLED=enabled,
                       GH_TOKEN="offline-test-only", GITHUB_TOKEN="offline-test-only",
                       TEST_PYTHON=Path(sys.executable).as_posix(), FAKE_GH=fake_gh.as_posix(),
                       TEST_ROOT=root.as_posix(),
                       ATTESTATION_BUNDLE=str(root / "bundle.json"))
            env.pop("BASH_ENV", None)
            env.pop("ENV", None)
            # This wrapper replaces every gh call. No real gh executable is
            # invoked, and no real attestation action or workflow is run.
            wrapper = 'gh() { "$TEST_PYTHON" "$FAKE_GH" "$@"; }; export -f gh\n'
            load = '''
while IFS='=' read -r name value; do
  case "$name" in
    zip) export ZIP="$value";; setup) export SETUP="$value";;
    zip-sha256) export ZIP_SHA256="$value";; setup-sha256) export SETUP_SHA256="$value";;
    sums-sha256) export SUMS_SHA256="$value";; release-id) export RELEASE_ID="$value";;
  esac
done < "$GITHUB_OUTPUT"
'''
            # A subshell also isolates the validator's cd and EXIT trap, as
            # Actions does when starting a separate run step.
            script = wrapper + "(\n" + step("Validate release assets", workflow_text) + ")\n" + load + step("Attach to release", workflow_text) + load
            if rerun:
                script += step("Attach to release", workflow_text)
            else:
                script += 'gh fake-attest "$ZIP" "$SETUP"\n' + step("Publish attested draft", workflow_text)
            result = subprocess.run([bash_path(), "--login", "-e", "-o", "pipefail", "-c", script],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=30)
            final = json.loads(state.read_text(encoding="utf-8"))
            calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
            self.assertFalse((root / "EXECUTED").exists())
            self.assertFalse((assets / "EXECUTED").exists())
            return result, final, calls, initial

    def test_absent_release_is_drafted_uploaded_attested_and_published_in_order(self):
        result, state, calls, _ = self.run_publisher()
        self.assertEqual(result.returncode, 0, result.stderr + "\n" + json.dumps(calls))
        self.assertEqual(state["phase"], "published")
        self.assertEqual(set(state["assets"]), {"HaloBattery-1.17.0.zip", "HaloBattery-1.17.0-setup.exe", "SHA256SUMS.txt"})
        create = next(i for i, call in enumerate(calls) if "POST" in call)
        upload = next(i for i, call in enumerate(calls) if call[:2] == ["release", "upload"])
        attest = next(i for i, call in enumerate(calls) if call[0] == "fake-attest")
        publish = next(i for i, call in enumerate(calls) if "PATCH" in call)
        self.assertLess(create, upload)
        self.assertLess(upload, attest)
        self.assertLess(attest, publish)
        self.assertEqual(len([c for c in calls if c[:2] == ["attestation", "verify"]]), 2)
        self.assertEqual(len([c for c in calls if c[:2] == ["release", "verify-asset"]]), 3)

    def test_existing_published_release_and_draft_are_refused_without_mutation(self):
        for phase in ("published", "draft"):
            with self.subTest(phase=phase):
                result, state, calls, initial = self.run_publisher(phase=phase)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state, initial)
                self.assertEqual(len(calls), 1)

    def test_partial_draft_rerun_cannot_overwrite_any_asset(self):
        result, state, calls, _ = self.run_publisher(rerun=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["phase"], "draft")
        self.assertEqual(len([c for c in calls if c[:2] == ["release", "upload"]]), 1)
        self.assertFalse(any("PATCH" in c for c in calls))

    def test_failed_upload_or_attestation_never_publishes(self):
        for mode in ("upload-fail", "attestation-fail", "remote-digest-mismatch", "remote-extra-asset"):
            with self.subTest(mode=mode):
                result, state, calls, _ = self.run_publisher(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["phase"], "draft")
                self.assertEqual(len([c for c in calls if c[:2] == ["release", "upload"]]), 1)
                self.assertEqual(any(c[0] == "fake-attest" for c in calls), mode == "attestation-fail")
                self.assertFalse(any("PATCH" in call for call in calls))

    def test_missing_immutability_acknowledgement_never_calls_gh(self):
        result, state, calls, _ = self.run_publisher(enabled="")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["phase"], "absent")
        self.assertEqual(calls, [])

    def test_initial_asset_lookup_failure_never_uploads_or_publishes(self):
        result, state, calls, _ = self.run_publisher(mode="initial-assets-api-fail")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["phase"], "draft")
        self.assertEqual(state["assets"], {})
        self.assertFalse(any(call[:2] == ["release", "upload"] or "PATCH" in call for call in calls))

    def test_api_failure_and_wrong_tag_source_never_create_release(self):
        for mode in ("api-fail", "wrong-source"):
            with self.subTest(mode=mode):
                result, state, calls, _ = self.run_publisher(mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state["phase"], "absent")
                self.assertFalse(any("POST" in call for call in calls))

    def test_unverified_extra_or_corrupted_assets_never_reach_publisher(self):
        for mutation in (lambda assets: (assets / "extra.exe").write_bytes(b"not verified"),
                         lambda assets: (assets / "HaloBattery-1.17.0.zip").write_bytes(b"changed")):
            result, state, calls, _ = self.run_publisher(mutation=mutation)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(state["phase"], "absent")
            self.assertEqual(calls, [])

    def test_external_immutability_setting_is_required_even_with_acknowledgement(self):
        result, state, calls, _ = self.run_publisher(mode="immutability-disabled")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state["phase"], "published")
        self.assertEqual(calls[-1][:2], ["release", "verify"])


if __name__ == "__main__":
    unittest.main()
