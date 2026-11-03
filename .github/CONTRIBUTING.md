# Contributing to Halo Battery

Thank you for helping. This page tells you how to:

1. [Report a device that is not detected or shows a wrong level](#1-report-a-device)
2. [Record a USB capture when the protocol is not known](#2-record-a-usb-capture)
3. [Open a pull request](#3-pull-requests)
4. [Understand the branches and releases](#4-branches-and-releases)
5. [See who the maintainers are](#5-maintainers)

## 1. Report a device

Before you report, do these steps:

1. Close the maker's software (Synapse, G HUB, the web driver and similar). It can hold the receiver.
2. Wake the device: move the mouse, press a key, or turn the headset on.
3. Right-click a Halo Battery icon and select **Diagnostics…**. The "No devices found" icon has this item too. If you run the app from source, `scripts\probe.bat` shows the same information in a console window; copy all of it into the issue.

   <picture>
     <source media="(prefers-color-scheme: dark)" srcset="../docs/contributing/diagnostics-menu-dark.png">
     <img src="../docs/contributing/diagnostics-menu-light.png" alt="The tray menu of a device, with the Diagnostics item marked" width="381">
   </picture>

4. The report opens in your text editor (usually Notepad). It is a text file, `diagnostics.txt`, in `%APPDATA%\HaloBattery`. It starts like this:

   ```
   === Poll result ===
   G502 LIGHTSPEED Wireless Gaming Mouse: 71%   [logitech:xxxxxxxx]

   === Protocol details ===
   [Logitech] pid=c539 'USB Receiver'
     idx=1 'G502 LIGHTSPEED Wireless Gaming Mouse' unit=xxxxxxxx feature 1001: 0f 57 00 00 -> 71%

   === All HID devices ===
   VID=046d PID=c539 if=2 usage=ff00:0001 'Logitech' 'USB Receiver'
   ```

   The **All HID devices** part is the most important for a device that is not supported: it shows the ids (`VID`, `PID`) and the collections (`usage`) of your device.

Then [open an issue](https://github.com/ahmedkhursheed23/HaloBattery/issues/new?template=device.yml) and:

- Write the device name, and how it is connected (receiver, cable or Bluetooth).
- Drag `diagnostics.txt` into the comment box (type `%APPDATA%\HaloBattery` in the address bar of File Explorer to find it). Do not paste only a part of it.
- If the maker's app shows a battery level, write that level and the level that Halo Battery shows.

The report contains Bluetooth MAC addresses and device serial numbers. You can replace them with `xx` before you post.

## 2. Record a USB capture

Halo Battery reads a battery only with a known protocol. The protocol comes from the maker's documentation, from an open-source project (for example OpenRazer, Solaar, HeadsetControl, rivalcfg or SDL), or from a capture of the maker's own app. Halo Battery does not send guessed commands to a device, because a wrong command can change the device's settings.

If you know an open-source project that reads your device's battery, put the link in your issue. That is the fastest way.

If there is no such project, and the maker's app (or web driver) shows the battery, a capture of that app gives the exact request and reply. USBPcap's [illustrated guide](https://desowin.org/usbpcap/tour.html#use-usbpcap-as-wireshark-extcap) has screenshots of each Wireshark window in these steps, and the [Wireshark USB page](https://wiki.wireshark.org/CaptureSetup/USB) has more detail.

1. Install [Wireshark](https://www.wireshark.org/download.html). In the installer, select **USBPcap**. Restart the computer.
2. **Close the maker's app completely** (also from the tray).
3. Start Wireshark and start a capture on the **USBPcap** interface that has your receiver or cable. If you are not sure which one, try each until moving the mouse makes packets appear.
4. **Start the maker's app** and wait until it shows the battery level. If it has a battery or device page, open it.
5. Wait approximately 10 seconds, then stop the capture.
6. Select **File → Save As** and save as `.pcapng`. Upload the file (for example with WeTransfer or Google Drive) and put the link in your issue.

If the capture has no packets from the maker's app, change one setting in the app (for example the DPI) during the capture and then change it back. That shows whether the capture sees the app at all.

Bluetooth devices: Halo Battery shows the level that Windows itself reports. If **Settings → Bluetooth & devices** shows no battery for your device, Halo Battery cannot show one either.

## 3. Pull requests

AI coding agents: read [AGENTS.md](../AGENTS.md) first.

- **Target `pre-release`**, never `main`. Fork, then branch from the latest `pre-release`.
- Branch names: `feat/`, `fix/`, `device/`, `docs/` or `test/` plus a short name, for example `device/razer-viper-v3`.
- One change per pull request. Keep the diff minimal; do not reformat unrelated code.
- Put a new device in the provider for its protocol family (`providers/*.py`). Make a new provider only for a new protocol. A new provider must be:
  - imported in `providers/__init__.py`;
  - added to `make_providers()` and `PROVIDER_LABELS` in `halo_battery.pyw`, with a `provider.*` key in every catalog in `halo/locales/`.
- **Only send commands that come from a source you can name.** Put the source (project, file and line, or your capture) in a comment at the top of the provider.
- Add unit tests in `tests/` that include edge cases (short or garbage replies, level out of range, device gone). They use fake HID devices, so no hardware is necessary. Run all tests from the repository root:

  ```
  python -m unittest discover -s tests
  ```

- Add a line to `CHANGELOG.md` under `[Unreleased]`. For a device, also add a row to the table in `docs/devices.md` and a section in `docs/protocols.md` that tells how the device is read and names the source.
- Do not change `VERSION`; only release pull requests do.
- In the description (the template asks for it) write:
  - which issue it closes;
  - the source of the protocol;
  - if you tested it on real hardware, and with which device;
  - which other open pull requests change the same files.
- The `Tests` check must pass; a maintainer then squash-merges it. No approval is required for `pre-release`, but maintainers should still read the diff.

Look at the open issues and pull requests first, so that two people do not do the same work.

## 4. Branches and releases

- `main`: stable, released code only. Tags `vX.Y.Z` are cut from `main`; `.github/workflows/release.yml` builds and publishes on a tag. The app's updater uses `/releases/latest`, so only `main` releases reach users.
- `pre-release`: integration branch. **All contributions open pull requests against `pre-release`.** They need passing CI (no approval required); any maintainer with write access merges them. Squash merge.
- Release: a maintainer opens a "Release X.Y.Z" pull request from `pre-release` into `main`. It bumps `VERSION` in `halo_battery.pyw` and moves the CHANGELOG `[Unreleased]` section to `[X.Y.Z] - date`. It needs passing CI and approval from a code owner (see `.github/CODEOWNERS`). Merge with a merge commit (not squash), then tag on `main`.
- Hotfix: branch `hotfix/<name>` from `main`, pull request into `main` (code-owner approval).
- After **every** release or hotfix, merge `main` back into `pre-release` (fast-forward when possible), so the two never drift apart.
- **Test builds**: every merge into `pre-release` publishes the [Pre-release build](https://github.com/ahmedkhursheed23/HaloBattery/releases/tag/prerelease-build) (`.github/workflows/prerelease.yml`). It is one rolling GitHub release, marked as a pre-release, so the app's updater never offers it. Testers download it there and report bugs with its commit id.

```
feat/* fix/* device/* --PR (squash)--> pre-release --release PR (merge commit)--> main --tag vX.Y.Z--> release
                                           ^                                       |
                                           +-------- merge back after release ---- hotfix/* branches from main
```

## 5. Maintainers

- **Contributors**: anyone. Fork, and open pull requests against `pre-release`.
- **Maintainers**: have write access. They review and merge pull requests into `pre-release`.
- **Lead maintainers** (2 to 5 of the most active people): also review, approve and merge `pre-release` into `main` and hotfixes, and cut releases. They are listed in `.github/CODEOWNERS`.
- Sustained good pull requests and reviews lead to an invitation from the owner.
- Even leads use pull requests: no direct pushes to `main` or `pre-release`. Only the owner may bypass this in an emergency.

Current leads: `ahmedkhursheed23`, `casparas123` (top contributor) and `HeyOkay` (the upstream author; counts once the invitation is accepted). A lead's pull request into `main` needs approval from another lead.
