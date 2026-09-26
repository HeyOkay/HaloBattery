# Changelog

All notable changes to Halo Battery are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [1.11.0] - 2026-09-27

### Added
- **Update check**: once a day the app asks GitHub for the latest release. When a newer
  one is out, a notification says so once, and the tray menu gets a "Download vX.Y.Z…"
  item that opens the release page. Nothing is downloaded or installed automatically,
  and "Check for updates" in the menu turns it off (#14).
- Sony DualShock 4 and DualSense / DualSense Edge over USB and Bluetooth, read straight
  from the controller's HID input report (the same source as the Linux drivers and
  DS4Windows): exact level and charging state. A controller on the cable and on
  Bluetooth at once keeps one icon. Thanks to @dendr203 (#7).
- HyperX Cloud II Wireless over HID (`03F0:0696`, `03F0:018B`): battery and charging, using the exchange HeadsetControl documents for these product ids. **Unverified** - no Cloud II Wireless was on hand, so a reply that does not echo the command is ignored and a level above 100 refused rather than shown
- SteelSeries Rival 3 Wireless (`1038:1830`) over HID: battery and charging on the mouse exchange, next to the existing Nova headsets and alongside SteelSeries GG. **Unverified** - the reply layout is the open question in [#5](https://github.com/HeyOkay/HaloBattery/issues/5), so a reply without the command echo is skipped and a level above 100 refused rather than shown
- Logitech support over HID++ 2.0, without G HUB (and alongside it). Every device
  paired to a Lightspeed or Unifying receiver gets its own icon, named as the device
  reports itself; the level comes from the unified battery, battery status or battery
  voltage feature, whichever the device has. The icon follows the device's unit id, so
  two identical mice get two icons and a mouse keeps its icon between the receiver and
  the cable. Tested on the G502 LIGHTSPEED (voltage, matches G HUB) and the G502 X PLUS
  (unified battery); other HID++ 2.0 mice and keyboards should work the same way.
  A dozing radio takes up to half a second to answer, so the first request waits up
  to 2 s; once a paired device stops answering (asleep or switched off) it is only
  pinged briefly until it answers again, and keeps its last level, greyed out, for
  5 minutes.
- SteelSeries Arctis Nova 7 support through its 2.4 GHz dongle, without SteelSeries GG
  (and alongside it): exact level and charging state. The other Arctis Nova 7 / 7X /
  7x variants and the Arctis Nova 5 / 5X are included from HeadsetControl's device
  list but not tested; new models with the same protocol are one line in
  `providers/steelseries.py`.
- Audeze Maxwell support, over the 2.4 GHz dongle (3329:4B19) and the USB-C cable
  (3329:4B1A), reading the vendor collection (usage page 0xFF13) with the sequence
  HeadsetControl uses: no Audeze HQ needed, and it works alongside it. The battery is
  attribute 0x0CD6, so the level is read as an attribute rather than hunted as an
  offset. Dongle and cable are one headset and share one icon, keyed on the serial
  number they report, so plugging in the cable does not make the icon disappear and
  come back. The Maxwell counts as a headset, so its icon gets the headset pictogram.
- Razer DeathAdder V4 Pro (1532:00BF) confirmed on hardware, listed in the README's
  table: transaction id 0x1F, command class 0x07, command 0x80 answers
  `02 1f 00 00 00 02 07 80 00 ab`, raw 0xAB = 171/255 = 67%, stable across polls and
  unchanged while Razer Synapse runs.
- MCHOSE M7 Ultra (5253:1020) over the 2.4 GHz receiver, on the vendor collection
  (usage page 0xFF01, whose sibling 0xFF0B never answers): feature report 0x11 (the shorter
  report, tried first) or 0x12 with command 0x06, every payload byte inverted, which returns
  `53 52 31 00 02 05 07 00 09 64 00 64` (vid, model, firmware, flags, level, charging).
  The request has to be repeated for every read, and the receiver only relays a real
  value while the mouse is awake - asleep it answers with zeros, so a silent mouse keeps
  its last level on a greyed icon, the same as an idle Razer mouse. On the cable the mouse
  answers on its own PID (5253:0031) with the charging byte set to 1 while the receiver
  goes quiet, and the two connections still share one icon.
- MCHOSE G7 (A8A5:2255, chip 'YJX-CHIP'), which is a different chip and a different
  protocol from the M7 Ultra: a 65-byte output report `00 55 30 A5 0B 2E 01 01 01`,
  answered by an input report starting `AA 30` with the level at byte 8 and the charging
  flag at byte 9. Implemented from @kek353's monitor and the device dump in #8 and
  **confirmed on their G7**: it answers `aa 30 a5 0b 0a 01 01 01 2e 00 00 00`, byte 8 =
  0x2E = 46% and byte 9 = 0 on the dongle, the same level their own tool shows. On its cable
  the same 0xFF01 read answers with the same level and the PID unchanged (`aa 30 a5 3c 0a 01
  01 01 2e 01 00 00`, byte 9 = 1 while charging), so a G7 keeps one icon either way; only the
  `AA 30` header is relied on, since byte 3 differs between dongle (`0x0b`) and cable (`0x3c`).
  A level out of range is still refused rather than reported as a made-up number. It gets an icon
  of its own, so it and an M7 Ultra on the same machine do not fight over one.
- MCHOSE A7 V2 Ultra (3837:100B), which is the same protocol as the M7 Ultra on MCHOSE's
  newer vendor id: the reference driver treats both identically and matches on the vendor
  id plus the vendor collection rather than by model list, which is what this provider
  does too. Its status read is documented on the shorter 0x11 report, so both report ids
  are tried, and a model the name table does not know is named from the receiver's own
  product string. **Unverified** - from the diagnostics in #4, no device here - and it
  gets an icon of its own, so it and an M7 Ultra on one machine stay two icons.

### Changed
- Audeze: the poll sends one packet instead of twenty. The packet that asks for
  attribute 0x0CD6 comes back with the marker on its own, on the dongle and on the
  USB-C endpoint alike: 0.19 s against 1.48 s. Both ran against each other every 30 s
  through a charge from 85% to 91% and agreed in 19 of 20 cycles, the one difference
  being the long sequence reading an older copy out of the device's rolling buffer.
  The full sequence stays as the fallback for a firmware that only reports after the
  initialisation.
- Audeze: charging is inferred from the cable endpoint answering, because the protocol
  carries no charging flag. Verified by diffing every record the headset returns while
  charging and while running off the dongle: identical apart from the echo of the query
  that was just sent. Docked and already full the icon still breathes where the LED is
  solid green, which is the trade for not telling someone to charge a headset that is
  plugged in.
- Razer: a sleeping mouse now keeps its last level on a greyed icon for five minutes
  instead of losing the icon after two failed polls (`STATUS_TIMEOUT`, "receiver
  present, device not responding"). That is what the README already described; the
  behaviour is gated so a switched-off headset still loses its icon.
- New controller pictogram for Xbox-compatible controllers (XInput and
  Windows.Gaming.Input), traced from the Xbox controller glyph: flat top, rounded
  shoulders, straight sides down to the grips, and the two sticks in the Xbox layout
  (left stick high, right stick low and nearer the middle). Nothing else is cut out,
  so it stays readable at 16 px.
- PlayStation controllers get a DualShock 4 pictogram in the same style: the outline
  with its stepped shoulder buttons and long grips, the touchpad and the two symmetric
  sticks cut out. The DualSense uses it too for now.
- HyperX: when the dongle has no 0xFF90:0x0303 collection, nothing is written to any
  other collection; the diagnostics list what the dongle offers instead.

### Fixed
- `--probe` now always lists every HID device it can see. The list was only printed when
  the poll found nothing at all, so on a machine whose other devices answered, a device
  that no provider sees - the case the list exists for - never appeared (found while
  answering #21).
- Razer mice that answer a battery request with somebody else's packet first are no
  longer written off as "off or asleep". Razer Synapse polls LED state on the same
  collection and its replies carry the same status byte as the battery reply, so the
  first packet could belong to a different command (seen on a DeathAdder V2 Pro in
  #3). The reply is now read on - bounded by the same deadline - until the answer to
  the request arrives, and a packet that is not that answer is never turned into a
  level, so a device that never answers still shows nothing rather than a number.
- After a device went missing (e.g. a Razer headset switched off while its receiver
  stays plugged in), all devices were polled every 3-4 seconds for as long as the app
  ran, instead of at the poll interval: the quick re-check that confirms a disconnect
  never stopped after the icon had been removed. Besides filling the log, this would
  keep waking wireless mice (e.g. Logitech) over the radio.
- A Razer device that is switched off while its receiver stays plugged in no longer
  writes the same six log lines on every poll: the failure is logged once, and again
  only when the reason changes or the device answers again.
- Bluetooth: a device that is also read over HID no longer gets a second icon from
  Windows' own Bluetooth battery API. A paired Maxwell reports the same level over both
  transports at once (90% over the cable endpoint and 90% over Bluetooth), so the
  Bluetooth copy is dropped and the HID reading - the device's own protocol, carrying
  the charging state - is kept. A device only Bluetooth can see keeps its Bluetooth
  icon, which is how a Maxwell used purely over Bluetooth is covered at all: the vendor
  collection the provider needs does not exist over Bluetooth. Game controllers are
  left out of this: for a controller on Bluetooth, Windows' own value is still the one
  shown, as in 1.10.1.
- Audeze: a switched-off headset no longer pays for the battery packet that cannot be
  answered (1.5 s per poll instead of 1.7 s), and its failure block is written once per
  outage instead of every poll.
- Audeze: a Maxwell that is switched off is no longer reported at the level it was
  on before. The dongle keeps answering with that value as if it were live (nine
  answers out of nine over two and a half minutes, all the same), so the dongle's
  own product string decides instead - "Audeze Maxwell Dongle" with no headset
  linked, "Audeze Maxwell HID" with one. Nothing is reported while it says Dongle,
  so the icon leaves the tray the way the README says a switched-off device does,
  and the 1.5 s battery sequence is not sent at all.

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
