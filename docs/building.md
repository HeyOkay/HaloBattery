# Building HaloBattery

Run `scripts/build_exe.bat` from the repository root on Windows. Install .NET SDK
10.0.404 first; `global.json` disables SDK roll-forward. Local packaging uses the
Python interpreter you invoke and records its version. CI packages with exactly
Python 3.12.10, the available Windows binary release for the existing 3.12 build
line; this is a toolchain pin, not a claim that it contains every later source-only
Python security fix. A Python runtime upgrade needs its own package validation.

The local builder installs `requirements-build.txt` into a fresh build-only
environment with mandatory hashes and binary wheels. Existing global packages
are not accepted as already-verified build dependencies. The environment is not
included in the application. The optional `--wheelhouse` argument to
`tools/build_exe.py` uses a supplied offline wheel directory with the same hash
requirements. Build and test tools are not runtime dependencies of HaloBattery.

## Native Settings

`scripts/build_settings.ps1` defaults to `-PublishMode Auto`: self-contained
Release without the verification harness uses NativeAOT. Debug, explicit
Release verification (`-EnableVerification`) and framework-dependent
(`-SelfContained $false`) builds use the managed JIT runtime. `-PublishMode Jit`
is an explicit development fallback; `-PublishMode Aot` rejects those unsupported
combinations. Direct project builds evaluate the same mode policy.

The publisher restores with committed locks, checks the complete signed NuGet
archives, then publishes without a second restore. It validates a fresh staging
tree before replacing the previous output. A failed restore, toolchain check,
compile or promotion preserves the old publication. Keep the complete published
folder: do not manually remove DLLs or language/image/font resources to reduce
size. Normal Release excludes the fixture harness; Debug retains it by default.

The project pins .NET runtime/targeting pack 10.0.15, SDK 10.0.404 and Windows SDK
package 10.0.26100.57. AOT uses `packages.aot.true.lock.json` plus
`packages.aot.archives.lock.json`. Managed self-contained and framework-dependent
builds use `packages.true.lock.json` / `packages.false.lock.json` and their
existing `packages.archives.lock.json`. The archive manifests pin SHA-512 of the
complete signed `.nupkg`, including SDK PackageDownload inputs omitted by the
NuGet dependency graph. These raw hashes differ from NuGet's contentHash.

Native compilation acquires a build-only MSVC 14.44 Hostx64/Targetx64 toolchain
and Windows SDK C++ x64 libraries from committed raw SHA-256/size pins in
`scripts/settings-aot-toolchain.lock.json`. The helper validates safe bounded
archive paths and Microsoft Authenticode for executable inputs before use. It
extracts data into an owned cache; it does not install Visual Studio, run an
installer, modify the registry or persist PATH/LIB/INCLUDE. The native tools,
SDK and caches are never bundled with HaloBattery. Normal Release uses generated
JSON metadata, explicit dynamic-XAML roots and the derived window-presenter
factory. Keep the `OverlappedPresenter` entry in `DynamicXaml.Descriptors.xml`: it
is needed when a native API returns the base presenter type. Startup validates
the custom frame rather than silently skipping it. JIT QA retains reflection.

The default native-tool cache is `.build/settings-aot-toolchain`.
`-ToolchainCache` selects a dedicated local build directory, and
`-OfflineToolchain` refuses a missing archive. CI uses a cache under RUNNER_TEMP.
The default NuGet feed is repository `NuGet.Config`; an explicit `-NuGetConfig`
and `-PackagesDirectory` can select an offline feed/cache. For example:

```powershell
./scripts/build_settings.ps1 -ToolchainCache D:/build-cache/halo-settings-aot
./scripts/build_settings.ps1 -PublishMode Jit
./scripts/build_settings.ps1 -EnableVerification
```

Ordinary builds never regenerate locks. A deliberate dependency/toolchain update
must review source/version changes and every affected mode's graph/archive pins
together, using the pinned SDK. Restore AOT with `-p:PublishAot=true`, managed
self-contained with `-p:PublishAot=false -p:SelfContained=true`, and
framework-dependent with `-p:PublishAot=false -p:SelfContained=false`, supplying
the corresponding NuGetLockFilePath. Only for that deliberate update use
`-p:RestoreLockedMode=false --force-evaluate`; retain `--runtime win-x64`. Record
raw complete-archive hashes and any SDK download inputs. Never change a hash to
bypass an ordinary failed build. Validate the supported publisher in each mode
and run the full tests after reviewing the resulting lock diff.

Primary and Python compatibility CI jobs set up the exact SDK before unit tests.
Offline console tests compare the real protocol modules with 73 independent JSON
goldens and seven stdin/stdout records, without WinUI, devices or network.
Release native symbols remain under `**/native/HaloBattery.Settings.pdb` in
intermediate output and are uploaded as a separate 90-day CI artifact.

## Installer

Use `$iscc = ./scripts/get_inno.ps1` in PowerShell, followed by
`& $iscc /Q /DAppVersion=1.17.0 installer/HaloBattery.iss` (substitute the actual
application version). The helper checks the official Inno Setup 6.7.4 installer's
pinned SHA-256 and publisher signature before portable extraction. It does not
use an arbitrary preinstalled compiler or an unversioned Chocolatey install.
The installer script also rejects other compiler versions.

`-ToolsDirectory` places this compiler under a chosen build directory, and
`-InstallerPath` can use a previously downloaded installer with the same mandatory
checks. Portable extraction uses `/PORTABLE=1 /CURRENTUSER /NOICONS` and does not
register an installed Inno Setup application. Building the HaloBattery installer
does not install or uninstall HaloBattery.


## Stable release publication

The owner enabled repository release immutability on 2026-11-17 and set the
Actions variable `STABLE_RELEASE_IMMUTABILITY_ENABLED=true`. This policy protects
future published releases; it does not retroactively lock earlier releases.
The existing `prerelease-build` release remains mutable for rolling test builds.
Do not delete and recreate it after enabling the policy.

The stable publisher receives validated build files on a fresh runner, creates
a new draft, uploads without replacement and checks the remote asset digests.
It refuses any existing release or draft for that tag. GitHub provenance
attestations bind the ZIP and installer to the exact tag commit and release
workflow. Publication follows verification of those attestations; afterward,
`gh release verify <tag>` and `gh release verify-asset <tag> <file>` check the
immutable release and its assets. These attestations do not code-sign the EXE
or replace the pending SignPath integration.

Only the publisher receives `contents: write`, `id-token: write` and
`attestations: write`. Build/test jobs keep read-only repository permissions,
and manual dispatch cannot publish. No persistent signing/admin token is
stored in the workflow. The owner acknowledgement variable is a prerequisite,
not a substitute for the actual GitHub repository setting.

A failed upload or attestation can leave an unpublished draft. A rerun stops
rather than modifying it. Inspect the failed run and draft, retain evidence,
and recover deliberately; there is no automated asset deletion or overwrite.
