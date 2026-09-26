# Changelog

All notable changes to Halo Battery are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Logitech support over HID++ 2.0, without G HUB (and alongside it). Every device
  paired to a Lightspeed or Unifying receiver gets its own icon, named as the device
  reports itself; the level comes from the unified battery, battery status or battery
  voltage feature, whichever the device has. Tested on the G502 LIGHTSPEED (voltage,
  matches G HUB); other HID++ 2.0 mice and keyboards should work the same way.
  A mouse whose radio dozes takes up to half a second to answer, so the first request
  waits longer; a mouse that is asleep keeps its last level, greyed out, for 5 minutes.
- SteelSeries Arctis Nova 7 support through its 2.4 GHz dongle, without SteelSeries GG
  (and alongside it): exact level and charging state. Other models with the same
  protocol are one line in `providers/steelseries.py`.
  
## [1.10.1] - 2026-09-26

### Added
- **Icon colour** in the tray menu: Automatic (as before), White or Black. For a
  transparent taskbar (e.g. TranslucentTB), where the Windows theme does not match
  what is behind the icons.
- README: the Razer Basilisk V3 Pro and Basilisk Ultimate, the FlyDigi Vader Pro
  controller, and Audio-Technica and JBL Tune 760NC Bluetooth headphones,
  confirmed working by users.

### Changed
- Releases are now a zip with a `HaloBattery` folder (`HaloBattery.exe` plus its
  libraries in `_internal`) instead of a single `HaloBattery.exe`. The single-file
  .exe unpacked Python into a temp folder at every start, which Windows Defender
  and other antivirus machine-learning heuristics flagged as a trojan by mistake
  (e.g. `Trojan:Win32/Sabsik.TE.A!ml`). The .exe now also carries version
  information (name, version, description) in its Properties, and is built
  without UPX compression. To update, replace the old `HaloBattery.exe` with the
  folder; "Start with Windows" is pointed at the new copy the first time it runs.
- "Windows Bluetooth devices" is now on by default for new installations, since
  many people use the app with Bluetooth headphones and controllers. Existing
  settings are kept: if the option was saved as off, it stays off.
- Bluetooth devices get a pictogram by their type instead of the Bluetooth rune:
  headphones and headsets the headset, mice the mouse, controllers the gamepad.
  The type comes from the device itself (the Bluetooth Class of Device, or the
  Appearance value for Bluetooth LE) or from its audio services; devices of other
  types, or whose type is unknown, keep the Bluetooth rune. Diagnostics show the
  detected type.

### Fixed
- An Xbox controller connected over Bluetooth showed up twice with "Windows
  Bluetooth devices" on: once as a Bluetooth device with the level Windows shows
  in Settings, and once as a controller with a wrong level (e.g. 10% instead of
  71%) and a generic "HID-compliant game controller" name. When the Bluetooth
  entry is there, the controller entry is now dropped; with Bluetooth devices
  turned off, it is shown without a level instead of the wrong one (over
  Bluetooth, Windows.Gaming.Input reported 100 of 1000 mWh for a controller at
  82%). Controllers named with a generic HID name are now called by their vendor
  ("Xbox controller") instead.
- Wired Razer devices without a battery (e.g. the Huntsman V2 keyboard) could show
  up as a tray icon: some of them answer the battery command too. Razer devices are
  now polled only when they are on the list of known wireless models or their name
  suggests a battery (HyperSpeed, wireless, receiver, dongle, dock, or a BlackShark,
  Barracuda or Nari headset). Skipped devices are still listed in the diagnostics.

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

[Unreleased]: ../../compare/v1.10.1...HEAD
[1.10.1]: ../../compare/v1.10.0...v1.10.1
[1.10.0]: ../../compare/v1.9.1...v1.10.0
[1.9.1]: ../../compare/v1.9.0...v1.9.1
[1.9.0]: ../../compare/v1.8.0...v1.9.0
[1.8.0]: ../../releases/tag/v1.8.0
