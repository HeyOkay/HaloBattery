# Changelog

All notable changes to Halo Battery are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [1.9.0] - 2026-09-25

### Added
- Xbox-compatible controllers, tested with the GameSir G7 Pro on its 2.4 GHz receiver.
  The battery is read through Windows.Gaming.Input (the API the Xbox Accessories app
  uses): an exact percentage and the charging state. XInput is used as a fallback;
  it only reports four levels, so the tooltip then shows an approximate value
  such as "about 55% (medium)".
- Gamepad pictogram: an Xbox controller silhouette with symmetric sticks.
- A controller's icon appears within a few seconds of switching it on and
  disappears within a few seconds of switching it off. Until Windows reports the
  battery, the icon is shown without an arc ("connected, battery level not reported yet").
- Automatic release builds: pushing a `v*` tag builds `HaloBattery.exe` on GitHub
  Actions and attaches it to the release. "Run workflow" builds it without releasing.
- The .exe has its own icon (a green ring on a dark disc), also used by `build_exe.bat`.

### Changed
- Devices that are switched off no longer stay in the tray as grey icons:
  - a Razer headset that is off while its receiver stays plugged in is hidden after
    two failed polls in a row;
  - a silent WLmouse keeps its greyed-out last level for 5 minutes (the receiver
    cannot tell a switched-off mouse from one that fell asleep) and is then hidden.
  Both come back as soon as the device answers again.
- The low battery notification says "battery is low" for devices that only report
  approximate levels, instead of an invented percentage.
- README: the supported devices table lists only hardware tested for real, and
  installation starts with the ready-made .exe.

### Fixed
- `--probe` no longer crashes on a cp1252 console when a device name contains
  non-ASCII characters.

## [1.8.0] - 2026-09-25

First public release.

### Added
- One tray icon per device: a battery ring with the device pictogram inside
  (headset, mouse or Bluetooth). The arc uses the taskbar colour, turns amber near
  the alert threshold and red at or below it; while charging it is green and slowly
  "breathes" (can be turned off).
- Razer BlackShark V2 Pro (2023) through the headset's own "PA" protocol, and the
  standard Razer HID battery command for the 2020 headset and wireless Razer mice.
  No Synapse required.
- WLmouse Beast X Max: receiver and USB cable share one icon; the cable wins while charging.
- Windows Bluetooth devices (optional): only devices connected right now are shown,
  using the WinRT connection status; the last known level covers polls where
  Windows omits it.
- Low battery notifications, poll interval and alert threshold settings, start
  with Windows, and a diagnostics report with raw protocol replies and recent log entries.
- Icons and the device list update within 2-3 seconds when a USB device is plugged
  in or unplugged.
- Settings and the autostart entry are migrated from the app's earlier name, Battery Tray.

[1.9.0]: ../../compare/v1.8.0...v1.9.0
[1.8.0]: ../../releases/tag/v1.8.0
