# Test signing with SignPath

Test signing is an explicit mode of the existing `release.yml` manual workflow.
Its build, packaging, signing and verification jobs live in the same workflow.
Normal manual builds, stable tag builds and per-build pre-releases retain their
existing behavior.

The job chain is:

1. Build and test the unsigned application without a signing environment or key.
2. On a fresh runner, sign only the two application executables.
3. Without a key, verify the returned signatures and unchanged PE content,
   preserve every dependency, then package the application and compile Inno Setup.
4. On another fresh signing runner, sign the outer installer.
5. Without a key, compare the signed installer with the unsigned original,
   calculate final checksums and upload the TEST artifacts.

Signing jobs run no project checkout, build scripts, tests or installer compiler.
All jobs are GitHub-hosted with read-only repository permissions. Signing inputs
come from exact artifact IDs in this same run, never a supplied external run ID.
Artifact names include the attempt number so reruns cannot overwrite an earlier
attempt. The workflow has no publisher, release permissions or tag operations.

## Account configuration

The owner confirmed the notification email for `CI builds`. The existing SignPath
organization is `HaloBattery [OSS]`, ID
`c54d1cb1-9c0a-478b-8eb8-036eb7084713`; project slug is `HaloBattery`.
The workflow pins policy `test-signing` and the self-signed test certificate
thumbprint `17F9F411C424674855A077F9A120A7A434990AF1`. These are public identifiers,
not credentials. Certificate replacement requires a reviewed pin update.

The two configurations saved in SignPath are:

| Slug | ZIP contents selected for signing |
| --- | --- |
| `payload-test` | `HaloBattery.exe`, `_internal/settings-ui/HaloBattery.Settings.exe` |
| `installer-test` | `HaloBattery-test-setup.exe` |

Their reviewed XML is in `.github/signpath/`. The default single-PE configuration
is not used by this workflow. No dependency DLL is selected for signing.

## GitHub preparation

Before enabling a run, create an environment named `signpath-test` with these
controls:

- Allow deployments only from the branch `pre-release`.
- Require a maintainer review; the owner may approve their own TEST run.
- Disable administrator bypass of the environment protection rules.
- Store `SIGNPATH_TEST_API_TOKEN` as an **environment** secret.

Use a SignPath CI user with Submitter permission on `test-signing` only. The
existing `CI builds` user also has Submitter on `release-signing`; a secret name
containing TEST does not narrow those account permissions. Do not give that
broader token to this test workflow. Create a separate test CI user or explicitly
remove its production role before generating/storing the test credential.
Enter the credential directly into GitHub; never put it in source, logs or chat.

The token must exist **only** as an environment secret: do not create a repository
or organization secret with the same name. Only `test_sign_payload` and
`test_sign_installer` select `environment: signpath-test` and reference it, after
the environment approvals. Build, packaging and verification jobs have no signing
environment, secret mapping or signing credential. There is no reusable-workflow
secret declaration, forwarding or inheritance. A missing environment value fails
before any signing request.

Keep these jobs in the ordinary manual workflow: GitHub
[documents secret forwarding for reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows#using-inputs-and-secrets-in-a-reusable-workflow).
Removing a reusable caller mapping alone is not a reliable credential boundary.
The ordinary jobs use the direct
[environment-secret boundary](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

Before enabling a run, verify secret **names** in the environment, repository and
any applicable organization settings, plus the environment reviewers, branch
restriction and administrator-bypass setting. Do not read or print secret values.

Keep repository variable `SIGNPATH_TEST_ENABLED` absent or `false` until the
source and environment are reviewed. Setting it to `true` enables the explicit
manual TEST mode; it does not schedule or start a run.

## First authorized run

The new source must first be published through an approved PR into `pre-release`.
Local changes alone cannot run on GitHub Actions. No workflow dispatch is part of
local preparation.

`release.yml` already exists on the default branch, so it provides the registered
manual entry point. After the approved integration, invoke its selected branch
version with explicit input:

```powershell
gh workflow run release.yml --repo HeyOkay/HaloBattery --ref pre-release -f signpath_test=true
```

Use the explicit CLI/API input because the Actions page on `main` may still show
the older input form. All TEST jobs come from the same selected `release.yml`
commit. This route follows GitHub's documented selected-ref behavior; an
end-to-end run still needs validation.

Review the two signing jobs when their environment approvals appear. Download
`HaloBattery-SELF-SIGNED-TEST-<run-id>-<attempt>` only after the final verification
job succeeds. It contains the application ZIP, setup, `SHA256SUMS.txt`, a signing
report with request/artifact IDs and a TEST notice. Check both signing requests
used the expected TEST policy/certificate and exact input layouts.

## Limits before production

The certificate is self-signed. The verifier requires the pinned embedded signer
and accepts `Valid` or `NotTrusted`; hash mismatch, missing/catalog signatures,
changed PE contents or unexpected returned files fail. It does not install the
test certificate into Windows trust or claim Smart App Control compatibility.

This test signs the application PEs and outer setup only. The generated Inno
uninstaller and temporary setup copies still need the separately reviewed
signed-uninstaller pipeline. Redistributed DLLs, including libusb, are a separate
publisher issue. The production certificate is still pending and no TEST artifact
may feed a stable or pre-release publisher.

References: [SignPath GitHub integration](https://docs.signpath.io/trusted-build-systems/github),
[GitHub reusable workflows](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows),
[GitHub CLI selected-ref dispatch](https://cli.github.com/manual/gh_workflow_run).
