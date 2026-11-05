# Protocols

How each device's battery level is read - one section per device, linked from the support table in
the [README](../README.md#supported-devices). These are the implementation notes: what is sent,
what comes back, and where each protocol was taken from.

## Headsets

### Astro A50 Gen 5 (Logitech 046D:0B1C)

**Connection:** Base station

The station's own vendor collection (usage page 0xFF32 / usage 0x0074): 64-byte reports, report id 0x02, `02 0c <len> 00 <cmd> <handle>`. Battery command 0x06 answers with the level in byte 6 and byte 8 set while the headset sits on the dock (charging). The protocol is HeadsetControl's, reverse-engineered from G HUB captures and verified on this USB id; it is neither HID++ nor the A50 X's Centurion protocol. **Unverified** - no A50 was on hand, so a level out of 0..100 is refused rather than shown

### Audeze Maxwell

**Connection:** 2.4 GHz dongle (3329:4B19) and USB-C cable (3329:4B1A)

The vendor collection (usage page 0xFF13): the sequence HeadsetControl uses, whose answer carries the battery as attribute 0x0CD6 (`05 5D <len> 00 D6 0C <percent>`). One packet is enough — the query for that attribute is answered with the marker on its own (0.19 s against 1.48 s for the whole sequence) — with the full sequence as the fallback. Dongle and cable are one headset and share one icon; charging is inferred from the cable answering, so the icon breathes while it sits on USB-C. With the headset switched off the dongle keeps answering with the last value it held, so the provider goes by the dongle's own product string — `"Audeze Maxwell Dongle"` with no headset linked, `"Audeze Maxwell HID"` with one — and reports nothing: the icon leaves the tray the way any switched-off device does

### Corsair Void Elite Wireless

**Connection:** 2.4 GHz receiver (1b1c:0a55)

The older Void exchange, from HeadsetControl's Void device: the battery collection is the vendor page `ffc5:0001` (interface 3), and a single output report asks for it - `c9 64` - with the answer arriving as a five-byte input report `64 00 <level> b1 <status>`. Bit 7 of the level byte is the microphone-lifted flag and is masked off; the status byte is 0 when the receiver has no headset on it, 1 normal, 2 low, 4 or 5 charging. No session and no framing are involved, unlike the family above. **Unverified** - read from HeadsetControl, a test build is out for [#330](https://github.com/HeyOkay/HaloBattery/issues/330).

### Corsair Void v2 Wireless, Virtuoso Max Wireless, HS80 Max Wireless

**Connection:** Wireless receiver (1B1C:2A08, 1B1C:2A02, 1B1C:0A97)

The receiver's vendor collection on interface 4: 65-byte writes `00 02 <endpoint> 02 <cmd>`, endpoint 0x08 the receiver and 0x09 the headset, and 64-byte replies. After a minimal wake handshake (the one HeadsetControl uses, which avoids the pop of switching the headset into software mode), battery command 0x0F answers with a 16-bit value in hundredths of a percent at bytes 4-5. **Unverified** - no Corsair headset was on hand; the receiver sometimes answers with something other than a level, so that is retried and then refused rather than shown

### Corsair Virtuoso RGB Wireless SE / XT

**Connection:** Slipstream receiver or USB cable - SE `1B1C:0A3E` receiver / `1B1C:0A3D` cable / `1B1C:0A40` the newer Slipstream Multi-Device receiver (#204); XT `1B1C:0A64` receiver / `1B1C:0A62` cable

The routed exchange the Dark Core row describes, framed on report `0x02`: the request is a 64-byte report - `02 <target> <command> <property>`, target `0x09` the headset behind a receiver, `0x08` the device itself, command `0x02` "get property" - answered on report `0x01` with `01 01 <command echo> <status> <value:4 LE>` (`00` ok, `05` no such property, `09` refused). Property `0x0F` is the level in tenths of a percent and `0x10` the charge state (1 = charging). The wired ids read with target `0x08` first and the receivers with `0x09`, each with the other as the fallback; reads need neither a session nor software mode (HeadsetControl notes only writes need software mode), and the headset's unsolicited volume reports (report id `0x0E`) are not replies and are skipped. On Windows the frame goes out bare - the 64 bytes with `0x02` first; every report-id-prefixed form is refused with `0x57` (#204, measured on the `0A40`). One icon covers a product's receiver and cable ids together, and a reading that reports charging wins the merge. Source: HeadsetControl's reworked Virtuoso XT/SE device ([#570](https://github.com/Sapd/HeadsetControl/pull/570)), with its four XT/SE ids run on its author's hardware per the #568/#570 discussion, plus the `0A40`'s end-to-end measurement with @jeffpeng3's probe runs (#204: 96 % in both states, charge `0x10` = 1 on the cable). **Confirmed in the app on the `0A40`** - @jeffpeng3's run read 97 % over the receiver (the level frame `01 01 02 00 ca 03` twice, identical) together with the charge state; the four XT/SE ids have not answered a HaloBattery build yet and stay marked unverified

### Endgame Gear XM2w 4K, OP1w 4K (4K dongle)

**Connection:** the 4K dongle, "Endgame Gear HS Dongle" (`3367:1970`)

One dongle id serves several mice - the XM2w 4K, the OP1w 4K and their v2 revisions - and the adapter itself stays neutral; the vendor software resolves which mouse is behind it. The exchange is the family's 17-byte frame, documented for the XM2we by okms/xm2we-linux and by Janlhin/Endgame_Gear_Web_Configurator: a feature report `0x08` carrying `<opcode> 00 ...` with a `0x55 - sum` checksum, answered by an input report `0x09` whose data starts at byte 6 and whose bytes sum to `0x55`. Battery is opcode `0x04` - `data[0]` the percentage, `data[1]` the charging flag - and it is read before the link state because plugging the cable drops the link for a moment while the level stays valid. The dongle's two collections are paired by the receiver part of their HID paths: the feature collection is `ff02:0002`, the replies arrive on an `ff01` collection. Only the two documented status reads are built; nothing is written to the mouse. **Unverified** on this dongle - the frame is the XM2we's, and a test build is out for [#324](https://github.com/HeyOkay/HaloBattery/issues/324).

### HyperX Cloud Alpha 2

**Connection:** 2.4 GHz station (03F0:08BE - the station's other function, 03F0:0ABE, is the audio-only "Chat" half and has nothing to read)

The battery collection is picked by usage page, 0xFF13:0xFF00. A 64-byte output report `50 02 00 00 ...` is answered by the input report starting `51 02`: the level is index 2 as a plain percentage, and bit 7 of index 6 was set after a recharge and clear before it, so it is shown as charging. Both were taken from two USBPcap captures of NGENUITY attached to issue #26, at 51 % and 67 %, and matched to the percentage NGENUITY displayed at the time; the `61 02` frame is byte-identical across the two states, so it is not the battery, and a level above 100 is refused rather than shown. **Confirmed on real hardware** by @azizen12 - the percentage was right on the station's first test. With NGENUITY running the station also emits thousands of `ff 01` / `44`-`45` housekeeping frames (its diagnostics showed the app reading only those); stale reports are therefore drained before the request and the reply is read until it arrives, so the two applications can run side by side - both states confirmed by @azizen12 on the final build. Credit for the captures: @azizen12.

### HyperX Cloud II Core Wireless

**Connection:** 2.4 GHz dongle (03F0:0995) or its second mode (03F0:0795)

The same `66 <cmd>` 62-byte exchange as the Cloud III Wireless row, from @dantifrice's USBPcap capture of NGENUITY in [#155](https://github.com/HeyOkay/HaloBattery/issues/155): NGENUITY wrote `66 89` to interface 3 as an output report and the dongle answered `66 89 0e d7 30 ...` (48 % - the level at byte 4, bytes 2-3 non-zero, as the reference parses it), and `66 8a` was answered `66 8a 00 00` (off the cable). **Verified on hardware** in the reporter's run: the icon read the level, and while charging both ids answer at once with the same reading (0995 and 0795) - they share one icon, with the charging reading winning

### HyperX Cloud II Wireless

**Connection:** 2.4 GHz dongle (03F0:0696, and 03F0:018B on the newer dongle revision)

The vendor collection, picked by usage page rather than position (0xFF90:0x0303): the dongle carries four collections on the one interface, so the first one is not the right one. A 52-byte output report `06 ff bb <command> 00` is answered by 20 bytes echoing the command: command 0x02 carries the level in byte 7 (voltage in bytes 5-6), command 0x03 reports charging in byte 4 - the same exchange HeadsetControl uses for these two product ids. **Unverified** - no Cloud II Wireless was on hand, so a reply that does not echo the command is ignored and a level above 100 refused rather than shown

### HyperX Cloud III S Wireless

**Connection:** 2.4 GHz dongle (03F0:02CC, and 03F0:06BE which the reference also lists)

Another protocol again (LennardKittner/HyperHeadset's `cloud_iii_s_wireless`, and NGENUITY's USB traffic from HyperHeadset #36): a 64-byte *output* report `0c 02 03 01 00 <cmd>` (byte 3 = 01 only reads; the dongle ignores the same bytes as a feature report), command 0x06 for the battery and 0x48 for charging; the answer is input report 0x0C with the command in byte 5 and the value in byte 6 (0xFF = no value). The request goes to the collection that takes output report 0x0C, and all the dongle's collections are read for the answer. **Verified on hardware** in #106 (02CC): 89 %, the same level as NGENUITY; and in #156 (06BE), the level and the charging state

### HyperX Cloud III Wireless

**Connection:** 2.4 GHz dongle (03F0:05B7, and 03F0:0C9D which the reference also lists)

A different protocol from the Cloud II: the vendor collection is `0xFF13:0x0001`, a 62-byte `66 <command> 00 ...` packet goes out (0x66 is the report id) and 62 bytes come back whose second byte is either the command echo or the matching response id - `0x89`/`0x0D` carries the level in byte 4 (only when byte 2 or 3 is non-zero), `0x8A`/`0x0C` carries charging in byte 2 (0 not charging, 1 charging, 2 fully charged). Taken from LennardKittner/HyperHeadset, which lists this product id; that project also documents that some HyperX dongles accept these packets as **feature reports** only on Windows (`write()` fails with "Incorrect function" and the packet is retried with `send_feature_report`, which the diagnostics say either way). **Unverified on hardware** - no Cloud III Wireless was available here

### JBL Quantum 910 Wireless

**Connection:** 2.4 GHz dongle (0ECB:2088)

The receiver exposes one vendor collection, `ff13:0001` (interface 5 on a real unit), and the headset pushes its own reports there; there is no request to send. The battery arrives as report 0x08 with the level in byte 1 - the pattern plugato/JBL_Baterry_Monitor confirmed on this USB id - and report 0x2f is the microphone mute state. The headset can stay quiet for long stretches, so the last level heard is kept and shown greyed out until something arrives. **Confirmed on a real unit**: the provider finds the receiver, matches its collection and shows the level as soon as the headset reports. That report is an event - on the unit tested it arrives when the headset is plugged into its charger (a capture read 95% at the moment JBL's own app said 95%), and pressing the buttons or the volume rocker does not produce it - so the last level heard is kept and shown greyed out until the next one. The receiver also pushes a power report (0x09 - byte 1 is `0x00` when the headset is switched off and `0x01` when it is on, confirmed on a real unit - plus a 0x02 frame); none of them is a level, and a headset last seen switched off says so in the diagnostics rather than only "nothing heard"

### Logitech G PRO X 2 LIGHTSPEED

**Connection:** 2.4 GHz receiver (046D:0AF7)

Not HID++ on ff43: Logitech's "Centurion" transport on the vendor collection `ffa0:0001`, report 0x51. The app lists the receiver's features, reaches the headset through the receiver's bridge feature (0x0003), lists the headset's features and reads its battery feature 0x0104 (percent, charging state), all with read-only functions. From Solaar (tested on this headset) and HeadsetControl; firmware without 0x0104 gets HeadsetControl's fixed request. Confirmed on hardware in #103 (75 %, level and charging equal to G HUB)

The PRO X 3 LIGHTSPEED (`046D:0B45`) lists the same three collections on the same interface in a reporter's diagnostics, so it is read the same way. **Unverified** - no PRO X 3 was on hand; a test build for [#343](https://github.com/HeyOkay/HaloBattery/issues/343) is out.
### Plantronics Voyager 8200 UC (BT600 adapter)

**Connection:** the headset's Bluetooth link through the BT600 USB adapter (`047f:02ee`, `047f:02f7`)

The adapter is a Bluetooth relay rather than a vendor dongle, and its settings live on the vendor collection `ffa2:0003` (interface 3), tunnelled in HID report `0x07`: `07 01 01 10 <len> <addr> 00 00 <op> <grp> <item> ...`, where `0x00` addresses the adapter and `0x20` the headset, and replies carry `0x00` or `0x02`. Nothing answers before the attach sequence - a two-byte output report `06 01`, a HELLO to the adapter, then a HELLO to the headset (`op 0x01`, group/item `01/02`, acked with `op 0x08`); it is idempotent, so it is replayed before every read, and the catalogue frames that follow each ack are skipped by the reply matcher. The battery is `GET 0A 1A`, answered by `op 0x03 0A 1A <level> <count> <charging> <minutes hi> <minutes lo> 01`; the percentage is Poly's own `level / (count - 1) * 100` (the count includes 0, so `0x0B` is ten steps), and the minutes are the remaining talk time, not the level. Only the battery is read - no setting is written. From HeadsetControl's `plantronics_bt600.hpp`. **Unverified** - the adapter id is the one from the report, a test build is out for [#328](https://github.com/HeyOkay/HaloBattery/issues/328).

### Razer Barracuda Pro (2.4 GHz)

**Connection:** 2.4 GHz dongle (1532:053a)

Its receiver publishes two collections and neither answers the standard Razer mouse request this app sends. The headset speaks the "PA" protocol instead: 64-byte vendor frames, `P`,`A` out and `P`,`I` back, battery command `0x21` with the level in the reply's data byte, charging `0x2A`. Decoded from a USBPcap capture of Razer Synapse on a real unit (it read 34%), then confirmed on hardware with @phl23's own Barracuda Pro: the level tracks (27% at the test), charging follows the charger, and a switched-off headset reports "no link" rather than a stale value. Over Bluetooth Windows reports the level itself.

### Razer BlackShark V2 Pro (2020)

**Connection:** 2.4 GHz receiver (1532:0528)

Uses a 64-byte feature report `0xFF` on usage page `0xFF00`, with request
`FF 0A 00 FD 04 12 F1 02 05`. Reads the device-reported level and power state.
Observed states are on battery (`0x01`), low battery (`0x02`), then very low battery (`0x03`),
charging (`0x09`) and charge complete (`0x06`). An unknown state keeps a fresh
battery reading visible and marks the charging state as unknown. Stale replies
are rejected. The charging cable (1532:052E) is skipped to keep one icon;
the receiver must remain plugged in.

**The device-reported battery percentage is imprecise and should be treated as
a rough indication of charge, not an exact measurement.** Observed discharge
levels were 100 -> 80 -> 50 -> 30 -> 10, with no intermediate values.
The full set of levels and their thresholds have not been established.
Connecting the charger produced an immediate jump from 10 to 80, so the reported
percentage during charging is unreliable. At charge completion the receiver
reported 100, which remained 100 after unplugging. The application currently
hides the unreliable level during active charging and shows the charging state.
On battery and after charge completion, it displays the reported level unchanged.

### Razer BlackShark V2 Pro (2023)

**Connection:** 2.4 GHz receiver (1532:0555)

The headset's own "PA" protocol: output reports 0x02 on the vendor interface 0xFF00, remote mode 0xE1, commands 0x21 (battery) and 0x2A (charging)

### Sony INZONE H9

**Connection:** the headset's USB dongle (`054C:0E53`), which exposes a serial (COM) port

Read over that COM port at 115200 baud rather than HID: the dongle answers a fixed exchange - two initialisation frames, then a status query - and replies with a 14-byte frame starting `04 FF 0B 00 96 C3 14 04`, with the level in byte 12 and the charging flag in byte 11 (1 = charging). The port is opened with DTR and RTS raised, and Sony's own app (INZONE Hub) has to be closed because it claims the same port.

The frame sequence and the reply signature come from [janit88/InZoneH9Tray](https://github.com/janit88/InZoneH9Tray) (`inzone_tray.py`), contributed for both the white and the black version in [#278](https://github.com/HeyOkay/HaloBattery/pull/278) by @BoringMan314. It needs `pyserial`, which the released builds bundle; a source run gets it from `requirements.txt`, and the provider says so in the diagnostics when it is missing.

### SteelSeries Arctis and GameBuds (other models)

**Connection:** wireless base station or dongle

The same `b0` exchanges as the Nova 7 above, applied to the other Arctis Nova 7 and Nova 5 family models; and the older Arctis 1, 7, 9, Pro Wireless, the Arctis 7+ (nine-step level) and the GameBuds (lower earbud level) exactly as HeadsetControl documents their requests. The Nova Pro Wireless stations (`1038:12E0`, `1038:12E5`) are handled too. The models in the support table above are the confirmed ones. The Nova 3P Wireless joined them in [#149](https://github.com/HeyOkay/HaloBattery/issues/149): 58 % on @Pekish992's unit, read with this same exchange

### SteelSeries Arctis Nova 7

**Connection:** 2.4 GHz dongle (1038:22A1)

Output report `00 b0` on interface 3 (usage page 0xFFC0); the reply carries the level and the status (off / charging / on battery), as documented by HeadsetControl. Works alongside SteelSeries GG. The other Nova 7 variants and the Nova 5 / 5X use the same request and are included, but not tested

### SteelSeries Arctis Nova Elite

**Connection:** Wireless base station, interface 3 (`1038:2244`)

A read-only status request `01 b0` (a 64-byte output report, report id `01`) goes to the vendor collection of interface 3 that takes it (`0xFFC0` first; the other one refuses the report id); the station answers with a direct `01 b0` reply (headset level in byte 6, power state in byte 14, charging in byte 15, the layout loteran/Arctis-Sound-Manager takes from SteelSeries GG's own description of the station) or with `07` frames, which can arrive on the other collection, so both are read. `07 b7` carries the headset level in byte 2 and charging in byte 4 (`02` charging, `08` on battery); `07 b5` carries the power state in byte 4 (`01` off, `02` cable charging, `04` standby, `08` online). A headset reported as off (power code `01`) shows nothing, and the spare battery level in byte 3 is not shown. A `07 b7` that the station sends by itself is read as well. Exchange from elegos/Linux-Arctis-Manager (made from a USB capture of SteelSeries GG on Windows), the same in loteran/Arctis-Sound-Manager. **Level and charging verified on hardware** in #138: the level matches SteelSeries GG (31 % at the first test, from the direct reply) and the charging animation follows the charger (build 1.12.0.7). A switched-off headset shows 0 %, as SteelSeries GG also does in its tray: on this station the off state does not arrive as power code `01`, so this is parity with the vendor rather than a gap. A level above 100 is refused rather than shown. The other ids of this station (`2246`, `2249`, `2270`) are not included yet

### SteelSeries Arctis Nova Pro Wireless (`1038:12E0`, `1038:12E5` X)

**Connection:** Wireless base station, interface 3 or 4

The same `b0` exchange as the other Nova headsets, asked for with report id `06` instead of `00`, as HeadsetControl asks for these base stations: the level is a nine-step code in byte 6 (shown as "about NN%") and the state in byte 15 (`01` off / out of range, `02` cable charging, `08` on battery). A reply with any other state byte, a level code above 8 or fewer than 16 bytes is refused rather than shown. The request goes only to a vendor collection of these two ids; the diagnostics in #41 show `1038:12e5` exposing `ffc0:0001` on interface 4 (plus a second vendor collection `ff00:0001` there), so both are tried and the answering one is remembered. The reply layout is HeadsetControl's - not yet seen on hardware here

## Mice

### AM Infinity 8K (Angry Miao)

**Connection:** 2.4 GHz receiver (3151:5007)

The AJAZZ Control Center project's AJ-series exchange: a zero-payload `0xF7` status poll brings the receiver's 2.4G telemetry up, then the charge reads from status report `0x05` (`05 00 00 64 01 01 01 02`, charge at byte 3 on Windows). A zero charge or junk bytes are shown as no level rather than a wrong value, and no charging state is reported. **Unverified** - no AM Infinity was on hand; the exchange is confirmed on the reference project's own unit, so the layout stands until the reporter of #72 confirms it

### ASUS ROG Gladius III Aimpoint and other ROG / TUF wireless mice

**Connection:** 2.4 GHz receiver or USB cable (for example 0B05:1A72)

The battery command G-Helper uses: output report 0 `12 07` (65 bytes) on the vendor collection of interface 0, answered by a report echoing `12 07` with the battery in byte 5 (a percentage, or a level 0-4 on older models such as the Chakram and Keris Wireless) and charging in byte 10. 0 without charging is standby, not empty, so it shows nothing; `ff aa` (command not known) and an all-zero reply are not read as a level. Receiver and cable share one icon. The OMNI receiver and models with other layouts are not included. **Unverified** - no ASUS mouse was on hand

### ASUS ROG OMNI receiver (mice and keyboards)

**Connection:** ROG OMNI receiver (0B05:1ACE), vendor collections on interface 2

The receiver holds a mouse and a keyboard and names both in a pair list, which is what lets one icon follow a device between the receiver and its own cable:

    ff02:0001 report 1   01 a0 00 00 ...   pair list from byte 5: four bytes a slot,
                                           the device pid little-endian, 0 ends
    ff01:0003 report 3   03 12 07          the mouse; the reply is the `12 07` battery
                                           frame one byte later (level byte 5, charging 10)
    ff00:0002 report 2   02 12 01          the keyboard; battery in byte 6 (byte 11 on
                                           the Falchion family, where byte 6 is a 0..10
                                           gauge) and charging in byte 9

A reply of `ff aa` means the device does not know the command (a keyboard asleep on battery answers this), and an all-zero reply or a level of 0 without charging is standby: no reading rather than an empty battery. Ids absent from the pair list are skipped, as G-Helper does; the collections of one receiver are matched by the device instance in their path, so two receivers do not mix.

Confirmed on a Harpe Ace Mini and a Falchion RX Low Profile, and on a Keris II Ace and an Azoth by @comipa in [#231](https://github.com/HeyOkay/HaloBattery/issues/231).

### Corsair Dark Core RGB Pro SE

**Connection:** 2.4 GHz dongle (1B1C:1B7F)

The newer routed exchange that ckb-next calls "bragi" and OpenLinkHub "slipstream": 64-byte frames behind report id 0, route `0x09` asks the mouse behind the receiver (the receiver itself is `0x08`), command `0x02` "get property", property `0x0F` battery. The answer `01 02 00 <value:2 LE> ...` carries the level in tenths of a percent (`26 02` = 550 = 55 %). Frames that are not exactly that shape - the receiver's device records, its notices - are not levels. The M75 Wireless (`1B1C:1BDC`, the "SLIPSTREAM WIRELESS USB Receiver") answers the same exchange and is confirmed on hardware by @SpaceJesusCosmic in #301. The request and the answer were captured from the reporter's dongle in #56 and the exchange is confirmed on hardware: KKiruano's test build run showed the same level SignalRGB does. Because the answer echoes the command and not the property, a second app on the same dongle (iCUE, SignalRGB) can put an answer for another property on the channel; the provider asks twice and takes only two equal answers, waits in a time window (traffic frames end a read early) and holds a per-dongle budget. No charging state is reported. The wired id 1B1C:1B7E is left out.

### G-Wolves HSK Pro ACE and the other models with a receiver of their own

**Connection:** the model's own receiver (e.g. 33E4:5803 for the HSK Pro ACE, #105) or USB cable

HSK Pro / Plus / Lite, HSK Plus ACE, HTX, HTX ACE, HTX Mini, HTS Plus, HTS Plus ACE, HTS Ultra, HTR, HTR Pro, HT-S2, HT-S2 Pro, Fenrir, Fenrir Max, VUK; HTM Plus, HSK Pro 2.0, HTXU, Fenrir Pro. The ids and the exchange per model come from G-Wolves' web driver list (mouse.xyz, `Config/env-models.json`). Models with "IsNewProtocol" 0 use the web driver's getOldBattery: feature report `00 02 8f 01` (00 on the cable), reply `a1 02 8f .. <charging> <battery %>`; the others use the exchange of the 8K receiver below. Same 64-byte feature report collection; receiver and cable of one model share an icon. **Unverified** - no such mouse was on hand

### G-Wolves WARG, HTS Plus (Pro), HTXU, Lycan, Fenrir Pro / Asym, HTX Mini

**Connection:** 8K receiver or USB cable

The same feature report exchange as the WLmouse mice (`00 00 02 02 00 83` out, `a1 ... 83 <charging> <battery%>` back), which G-Wolves' web driver mouse.xyz uses for every mouse on this receiver. The request goes only to the collection that Windows reports with a 64-byte feature report (HidP_GetCaps), as the web driver chooses it. A sleeping mouse keeps its last value on a greyed icon. **Unverified** - no G-Wolves mouse was on hand

### Hitscan Hyperlight

**Connection:** 2.4 GHz receiver (3770:0200) or USB cable (3770:0100)

The same 17-byte frames as the Pulsar / ATK / VXE row, on the vendor collection `ff02:0002`: [sopparus/hitscan-battery](https://github.com/sopparus/hitscan-battery) mapped them from USB captures of Hitscan Utility 1.0.2 (command 0x04, level in byte 6, charging in byte 7, millivolts in bytes 8-9) and reads them on Linux. Note the vendor application's own battery indicator is broken - it showed 100 % while the device answered 75 - so the raw byte is the truth. **Unverified** here: no Hyperlight was on hand, [so #105's reporter confirming the level](https://github.com/HeyOkay/HaloBattery/issues/105) would settle it

### HyperX Pulsefire Haste 2 Wireless (NGENUITY protocol)

**Connection:** 2.4 GHz dongle (03F0:0D97 and 03F0:0F98), vendor collection on interface 2 with usage page 0xFF00, 64-byte packets

NGENUITY's "universal" HID protocol, from [PMarinov1994/ngenuity-linux-cli](https://github.com/PMarinov1994/ngenuity-linux-cli), reverse engineered from NGENUITY 3.1.0's communication hub and confirmed on a Pulsefire Haste 2 Wireless:

    request   50 02                 report id = command byte, then the sub-command, zero padded to 64
    reply     51 02                 id = command + 1, byte 1 = sub-command
              [2]    level 0..100
              [3]    charge status: 0 not charging, 1 charging, 2 full, 3 charger error, FF unknown
              [4..5] temperature, i16 LE (degrees C)
              [6..7] voltage, u16 LE (mV)
              [8]    gauge chip type, [9] charger chip type
    link      52 01 -> 53 01 <connected>, 0 = asleep or off

Packets are paced at least 10 ms apart, as NGENUITY does. A full battery on the charger (status 2) counts as charging; a charger error and an unknown status still show the level but suppress the charging state and its alerts. A sleeping mouse keeps its last reading greyed for a while, the way the Finalmouse provider does.

Claimed from [#304](https://github.com/HeyOkay/HaloBattery/issues/304)'s collection list - `03F0:0D97` publishes exactly `ff00:0001` on interface 2 - and the reference project's dongle is `03F0:0F98`, while `0x0D97` appears in the device-information reply of that hardware. **Unverified on this app's side** until the reporter runs the test build.

### LAMZU Maya X

**Connection:** 8K dongle (373E:001E) or USB cable (373E:001C)

The same feature report exchange as the WLmouse and G-Wolves mice (`00 00 02 02 00 83` out, `a1 00 02 02 00 83 <charging> <battery %>` back), on the vendor collection `ffff:0000` of interface 2 only; protocol from Sheroune/lamzu-battery-monitory (MIT). Confirmed on a Maya X on its 8K dongle

### MCHOSE A5 Pro Max

**Connection:** 1K receiver (2023:F013), 4K receiver (2023:F015) or USB cable (2023:F019)

A third MCHOSE exchange, on a third vendor id: 64-byte feature reports on the vendor collection (usage page `0xFFFF`, report id 0), from the community web driver [z750sasr/mchose-a5-pro-max-web-driver](https://github.com/z750sasr/mchose-a5-pro-max-web-driver) - the same route/length/page/command framing for every command, battery on page `0x00` command `0x83`, whose reply carries the `0xA1` marker, echoes page and command, and holds the charging state in byte 6 and the level in byte 7 (clamped to 100, as the reference clamps it). The transport is corroborated by [Klegus/mchose-macos](https://github.com/Klegus/mchose-macos), an independent reverse-engineering of the same vendor id's driver. **Confirmed on hardware** (@Pekish992, [#149](https://github.com/HeyOkay/HaloBattery/issues/149)): his 1K receiver answered 22 % and then 25 % while charging, matching the MCHOSE software and its charging animation; the 4K receiver and cable ids are still untried

### MCHOSE A7 V2 Ultra

**Connection:** 2.4 GHz receiver (3837:100B, RealTek strings)

The same protocol as the M7 Ultra on MCHOSE's newer vendor id, which the reference driver treats identically: the diagnostics in [#4](https://github.com/HeyOkay/HaloBattery/issues/4) show the same interface shape (collections 0xFF0B:0x104 and 0xFF01:0x01 on interface 2). The status read is documented on the shorter 0x11 report, so both report ids are tried, and the mouse is named from the receiver's own product string. Icon of its own, so it and an M7 Ultra stay two devices. **Unverified** — no A7 V2 Ultra was on hand, so a level out of range is refused rather than shown

### MCHOSE G7

**Connection:** USB (A8A5:2255, chip 'YJX-CHIP')

A different chip from the M7 Ultra, and a different protocol: a 65-byte output report `00 55 30 A5 0B 2E 01 01 01`, answered by an input report starting `AA 30` whose byte 8 is the level and byte 9 the charging flag. Written from @kek353's own monitor and the device dump in [#8](https://github.com/HeyOkay/HaloBattery/issues/8); only its 0xFF01 vendor collection is written to. Confirmed on @kek353's G7, which answers `aa 30 a5 0b 0a 01 01 01 2e 00 00 00` — 46%, not charging, the same level their own tool shows — and again on its cable (`aa 30 a5 3c 0a 01 01 01 2e 01 00 00`, byte 9 = 1 while charging) with the PID unchanged, so a G7 keeps one icon on the dongle or on the cable. Only the `AA 30` header is relied on: byte 3 differs between the two (`0x0b` against `0x3c`). A level out of range is refused rather than shown

### MCHOSE M7 Ultra

**Connection:** 2.4 GHz receiver (5253:1020)

The vendor collection (usage page 0xFF01; the sibling 0xFF0B never answers): feature report 0x11 (the shorter report, tried first) or 0x12 with command 0x06, every payload byte inverted, which returns `53 52 31 00 02 05 07 00 09 64 00 64` — vid 0x5253, model 0x31, firmware, flags, then the level and the charging byte (1 while charging). The request has to be repeated for each read, and the receiver only relays a real value while the mouse is awake: asleep it answers with zeros, so a silent mouse keeps its last level on a greyed icon. On the cable the mouse answers on its own PID (5253:0031, the number it reports as its model id) and the receiver goes quiet: both connections share one icon, and whichever one reports charging wins

### Pulsar X2 V2 Mini, Pulsar X2 CrazyLight Mini, ATK VXE R1 SE+, VXE R1 Pro Max

**Connection:** 2.4 GHz dongle (3554:F508, 373B:1085, 3554:F58A, 3554:F505, 3710:5406) and USB cable (3554:F507, 3554:F58F, 3554:F58C, 3554:F506, 3710:3414)

17-byte big-endian frames, report id 0x08: command 0x04 asks for the power details and answers with the level in byte 6, the on-cable flag in byte 7 and millivolts in bytes 8-9, with a checksum (0x55 minus the sum of the first 16 bytes) in byte 16. From andrewrabert/python-pulsar-mouse-tool, which also backs the "HID: pulsar" driver in review for Linux; the Kysona M600 and VXE Dragonfly R1 Pro use the same protocol but their ids are not claimed here. The ATK/VXE control panel of the OpenMouse project (@openmouse/protocol, drivers/atk) lists the R1 Pro Max receiver as 3554:f58a and reads it with the same command 0x04 frame, opening the collection with usage page 0xFF02 and usage 0x0002 - the R1 Pro Max dongle has five other interface-1 collections and the first of them is not the one that answers, so that collection is now preferred; a collection whose output report cannot carry the 17-byte frame is skipped, because Windows refuses that write and the refusal looks exactly like a device that is switched off. The framing is also in G-Wolves' own web driver (mouse.xyz), whose Compx class sends the same report-0x08 frame with a checksum of 0x55 minus the sum of the payload. The R1 Pro Max is confirmed on hardware on both of its transports, by the reporter of #87: this provider read the mouse on its stock 1 kHz receiver, on the `ff02:0002` collection, and on its cable the level agreed with ATK's own panel (hub.atk.pro) with the charging flag following the cable in both directions, so the cable id (3554:F58C) is read on hardware as well rather than only claimed from their report. A frame whose checksum does not match is refused rather than shown. The X2 CrazyLight Mini (#164) was tested by its reporter on both of its transports with this same frame - the dongle and the cable both answered command 0x04 with valid checksums, the cable reporting charging - so both of its ids (3710:5406, 3710:3414) are claimed from that report; the app-side reading was confirmed on their run of this branch in #164 - the dongle read 20 %, the same raw level as their own test, and the cable read while charging. VGN's Dragonfly 4K receiver (`3554:f505`) and the same mouse on its cable (`3554:f506`) follow the same exchange - the receiver lists six vendor pages on interface 1 with `ff02:0002` among them. The receiver is **confirmed on hardware by @TweaknFreak** (#323): the level it showed agreed with the vendor tool's 53 %, so the id is claimed from a working read rather than from the collection shape; the cable id stays claimed from the report until someone runs it with the mouse plugged in.
17-byte big-endian frames, report id 0x08: command 0x04 asks for the power details and answers with the level in byte 6, the on-cable flag in byte 7 and millivolts in bytes 8-9, with a checksum (0x55 minus the sum of the first 16 bytes) in byte 16. From andrewrabert/python-pulsar-mouse-tool, which also backs the "HID: pulsar" driver in review for Linux; the Kysona M600 and VXE Dragonfly R1 Pro use the same protocol but their ids are not claimed here. The ATK/VXE control panel of the OpenMouse project (@openmouse/protocol, drivers/atk) lists the R1 Pro Max receiver as 3554:f58a and reads it with the same command 0x04 frame, opening the collection with usage page 0xFF02 and usage 0x0002 - the R1 Pro Max dongle has five other interface-1 collections and the first of them is not the one that answers, so that collection is now preferred; a collection whose output report cannot carry the 17-byte frame is skipped, because Windows refuses that write and the refusal looks exactly like a device that is switched off. The framing is also in G-Wolves' own web driver (mouse.xyz), whose Compx class sends the same report-0x08 frame with a checksum of 0x55 minus the sum of the payload. The R1 Pro Max is confirmed on hardware on both of its transports, by the reporter of #87: this provider read the mouse on its stock 1 kHz receiver, on the `ff02:0002` collection, and on its cable the level agreed with ATK's own panel (hub.atk.pro) with the charging flag following the cable in both directions, so the cable id (3554:F58C) is read on hardware as well rather than only claimed from their report. A frame whose checksum does not match is refused rather than shown. The X2 CrazyLight Mini (#164) was tested by its reporter on both of its transports with this same frame - the dongle and the cable both answered command 0x04 with valid checksums, the cable reporting charging - so both of its ids (3710:5406, 3710:3414) are claimed from that report; the app-side reading was confirmed on their run of this branch in #164 - the dongle read 20 %, the same raw level as their own test, and the cable read while charging. The ATK X1 Pro 1K dongle (373B:101A, #230), the ATK Fire X 8K (373B:101B) and the Compx keyboard receivers (3554:FA09 - seen on the AULA F75 in #247 and the LEOBOG Hi75C Pro in #257) list the same ff02:0002 control collection and are read with the same frame; the receiver id is shared by those keyboards, so the tray name is the receiver's, not one model's. The Corsair Sabre v2 Pro Ultralight (1B1C:2B2A, #290) is the same exchange too:
its receiver publishes ff02:0002 and no ff42 at all, so it is read here rather than by
the bragi path. The Teevolution Terra Pro (3554:F523, #300) is the same exchange behind the 'RapidSync' product string, with its keyboard collection on interface 2. The ATK X1 Pro is confirmed on hardware by @gnrlk3nobi in #230: it read immediately, the level stayed within about 5 % of ATK HUB in both states (the two apps sample at different moments, so the sign flips), and the charging state followed the cable. The keyboard receiver and the Fire X 8K are still waiting for their testers.

### iRocks M31R

**Connection:** 2.4 GHz receiver (14A5:C009) and USB cable (14A5:C010)

The same 17-byte Compx frame (command 0x04, level byte 6, on-cable byte 7, checksum 0x55 minus the sum of bytes 0-15) over a **different transport**, measured on hardware by @jimchen5209 in #309:

- The request goes out as a **feature report** on `ff02:0002` (that collection has no output report); the reply arrives as an **input report** on `ff01:0000`, so both collections are opened.
- Replies may carry header **0x09** as well as 0x08; the checksum still counts the header. Bytes 8-9 are always zero on this model (no millivolt field).
- The receiver answers a power query from a **cache** while its mouse is off or asleep, so it is asked command **0x03** (connection status) first: only a valid answer with byte 2 == 0 and byte 6 == 1 means online, and only then is the battery read. Offline: no level is read, and the last online reading stays greyed for 300 s.
- The cable has no offline state and is never asked 0x03; a silent poll reports nothing for it.

Captured examples: `09 04 00 00 00 02 37 00 ... 0f` = 55 %, not charging; `09 04 00 00 00 02 4b 01 ... fa` = 75 %, charging; `09 03 00 00 00 01 01 ... 47` = online, `... 01 00 ... 48` = offline.

**Confirmed on hardware** by @jimchen5209 in #309, on the receiver and on the cable: the level matched the vendor app (80 %), charging showed 90 % and cleared to 85 % when unplugged, the sleeping receiver greyed out and came back, and with both transports connected the wired mouse was shown while the wireless one was greyed until the cable came out.

### Razer Basilisk V3 Pro, Razer Basilisk Ultimate

**Connection:** 2.4 GHz receiver

The standard Razer 90-byte feature report, as used by Synapse and OpenRazer: power class 0x07, commands 0x80 (battery) and 0x84 (charging)

### Razer DeathAdder V4 Pro

**Connection:** 2.4 GHz receiver (1532:00BF)

Same mice protocol: transaction id 0x1F, command class 0x07, command 0x80 answers `02 1f 00 00 00 02 07 80 00 ab`, and raw 0xAB = 171/255 = 67%, stable across polls and unchanged while Synapse runs. Idle, the mouse answers status 04 and keeps its last level on a greyed icon

### Razer wireless mice (other OpenRazer models)

**Connection:** 2.4 GHz receiver or USB cable

The same exchange as the Razer mice above: the 90-byte feature report, command class `0x07`, command `0x80`, transaction ids from OpenRazer's device entries. Pro Click, Pro Click V2 and V2 Vertical, Naga, Viper, DeathAdder, Basilisk, Mamba, Lancehead and others - `KNOWN` in `providers/razer.py` holds the full list. The **Viper V4 Pro** (`1532:00E5` on the cable, `1532:00E6` through the receiver, transaction id `0x1F`) is confirmed on hardware by @amlidos in #333: the level matches Synapse and the charging state follows the cable. Battery is only read for the models OpenRazer itself reads.

### Glorious Model O Wireless

**Connection:** 2.4 GHz receiver (258A:2022)

Sinowealth's framed battery query, documented and confirmed on hardware by Rodrigo-200/GloriousBatteryMonitor-Go (crediting AwesomeTy18/GloriousBatteryMonitor, glorious-mouse-battery-system-tray, glorious-indicator and OpenRGB's SinowealthGMOWController): a **feature report**, report id 0, of 65 bytes (id + 64) shaped `00 | 00 00 02 02 00 83 | 00 ...` - DevSel `0x02` wireless, CmdType `0x02` battery, Func `0x83` - then a ~100 ms wait for the RF round trip and a GetFeature of the same report. The answer carries `0x83` at byte 6, the charging byte at 7 (0 = no, non-zero = on the charger) and the level at 8; status byte 1 is `A1` awake, `A2` waking, `A4` asleep. The receiver publishes vendor collections on `ffa0` and `ffff`; the one whose feature report is 65 bytes is tried first.

The level is **confirmed in this app on hardware by @gumocska** (#281): the icon appeared and the percentage matched Glorious Core. Charging over a cable is read the way GloriousBatteryMonitor-Go reads it - from the **wired id appearing** (`258a:2011` for the Model O, `DeviceDatabase.cs`), not from the reply: while the mouse charges over USB it leaves the 2.4 GHz link, so the receiver goes quiet and the provider reports the wired id as charging, keeping the last level. Charging from a wall adapter gives no signal at all - the mouse is not talking to the receiver, so there is nothing to read and the icon keeps its last value.

The **Lamzu Tachi** (`37B0:000C`, 8K receiver, #262) publishes the same collection layout (`ffa0:0001`, `ffff:0001/0000`), so the same query is tried on it - that layout is the only evidence, and the test build decides whether the Tachi is another Sinowealth device. **Unverified** on hardware; a test build is with the reporter of #262.

### SteelSeries Aerox 3 Wireless

**Connection:** 2.4 GHz dongle (1038:1838)

The receiver's battery query: it flags its configuration opcodes with `0x40` over their wired values, so the wired `92` stays silent and a 64-byte `00 d2 ...` is answered by a report echoing `d2`. The level byte is the charging flag in bit 7 plus a 1..21 step value (or a direct percentage above 21), as yurtemre7/steel-mouse decodes it; the interface and the product id come from alloyctl's reverse engineering of this exact id, cross-checked against the capture notes at gort818/aerox3-wireless. A level byte of 0 means off or asleep rather than empty, and the 2.4 GHz link sleeps when the mouse is idle - it wakes on the next movement, so a poll that lands on a sleeping mouse simply shows nothing. Works alongside SteelSeries GG; the CS2 Dragon Lore edition (1038:1878) shares the protocol and is included untested The Aerox 5 Wireless (1038:1852, 185C, 1860) and Aerox 9 Wireless (1038:1858, 1874) in 2.4 GHz mode use the same battery exchange in rivalcfg (`0x92` with the wireless flag `0x40`, level `(value - 1) * 5`); the level is confirmed on an Aerox 9 Wireless by @AJD00m in #79 (`d2 04 ...` = 15 %, the same as SteelSeries GG), the charging bit is not tested yet

### SteelSeries Rival 3 Wireless

**Connection:** 2.4 GHz dongle (1038:1830)

The mouse exchange on the same interface and the same `0xFFC0` configuration collection as the headsets, but not the `b0` request: a 64-byte `00 aa 01 ...` out, answered by a report carrying the level. The two references disagree about the reply and neither has been on hardware here, so both shapes are read: a reply that echoes `aa` (level in byte 1, charging in byte 3, as yurtemre7/steel-mouse reads it) and a 3-byte reply read the way flozz/rivalcfg does (level in byte 0, charging in byte 2, the same request and the same hidapi read). A leading report id byte of `0x00` is skipped, a level above 100 is refused in both shapes, and in the 3-byte shape the charging byte has to be 0 or 1 so a stray report cannot pass as a level - the diagnostics print the raw reply and say which shape was used. The collection is picked by usage page `0xFFC0` rather than by interface number, because the Rival 650 has it on interface 0 (rivalcfg issue #202). Works alongside SteelSeries GG; the Gen 2 revision (1038:1872) is included untested

### WLmouse Beast X and Beast X Mini Pro

**Connection:** 8K or 1K receiver, or USB cable

The same feature-report exchange as the Beast X Max (`00 00 02 02 00 83` out, `a1 00 02 02 00 83 <charging> <battery %>` back), on the vendor collection `ffff:0000`. The provider's receiver table lists `0xA887` (Beast X) and `0xA868` (Beast X Mini Pro); the Max is confirmed, these two are not.

### WLmouse Beast X Max

**Connection:** 8K receiver (36A7:A880) and USB cable

Feature request `02 02 00 83`; if there is no reply, the mouse heartbeat is used. Receiver and cable share one icon. A receiver whose mouse is switched off is walked only every fifth poll once nothing is shown (each walk refuses on every vendor collection, ~0.75 s apiece, then listens ~2 s), and at once when the receiver's device set changes (#62 item 5)

### NZXT Lift Elite Wireless (1e71:2101)

**Connection:** 2.4 GHz receiver (enumerates as "NZXT Lift Elite Dongle"); the
same mouse on its USB cable enumerates as `1e71:2129` and speaks the same
protocol, so both ids are read.

No open-source project reads this mouse's battery (OpenRGB covers the wired
Lift's LEDs only), so the protocol comes from a USBPcap capture of NZXT CAM
talking to the dongle ([#148](https://github.com/HeyOkay/HaloBattery/issues/148),
2026-09-30). CAM speaks in 64-byte reports that start `4e`: a request on
interrupt OUT, `4e 02 81 00 b0 ..` (02 = the mouse behind the dongle, `81` =
read a property, `b0` = the telemetry one), answered on interrupt IN with
`4e 02 97 ..` about 25 ms later, after a `4e e5` acknowledgement. In the
reply, bytes 17-18 little-endian are the battery percent (the captures hold
77 then 78 across the reporter's charging session, and 84 while wired) and
bytes 8-9 are the cell voltage in millivolts (4093 wireless, 4348 while
charging); byte 22's low bit reads 1 while the mouse is on its charging cable.
The wired id (`1e71:2129`) carries the identical `ffca:0001` collection and
was read by CAM with the very same request. The flags byte 7 and the
rest of the frame are not decoded and nothing is guessed from them. The
conversation rides the `ffca:0001` collection (the same page OpenRGB uses for
the wired Lift), picked by usage page. **Confirmed on hardware** by @MrBeat93:
both connections read (2.4 GHz and USB cable); the level tracks CAM (one step
above its smoothed panel) and charging follows the cable. While NZXT CAM
itself is open the dongle stops answering this request - the app keeps the
last value on a greyed icon, so live readings want CAM closed.

## Mice and keyboards

### Keychron Ultra-Link 8K, Keychron M5

**Connection:** 2.4 GHz receiver (3434:D028) and USB cable (3434:D048)

Keychron's vendor protocol on interface 4: a 64-byte feature report `b3 06` (status), answered by a 64-byte input report `b4 06` whose byte 20 is the level, retried up to three times. From csutcliff/keychron-battery-dkms, which implements it for these two ids. The **Nape Pro** (`3434:d026`, the "Keychron Link-KM" receiver, #270) speaks the same exchange but publishes its vendor collection on **interface 1** (`ffc1:0001`), not 4, so the provider picks interface 4 first and then any vendor page. **Unverified** on hardware; a test build is with the reporter. **Unverified** - no Keychron device was on hand, so a level above 100 is refused rather than shown

### Lofree Hyzen

**Connection:** 2.4 GHz dongle (388D:0025)

The transaction Lofree's own web driver (hyzen.lofree.tech) uses, on report 0x04 of the vendor collection 0xFF1C:0x92: start (`00 00 01`), the command, end (`00 00 02`), each acknowledged in byte 2. Command `AA` asks if the keyboard is online, command `1A` returns the level in byte 7. As in the web driver, only the dongle is read, and not while the same keyboard is on its cable; no charging state. **Unverified** - no Lofree keyboard was on hand

### Logitech G502 LIGHTSPEED, G502 X PLUS

**Connection:** Lightspeed receiver (046D:C539, 046D:C547)

HID++ 2.0 on the receiver's vendor interface: the device name (feature 0x0005) and the first battery feature the device supports (0x1004 unified battery, 0x1000 battery status or 0x1001 battery voltage; the G502 LIGHTSPEED reports voltage, converted to % with the Li-ion curve used by Solaar, the G502 X PLUS the unified battery percentage). The icon follows the device's unit id (feature 0x0003). Works alongside G HUB

### Razer BlackWidow V3 Pro

**Connection:** 2.4 GHz receiver (1532:025C), cable (1532:025A)

The standard Razer commands: class `0x07` id `0x80` for the level (0-255, shown as a percentage) and id `0x84` for charging. The keyboard answers on its own control collection, not a `ff00` vendor page - the probe order's ranking is a preference, not a filter, so it reaches any collection that answers. From OpenRazer's keyboard driver: `razer_attr_read_charge_level()` reads the wireless id with transaction id `0x9F` and the wired id with `0x3F`, and `razer_get_report_params()` puts both on USB interface 2. **Unverified** - no Razer keyboard was on hand; the diagnostics name every interface/usage they try, so a dump from the reporter of #56 settles it

### Razer DeathStalker V2 Pro TKL

**Connection:** HyperSpeed receiver (1532:0296) or USB cable (1532:0298)

The standard Razer commands (class `0x07` id `0x80` for the level, 0-255 shown as a percentage, and id `0x84` for charging) with OpenRazer's keyboard-driver values: transaction id `0x9F` on the receiver on USB interface 2, `0x1F` on the cable on interface 3 (`razer_attr_read_charge_level()` and `razer_get_report_params()`). That interface is asked first, the others stay a fallback. The receiver's product string is " DSV2Pro TKL", without a wireless word, so the PID is listed in `KNOWN` and always polled. The icon shows the keyboard pictogram. Confirmed on hardware over the receiver (#106)

### Razer DeathStalker V2 Pro, BlackWidow V3 Mini, V4 Mini and V4 Tenkeyless HyperSpeed

**Connection:** HyperSpeed receiver or USB cable

The same exchange as the DeathStalker V2 Pro TKL above, with the ids and interfaces from OpenRazer's keyboard driver: DeathStalker V2 Pro 1532:0290 (receiver, `0x9F`, interface 2) and 1532:0292 (cable, `0x1F`, interface 3); BlackWidow V3 Mini HyperSpeed 1532:0271 / 0258 and BlackWidow V4 Mini HyperSpeed 1532:02BA / 02B9 (`0x9F` / `0x1F`, both on interface 3); BlackWidow V4 Tenkeyless HyperSpeed 1532:02D5 (receiver, `0x9F`, interface 2) and 1532:02D7 (cable, `0x1F`, interface 3). **Unverified** - only the DeathStalker V2 Pro TKL has been tested on hardware so far

## Controllers

### 8BitDo Pro 2, Pro 3, SN30 Pro, SF30 Pro in D-input mode

**Connection:** Bluetooth (2DC8:6006 and the other ids in `providers/eightbitdo.py`) or USB

Listened to, never written: byte 14 of the controller's enhanced input report (report 0x01 over Bluetooth, 0x04 over USB; bits 0-6 the level in %, bit 7 charging), as SDL reads it. The controller sends that report only after Steam or a game has switched it on, see the note below the table. The Pro 3 (2DC8:6009) is the exception: its descriptor declares one input report, 0x04, on both transports, and it carries the level in byte 14 at all times, so the Pro 3 shows its level without Steam - **confirmed on a real Pro 3 over Bluetooth**, with Steam closed and the controller freshly turned on. In XInput mode these controllers are read as Xbox controllers. The other models are **unverified**, so a level of 0 or above 100 is refused rather than shown

### GameSir G7 Pro; FlyDigi Vader Pro

**Connection:** 2.4 GHz receiver (shows up as an Xbox controller)

Windows.Gaming.Input battery report: exact percentage and charging state. XInput is the fallback (four levels only)

### Nintendo Switch Pro Controller, Joy-Con (L) / (R)

**Connection:** Bluetooth (057E:2009, 2006, 2007)

Byte 2 of the controller's own input report: level 0-8 in steps of 2 (full, medium, low, critical, empty) and the charging bit, shown as SDL shows it (level / 8, so 100 / 75 / 50 / 25 / 0 %) with the level name in the tooltip. When Steam has put the controller in the full mode, the 0x30 reports carry the byte and nothing is written; otherwise one read-only subcommand (0x02, request device info) is sent and its 0x21 reply carries the byte. The controller mode is never changed. USB is not read (the controller charges there). **Unverified** - no Switch controller was on hand, so a level above 8 is refused rather than shown

### Sony DualSense (PS5)

**Connection:** USB or Bluetooth

Read straight from the HID input report (USB byte 53, Bluetooth full report byte 54). Over Bluetooth, see the note below the table

### Sony DualShock 4 (PS4)

**Connection:** USB cable and Bluetooth (054C:09CC)

Read straight from the HID input report: exact percentage and charging state (USB byte 30, Bluetooth full report byte 32). Over Bluetooth, see the note below the table

### Steam Controller (2025) with its puck

**Connection:** 2.4 GHz puck (28DE:1304 Proteus, 28DE:1305 Nereid)

Listened to, never written: the puck relays each controller on its own vendor collection (usage page 0xFF00 / usage 0x0001) on interfaces 2 to 5, one interface per controller slot, as SDL's driver reads it (src/joystick/hidapi/SDL_hidapi_steam_triton.c). Input report 0x43 carries the charge state in byte 0 (1 discharging, 2 charging, 4 charging done) and the level in % in byte 1; reports 0x79 and 0x46 report a controller connecting or disconnecting, and 0x42, 0x45 and 0x47 are the controller's state reports. The puck sends 0x43 about every 2.5 s while the controller is awake - ~0.4 Hz, measured over ~9k frames by [CouchTurtle/sc2-research](https://github.com/CouchTurtle/sc2-research) and on live hardware by [eva-val/steambattery](https://github.com/eva-val/steambattery), and #58's first tester run is what showed the original 0.4 s listen window missing it. A slot with a live controller is therefore listened to for up to 5 s per poll (twice the cadence) and stops early on a disconnect; a quiet slot costs only the short window. The last level is kept between reports the way SDL keeps it; a slot that goes quiet keeps its last value greyed, and a disconnect removes the icon at once. SDL sends rumble, IMU and a lizard mode command over the same report and disables lizard mode every 3 seconds - none of that is sent here, so the controller keeps behaving the way the user's games expect. The older 2015 Steam Controller and its dongle speak a different protocol and are not covered. **Confirmed on hardware** (@Kosminaut13, #58): his run caught the 0x43 report inside the 5 s window - `43 01 5f` at 95 % while awake and `43 04 64` at 100 % charging on the cable, with the tray following both states and an asleep slot keeping its last value greyed. A level above 100 is refused rather than shown

### Xbox-compatible controllers (other models)

**Connection:** USB or the Xbox wireless adapter

Any Xbox-compatible controller is read the same way as the GameSir G7 Pro: the battery level from `Windows.Gaming.Input.RawGameControllers`, with XInput's coarse level (low / medium / full) as the fallback. Controllers other than the GameSir have not been individually tested.

## Microphones

### DJI Mic Mini 2

**Connection:** USB receiver (`2CA3:4011`, "Wireless Mic Rx") - interface 6, which needs WinUSB

The receiver keeps its status on a vendor-specific USB interface (class `ff`, subclass `f0`, bulk IN `0x86` / bulk OUT `0x06`), not on HID: its four HID collections are silent, and Windows reports no battery for a USB microphone. The frames are DJI's DUML framing - `0x55`, total length, `0x04`, a CRC-8 over the first three bytes, the body, then a reflected CRC-16 (`0x8408` / X-25); v2 firmware seeds that CRC with `0x3692` and the whole frame then checksums to zero. The device pushes on its own, and nothing is ever sent to it.

Three pushes arrive: a **status** push with one 32-byte slot per connected transmitter, an **identity** push (serials and product names) and an **audio-level** push. In the status push, header offset 44 is the connected bitmask (bit 0 = TX1, bit 1 = TX2) and each slot carries, at slot offset +7, the docked/charging bit (`0x02`) and a **seven-step gauge** in bits 2-4 (`1` = full, `7` = the last reading before the transmitter shuts off). That is a gauge, not a percentage, so the tray shows an estimate and says so, while the raw gauge stays in the diagnostics. A transmitter that is switched off has no slot and loses its bit, so its icon leaves the tray - after a short grace period, so a dropped push does not make it blink.

Protocol from [ShadowBitBasher/DJI-Mic-Control](https://github.com/ShadowBitBasher/DJI-Mic-Control) (PROTOCOL.md, Unlicense; its USB id table is this receiver's id). The frame layout, both CRC seeds and the gauge were re-derived against a receiver here - 118-byte status pushes, 182-byte identity pushes naming both transmitters, every frame CRC-valid - and **confirmed on hardware** with two Mic Mini 2 transmitters.

**The driver step.** Windows cannot open interface 6 until WinUSB (or libusbK) is bound to it: with [Zadig](https://zadig.akeo.ie), pick the receiver's "Interface 6", target driver **WinUSB**, Replace Driver, then replug. Nothing else changes and the audio path keeps its own driver. Without that, or without `pyusb`, this provider reports nothing and says so in the diagnostics; every other device is unaffected. It is the only device in the app that needs a driver step, which is why it is documented here instead of being assumed.

## Bluetooth

### Bluetooth devices, tested on the 1MORE SonoFlow headset

**Connection:** Bluetooth (on by default, can be turned off in the menu)

The level Windows itself knows (`DEVPKEY_Bluetooth_Battery`). Only devices connected right now are shown: the link state comes from WinRT (`BluetoothDevice.ConnectionStatus`, the same source as Windows Settings). A device that is also read over HID keeps one icon: the HID reading wins and the Bluetooth copy is dropped

## Others

### Alienware Pro Wireless Gaming Keyboard
### Razer Barracuda X (2022) (2.4 GHz)

**Connection:** 2.4 GHz receiver (413C:211D, "Alienware Pro KB Gaming Receiver"), vendor collection `ff00:0001` on interface 1.
**Connection:** 2.4 GHz dongle (1532:0552, "Razer Barracuda X 2.4", Macronix bridge)

Alienware Command Center v6.14.54.0 was captured with USBPcap for #287. It sends a 64-byte **feature** report (`SET_REPORT`, **wValue 0x0300 = report id 0**) starting `0e a1 00 ...`, and the receiver answers with an input report `0e a1 <level> <state> ...` - `0x25` = 37 %, exactly what Command Center displayed in both captures. The same stream carries unsolicited state reports (`0e 05`, `0e 17`, `0e 83`, `0e 88`, `0e 89`, `0e 97`), so a reader must filter on the `a1` command rather than take the next report.
This dongle does not answer the Razer mouse request, and the headset ("PA") frames are refused
with `WriteFile: 0x00000001 Incorrect function` - the dongle bridges USB HID to an **Airoha**
chip and speaks Airoha's RACE protocol instead, framed for the Macronix bridge:

The report id is the detail that matters: hidapi treats the first byte of the buffer as the report id, so the frame must be handed over as `00 | 0e a1 ...` (65 bytes). The first build passed `0e` as the report id, the receiver ignored the writes and every poll ended in "no link (off or asleep)" - the test build's report in #287. The reply may come back with the same `0x00` in front or without it, so the mark is looked for at either position.
    app mode   > 01 40                    < 01 40 01 01
    request    > 01 80 <len> 50 41 <group> <seq> <body...>
    reply      < 01 80 <len> 50 49 ....... payload: [group, 0x80|seq, status, data...]

Byte 3 is the charger state: `0x00` on battery and `0x04` with the cable in, the only two values in the captures (the charging one is `process3`). It is reported as charging while `0x04` and as not charging otherwise; a state never seen is written to the diagnostics rather than interpreted.
    remote mode  group 0x0E, body 02 E1 01   (the dongle forwards to the headset only then;
                                              in local mode it answers GET_BATTERY itself
                                              with a meaningless value)
    GET_BATTERY  group 0x06, body 01 00 31   -> payload[3..4] = millivolts (LE)
    local mode   group 0x0E, body 02 E1 00   (always, on the way out)

The headset reports **voltage only** over 2.4 GHz - no percentage, no charging flag (its
Bluetooth side has a standard battery level). The percentage is interpolated on a Li-ion
curve (3300 mV = 0 %, 4150 mV = 100 %), and charging is inferred the way
[mehdibouchami/razer-barracuda-x-battery](https://github.com/mehdibouchami/razer-barracuda-x-battery)
does it: several samples ~350 ms apart, charging when their spread is at least 12 mV or the
lowest is at or above 4180 mV. The read takes about 2.5 s because of those samples.

**Confirmed on hardware** in the test-build run: `3822 mV -> 38 %` on a Barracuda X (2022), the seven samples within 9 mV and reported as not charging. The charging inference itself still needs a run with the cable in.

Only the Macronix bridge (`0552`) is read. The `0550` variant uses the YS-Tech bridge: the
same RACE commands wrapped in `02 80 <len> <crcLo> <crcHi> payload` with a CRC-16/XMODEM
over the whole report and an `'O'`/`'F'` input-report acknowledgement - documented in that
project, not implemented here until a 0550 unit can test it.

**Confirmed on hardware by @notisv** on the second test build: the icon showed the level continuously (`reply: 0e a1 5c 00` = 92 %), agreed with Bluetooth, Windows and Command Center at the same time, and the charging state followed the cable in and out. They kept it running for over an hour before reporting.

### Logitech (more HID++ 2.0 devices and G-series headsets)

**Connection:** Lightspeed, Unifying or Bolt receiver

The provider reads HID++ 2.0 generically from the receiver's `ff00` vendor collections (slots 1-6), so most other HID++ 2.0 mice and keyboards on a Lightspeed, Unifying or Bolt receiver should answer the same `0x1004` unified battery request. The G-series headsets - G533, G535, G633, G635, G733, G933, G935, G PRO, G PRO X - are in the headset pid table and use feature `0x1F20` (the G535 is the exception: it has no feature table and answers its 1.0 charge register, see below). Only the models listed in the support table above are confirmed so far.

The **G535** (0AC4) is long-only: its dongle exposes just the consumer collection `000C:0001`, with no `ff00` pair, so there is no feature table to ask. Its charge register is read with a *long* report instead - `11 FF 05 0D` is answered by `10 FF 05 0D <mV hi> <mV lo> <state>` (state `01` discharging, `03` charging) - and the voltage is mapped through HeadsetControl's G535 curve. It is read *before* the HID++ 2.0 ping, because a 1.0 headset does not answer one and otherwise looks asleep. The register is answered on the `ff43:0202` collection; the consumer collection is silent, so both are tried (#339). **Confirmed on hardware** by @ti-henrq: `register 0d: 4069 mV -> 85%`, icon shown.

Devices with no feature table at all (the older HID++ 1.0 generation) are read through the old battery registers instead: a short report `10 <slot> 81 <register>` on the short collection reads register `0x0D` (a percentage with a charging status - `30` discharging, `50` recharging, `90` full) or register `0x07` (a discrete level - 7 full, 5 good, 3 low, 1 critical - beside a charging byte), the same registers and order Solaar uses. This was added for the M710 keyboard of the MK710 wireless desktop ([#159](https://github.com/HeyOkay/HaloBattery/issues/159)), built from Solaar's implementation; **confirmed on hardware** in the reporter's test-build run - the icon shows the keyboard's coarse level.

### Endgame Gear XM2we

Connection: original 2.4 GHz receiver (3367:1961). Windows exposes feature
report 0x08 on ff02:0002 and input report 0x09 on ff01:0000, confirmed from
the owner's receiver descriptors. Open both collections on the same receiver.

Source: [okms/xm2we-linux protocol documentation](https://github.com/okms/xm2we-linux/blob/main/PROTOCOL.md),
Framing and Battery sections, and its `xm2we` query/battery implementation.
Feature report 0x08 sends a 17-byte checksummed status request; input report 0x09
returns the same opcode. Command 0x04 returns percentage and a charging flag;
0x03 returns radio link status. Battery is queried first because charging can
briefly interrupt the radio link. No configuration commands are sent.

The receiver can return a cached battery level while the mouse is offline; such
readings are marked offline unless charging. Percentage is a coarse voltage
estimate. Wired XM2we, OP1we and the newer XM2w family are not enabled by this
provider. Verified on an XM2we receiver on Windows: 90% matched the official
tool, charging became true with USB power connected and false after unplugging.
While charging, link returned 0 but the valid charging flag kept the device online.
Verification used the provider probe; the complete tray application has not yet
been tested with this provider.
