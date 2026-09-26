# Changelog

All notable changes to Halo Battery are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [1.10.0] - 2026-09-26

### Added
- MyDockFinder support. MyDockFinder draws its own macOS-style menu bar and switches
  it between light and dark by the wallpaper or the full-screen app, while the Windows
  theme stays the same. While MyDockFinder is running, the icon colour now follows the
  same rule as its bar: when windows cover the whole area right under the bar
  (maximized, full-screen or snapped side by side), by their title bars (a thin strip
  at the top right of the screen is sampled; no cursor flicker, nothing is saved);
  as soon as the desktop shows anywhere under the bar, by the wallpaper as a whole,
  so a dark sky over a light landscape still counts as light. Shell overlays such as
  Task View, Snap Assist, Alt+Tab and the Start menu do not count as windows.
  The colour is re-checked the moment a window is maximized, restored, snapped,
  moved, minimized, closed or brought to the front (the same system window events
  MyDockFinder reacts to), again as the window animation settles, and every
  0.25 seconds otherwise; both colour variants of each icon are drawn in advance,
  so the icons switch together with the bar. The icons are black on a
  light bar and white on a dark one. With the standard Windows shell nothing changes:
  the icons follow the Windows theme as before.

## [1.9.1] - 2026-09-26

### Fixed
- Possible USB keyboard dropouts while the app is running. To notice devices being
  plugged in or removed, the app listed all HID devices every 2.5 seconds through
  hidapi, which briefly opens every HID device on the system, the keyboard included;
  some high-polling-rate keyboards (e.g. NuPhy Air75 HE) may not tolerate that.
  - Plug and unplug detection now reads only the list of device paths from the
    Windows configuration manager and does not open any device.
  - The full device list is re-read only when a device is actually plugged in or
    removed, and only for the vendors the app supports (Razer, WLmouse, and the
    controller vendors used for naming); vendors that are not present are skipped.
  - Controller polls no longer read the product strings of every HID device.
- The icon of a device whose receiver was unplugged or that was switched off could
  stay for up to a minute: the second, confirming check waited for the next
  scheduled poll. It now runs 3 seconds after the first miss.
- Bluetooth devices took about a minute to appear after connecting and up to two
  minutes to disappear after disconnecting: they were checked by a new PowerShell
  run once a minute, and a disconnect had to be confirmed by the next run.
  One long-lived PowerShell process now checks only the connection state every
  2 seconds (a direct WinRT query by MAC address, no device scan) and reads the
  battery levels right after a device connects or disconnects, again at +3, +8 and
  +15 seconds (Windows reports the battery a moment after connecting) and once a
  minute. A connected device now shows up within a few seconds, and a disconnected
  one disappears in about 5 seconds (a disconnect is still confirmed once, so a
  momentary dropout does not hide the icon). If that process cannot run, the app
  falls back to the old once-a-minute check. The process exits together with the app.

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

[1.10.0]: ../../compare/v1.9.1...v1.10.0
[1.9.1]: ../../compare/v1.9.0...v1.9.1
[1.9.0]: ../../compare/v1.8.0...v1.9.0
[1.8.0]: ../../releases/tag/v1.8.0
