# Code signing policy

SignPath Foundation approved HaloBattery on 18 November 2026 and created the
project organization with a valid self-signed test certificate. The owner accepted
the invitation and confirmed the CI notification email. The two explicit TEST
artifact configurations are valid, and GitHub's `signpath-test` environment is
restricted to reviewed `pre-release` runs with owner approval.

The local [test-signing pipeline](signing.md) is prepared. A test-only CI
credential, authorized source integration and the first successful hosted run are
still required before CI signing is active. The production certificate remains
pending. This setup does not identify any published binaries as signed.

## Responsibilities

- Source repository: [HeyOkay/HaloBattery](https://github.com/HeyOkay/HaloBattery).
- Code owners and reviewers: `@HeyOkay`, `@ahmedkhursheed23`, `@casparas123`, as
  recorded in [CODEOWNERS](../.github/CODEOWNERS).
- Signing approver: `@HeyOkay`. Each release signing request requires their
  explicit approval; a source change or successful build alone is not approval
  to sign a release.

## Intended signing scope

Only reviewed binaries built from this project's own source by its CI are
candidates for the project's signing policy: the Python application executable,
the Settings executable/assembly and the installer. Product names and versions
must agree with the reviewed application version.

Third-party libraries retain their upstream signatures and licenses. They are
not represented as HaloBattery's own code. Signing the application's executable
does not sign an unsigned dependency or guarantee antivirus/Smart App Control
acceptance of the complete package.

The [privacy policy](privacy.md) describes local data processing, GitHub update
and release-note requests, and user-initiated Iconify downloads.

The planned service is subject to the [Foundation conditions](https://signpath.org/terms)
and [application process](https://signpath.org/apply).
Team MFA, service approval, artifact restrictions and the signing integration
must be confirmed before this policy can describe an active signing service.
