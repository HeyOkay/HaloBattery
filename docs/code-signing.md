# Code signing policy

The production signing pipeline verifies Authenticode signatures and the
reviewed source of each build. Downloaded files should be checked individually
for a valid signature.

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

<a href="https://signpath.org/"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/HeyOkay/HaloBattery/docs/readme-download-stats/signpath-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/HeyOkay/HaloBattery/docs/readme-download-stats/signpath.svg">
  <img src="https://raw.githubusercontent.com/HeyOkay/HaloBattery/docs/readme-download-stats/signpath.svg" alt="Code signing">
</picture></a>
