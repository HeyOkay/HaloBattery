# Changelog

All notable changes to Halo Battery are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- MCHOSE A5 Pro Max (2023:f013 1K receiver, 2023:f015 4K receiver, 2023:f019 wired):
  a third MCHOSE exchange on a third vendor id - 64-byte feature reports, battery on
  page 0 command 0x83 - from the community web driver for this model
  ([#149](https://github.com/HeyOkay/HaloBattery/issues/149)). Confirmed by the
  reporter's run on his 1K receiver: the level and the charging state, matching the
  MCHOSE software.

## [1.13.0] - 2026-09-29

A new Windows 11 style tray menu and a batch of tray features: turn device types off,
an alert level per device, estimated time left, the percentage in the icon, quiet while
gaming and a status file for other apps. PS4 / PS5 controllers over Bluetooth no longer
break DirectInput games (#96), and 8BitDo controllers in D-input mode are read the same
listen-only way. New devices: Corsair Dark Core RGB Pro SE, Razer BlackWidow V3 Pro,
Hitscan Hyperlight, HyperX Cloud Alpha 2 and Angry Miao AM Infinity 8K, plus many fixes
from contributors' reviews. Notifications are now titled "HaloBattery".

### Added
- 8BitDo Pro 2, Pro 3, SN30 Pro and SF30 Pro in D-input mode (#101). The level is read
  from the controller's enhanced report while Steam or a game has switched it on; the app
  never switches it itself, because that mode hides the controller from DirectInput games
  until it is turned off. Otherwise the icon shows the controller without a level. In
  XInput mode these controllers already worked. Unverified on hardware here.
- LAMZU Maya X: confirmed on a real mouse on its 8K dongle with 1.12.0, so it is no longer
  marked unverified.
- **Device types** in Preferences: turn off any brand or device family (Razer, Logitech,
  PlayStation controllers, ...). A type that is off is not polled and its devices are not
  opened; its icons go away at once.
- **Low battery alert at** in the menu of a device: an alert level for that device only,
  or Default to follow Preferences. The red ring of the icon follows it too.
- **Estimated time left** in the tooltip ("about 5 h of use left"), from a least-squares
  fit of the level against the time the device was awake and on battery since its last
  charge. Time asleep, switched off or with the PC suspended does not count. No estimate
  until 30 minutes of use and a 3% drop; kept in `%APPDATA%\HaloBattery\history.json`
  so it survives a restart. Can be turned off in Preferences.
- **Percentage in the icon** in Preferences: the level as a number in the ring instead of
  the pictogram, sized to stay inside the ring ("100" included) and amber or red like the
  arc when the battery is low. Devices that only report rough steps keep their pictogram.
- **Quiet while gaming** in Preferences (on by default): while a full-screen app is in
  front (`SHQueryUserNotificationState`), notifications are held and shown when it
  closes, one per device and kind, and the poll interval becomes 5 minutes so the app
  talks to the devices less during a game (#76). A plug-in still polls at once.
- **Status file for other apps** in Preferences (off by default):
  `%APPDATA%\HaloBattery\status.json`, rewritten after every poll (atomically), with each
  device's name, level, charging, online, kind, alert level, seconds of use left and
  tooltip text, for Rainmeter, Stream Deck or scripts.
- Audeze Maxwell: a **stuck dongle** is recognised. An Xbox dongle (`3329:4B18`) that said a
  headset was linked, in PC mode with the headset on and playing audio, answered every
  packet with an empty echo of the request (`07 00 80 00 ...`), so no battery ever arrived
  and there was no icon; unplugging the dongle and plugging it back in fixed it at once.
  After two such polls in a row the headset now gets a greyed icon that says to replug the
  dongle, and the diagnostics say what was seen. This may be HeadsetControl #460.
- Corsair Dark Core RGB Pro SE: the battery over its 2.4 GHz dongle (1B1C:1B7F),
  through the Dark Core / Ironclaw "nxp" protocol from ckb-next. Its five-step
  level is shown as a gauge ("about 50%") and no charging state is reported.
  **Unverified** here: no Corsair mouse was on hand, so the collection and the
  offsets are the reference's until the reporter of #56 confirms.
- Razer BlackWidow V3 Pro: the battery over its 2.4 GHz receiver (1532:025C), through
  the same class 0x07 commands the mice use (OpenRazer's RazerBlackWidowV3ProWireless
  class lists them). The wired id 1532:025A has no battery and is left out.
  **Unverified** here: no Razer keyboard was on hand, so the level arrives as the
  reference describes it until the reporter of #56 confirms.
- HyperX Cloud Alpha 2 (station 03F0:08BE): the battery percentage and the charging
  state over the station's vendor collection. Decoded from the two USBPcap captures
  attached to issue #26 at 51 % and 67 % and matched to NGENUITY's own display;
  confirmed on real hardware by the reporter.
- AM Infinity 8K (Angry Miao) on its 2.4 GHz receiver (3151:5007), the same id
  as the AJAZZ AJ159 APEX (2.4G 8K): the AJAZZ Control Center project's
  AJ-series exchange over the receiver's ffff:0002 control collection - a
  zero-payload 0xF7 status poll brings the 2.4G telemetry up, then the charge
  reads from status report 0x05. No charging state is reported. **Unverified**
  here: the exchange is confirmed on the reference project's own unit, so the
  layout stands until the reporter of #72 confirms it on the AM Infinity.
- **Hitscan Hyperlight** over its receiver (3770:0200) and on the cable (3770:0100):
  the same 17-byte frame the Pulsar / ATK / VXE mice use, from @sopparus's captures, notes and
  Linux reader ([sopparus/hitscan-battery](https://github.com/sopparus/hitscan-battery)).
  Unverified: no Hyperlight was on hand, so [#105](https://github.com/HeyOkay/HaloBattery/issues/105)
  will confirm.

### Changed
- New tray menu in the Windows 11 style: Segoe UI Variable text, Fluent icons, an acrylic
  (blurred, translucent) background, rounded corners on Windows 11 and the light or dark
  app theme. It fades in, opens next to the taskbar centred on the click (like the menus
  of the Windows 11 taskbar) and opens submenus on hover; Esc or a
  click elsewhere closes it, and it works from the keyboard. Set `"fluent_menu": false`
  in the settings file to get the classic Windows menu back.
- **Poll interval** and **Low battery alert** are now − / + counters in Preferences; the
  menu stays open while you change them, and the mouse wheel works on them too.
- The top of the tray menu shows the device's name on one line and its level,
  charging state and time left on the line below. A long name or state wraps onto
  more lines instead of making the whole menu wider. A small pencil at the right of the
  name renames the device; it replaces the Rename item (the classic menu keeps it).

### Fixed
- Notifications were titled "Python" instead of the app's name: the app now sets its
  own app id and registers the name "HaloBattery" (and its icon) for the notification
  header, per user, no admin rights.
- The menu text was small and blurry on displays scaled above 100 %: the app is now DPI
  aware, so the menu and the tray icons are drawn at the display's real resolution.
- PS4 / PS5 controllers over Bluetooth stopped working in some games (DirectInput, for
  example Rocket League from the Epic launcher) until they were turned off and on (#96).
  To read the battery, the app switched the controller to its full report, and that
  mode stays on. Over Bluetooth the app now only listens: the level shows while Steam or
  a game has already switched the controller, and the icon shows no level otherwise.
  **Preferences > PlayStation full mode (Bluetooth)** brings the old behaviour back for
  those who do not play such games. USB is unchanged.
- Audeze Maxwell: no more "Low battery, 0% left" when the headset is switched on. Right
  after power-on it reports 0% for a moment (measured on an Xbox dongle: 0%, then the real
  80% a poll later). A 0% in the first 90 seconds is now shown as "battery level not
  reported yet", and the app re-checks every 3 seconds until the real level arrives
  instead of waiting a full poll interval. After 90 seconds 0% is believed.
- A Razer mouse could show "no link (off or asleep)" while in use, when Synapse or other
  RGB software was sending lighting frames to it (#108). Every reply the app read was an
  answer to the other app, and the app took that as a success without a level. It now
  asks again up to three times, keeps the last level greyed out, and the diagnostics say
  that another app is using the device.
- An Xbox controller over Bluetooth could show a wrong "10%" when **Windows Bluetooth
  devices** was off (#97, #108). Windows.Gaming.Input reports 100 of 1000 for it, and the
  app did not always see that the controller is on Bluetooth. The product ids that Xbox
  controllers use only over Bluetooth (from SDL) now say so, and the icon asks you to turn
  on **Windows Bluetooth devices** for the real level.
- An 8BitDo Ultimate controller on its dock's 2.4 GHz dongle showed "on cable, charging"
  while it was off the dock and off the cable (#110). The dongle tells XInput that the
  controller is wired, but Windows.Gaming.Input says that its battery is discharging. The
  app now believes the second: no "charging", and the icon shows no level, because the
  dongle does not report a real one (it always says 100%).
- **A device list that hidapi returned incomplete is no longer cached for the rest of the
  session.** `hidlist.enumerate()` caches its result against the set of HID paths, and only
  re-reads when that set changes - but hidapi *opens* every device it lists, so a collection it
  could not open at that moment is simply missing from the result, with the path set unchanged.
  The short list was then served from the cache indefinitely: measured on this machine, 9 of 10
  present collections came back for 5 calls in a row with a single hidapi call, and only an
  unplug cleared it. A result with fewer entries than the vendor has present interfaces is now
  re-read, at most once every `SHORT_RETRY` (30 s) so that a permanently unopenable collection
  cannot turn every call into a full enumeration. Reported by @ahmedkhursheed23 in
  [#62](https://github.com/HeyOkay/HaloBattery/issues/62).
- **The Bluetooth watcher now notices a stalled PowerShell instead of pretending to be
  healthy.** Its output was read with `for line in proc.stdout`, which parks forever on a child
  that has wedged inside a WinRT call: no snapshot arrives, `failed` stays False, `running()`
  keeps answering True, and the app never falls back to its once-a-minute polling. The child is
  read through a queue with a watchdog now (the script's slowest cadence is a snapshot every
  60 s, so 180 s of silence is a fault); it is killed and restarted, and two stalls in a row make
  the watcher give up so the fallback happens. Reported by @ahmedkhursheed23 in
  [#62](https://github.com/HeyOkay/HaloBattery/issues/62).
- **A "PA" headset (BlackShark V2 Pro 2023, Barracuda) that is switched off costs one probe per
  poll instead of one per collection.** `_poll_pa` remembered the collection that answered, but
  only on a success, so a headset that had been off since the app started had nothing remembered
  and every poll walked all of its vendor collections (~0.7 s each).
  The obvious fix - remember whichever collection reports "no reading" - would pin a *wrong*
  collection, because `read_battery`'s `offline` covers two different situations: the interface
  opened but never accepted a single command (which is exactly what a wrong collection looks
  like), and the interface accepted a command while the headset did not answer (the right
  collection, headset off). `read_battery` now says which of the two it is, and only the second
  is remembered. A switched-off headset still shows "no link" rather than losing its icon, and a
  headset switched on afterwards is still found. Reported by @ahmedkhursheed23 in
  [#62](https://github.com/HeyOkay/HaloBattery/issues/62).
- HyperX Cloud III Wireless: a dongle that takes the battery request only as a feature
  report ("Incorrect function" on a normal write) showed no level. The app now notices
  the refused write and sends the request as a feature report, as intended.
- Razer Barracuda Pro: while the headset was off, each poll waited about 4 seconds longer
  than needed and held back the icons of all other devices. The app now stops asking as
  soon as the headset does not answer, and it retries when the receiver refuses a command.
- Two PS4 / PS5 controllers of the same model on USB showed only one icon, with the
  level of one of them. Each controller now has its own icon. A single controller on
  USB keeps its icon, name and hidden setting.
- A Razer mouse plugged in by cable while its receiver stayed in showed two icons: the
  cable (charging) and a greyed copy from the receiver for 5 minutes. The greyed copy
  now goes away while the same model answers on the cable.
- A device used over Bluetooth lost its battery level when its USB receiver was also
  plugged in (for example a Razer Barracuda Pro, or a mouse switched to its Bluetooth
  channel): the Bluetooth reading was hidden as a duplicate and only the grey "no link"
  icon of the receiver was left. The Bluetooth reading is now hidden only while the
  receiver actually reads the device.
- Tray menu: the item under the mouse was not highlighted. Opening the menu brings it to
  the front, and its acrylic background then hid the highlight window behind it. The
  highlight is now put in front of the menu each time it is shown.
- The tray menu or one of its submenus could open behind the taskbar when the work area
  includes the taskbar: with an auto-hide taskbar, or over a full screen game after the
  Windows key brings the taskbar up. The menus now leave the taskbar's rectangle out.

## [1.12.0] - 2026-09-28

A big device release: support for many more mice, keyboards and headsets - ASUS, G-Wolves,
LAMZU, Lofree, Keychron, Pulsar/ATK/VXE, Nintendo Switch controllers, Logitech headsets,
more SteelSeries Arctis and Aerox models, HyperX Cloud III, Astro A50 Gen 5 and Corsair
headsets, among others. Devices can now be hidden or renamed, the app can tell you when
a device is fully charged, and each device can get the pictogram you pick. Tray icons keep
their place between starts, the extra "No devices found" icon is gone, and settings and
autostart are more robust. Many of the new devices are marked **Unverified** below: if you
own one, please tell us whether the level matches.

### Added
- **Hide a device** (#23): "Hide this device" in the menu of a device icon removes the
  icon and stops the low battery alert for that device, for example a controller that
  always reports 100%. "Hidden devices" lists them; a click shows one again.
- **Rename a device**: "Rename…" opens a Windows input box. The name is used in the
  tooltip, the menu header and the low battery alert; "Reset name" goes back to the
  device's own name. The pictogram does not change.
- **Fully charged**: a notification when a charging device reaches 100%, once per charge
  (a 99/100% wobble on the charger does not repeat it). "Alert when fully charged" in
  Preferences turns it off.
- **Icon** in the menu of a device: pick its pictogram (Automatic, Mouse, Keyboard, Headset,
  Controller, Bluetooth), for example a controller over Bluetooth that got the Bluetooth
  pictogram. The choice is kept per device, like the name.
- Logitech headsets: G533, G535, G633, G635, G733, G933, G935, G PRO, G PRO X and
  G PRO X 2 (HID++ models). The app reads the battery voltage with feature 0x1F20 and
  shows the level, charging, and "headset off". The model list comes from
  HeadsetControl; the feature layout comes from Solaar. A G733 is read on hardware (#75);
  its percentage is an estimate from the voltage and can differ from G HUB's.
- HyperX Cloud III Wireless (`03F0:05B7`) over HID: battery and charging from the dongle's `0xFF13` vendor collection, next to the Cloud II support and alongside NGENUITY. **Unverified on hardware** - the packets, the reply ids and the level byte come from LennardKittner/HyperHeadset's implementation for this product id, which also documents the Windows-only fallback where a dongle accepts the packet only as a feature report; that fallback is implemented and the diagnostics say which path was taken. A level above 100 and the reference's all-zero state are both refused rather than shown as a reading
- Lofree Hyzen keyboards on their 2.4 GHz dongle (`388D:0025`, #82), with the battery query of Lofree's
  own web driver (command `1A` in its report 0x04 transaction). **Unverified** - no Lofree keyboard was on
  hand. `tests/test_lofree.py` uses the collections from the #82 report.
- Astro A50 Gen 5 support through its base station, without G HUB: exact level, and
  charging while the headset sits on the dock (the station reports that as byte 8).
  The protocol is HeadsetControl's, reverse-engineered from G HUB captures and
  verified on the same station (046D:0B1C); it is neither HID++ nor the A50 X's
  "Centurion" protocol, and the provider only matches that USB id.
  **Unverified** here: no A50 was on hand, so a level out of 0..100 is refused
  rather than shown.
- Corsair wireless headsets (Void v2 Wireless, Virtuoso Max Wireless, HS80 Max
  Wireless) through their receiver, without iCUE: exact level, from HeadsetControl's
  corsair_void_v2w protocol. A minimal handshake wakes a sleeping headset for the
  read, the same one HeadsetControl uses, which avoids the audible pop of switching
  the headset into software mode.
  **Unverified** here: no Corsair headset was on hand, and the receiver sometimes
  answers with something other than a level, so that is retried and then refused
  rather than shown. Charging is not reported - the reply carries no such flag, and
  HeadsetControl reports this family as not charging either.
- JBL Quantum 910 Wireless support through its dongle: the headset pushes report 0x08
  with the level in byte 1, the pattern plugato/JBL_Baterry_Monitor confirmed on this
  USB id (0ECB:2088, one vendor collection `ff13:0001`, interface 5 on a real unit). There is no request to send, and the headset can
  stay quiet for long stretches, so the last level heard is kept and shown greyed out.
  Confirmed on a real unit: the provider finds the receiver, matches its collection and shows the level,
  and a capture from that unit reads `08 5f 03 ...` - report 0x08 with byte 1 = `0x5f` = 95%, at the
  moment JBL's own app showed 95%. The report is an event: it arrives when the headset is plugged into
  its charger, and pressing buttons or the volume rocker does not produce it, so the listen window is ten
  seconds and the last level heard is kept. The receiver also pushes a power report 0x09 (byte 1 is 0x00
  when the headset is off and 0x01 when it is on, confirmed on a real unit) and a 0x02 frame; none of them
  is a level, and a headset last seen switched off explains itself in the diagnostics. The report has no charging flag, so none is shown.
- Keychron support over the Ultra-Link 8K receiver (3434:D028) and the cable (3434:D048,
  the M5), without Keychron's own software: the vendor protocol - a 64-byte feature
  report `b3 06` answered by a 64-byte input report `b4 06` whose byte 20 is the level -
  as implemented by csutcliff/keychron-battery-dkms for these two ids.
  **Unverified** here: no Keychron device was on hand, so a level above 100 is refused
  rather than shown. Charging is not reported - the reply carries no such flag.
- Pulsar, ATK and VXE wireless mice (Pulsar X2 V2 Mini, ATK VXE R1 SE+) over their dongle
  or cable, without vendor software: 17-byte frames carrying a checksum, command 0x04 for
  the power details, level in byte 6 and the on-cable flag in byte 7. Protocol from
  andrewrabert/python-pulsar-mouse-tool, which also backs the "HID: pulsar" driver in
  review for Linux. **Unverified** here: no such mouse was on hand, so a frame whose
  checksum does not match is refused rather than shown.
- Pulsar/ATK: the VXE R1 Pro Max 1 kHz dongle (`3554:F58A`) reported in #87. The
  provider never queried it, because the id was not in its table. The ATK/VXE control panel of
  the OpenMouse project (`@openmouse/protocol`, `drivers/atk`) lists it as the R1 Pro Max receiver and reads
  the battery with the same command `0x04` frame this provider already speaks, from the
  collection with usage page `0xFF02` and usage `0x0002` - which is now preferred, because
  the dongle has five other interface-1 collections and the first of them is not the one
  that answers. The level, the charging flag and the millivolts come out at the same
  offsets. A collection whose output report cannot carry the 17-byte frame is skipped, so a
  dongle whose control collection only takes a longer report still gets read instead of
  looking switched off (spotted by ahmedkhursheed23). The cable id (`3554:F58C`) is added
  from the reporter's second report, where the wired mouse lists the same eight collections
  and the same frame applies. Both transports are now confirmed on the reporter's hardware:
  the receiver read the mouse's level, and on the cable the level matched ATK's own panel
  (hub.atk.pro) with the charging flag following the cable in both directions. Reported by
  huyxs2005.
- Nintendo Switch Pro Controller and Joy-Con over Bluetooth (#63). Windows does not report
  their battery, so the app reads the battery byte from the controller's own input report,
  as SDL does: five levels (full, medium, low, critical, empty) and the charging bit. The
  controller mode is never changed: the app only listens, or sends one read-only subcommand
  (0x02, request device info). **Unverified** - no Switch controller was on hand.
  `tests/test_nintendo.py` covers every battery byte, the packet, both modes and the timeouts.
- ASUS ROG / TUF wireless mice on their receiver or cable (#81: ROG Gladius III Wireless
  AimPoint), with the battery command that G-Helper uses (`12 07` on the vendor collection of
  interface 0): Gladius III / III Aimpoint / Eva 2, Chakram / Chakram X, Keris Wireless /
  Aimpoint / EVA, Harpe Ace Aim Lab, Spatha X, Pugio II, Strix Impact II Wireless, TUF M4
  Wireless and TX / TUF Gaming Mini. **Unverified** - no ASUS mouse was on hand.
  `tests/test_asus.py` uses the interfaces from the #81 report.
- G-Wolves mice on the 8K receiver (`33E4:3854`) or the cable (#82: G-Wolves WARG 8K), with the
  WLmouse feature report exchange that G-Wolves' own web driver (mouse.xyz) uses for these
  mice. The request goes only to the collection with a 64-byte feature report. **Unverified** -
  no G-Wolves mouse was on hand. `tests/test_gwolves.py` uses the collections from the #82 report.
- LAMZU Maya X on its 8K dongle (`373E:001E`) or the cable (`373E:001C`), without Lamzu's own
  software. It is the same feature report exchange as the WLmouse and G-Wolves mice - one
  request, `00 00 02 02 00 83`, answered by `a1 00 02 02 00 83 <charging> <battery %>` - sent
  only to the vendor collection (usage page `0xFFFF`) of interface 2. Protocol from
  Sheroune/lamzu-battery-monitory (MIT) and its Linux port; the collections match a
  diagnostics report from a real Maya X 8K dongle. **Unverified** on hardware here.
- SteelSeries Aerox 3 Wireless (`1038:1838`) over HID: battery and charging on the receiver's vendor protocol, next to the existing Nova headsets and alongside SteelSeries GG. **Unverified on hardware** - the interface, the `0xD2` query and the level scale come from three sources that agree on this product id (alloyctl's reverse engineering of `1038:1838`, yurtemre7/steel-mouse, and the capture notes at gort818/aerox3-wireless), but no Aerox 3 Wireless was available here. A level byte of 0 is read as off or asleep rather than empty, and a reply without the `d2` echo is refused rather than shown as a level. The CS2 Dragon Lore edition (`1038:1878`) is included untested
- SteelSeries Aerox 9 Wireless (`1038:1858`, WOW Edition `1874`, #79) and Aerox 5 Wireless (`1038:1852`, `185C`, `1860`) in 2.4 GHz mode, on the same `00 d2` exchange as the Aerox 3 Wireless: rivalcfg builds the wireless profiles of all three mice the same way. The level is confirmed on an Aerox 9 Wireless by @AJD00m (#79): the reply `d2 04 ...` gives 15 %, the same as SteelSeries GG. Not tested yet: the charging bit, and the Aerox 5. `tests/test_steelseries_aerox.py` uses the interfaces and the reply from the #79 reports.
- SteelSeries Arctis Nova Pro Wireless (base stations `1038:12E0` and `1038:12E5` X),
  ported from HeadsetControl's `steelseries_arctis_nova_pro_wireless.hpp`: the same `b0`
  exchange as the other Nova headsets, asked for with report id `06` - on interface 4 as
  HeadsetControl asks, and interface 3 too. The request reaches only a vendor collection
  of those two product ids. The level is a nine-step code in byte 6 and the state in byte
  15 (`01` off / out of range, `02` cable charging, `08` on battery), so nine steps are
  shown as "about NN%", `01` gives no reading at all instead of 0 %, and any other state
  byte, a level code above 8 or a reply shorter than 16 bytes is refused rather than
  shown. Covered by the Nova Pro cases in `tests/test_steelseries.py`. The interface and the
  collection are confirmed by the diagnostics in
  [#41](https://github.com/HeyOkay/HaloBattery/issues/41) (`1038:12e5` exposes `ffc0:0001`
  on interface 4, with a second vendor collection `ff00:0001` on the same interface, so
  both are tried and the one that answers is remembered). **Unverified**: the reply
  layout - the nine steps and the state bytes - is HeadsetControl's, not yet seen here.
- SteelSeries: older and other Arctis headsets. Not tested on these headsets; the raw
  replies go to the diagnostics.
  - On the `b0` exchange (interface 3): Arctis Nova 7P, Nova 3P / 3X Wireless,
    Arctis 7+ (and the PS5 / Xbox / Destiny editions), Arctis GameBuds.
  - With their own requests: Arctis 1 Wireless / 7X / 7P (`06 12`), Arctis 7
    (`06 14` then `06 18`), Arctis Pro Wireless 2019 (`06 18`), Arctis 9 (`00 20`),
    Arctis Pro Wireless (`41 aa` then `40 aa`).
  - Model list and layouts from HeadsetControl; the echo checks and the Arctis 7
    connection query from the Linux driver `hid-steelseries-arctis.c`. A reply that
    does not answer the request is never read as a level. Only vendor collections get
    a request. The Arctis Pro GameDAC is left out, because it is a wired headset.
- Razer Barracuda Pro (2.4 GHz) support through its receiver (1532:053a), which does not
  answer the standard Razer request: the headset speaks the "PA" protocol, decoded from a
  USBPcap capture of Razer Synapse on a real unit. Battery command 0x21, charging 0x2A, and
  the `razer` provider hands this PID over instead of reporting "no reply" for it.
  Confirmed on hardware by @phl23 in
  [#10](https://github.com/HeyOkay/HaloBattery/issues/10): the level tracks (34% while the
  capture was taken, 27% when the branch was tested), the charging state follows the
  charger, and a switched-off headset reports "no link" instead of a stale value.
- Razer: the model table now has every wireless mouse whose battery OpenRazer reads, with
  OpenRazer's transaction id: for example the Pro Click V2 Vertical Edition (#58), Pro Click V2,
  Pro Click (Mini), Naga V2 Pro, Naga V2 HyperSpeed, Viper V3 HyperSpeed, Viper Mini SE,
  DeathAdder V3 HyperSpeed, Basilisk Mobile, Orochi V2, Atheris and the older Mamba / Lancehead
  mice. Before, a mouse without "wireless" or "HyperSpeed" in its name was skipped.
  `tests/test_razer.py` checks the table against a copy of OpenRazer's list.

### Changed
- **The keyboard pictogram is a single keycap with a K.** The old one, a whole keyboard with
  rows of keys cut out, turned into a grey bar at 16 px; one square key with a bold K reads
  at tray size on a light and a dark taskbar and does not look like the mouse.
- The settings are in a **Preferences** submenu: poll interval, low battery alert,
  Bluetooth, pictogram, charging animation, icon colour, start with Windows and the
  update check. The main menu keeps the items used often.
- Logitech receivers are also recognised by their product id (Solaar's list), not
  only by "receiver" in the product name.
- **Tray icons keep their place between starts** (#38). Windows remembers whether an icon
  sits on the taskbar or in the hidden-icons flyout by the program and the icon's id, but
  pystray gave every icon a new id on every start, so a device icon moved to the taskbar
  went back to the flyout after a restart. Each device icon now has an id of its own that
  stays the same (and so does the "no devices found" icon), so the device icon and the
  empty icon can be placed separately and should stay there.

### Fixed
- Tray: an extra "No devices found" icon could stay next to a device icon, for example
  after a mouse woke up from sleep (#95, #38). The "no devices" icon is now made once
  and only shown or hidden, and an icon is shown only after its window exists. pystray
  ignores a stop and loses a show that comes before that.
- **The charging animation no longer writes a temporary file for every frame** (#61).
  pystray saves each new image to a temporary `.ico` file and loads it back, so a charging
  device meant about ten file writes a second, which disks and antivirus programs notice.
  The icon handle of every frame is now kept, and the 30 frames of a cycle are loaded
  once.
- **"Start with Windows" is not pointed at a temporary folder** (#61). Opened straight
  from the ZIP (Explorer or WinRAR unpack it into `%TEMP%` and delete it later), the app
  wrote that path into the autostart entry, which then led nowhere after the next
  reboot. It now says to extract the ZIP first instead, and a copy running from a
  temporary folder never takes over an existing autostart entry.
- **A damaged settings file no longer breaks the app** (#61). The settings are written to
  a temporary file and swapped in, so a crash or a full disk while saving cannot leave
  half a file. A file that is not valid JSON is kept as `config.json.bad` and the
  defaults are used, and a single wrong value (a string as the poll interval, an interval
  of 0) falls back to its default instead of being used.
- Logitech: "slow charging" (status 4) now shows as charging.
- Logitech: an error reply is accepted only when it answers our own request. Before,
  an error reply to G HUB's request could make a mouse show as "off".
- Logitech: the receiver's error code now tells an empty slot (08) from a device
  that is switched off (09). When a slot becomes empty, the app forgets the old
  device name, so a new device in that slot shows its own name.
- Logitech: a device that reports no percentage (unified battery level 0) showed 0%
  and could trigger the low battery alert. It now shows the approximate level from
  the level flags ("about 50% (good)"), as Solaar does.
- Logitech: each request now uses a different software id. A late reply to an earlier
  request can no longer be taken as the reply to the current one.
- Logitech: a device name that could not be read (for example just after the mouse
  wakes up) is no longer kept until the app restarts. When the icon key of a slot
  changes, the old icon goes away at once instead of staying grey for 5 minutes.
- Arctis Nova 7: while the headset is off or still switching on, the dongle repeats
  the last battery level. The app showed that old level as live for a few seconds.
  The link byte (byte 1: 03 = connected, 02 = not connected) is now checked too.
  Verified on a real Nova 7 (22A1).
- SteelSeries: the Nova headsets are read only from their 0xFFC0 collection, not from
  whatever collection comes first on interface 3.
- Razer: PIDs 008F / 0090 are the Naga Pro, not the Naga V2 Pro (OpenRazer). The Naga V2 Pro
  (00A7 / 00A8) was missing and was skipped.
- Razer: the Basilisk X HyperSpeed (0083) is asked with transaction id 0xFF first, as in
  OpenRazer, not 0x1F.
- Razer: PID 0078 is the wired Razer Viper, which has no battery. It was in the table as a
  "Viper Ultimate" and got battery requests. The Viper Ultimate is 007A / 007B.
- **SteelSeries Rival 3 Wireless replies are read in either layout.** flozz/rivalcfg sends the same `aa 01` request and reads the reply with the same hidapi call, but takes a 3-byte reply with the level in byte 0 and charging in byte 2, while yurtemre7/steel-mouse reads an `aa` echo with the level in byte 1 and charging in byte 3. Nothing has been on hardware here, so both are accepted now instead of only the echo shape - a reply without the echo used to be skipped, which would have left the mouse showing nothing if rivalcfg is the correct one. The diagnostics print the raw reply and name the shape. The discrepancy was reported by @ahmedkhursheed23 in [#5](https://github.com/HeyOkay/HaloBattery/issues/5).
- **SteelSeries devices are found by usage page, not by interface number.** The configuration collection is `0xFFC0`, and the Rival 650 exposes it on interface 0 (rivalcfg's profile says endpoint 0, and rivalcfg issue #202 is about needing the usage page to find it dependably). Filtering on interface 3 skipped that mouse entirely. When the page appears more than once the collection on interface 3 wins, so the Nova headsets and the Rival 3 are read exactly as before. Pointed out by @ahmedkhursheed23 in [#5](https://github.com/HeyOkay/HaloBattery/issues/5).
- **Keyboards were drawn with a mouse pictogram.** Both providers report
  `kind="keyboard"` for the keyboards they support (Logitech HID++ over a receiver, and
  anything Bluetooth whose class says keyboard), but the badge list did not know the
  kind: a Logitech keyboard fell through to the mouse badge, and a Bluetooth one to the
  Bluetooth badge. There is a keyboard pictogram now, wide and low enough to read
  against the mouse at 16 px.
- **A short device name could hide an unrelated Bluetooth device.** The duplicate check
  accepted a match whenever the Bluetooth name was at least six characters and contained
  the HID name (or vice versa), without looking at the second name's length, so an HID
  device named "Razer" dropped a Bluetooth "Razer Barracuda Pro" and an HID "G Pro"
  dropped a "Logitech G Pro X Wireless". Both names now have to be at least six
  characters for a substring match; identical names still match as before.
- A wired Xbox-compatible controller could show **5%** while on the cable: `BatteryLevel`
  is documented as valid only for wireless devices, and a wired pad's byte is whatever the
  driver left in the field, so it is no longer read as a level (the last wireless reading
  is still kept, and a pad with none says full).
- With two Xbox-compatible controllers, one could take the *other* controller's level and
  name: `RawGameControllers` was indexed by slot number, but it is not the XInput slot list
  - it also holds controllers XInput cannot see (a DualSense, a wheel) whose order is not
  the slot order. Only the vendors this provider reads are considered now, and the reports
  are paired with the slots only when the two counts agree; otherwise the coarse XInput
  level is used instead of another controller's report.
- A controller connected over Bluetooth could appear twice - its XInput icon plus the
  Windows Bluetooth one - whenever Windows.Gaming.Input returned no report. The transport
  is now also derived from the HID device paths, which cannot mistake a 2.4 GHz receiver
  for a Bluetooth link because such a receiver also exposes a non-Bluetooth interface. A
  game controller of these vendors on a Bluetooth path counts on its own, so another device
  of the same vendor on USB - a Microsoft mouse or keyboard - no longer switches that
  detection off for Xbox pads.
- Windows.Gaming.Input is now asked only for controllers Windows can hand out as a
  **Gamepad**. `RawGameControllers` lists wheels, flight sticks and other controllers XInput
  cannot see as well, and they were being matched against the XInput slots they do not have.
- A controller that Windows.Gaming.Input cannot place on Bluetooth, because its device path
  carries no Bluetooth service guid, still showed a second icon: "Xbox controller: 10%" (the
  unusable remain=100 against full=1000) next to the correct "Xbox Wireless Controller: 77%"
  from Windows' own Bluetooth battery. A Bluetooth gamepad of the same device family now
  counts as the same device, compared exactly so the "Xbox controller 1"/"Xbox controller 2"
  names of two controllers cannot collapse into one icon. Reported by a reader on Reddit.
- **The MCHOSE G7 request goes only to the G7.** `0xA8A5` is a chip maker's vendor id
  ("YJX-CHIP") rather than a model, so other devices can sit behind it - and the G7's output
  report was written to the `0xFF01` collection of any of them. `G7_PID` was defined but never
  checked. Devices on that vendor id that are not the G7 are now left alone completely.
- **The Audeze sequence goes only to known Maxwells, and only to a vendor collection.** The 14
  packets were written to any device with vendor `0x3329` (`KNOWN` was only used to pick the
  display name), and on a device that has no `0xFF13` collection they went to *every* collection
  it exposes, including the consumer-control and telephony pages that answer nothing at all.
  Only the product ids in `KNOWN` are talked to now, and the sequence only ever goes to a vendor
  collection; a known Maxwell without one gets an icon and nothing is written to it.
- **The Audeze docstring no longer claims the sequence writes nothing.** It said the byte that
  marks a write (`0x00` or `0x82`) never appears in the sequence, but the seventh packet -
  `06 07 00 05 5A 03 00 07 1C` - has `0x00` there. That packet is HeadsetControl's own, byte for
  byte (`lib/devices/audeze_maxwell.hpp`, `UNIQUE_REQUESTS`), so either that byte is not a
  read/write marker or that packet is not a plain read: the claim was asserted rather than
  measured and is withdrawn. Reported by @ahmedkhursheed23 in
  [#62](https://github.com/HeyOkay/HaloBattery/issues/62).
- **Two Logitech receivers of the same kind** (any two Unifying receivers share
  `0xC52B`, and Lightspeed receivers share ids too) were merged into one group, so the
  second receiver's interface paths overwrote the first one's and the devices paired to
  the first receiver were never read. Receivers are now grouped by product id *and*
  device instance, which the HID path carries; two receivers of the same kind say which
  one they are in the diagnostics, and their devices get keys of their own. A single
  receiver keeps the plain keys, so existing icons do not move.
- **The receiver instance key stops short of the collection number.** One interface numbers
  its collections in the last part of that path segment (`Col01` ends in `&0000`, `Col02` in
  `&0001`), so keeping the whole segment split a single receiver into two groups: the group
  holding only the long-report collection was polled without the short-report one, the empty
  slots' error reports could therefore never be read, every slot burnt its full timeout and
  was then pinged with the short one ever after - about 11 s for the first poll and 3.3 s for
  each one after, on a receiver with a single paired mouse, with slots 2-6 wrongly reported
  as "no answer (asleep or off)". Reported by @ahmedkhursheed23 with hardware measurements,
  and reproduced here on a `046D:C539` receiver: 11.08 s -> 1.06 s and 3.33 s -> 0.34 s.
  The rest of the segment comes from the interface, so two receivers still differ.
- **The module compiles without an invalid-escape warning.** The `_instance` docstring shows
  a Windows device path and was not a raw string, so Python 3.12 warned about `\H`.
- The PlayStation provider waited out its full 1.5 s window on *every* collection a
  controller exposes, so a DualShock 4 or DualSense that answered on none of its audio,
  touch or sensor collections held the poll loop for up to 6 s and delayed every other
  device's update behind it (measured: 6.0 s with four silent collections, 4.5 s when
  the gamepad collection was listed third). The gamepad collection - the one that
  carries the battery - is now tried first, and one controller may cost the poll at most
  2.5 s in total, after which the remaining collections are skipped and the diagnostics
  say so.
- A controller that is connected but whose battery can never be read - another app such as
  DS4Windows or HidHide holding the device, so every attempt to open it fails - kept the app's
  fast re-check running indefinitely: that path shortens the poll interval for *every* provider
  to 3 s, not just this one, so it never got cheaper. The fast path is now given up 120 s after
  a reading first goes missing, with the reason in the diagnostics; the icon stays, the poll
  cost does not.
- **A DualShock 4 wireless adapter with no controller on it no longer shows a 0% icon.** The
  adapter (`054C:0BA0`) streams report `01` whether or not a controller is paired with it, and
  fills the battery field with zeros in that case - which was read as a real 0% reading, and at
  0% the app also raises its low-battery alert, for a controller that is not there. Bit 2 of
  `status[1]` is the adapter's "no controller connected" flag: the Linux driver calls it
  `DS4_STATUS1_DONGLE_STATE` and treats it as not connected (`hid-playstation.c`; `hid-sony.c`
  has no DualShock 4 or dongle code any more). The adapter is now left without a reading when
  that bit is set, and the diagnostics say so. Reported by @ahmedkhursheed23 in
  [#62](https://github.com/HeyOkay/HaloBattery/issues/62).
- The menu header of a device said "No devices found" instead of the device and its
  level. pystray builds the Windows menu once, before the first reading; the menu is
  now rebuilt when the device's text changes.
- `--probe` now always lists every HID device it can see. The list was only printed when
  the poll found nothing at all, so on a machine whose other devices answered, a device
  that no provider sees - the case the list exists for - never appeared (found while
  answering #21).

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

[Unreleased]: ../../compare/v1.13.0...HEAD
[1.13.0]: ../../compare/v1.12.0...v1.13.0
[1.12.0]: ../../compare/v1.11.0...v1.12.0
[1.11.0]: ../../compare/v1.10.1...v1.11.0
[1.10.1]: ../../compare/v1.10.0...v1.10.1
[1.10.0]: ../../compare/v1.9.1...v1.10.0
[1.9.1]: ../../compare/v1.9.0...v1.9.1
[1.9.0]: ../../compare/v1.8.0...v1.9.0
[1.8.0]: ../../releases/tag/v1.8.0
