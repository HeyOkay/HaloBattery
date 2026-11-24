# Protocols

How each device's battery level is read - one section per device, linked from the support table in
the [README](../README.md#supported-devices). These are the implementation notes: what is sent,
what comes back, and where each protocol was taken from.

## Headsets

### Astro A50 Gen 5 (Logitech 046D:0B1C)

**Connection:** Base station

The station's own vendor collection (usage page 0xFF32 / usage 0x0074): 64-byte reports, report id 0x02, `02 0c <len> 00 <cmd> <handle>`. Battery command 0x06 answers with the level in byte 6 and byte 8 set while the headset sits on the dock (charging). The protocol is HeadsetControl's, reverse-engineered from G HUB captures and verified on this USB id; it is neither HID++ nor the A50 X's Centurion protocol. **Unverified** - no A50 was on hand, so a level out of 0..100 is refused rather than shown

### Skullcandy PLYR / Crusher PLYR 720

**Connection:** `34F0:3210` / `34F0:5310` dongle, vendor page `FF13`.

The exact existing RACE battery request uses output report 6, recipient `80`,
opcode `0cd6`, role zero. Source:
[Headroom at commit 87a9892](https://github.com/patalbansishashank/Headroom/blob/87a9892/bin/headroom_race.py),
`read_battery`, `drain`, `poll`. Old input is drained with a bounded loop before
asking. A post-query interrupt packet proves new delivery even when its bytes and
percentage are unchanged. A GET_REPORT fallback must differ from a known
pre-query snapshot; an unchanged or unknown-baseline snapshot is not fresh.
Replies must stay inside the declared headset report and match recipient,
opcode, role, successful status and range. The outbound command is unchanged.
The prior PLYR reading was confirmed in [#104](https://github.com/HeyOkay/HaloBattery/issues/104);
these freshness changes were tested with fake interrupts/snapshots only and need
Windows hardware confirmation.

### Logitech G435 LIGHTSPEED (046D:0ACB, Realtek receiver)

**Connection:** LIGHTSPEED receiver

This is the original Realtek receiver, not the Holtek variant (`046D:0ADF`). It speaks no HID++, and it has no passive status channel: volume keys, mute, power cycles and USB-C charge events produced no frames on either vendor collection during two long listens. The battery is only reachable through the receiver's own PA command bridge, in bootloader mode.

Feature report 6 on usage page `0xFFC0` (63 data bytes) carries `[06, 01, 03, 0…]` to enter bootloader, `[06, 08, 03, 0…]` to leave it, and `[06, 04, 03, 00, len, …]` to forward a PA command. The commands are `LQ 0x0E E1 01` (remote mode), `OTA 0x06 31` (get battery) and `LQ 0x0E E1 00` (local mode). The reply is `06 80 00 <millivolts u16 LE> 00 00`.

Two traps: without the remote-mode switch the receiver answers locally with `102`, even with the headset switched off and even after a cold replug; and the first read after entering bootloader can arrive without the `P I` prefix, so the payload is searched for rather than indexed.

Bootloader mode mutes USB audio for about 0.8 s, so the provider reads at most once every 15 minutes and caches between reads. A headset switched off inside that window keeps showing its last level, still marked online, until the next read; the greyed state only appears once a read actually fails. The percentage is an estimate from a generic 3.7 V Li-ion curve, and the protocol exposes no charging flag, hence `approx` and `charging_known=False`.

**Verified on hardware:** running Halo from source with the receiver plugged in and the headset switched on, `--probe` reads `about 73%` (3954 mV), consistent with the headset's LED band, which shows green (31–100%). The protocol itself was measured with an independent reader (https://github.com/fr0styJS/g435-lightspeed-battery).

### Audeze Maxwell

**Connection:** 2.4 GHz dongle (3329:4B19) and USB-C cable (3329:4B1A)

The vendor collection (usage page 0xFF13): the sequence HeadsetControl uses, whose answer carries the battery as attribute 0x0CD6 (`05 5D <len> 00 D6 0C <percent>`). One packet is enough — the query for that attribute is answered with the marker on its own (0.19 s against 1.48 s for the whole sequence) — with the full sequence as the fallback. Dongle and cable are one headset and share one icon; charging is inferred from the cable answering, so the icon breathes while it sits on USB-C. With the headset switched off the dongle keeps answering with the last value it held, so the provider goes by the dongle's own product string — `"Audeze Maxwell Dongle"` with no headset linked, `"Audeze Maxwell HID"` with one — and reports nothing: the icon leaves the tray the way any switched-off device does

### Cooler Master MM712 / MM731

**Connection:** 2.4 GHz receiver (`2516:01B7`) or USB cable (MM712 `2516:0169`, MM731 `2516:0167`).

The receiver and both cables expose a vendor collection on interface 3, usage page `FF0A`. Sending the 4-byte output request `00 46 81 00` produces a `47 81 <model> ...` status report: byte 10 is the level (1-100), byte 11 charging, byte 12 the transport (0 cable, 1 receiver). On the shared dongle model byte 50 is the MM712 and 70 the MM731. Only the status request is sent. Protocol from syntactic-spice/coolermaster-usb-protocol and riVFerd/MM731-Controller; the MM712 was tested there, the MM731 path and its model byte are not. **Unverified in this app.**

### Corsair Void Elite Wireless

**Connection:** 2.4 GHz receiver (1b1c:0a55)

The older Void exchange, from HeadsetControl's Void device: the battery collection is the vendor page `ffc5:0001` (interface 3), and a single output report asks for it - `c9 64` - with the answer arriving as a five-byte input report `64 00 <level> b1 <status>`. Bit 7 of the level byte is the microphone-lifted flag and is masked off; the status byte is 0 when the receiver has no headset on it, 1 normal, 2 low, 4 or 5 charging. No session and no framing are involved, unlike the family above. **Unverified** - read from HeadsetControl, a test build is out for [#330](https://github.com/HeyOkay/HaloBattery/issues/330).
### Corsair HS80 RGB Wireless (original), direct USB and wireless receiver

The powered-on headset identifies as `1b1c:0a69` by direct USB; when switched off but charging it re-enumerates as `1b1c:0a6a`; its receiver is `1b1c:0a6b`. The powered-on device and receiver expose the control HID collection on interface 3, usage page `ff42`. Halo Battery sends the 64-byte battery query `02 <target> 02 0f`: target `08` in direct USB mode and `09` through the receiver. It accepts only a matching response report `01 <channel> 02 00`, where channel is target minus `08`, with a little-endian battery value in tenths of a percent at bytes 4-5 (0..1000). The `0a6a` charging identity has no battery-control collection, so when only it is present Halo Battery keeps an unknown-level headset icon and infers charging from that USB identity; if a receiver reading is available, it reuses the level and adds the charging animation. This is distinct from the HS80 Max protocol. Reference: [ST1CKEL/HS80_Control PR #2](https://github.com/ST1CKEL/HS80_Control/pull/2).

### Corsair Void v2 Wireless, Virtuoso Max Wireless, HS80 Max Wireless

**Connection:** Wireless receiver (1B1C:2A08, 1B1C:2A02, 1B1C:0A97)

The receiver's vendor collection on interface 4: 65-byte writes `00 02 <endpoint> 02 <cmd>`, endpoint 0x08 the receiver and 0x09 the headset, and 64-byte replies. After a minimal wake handshake (the one HeadsetControl uses, which avoids the pop of switching the headset into software mode), battery command 0x0F answers with a 16-bit value in hundredths of a percent at bytes 4-5. **Unverified** - no Corsair headset was on hand; the receiver sometimes answers with something other than a level, so that is retried and then refused rather than shown

### Corsair Virtuoso RGB Wireless SE / XT

**Connection:** Slipstream receiver or USB cable - SE `1B1C:0A3E` receiver / `1B1C:0A3D` cable / `1B1C:0A40` the newer Slipstream Multi-Device receiver (#204); XT `1B1C:0A64` receiver / `1B1C:0A62` cable

The routed exchange the Dark Core row describes, framed on report `0x02`: the request is a 64-byte report - `02 <target> <command> <property>`, target `0x09` the headset behind a receiver, `0x08` the device itself, command `0x02` "get property" - answered on report `0x01` with `01 01 <command echo> <status> <value:4 LE>` (`00` ok, `05` no such property, `09` refused). Property `0x0F` is the level in tenths of a percent and `0x10` the charge state (1 = charging). The wired ids read with target `0x08` first and the receivers with `0x09`, each with the other as the fallback; reads need neither a session nor software mode (HeadsetControl notes only writes need software mode), and the headset's unsolicited volume reports (report id `0x0E`) are not replies and are skipped. On Windows the frame goes out bare - the 64 bytes with `0x02` first; every report-id-prefixed form is refused with `0x57` (#204, measured on the `0A40`). One icon covers a product's receiver and cable ids together, and a reading that reports charging wins the merge. Source: HeadsetControl's reworked Virtuoso XT/SE device ([#570](https://github.com/Sapd/HeadsetControl/pull/570)), with its four XT/SE ids run on its author's hardware per the #568/#570 discussion, plus the `0A40`'s end-to-end measurement with @jeffpeng3's probe runs (#204: 96 % in both states, charge `0x10` = 1 on the cable). **Confirmed in the app on the `0A40`** - @jeffpeng3's run read 97 % over the receiver (the level frame `01 01 02 00 ca 03` twice, identical) together with the charge state; the four XT/SE ids have not answered a HaloBattery build yet and stay marked unverified


The wired XT (`0A62`) is an explicit reply-source exception: when target `08`
is queried, it responds as source `00`, not source `01`. Accept that form only
for this PID and target, with a complete `01 00 02 00 <LE32 value>` GET reply.
The wired SE and receiver routes retain their existing source checks. Source:
[HeadsetControl target/source handling](https://github.com/Sapd/HeadsetControl/blob/163c6edf744c28804d95e3653e5ea7bc9486b46c/lib/devices/corsair_virtuoso_xt.hpp#L370-L405),
[property payload](https://github.com/Sapd/HeadsetControl/blob/163c6edf744c28804d95e3653e5ea7bc9486b46c/lib/devices/corsair_virtuoso_xt.hpp#L440-L462),
and [response matching](https://github.com/Sapd/HeadsetControl/blob/163c6edf744c28804d95e3653e5ea7bc9486b46c/lib/devices/corsair_virtuoso_xt.hpp#L534-L554).
This exception has offline regression coverage; no new Corsair hardware test is claimed.

### Endgame Gear XM2w 4K, OP1w 4K (4K dongle)

**Disabled in 1.17.0:** the provider does not open the 4K dongle. Owner tests in
[#321](https://github.com/HeyOkay/HaloBattery/issues/321) and
[#324](https://github.com/HeyOkay/HaloBattery/issues/324) reject the historical
XM2we-derived exchange below with `short send (-1)`. The separate correction has a
later detection-loss report ([public status](https://github.com/nqble/HaloBattery/blob/main/docs/endgame/4k-status.md));
it is not included here. Confirmed XM2we support uses its separate provider.

**Connection:** the 4K dongle, "Endgame Gear HS Dongle" (`3367:1970`)

One dongle id serves several mice - the XM2w 4K, the OP1w 4K and their v2 revisions - and the adapter itself stays neutral; the vendor software resolves which mouse is behind it. The exchange is the family's 17-byte frame, documented for the XM2we by okms/xm2we-linux and by Janlhin/Endgame_Gear_Web_Configurator: a feature report `0x08` carrying `<opcode> 00 ...` with a `0x55 - sum` checksum, answered by an input report `0x09` whose data starts at byte 6 and whose bytes sum to `0x55`. Battery is opcode `0x04` - `data[0]` the percentage, `data[1]` the charging flag - and it is read before the link state because plugging the cable drops the link for a moment while the level stays valid. The dongle's two collections are paired by the receiver part of their HID paths: the feature collection is `ff02:0002`, the replies arrive on an `ff01` collection. Only the two documented status reads are built; nothing is written to the mouse. **Unverified** on this dongle - the frame is the XM2we's, and a test build is out for [#324](https://github.com/HeyOkay/HaloBattery/issues/324).

### HyperX Cloud Alpha 2

**Connection:** 2.4 GHz station (03F0:08BE - the station's other function, 03F0:0ABE, is the audio-only "Chat" half and has nothing to read)

The battery collection is picked by usage page, 0xFF13:0xFF00. A 64-byte output report `50 02 00 00 ...` is answered by the input report starting `51 02`: the level is index 2 as a plain percentage, and bit 7 of index 6 was set after a recharge and clear before it, so it is shown as charging. Both were taken from two USBPcap captures of NGENUITY attached to issue #26, at 51 % and 67 %, and matched to the percentage NGENUITY displayed at the time; the `61 02` frame is byte-identical across the two states, so it is not the battery, and a level above 100 is refused rather than shown. **Confirmed on real hardware** by @azizen12 - the percentage was right on the station's first test. With NGENUITY running the station also emits thousands of `ff 01` / `44`-`45` housekeeping frames (its diagnostics showed the app reading only those); stale reports are therefore drained before the request and the reply is read until it arrives, so the two applications can run side by side - both states confirmed by @azizen12 on the final build. Credit for the captures: @azizen12.

### HyperX Cloud Core Wireless

**Connection:** 2.4 GHz dongle (0951:173F) or USB-C (0951:1740); HP also lists its vendor id (03F0)

The protocol from LennardKittner/HyperHeadset's `cloud-core` branch, reverse-engineered by the reporter in its [#59](https://github.com/LennardKittner/HyperHeadset/issues/59): the battery collection is `FF00:0001` on interface 3, and a 64-byte feature report `FF 07 00 FD 04 0C F1 02 01 04 F0 0C` + zeroes asks for it. The answer is a short-lived feature report starting `FF 12`; the voltage is little-endian at bytes 11..12, and byte 9 is `03` on battery / `05` wired charging. The reference polls the feature report for about 100-150 ms instead of one delayed read, so this does too. The headset reports no percentage: the reference converts voltage with a 20-point curve and a 20-sample average. Its latest Cloud Core curve was measured on a unit whose battery later failed and maps 3.6 V to 100 %, so a healthy 3.85 V reading would look full; this uses the Cloud Flight curve the same reference used before that calibration, which reads 3.85 V as about 70 %. The level is still approximate ("about NN%"), and a reply outside 2500-4500 mV or with an unknown charge byte is not shown as a valid state. Both ids share one icon. **Unverified on hardware** - the request is the reference's; no Cloud Core Wireless was available here

### HyperX Cloud II Core Wireless

**Connection:** 2.4 GHz dongle (03F0:0995) or its second mode (03F0:0795)

The same `66 <cmd>` 62-byte exchange as the Cloud III Wireless row, from @dantifrice's USBPcap capture of NGENUITY in [#155](https://github.com/HeyOkay/HaloBattery/issues/155): NGENUITY wrote `66 89` to interface 3 as an output report and the dongle answered `66 89 0e d7 30 ...` (48 % - the level at byte 4, bytes 2-3 non-zero, as the reference parses it), and `66 8a` was answered `66 8a 00 00` (off the cable). **Verified on hardware** in the reporter's run: the icon read the level, and while charging both ids answer at once with the same reading (0995 and 0795) - they share one icon, with the charging reading winning

### HyperX Cloud II Wireless

**Connection:** 2.4 GHz dongle (03F0:0696, and 03F0:018B on the newer dongle revision)

The vendor collection, picked by usage page rather than position (0xFF90:0x0303): the dongle carries four collections on the one interface, so the first one is not the right one. A 52-byte output report `06 ff bb <command> 00` is answered by 20 bytes echoing the command: command 0x02 carries the level in byte 7 (voltage in bytes 5-6), command 0x03 reports charging in byte 4 - the same exchange HeadsetControl uses for these two product ids. **Unverified** - no Cloud II Wireless was on hand, so a reply that does not echo the command is ignored and a level above 100 refused rather than shown

The **Kingston-branded revision** (`0951:1718`, sold before HyperX moved to HP vendor ids) is the same headset with a longer exchange, documented by three sources that agree byte for byte: HeadsetControl's `hyperx_cloud_2_wireless_kingston.hpp` (written from LennardKittner/HyperHeadset), HyperHeadset's `cloud_ii_wireless.rs`, and Agustin-Jerusalinsky/hyperx-cloud-II-battery. Its vendor collection is `0xFF13:0x0001`, not the consumer pages beside it; the request is 62 bytes, `06 00 02 00 9A 00 00 68 4A 8E 0A 00 00 00 BB <cmd> <payload>`, sent after one input-report read that the references use as a prepare step. The reply is report `0x0B` (`0B 00 BB <cmd> ...`): byte 7 is the level for command `0x02`, and byte 4 is the charging state for command `0x03` (1 charging, 2 fully charged - both mean on the cable; 0 not charging). **Confirmed on hardware** in [#192](https://github.com/HeyOkay/HaloBattery/issues/192): the level and the charging indication both followed the cable on the reporter's dongle.

### HyperX Cloud III S Wireless

**Connection:** 2.4 GHz dongle (03F0:02CC, and 03F0:06BE which the reference also lists)

Another protocol again (LennardKittner/HyperHeadset's `cloud_iii_s_wireless`, and NGENUITY's USB traffic from HyperHeadset #36): a 64-byte *output* report `0c 02 03 01 00 <cmd>` (byte 3 = 01 only reads; the dongle ignores the same bytes as a feature report), command 0x06 for the battery and 0x48 for charging; the answer is input report 0x0C with the command in byte 5 and the value in byte 6 (0xFF = no value). The request goes to the collection that takes output report 0x0C, and all the dongle's collections are read for the answer. **Verified on hardware** in #106 (02CC): 89 %, the same level as NGENUITY; and in #156 (06BE), the level and the charging state

### HyperX Cloud III Wireless

**Connection:** 2.4 GHz dongle (03F0:05B7, and 03F0:0C9D which the reference also lists)

A different protocol from the Cloud II: the vendor collection is `0xFF13:0x0001`, a 62-byte `66 <command> 00 ...` packet goes out (0x66 is the report id) and 62 bytes come back whose second byte is either the command echo or the matching response id - `0x89`/`0x0D` carries the level in byte 4 (only when byte 2 or 3 is non-zero), `0x8A`/`0x0C` carries charging in byte 2 (0 not charging, 1 charging, 2 fully charged). Taken from LennardKittner/HyperHeadset, which lists this product id; that project also documents that some HyperX dongles accept these packets as **feature reports** only on Windows (`write()` fails with "Incorrect function" and the packet is retried with `send_feature_report`, which the diagnostics say either way). **Unverified on hardware** - no Cloud III Wireless was available here

### JBL Quantum 350 Wireless

**Connection:** 2.4 GHz receiver (0ECB:206B), generic-desktop HID collection `0x0001:0x0000` on interface 3 with a 2-byte feature report.

The receiver has no vendor collection. It answers two numbered one-byte feature reports, documented by [krisgfx9/Wireless-Device-Battery-Monitor's JBL driver](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor/blob/main/src/drivers/jbl.cpp) and matching the `#454` diagnostics:

    0x68  link: 1 = headset on, 0 = headset off
    0x49  battery percentage in 5 % steps; the dongle keeps the last value while the headset is off

Only read-only feature reads are sent. There is no charging byte in this protocol and no wired device is documented for the 350, so the app shows the level with the charging state unknown rather than guessing. **Test build pending** on the reporter's receiver.

The `03F0:0FCC` receiver is matched separately, using the same `FF13:0001`
collection and existing requests. Source:
[HyperBatteryMonitor Cloud3WirelessAlternateReceiverDevice.cs](https://github.com/davidsantanaalves/HyperBatteryMonitor/blob/main/Devices/Cloud3WirelessAlternateReceiverDevice.cs),
`Definition`, `QueryBatteryAsync`, `QueryChargeStatusAsync`, `ProcessInputReport`.
Its battery parser uses the same state gate and byte 4 percentage. HBM pads output
reports to the descriptor length; Halo retains its existing 62-byte request, which
the reporter confirmed on their receiver. HBM treats charging byte 1 as charging,
while Halo retains its existing 1/2 interpretation. No new commands or microphone
queries are introduced. The reporter confirmed this path on hardware: detection,
level, charging, restart and receiver replug all recovered.
The key remains `hyperx:0fcc`; it is not merged with another receiver PID.

### JBL Quantum HIDV3 (520 / 650 / 850 / 950)

**Connection:** 2.4 GHz receiver or USB cable. PIDs: 520 `218D`/`218E`, 520X `21A7`/`21A9`, 650 `2125`/`2126`, 650X `213B`/`213D`, 850 `219A`/`219B`, 950 `2127`/`2128`, 950X `213E`/`2140`.

The newer Quantum models expose a vendor collection on usage page `0xFF12` (`featLen` at least 11, input report at least 16 bytes). A feature report `0x88` asks for the two-byte feature id `0x000D` (battery):

    request  88 00 DD 01 00 01 00 02 00 0D 00
    reply    86 00 DD 01 00 01 ... 0D 00 <size> <value...>

The reply's status bit 15 rejects an error; the feature id must match; an empty feature is refused. The value byte has bit 7 charging and bits 0-6 the level. Only the read-only device-info query is sent. The protocol comes from JBL QuantumENGINE 2.9.4 as implemented by krisgfx9/Wireless-Device-Battery-Monitor, which tested a Quantum 650; this app has **not** run it on hardware yet.

### JBL Quantum 910 Wireless

**Connection:** 2.4 GHz dongle (0ECB:2088)

The receiver exposes one vendor collection, `ff13:0001` (interface 5 on a real unit), and the headset pushes its own reports there; there is no request to send. The battery arrives as report 0x08 with the level in byte 1 - the pattern plugato/JBL_Baterry_Monitor confirmed on this USB id - and report 0x2f is the microphone mute state. The headset can stay quiet for long stretches, so the last level heard is kept and shown greyed out until something arrives. **Confirmed on a real unit**: the provider finds the receiver, matches its collection and shows the level as soon as the headset reports. That report is an event - on the unit tested it arrives when the headset is plugged into its charger (a capture read 95% at the moment JBL's own app said 95%), and pressing the buttons or the volume rocker does not produce it - so the last level heard is kept and shown greyed out until the next one. The receiver also pushes a power report (0x09 - byte 1 is `0x00` when the headset is switched off and `0x01` when it is on, confirmed on a real unit - plus a 0x02 frame); none of them is a level, and a headset last seen switched off says so in the diagnostics rather than only "nothing heard"

### Logitech G PRO X 2 LIGHTSPEED

**Connection:** 2.4 GHz receiver (046D:0AF7)

Not HID++ on ff43: Logitech's "Centurion" transport on the vendor collection `ffa0:0001`, report 0x51. The app lists the receiver's features, reaches the headset through the receiver's bridge feature (0x0003), lists the headset's features and reads its battery feature 0x0104 (percent, charging state), all with read-only functions. From Solaar (tested on this headset) and HeadsetControl; firmware without 0x0104 gets HeadsetControl's fixed request. Confirmed on hardware in #103 (75 %, level and charging equal to G HUB)

The PRO X 3 LIGHTSPEED (`046D:0B45`) was tried with the PRO X 2 exchange because
its collections match. Hardware tests in [#343](https://github.com/HeyOkay/HaloBattery/issues/343)
report no reply even to discovery, so collection layout does not establish compatibility.
**Disabled in 1.17.0:** this PID is excluded before opening HID, pending a receiver
protocol source/capture. Confirmed PRO X 2 (`046D:0AF7`) remains enabled.
### Plantronics Voyager 8200 UC (BT600 adapter)

**Connection:** the headset's Bluetooth link through the BT600 USB adapter (`047f:02ee`, `047f:02f7`)

The adapter is a Bluetooth relay rather than a vendor dongle, and its settings live on the vendor collection `ffa2:0003` (interface 3), tunnelled in HID report `0x07`: `07 01 01 10 <len> <addr> 00 00 <op> <grp> <item> ...`, where `0x00` addresses the adapter and `0x20` the headset, and replies carry `0x00` or `0x02`. Nothing answers before the attach sequence - a two-byte output report `06 01`, a HELLO to the adapter, then a HELLO to the headset (`op 0x01`, group/item `01/02`, acked with `op 0x08`); it is idempotent, so it is replayed before every read, and the catalogue frames that follow each ack are skipped by the reply matcher. The battery is `GET 0A 1A`, answered by `op 0x03 0A 1A <level> <count> <charging> <minutes hi> <minutes lo> 01`; the percentage is Poly's own `level / (count - 1) * 100` (the count includes 0, so `0x0B` is ten steps), and the minutes are the remaining talk time, not the level. Only the battery is read - no setting is written. From HeadsetControl's `plantronics_bt600.hpp`. **Confirmed on hardware by @DindonOz** (#328): the level agreed with Poly's own app and the charging state was correct.

### Royal Kludge MG6 Pro

**Connection:** 2.4 GHz receiver (`2E3C:00A6` / `00E6`) or USB cable (`2E3C:00A5` / `00E5`).

The vendor collection is `FF00:0001` on the receiver and `FFF2:0001` on the cable, with 65-byte input/output reports. RK's web driver sends a QiWang configuration frame as output report 0:

    01 01 02 01 00 ...        GET_BATTER_VOL

The reply keeps the first three bytes and sets access_res in bit 4 of byte 3; bytes 4-5 are millivolts, 6-7 the percentage, and byte 10 the USB-plugged flag. A zero access_res means the receiver answered for a mouse that did not, so it is not shown as a level. Only the read-only query is sent. **Unverified in this app.**

### ZOWIE EC-CW, DW series and U2

**Connection:** EC-CW receiver `04A5:8006` or cable `04A5:8005`; DW receiver `04A5:800A`; U2 receiver `04A5:8008`.

Two read-only battery protocols. The EC-CW receiver/cable takes an unnumbered 3-byte request `00 A5 03`; the reply is millivolts (little endian) then the percentage, and zero volts means the mouse is not linked. The DW/U2 path uses command report `0x08 B4 15` on usage page `FF03` and reply report `0x09` on `FF04`, with millivolts in bytes 1-2 times 10 and byte 4 `0xC8`. Charging is inferred from the voltage trend, as in the MIT zowie-battery-tray; ZOWIE has no charging contact over USB. From yleshchuk/zowie-battery and IgnacioBarb/zowie-battery-tray. **Unverified in this app.**

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

### Razer Kraken V4

**Connection:** 2.4 GHz receiver (1532:056C) or USB-C cable (1532:056B), both on usage page 0xFF14

The receiver presents a MediaTek command channel, the same 64-byte frame family as the BlackShark V2 HyperSpeed: report id 2, sequence in byte 2, payload length in byte 6, address/domain in byte 9 (0x80 through the receiver, 0x00 on the cable), command in byte 10 and an XOR checksum in byte 62. Commands 0x21 (battery) and 0x2A (charging) are sent in that order. The reply's value byte is the percentage for 0x21, and 0 on battery / 1 while charging for 0x2A; the data-valid byte is not the charging state. If 0x2A is silent or returns anything other than 0/1, the level is still shown with charging unknown.

OpenRGB's [Kraken V4 merge request](https://gitlab.com/CalcProgrammer1/OpenRGB/-/merge_requests/3042) documents this frame and the packet capture for this model, and [openrazer issue 2298](https://github.com/openrazer/openrazer/issues/2298) identifies the device. OpenRGB lists commands 0x21 and 0x2A as present but not decoded; the BlackShark sibling uses 0x21 for the level and 0x2A for charging. [Sebax95's Kraken V4 Pro battery tool](https://github.com/Sebax95/Razer-Kraken-V4-Pro-Battery-Detector-Linux) documents the same 0x2A mapping and warns that the reply's data-valid byte is always 1, not the charging state. Read-only: no setting, lighting or firmware command is sent.

**Status:** **confirmed on hardware by @Zireael3Z**: the level matched the receiver at 87%, and the charging ring changed and reported charging while the cable was connected.

### Razer BlackShark V2 HyperSpeed (MediaTek)

**Connection:** 2.4 GHz receiver (1532:0565, "Razer BlackShark V2 HS 2.4", manufacturer "MediaTek Inc") or USB-C cable (1532:056E)

A MediaTek command channel, not the PA protocol of the 2020/2023 models: report id 2 on usage page 0xFF14, 64-byte frames, addressed to domain `0x80` through the dongle and `0x00` on the cable. While the cable is plugged in the dongle stops answering, so the cable is read first.

    request  [0]=02 [2]=60|seq [6]=04 [9]=80/00 [10]=21/2A [62]=XOR of bytes 0..61
    reply    [0]=02 [2]&1F=seq [8]=80 [9]=domain [10]=cmd [11]=01 ack [12]=count [13]=value

`0x21` reads the percentage, `0x2A` the charging flag; both are GETs and nothing else is sent. A bad checksum, a wrong sequence/domain/command, a missing acknowledgement or a zero payload count is refused, as openrazer's kernel driver does.

Protocol from [justik13/razer-blackshark-v2-hyperspeed-webhid](https://github.com/justik13/razer-blackshark-v2-hyperspeed-webhid) (MIT), with read-only implementations in [arkady-emelyanov/blackshark-battery](https://github.com/arkady-emelyanov/blackshark-battery) and [openrazer PR #2974](https://github.com/openrazer/openrazer/pull/2974). **Confirmed on hardware** in [#86](https://github.com/HeyOkay/HaloBattery/issues/86): the reporter saw the level and the charging state; the receiver and cable paths were not reported separately and no raw reply was posted.

### Sony INZONE H9

**Connection:** the headset's USB dongle (`054C:0E53`), which exposes a serial (COM) port

Read over that COM port at 115200 baud rather than HID: the dongle answers a fixed exchange - two initialisation frames, then a status query - and replies with a 14-byte frame starting `04 FF 0B 00 96 C3 14 04`, with the level in byte 12 and the charging flag in byte 11 (1 = charging). The port is opened with DTR and RTS raised, and Sony's own app (INZONE Hub) has to be closed because it claims the same port.

The frame sequence and the reply signature come from [janit88/InZoneH9Tray](https://github.com/janit88/InZoneH9Tray) (`inzone_tray.py`), contributed for both the white and the black version in [#278](https://github.com/HeyOkay/HaloBattery/pull/278) by @BoringMan314. It needs `pyserial`, which the released builds bundle; a source run gets it from `requirements.txt`, and the provider says so in the diagnostics when it is missing.

### Sony INZONE H5

**Connection:** the headset's USB dongle (`054C:0EBF`), vendor HID collection `FF04:0001` on interface 5

The same Sony frames as the H9, carried over HID instead of a COM port (the H5 dongle has none). The request is a 64-byte output report: report id `0x02`, the frame length (`0x0C`), then the battery GET frame `01 00 FC 08 96 C3 41 04 01 <tid lo> <tid hi> <sum>` - the H9 query with our own transaction id, the last byte being the low byte of the sum of `96` through the id. The reply is `02 0E` followed by the 14-byte frame that starts with the H9 signature `04 FF 0B 00 96 C3 14 04`, then the reply type `0x10`, the echoed transaction id, the charging flag (0 on battery, 1 charging, anything else unknown), the level and a checksum over the same bytes. A reply is accepted only when the id and the checksum match, since INZONE Hub shares the channel and its own replies arrive on it too. Level `0xFF` means the dongle is in but the headset is off, and is shown as offline; any other level above 100 is refused. INZONE Hub does not need to be closed.

Sources: [Cinnabarz/InzoneBattery](https://github.com/Cinnabarz/InzoneBattery/blob/0a48f8b03fd91f26803756b28b3af486eb0ad642/src/InzoneHid.cs) (`InzoneHid.cs`: `BuildCommand`, `WriteCommand`, `Parse`, `GetBattery`; MIT, verified there on the H5) and [RobotRoss/linzone-hub](https://github.com/RobotRoss/linzone-hub/blob/d12fe2376193aecf9caec1a438369c75b1aa173a/kernel/hid-inzone-battery-dkms/hid_inzone_battery.c) (`inzone_send_hci_get`, `inzone_parse_battery_event`, and `0x0ebf` in its device table). Confirmed on an H5: the dongle answered `02 0e 04 ff 0b 00 96 c3 14 04 10 02 01 00 32 b6`, 50 % on battery; the tray showed the charging state on the cable, and the icon went away while the headset was off.

### SteelSeries Arctis and GameBuds (other models)

**Connection:** wireless base station or dongle

The same `b0` exchanges as the Nova 7 above, applied to the other Arctis Nova 7 and Nova 5 family models; and the older Arctis 1, 7, 9, Pro Wireless, the Arctis 7+ (five-step level) and the GameBuds (lower earbud level) exactly as HeadsetControl documents their requests. The Nova Pro Wireless stations (`1038:12E0`, `1038:12E5`) are handled too. The models in the support table above are the confirmed ones. The Nova 3P Wireless joined them in [#149](https://github.com/HeyOkay/HaloBattery/issues/149): 58 % on @Pekish992's unit, read with this same exchange

Direct percentages must be integer bytes in `0..100`; five-step codes must be in
`0..4`. Both active GameBuds are validated before selecting the lower level.
Out-of-range fields give no reading instead of being clamped to 100%, following
the [pinned HeadsetControl layouts](https://github.com/Sapd/HeadsetControl/tree/62e361b085d7716805a3bf524bc9cca3761e0e15/lib/devices).
Two documented conversions remain: Arctis 7 (2018) can overreport, and Arctis 9
uses a raw scale with saturated endpoints. These follow the
[Linux driver](https://github.com/torvalds/linux/blob/fe2ec83746e501645709761605c2464a44fd2929/drivers/hid/hid-steelseries-arctis.c#L144-L198);
they are not treated as invalid direct percentages.

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

The battery command G-Helper uses: output report 0 `12 07` (65 bytes) on the vendor collection of interface 0, answered by a report echoing `12 07` with the battery in byte 5 (a percentage, or a level 0-4 on older models such as the Chakram and Keris Wireless) and charging in byte 10. 0 without charging is standby, not empty, so it shows nothing; `ff aa` (command not known) and an all-zero reply are not read as a level. Receiver and cable share one icon. Other layouts are described in their own sections below. **Unverified** - no ASUS mouse was on hand

### ASUS ROG Harpe II Ace / Harpe II Extreme

**Connection:** its own 2.4 GHz receiver (0B05:1AD0) or USB cable (0B05:1C69)

The same `12 07` battery command as the other ROG mice, from G-Helper (`app/Peripherals/Mouse/Models/HarpeIIAce.cs`, `AsusMouse.cs`), but on the receiver it goes to the collection `mi_02&col03` with report id 3, in a 64-byte packet: `03 12 07`, answered by `03 12 07 ..` with the battery in byte 5 and charging in byte 10 (hidapi keeps report id 3 in front). On the cable it is report id 0 on the vendor collection of interface 0. The receiver is shared with the Harpe II Extreme; as in G-Helper, "EXTREME" in the product name tells them apart. 0 without charging, `ff aa` and an all-zero reply give no level. **Confirmed on hardware** for the Harpe II Ace on its receiver (product name 'ROG SPEEDNOVA 8K RECEIVER'): `03 12 07 00 00 35 02 14 fd 0e 00 00 ..` read as 53 %, not charging. Bytes 8-9 look like the cell voltage in mV (`fd 0e` = 3837) and are not used. The cable id and the Harpe II Extreme are still untried

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

The keyboards are named from the pair list: Azoth, Azoth Extreme (and its SE),
**Azoth X** (`1c25`, #269), Strix Scope II 96 (and its RX), Falchion RX Low
Profile, and the **Strix Morph 96 Wireless** (`1cea`, #340) - the last on the
family's byte-6 layout. The reporter confirmed an accurate level on the
test build in #340 on 2026-11-05; the separate Maono microphone request remains open.
The Azoth X itself was confirmed on hardware in #269 with test build 2 (100%).

A reply of `ff aa` means the device does not know the command (a keyboard asleep on battery answers this), and an all-zero reply or a level of 0 without charging is standby: no reading rather than an empty battery. Ids absent from the pair list are skipped, as G-Helper does; the collections of one receiver are matched by the device instance in their path, so two receivers do not mix.

Confirmed on a Harpe Ace Mini and a Falchion RX Low Profile, and on a Keris II Ace and an Azoth by @comipa in [#231](https://github.com/HeyOkay/HaloBattery/issues/231).

### CHERRY MX 3.0S Wireless

**Connection:** 2.4 GHz receiver (`046A:00EA`) or USB cable (`046A:00E3`).

A 64-byte vendor channel on `FF1C:0092`. The frame is report id `0x04`, a
little-endian checksum of bytes 3-63, type `0x1A`, then payload `03 00 00 55`.
The reply keeps report id and type; byte 8 is the percentage and byte 9 is
non-zero while USB powered. From B1Chat/BatteryWatch (MIT), verified on a real
MX 3.0S through the dongle; the cable path is assumed. **Unverified in this app.**

### Corsair Dark Core RGB Pro SE

**Connection:** 2.4 GHz dongle (1B1C:1B7F)

The newer routed exchange that ckb-next calls "bragi" and OpenLinkHub "slipstream": 64-byte frames behind report id 0, route `0x09` asks the mouse behind the receiver (the receiver itself is `0x08`), command `0x02` "get property", property `0x0F` battery. The answer `01 02 00 <value:2 LE> ...` carries the level in tenths of a percent (`26 02` = 550 = 55 %). Frames that are not exactly that shape - the receiver's device records, its notices - are not levels. The M75 Wireless (`1B1C:1BDC`, the "SLIPSTREAM WIRELESS USB Receiver") answers the same exchange and is confirmed on hardware by @SpaceJesusCosmic in #301. The request and the answer were captured from the reporter's dongle in #56 and the exchange is confirmed on hardware: KKiruano's test build run showed the same level SignalRGB does. Because the answer echoes the command and not the property, a second app on the same dongle (iCUE, SignalRGB) can put an answer for another property on the channel; the provider asks twice and takes only two equal answers, waits in a time window (traffic frames end a read early) and holds a per-dongle budget. No charging state is reported. The wired id 1B1C:1B7E is left out.

### Corsair M65 RGB Ultra Wireless

**Connection:** 2.4 GHz SLIPSTREAM receiver (`1B1C:1BA6`) or USB cable (`1B1C:1BB5`)

The receiver publishes the same `FF42:0001`/`FF42:0002` pair as the Dark Core dongle and answers the routed `0x09` battery property. While the mouse is charging on its cable it changes identity to `1B1C:1BB5` and the receiver stops answering. The cable identity publishes the same pair and uses target `0x08` for both the level (`00 08 02 0F`) and the charge property (`00 08 02 10`), as OpenLinkHub's `m65rgbultraWU` package does. The two identities share the `corsair:m65` icon; when both are present the cable reading, with its charge state, replaces the receiver's. The receiver is also asked property `0x10` (it answered state 2, on battery, in #411), so the charging ring comes from the cable identity. Both identities answered on `FF42:0001` only, so the M65 is asked there and on no other collection, and the cable is asked on target `0x08` only: OpenLinkHub sends nothing to `0x09` on the cable, and in #411 that fallback only got the error `01 02 06`.

**Confirmed on hardware** in #411: test build 5 read 83-84 % while charging, which the reporter's iCUE showed as 85 %, and about 55 % unplugged. The cable's percentage rising while charging is real, not a stale or fixed value. The cable identity is preferred when it answers; the receiver is skipped while the cable owns the mouse. The receiver's own level is used when the cable is absent.

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

### IO Aurora

**Connection:** the receiver `3554:fb08` (product `IO Aurora Receiver`), or the mouse on its charging cable as `3554:fb09` (product `IO Aurora`, without "Receiver").

The receiver exposes the CompX control collection `ff02:0002` on interface 1. Its battery query is the shared CompX report-8 protocol: a 17-byte output report `08 04 ... <checksum>` with the level at byte 6 and the power/cable flag at byte 7, read back as a 17-byte input report (with a feature-report fallback). The request is read-only; settings and firmware commands are never sent.

The protocol is shared with the Lamzu Thorn/Atlantis family and is taken from the open-source `lavandiI/lamzu-battery` implementation.

Pinned source: [CompX request and parser, lines 7-14 and 65-121](https://github.com/lavandiI/lamzu-battery/blob/ef51ad02a875bd386f7ba22e4e91bfc98d41fe87/src/protocol/compx.rs#L7),
[request-before-read and feature fallback, lines 125-158](https://github.com/lavandiI/lamzu-battery/blob/ef51ad02a875bd386f7ba22e4e91bfc98d41fe87/src/hid.rs#L125),
and [power states, lines 77-85](https://github.com/lavandiI/lamzu-battery/blob/ef51ad02a875bd386f7ba22e4e91bfc98d41fe87/src/protocol/mod.rs#L77).
The owner's [charging inventory](https://github.com/user-attachments/files/35143662/diagnostics-charging.txt)
at lines 84-99 proves interface 1, `ff02:0002` for both `fb08` and `fb09`, with
product strings `IO Aurora Receiver` and `IO Aurora` respectively. Only these
exact VID/PID/product/collection combinations are queried; other collections are
never tried as a fallback. Multiple matching endpoints remain ambiguous.

The cable is preferred while present. Old queued input is discarded until the
queue is empty before a new request; a queue that will not drain is not queried.
An incomplete/refused write cannot refresh the cached sample. Opened native
handles provide immutable cached input/output/feature capacities: the 17-byte
request is padded by the transport and must complete to the full output capacity.
No additional caps probe is opened. Legacy HIDAPI must complete exactly 17 bytes.
The source's feature read is tried only after an accepted request; native feature
buffers use the cached capacity; a short optional feature capacity disables only the fallback, not the
primary interrupt query. Failed reads keep the previous sample greyed,
without updating its timestamp. Power state 3 keeps the percentage with charging
unknown; states above 3 and malformed frames are refused. Shared CompX/LAMZU
protocol code and its existing normal behavior are unchanged.
These collection/freshness/capacity safeguards have offline fake-device and
mocked-native-transport coverage; they have not been retested on the owner's
hardware after the review fixes.

While the mouse charges from a PC USB port it leaves the receiver and comes back as `3554:fb09`; the same query there carries the charging flag, and that is what draws the charging ring. A wall adapter never shows a state, because the mouse has no USB link to read then.

**Confirmed on hardware** ([#493](https://github.com/HeyOkay/HaloBattery/issues/493)): the reporter compared the level against IO Hub (the vendor app) and confirmed that the charging ring appears while the mouse is on the PC cable and clears when it is unplugged.

### MonsGeek FUN60 Pro

**Connection:** `3151:5026` receiver, interface 2, `FFFF:0002`.

MonsGeek Driver v4's `resources/app/dist/js/index.bc699543.js`,
`___dongleCheck24GStatusByF7`, defines feature report ID zero plus payload `f7`
and zero padding. Payload byte 1 is the keyboard level; payload byte 3 is its
link byte, zero while the keyboard is online (the driver reads
`isOnline: n[3] === 0`; payload byte 4 is the same for the mouse); payload
byte 6 distinguishes keyboard (`1`), mouse (`2`) and both (`3`). Only online
keyboard-containing replies may publish a current keyboard level. A mouse-only frame or a disconnected keyboard cannot refresh its history;
the last confirmed value is retained greyed for the existing cache interval.
The original reading was confirmed by the reporter. The online check follows
the driver's polarity, which a real ROYUAN receiver reply with its keyboard in
use also shows (`00 64 00 00 01 01 01` after the report id, #372); it sends no
new hardware commands.
### Inphic and Attack Shark mice (1D57 receivers)

**Connection:** the model's own 2.4 GHz dongle — `1d57:fa65` (Inphic In9 Pro) or `1d57:fa60` (Attack Shark X11, R1, X3 Max)

The mouse announces its level and charging state by itself; nothing is ever written to it. The frames were decoded from INPHIC HUB, the vendor's own Windows app, which opens the status collection `000a:0000` with hidapi (and keeps `ff00:0001` for its writes). Windows hands the report id back first:

    standard frame    03 <model> 40 <sub> <level>
    X6/R1 generation  03 10 40 <stage> <level/10>

`<sub>` is `0x01` for a level, `0x02` for full (shown as 100 %) and `0x03` for charging. A frame carrying only the charging state keeps the last level on screen, as in the vendor app; a level outside 1..100 is refused. On the X3 Max `0x02` is its cable state rather than a full battery — a 67 % unit sent it a minute after plugging in — so the last level stays and the icon shows charging. The `0x10` generation is the exception: the stage byte holds the DPI stage rather than the charging sub-command, the level arrives in steps of ten, and its frames carry no charging state at all — so an R1 on the cable keeps its normal icon by design. The dongle's config ACK (`03 10 50 ...`) and DPI-button report (`03 10 10 ...`) share the shape and are rejected by the `0x40` command check.

Model codes: `0x95 / 0x90 / 0x93 / 0x99` on the Inphic In9 Pro and `0x3F` on the Delux M900 Pro (see [Delux M900 Pro and VGN N75 PRO](#delux-m900-pro-and-vgn-n75-pro)) on `fa65`, `0x55` (X11), `0x4E` (X3 Max) and `0x10` (X6/R1) on the `fa60` receivers. Each receiver is read only for the codes proven on it. Frames arrive every couple of seconds; a poll that misses one keeps the last level, greys it out after a while, then drops the icon — a value is never invented.

Sources: INPHIC HUB's own parse (the vendor app the reporter linked in [#160](https://github.com/HeyOkay/HaloBattery/issues/160)); [blak0p/attack-shark-linux](https://github.com/blak0p/attack-shark-linux) (`docs/protocol-x6.md`, validated live on the dongle, MIT) for the X6/R1 shape. **Confirmed on hardware**: In9 Pro ([#160](https://github.com/HeyOkay/HaloBattery/issues/160), including the charging state), X11 ([#163](https://github.com/HeyOkay/HaloBattery/issues/163)) and R1 ([#69](https://github.com/HeyOkay/HaloBattery/issues/69), `03 10 40 01 09` = 90 %); the X3 Max's 30 % frame was caught in its reporter's diagnostics ([#228](https://github.com/HeyOkay/HaloBattery/issues/228)).

### LAMZU Maya X

**Connection:** 8K dongle (373E:001E) or USB cable (373E:001C)

The same feature report exchange as the WLmouse and G-Wolves mice (`00 00 02 02 00 83` out, `a1 00 02 02 00 83 <charging> <battery %>` back), on the vendor collection `ffff:0000` of interface 2 only; protocol from Sheroune/lamzu-battery-monitory (MIT). Confirmed on a Maya X on its 8K dongle

### LAMZU Atlantis V2

**Connection:** 2.4 GHz receiver (3554:F50D, "LAMZU Pro 1K Receiver") or USB cable (3554:F50F)

The Compx 17-byte power query used by the Pulsar / ATK family: the receiver publishes `ff02:0002` and answers level byte 6, on-cable byte 7 and millivolts in bytes 8-9. The cable id `3554:F50F` is shared with the LAMZU Thorn; Windows exposes the product string, so only a cable whose product string contains "ATLANTIS" is treated as the Atlantis Pro, and it shares the `pulsar:3554f50d` icon with the receiver. Confirmed on hardware in [#408](https://github.com/HeyOkay/HaloBattery/issues/408): receiver 70% matching the Lamzu driver, cable 95% while charging. The Atlantis cable is shown as "LAMZU Atlantis Pro (wired)" because that is the name Windows reports.

### GANSS GS3075T

The 2.4 GHz receiver (`0C45:FEFE`) exposes the Sonix vendor collection
`FF60:0061` on interface 3. The 32-byte query `AA 10 18 00 ...` is answered by
`55 10 18 ...` with the level at byte 25; byte 26 is `0x02` while charging,
`0x01` when the cable is in and the battery is full, and `0x00` on battery.
Both cable states are shown as charging, meaning on external power. On Windows, hidapi treats the first byte passed to `write()`
as the report id, so the query is sent as `00 AA 10 18 ...` (33 bytes to hidapi,
32 on the wire); without the leading zero the receiver answers only a short
`55 18` acknowledgement and no level arrives. The first request after the link
wakes up can be lost, and the receiver answers more readily while the 2.4 GHz
link is active; the query is still sent once per poll and never re-sent after
silence or a refused write. Measured on a GS3075T by the reporter of #379. The
same receiver ships with other Sonix boards (other GANSS T-series models, the
ATTACKSHARK X66), so only a receiver whose product string contains "GS3075T"
(the reporter's says "GS3075T 2.4G") is asked.

### Darmoshark N5

**Connection:** 2.4 GHz receiver (`1915:0723`, "1K NRF Receiver") or USB cable (`1915:073c`)

The non-numbered 64-byte output/input pair on `ff0a:0001`. The power query is
`41 00 81 01` followed by zeroes (the `0x40` bit marks the receiver; the wired
mouse uses `01`), with the last byte completing the report's sum to `0xA1`.
The reply is matched by operation `0x01` and sub-command `0x01`; the legacy
layout carries the percentage at byte 6, and the `15 19` pair at bytes 6-7 marks
the v2 layout with the percentage at byte 11. Only 0-100 is accepted. The
charging-state byte is not decoded, so the app shows no charging ring rather
than guessing.

Protocol and offsets from [OpenLogi's Darmoshark driver](https://github.com/ayasaki-exs/OpenLogi/tree/feat/darmoshark-driver)
and [ayasaki-exs/darmoshark-battery](https://github.com/ayasaki-exs/darmoshark-battery);
the receiver and its docs were supplied by the [#430](https://github.com/HeyOkay/HaloBattery/issues/430)
reporter. **Confirmed on hardware** in #430: the receiver's power reply read
**63%**, matching the mouse. Charging is not decoded.

### MCHOSE K87 / G98 Pro

MCHOSE's web driver (M HUB) defines a 19-byte getBattery template and appends
its additive checksum, producing this complete 20-byte output report:

    13 4A 01 00 00 ... 00 5E

The full reply is:

    13 4A 01 00 02 <level> <flags> ... <checksum>

on the `ff02:0002` vendor collection; the checksum is the low byte of the sum of
bytes 0..18. The captured K87 answer (`41E4:2001`) had level `0x64` (100 %), and
the byte after the level was `0x01` with the keyboard full and **not** on the
cable, so the low nibble is a full-status flag, not "charging now".

The **G98 Pro** (`41E4:2004`, product string `MCHOSE 2.4G Wireless`) publishes
the same command collection and is queried with the identical frame. On the
reporter's hardware it answers 94 % with flags `0x00` on 2.4 GHz and 50 % with
flags `0x10` on the charging cable, so the high nibble is the charge state: the
K87's `0x01` while full and off the cable stays a full status and never shows a
ring. Level and charging confirmed in
[#378](https://github.com/HeyOkay/HaloBattery/issues/378).

### MCHOSE A5 Pro Max

**Connection:** 1K receiver (2023:F013), 4K receiver (2023:F015) or USB cable (2023:F019)

A third MCHOSE exchange, on a third vendor id: 64-byte feature reports on the vendor collection (usage page `0xFFFF`, report id 0), from the community web driver [z750sasr/mchose-a5-pro-max-web-driver](https://github.com/z750sasr/mchose-a5-pro-max-web-driver) - the same route/length/page/command framing for every command, battery on page `0x00` command `0x83`, whose reply carries the `0xA1` marker, echoes page and command, and holds the charging state in byte 6 and the percentage in byte 7. Halo Battery refuses values above 100 rather than turning a malformed reading into a full battery. The transport is corroborated by [Klegus/mchose-macos](https://github.com/Klegus/mchose-macos), an independent reverse-engineering of the same vendor id's driver. **Confirmed on hardware** (@Pekish992, [#149](https://github.com/HeyOkay/HaloBattery/issues/149)): his 1K receiver answered 22 % and then 25 % while charging, matching the MCHOSE software and its charging animation; the 4K receiver and cable ids are still untried

### MCHOSE A7 V2 Ultra

**Connection:** 2.4 GHz receiver (3837:100B, RealTek strings)

The same protocol as the M7 Ultra on MCHOSE's newer vendor id, which the reference driver treats identically: the diagnostics in [#4](https://github.com/HeyOkay/HaloBattery/issues/4) show the same interface shape (collections 0xFF0B:0x104 and 0xFF01:0x01 on interface 2). The status read is documented on the shorter 0x11 report, so both report ids are tried, and the mouse is named from the receiver's own product string. Icon of its own, so it and an M7 Ultra stay two devices. **Unverified** — no A7 V2 Ultra was on hand, so a level out of range is refused rather than shown

The generic `3837` mouse exchange is restricted to two sourced identities:
`3837:100B` (A7 V2 Ultra, from [#4](https://github.com/HeyOkay/HaloBattery/issues/4)
and [#20](https://github.com/HeyOkay/HaloBattery/pull/20)) and `3837:100A`
(A7 V2 Pro, measured on one unit), always interface 2, `FF01:0001`. VID `3837`
also covers keyboards and products with different protocols: the K99 `3033` and
V7 `1016` diagnostics in [#189](https://github.com/HeyOkay/HaloBattery/issues/189)
do not justify sending the RealTek mouse feature request to them. Those and other
unmapped products remain unopened by that route. The separately confirmed V9
headset routes `6008`/`600A` are preserved. The original `5253`, G7 and A5 Pro Max
exchanges are unchanged.

### MCHOSE A7 V2 Pro

**Connection:** 2.4 GHz receiver (3837:100A, RealTek strings)

The same exchange as the A7 V2 Ultra, on the same collection (interface 2,
`FF01:0001`) and the same report ids. Measured on one unit: the 0x06 status read
is answered on the short 0x11 report with
`11 f9 c8 c7 e7 bf fa d5 fd fb f6 aa ff a8 ff ff` - 85 %, not charging, model
`0x4018`, link flag `0x09`. On its cable the mouse enumerates as `3837:4018`
(its model id, the same pattern as the M7 Ultra's `5253:0031`) and the same read
answers `11 f9 c8 c7 e7 bf fa d5 fd fb ff aa fe aa ff ff` - 85 %, charging,
flags `0x00` - while the receiver goes quiet, so the cable is the charging source.
Both identities share the family icon key, so the mouse keeps one icon. It is
named from the receiver's product string ("MCHOSE A7 V2 Pro").

### MCHOSE G7

**Connection:** USB (A8A5:2255, chip 'YJX-CHIP')

A different chip from the M7 Ultra, and a different protocol: a 65-byte output report `00 55 30 A5 0B 2E 01 01 01`, answered by an input report starting `AA 30` whose byte 8 is the level and byte 9 the charging flag. Written from @kek353's own monitor and the device dump in [#8](https://github.com/HeyOkay/HaloBattery/issues/8); only its 0xFF01 vendor collection is written to. Confirmed on @kek353's G7, which answers `aa 30 a5 0b 0a 01 01 01 2e 00 00 00` — 46%, not charging, the same level their own tool shows — and again on its cable (`aa 30 a5 3c 0a 01 01 01 2e 01 00 00`, byte 9 = 1 while charging) with the PID unchanged, so a G7 keeps one icon on the dongle or on the cable. Only the `AA 30` header is relied on: byte 3 differs between the two (`0x0b` against `0x3c`). A level out of range is refused rather than shown

### MCHOSE M7 Ultra

**Connection:** 2.4 GHz receiver (5253:1020)

The vendor collection (usage page 0xFF01; the sibling 0xFF0B never answers): feature report 0x11 (the shorter report, tried first) or 0x12 with command 0x06, every payload byte inverted, which returns `53 52 31 00 02 05 07 00 09 64 00 64` — vid 0x5253, model 0x31, firmware, flags, then the level and the charging byte (1 while charging). The request has to be repeated for each read, and the receiver only relays a real value while the mouse is awake: asleep it answers with zeros, so a silent mouse keeps its last level on a greyed icon. On the cable the mouse answers on its own PID (5253:0031, the number it reports as its model id) and the receiver goes quiet: both connections share one icon, and whichever one reports charging wins

### MCHOSE X9

**Connection:** 2.4 GHz dongle (3837:6045, C-Media strings)

MCHOSE's own web driver, M HUB (www.mchose.com.cn, `assets/main-DQZs6ZzX.js`, fetched 2026-11-17), lists the headset as `createAudioHidProduct({modelKey:"MCHOSE X9", vendorId:14391, productId:24645, protocolProfile:AUDIO_PROTOCOL_PROFILE.BLK})`. The only request sent is that BLK profile's `audio.blk.readBattery`: output report 0x55 with 63 data bytes, `65 01` and zeros. The reply on input report 0x55 carries the level in data byte 1 and the state in byte 2, decoded the way M HUB's `parseBatteryResponse` does it: 2 discharging, 3 charging, 4 full, 26 asleep (no level shown). Any other state, a level above 100 or a short reply is refused. The report lives on the vendor collection `ff90:0001`, the only one written to. Confirmed on an X9 on its dongle: `55 65 28 02`, which is 40 % and discharging, within 30 ms. On its cable the headset drops its radio link and the dongle stops answering. The headset then shows up as a second USB device, 3837:6044 "MCHOSE X9 HEADSET", which M HUB does not list, so nothing is written to it: a charging X9 keeps its last level greyed out for five minutes, like the other MCHOSE devices, and no charging state is shown. State 3 is decoded as charging, as M HUB decodes it, but has never been observed. The level is also confirmed on Windows by @Kolomhon in [#458](https://github.com/HeyOkay/HaloBattery/issues/458), with test builds carrying this code: it showed the correct percentage, and with the cable in, M HUB switched to wired mode without a charging state, as the app does.

### MCHOSE K99 V3 (2.4 GHz)

**Connection:** 2.4 GHz receiver (3837:3033, "MCHOSE K99 V3 2.4G"), generic-desktop HID collection `0x0001:0x0000` on interface 1.

The K99 V3 uses M HUB's **GLW func-config** read on the id-less 64-byte generic-desktop collection (usage `0x0001:0x0000`, with `0xFF31:0x0074` as the fallback that answers the same frames):

    request  00 | 55 05 00 38 38 00 00 00 ...     read 56 bytes at offset 0
    reply    AA 05 00 <checksum> 38 00 00 00 ...  payload starts at byte 8

The request header is flag `0x55`, command `0x05`, reserved, checksum, size and little-endian offset; for a read with no payload the checksum is size + offset = `0x38`. The reply carries the same fields and its checksum covers size + offset + payload. The battery byte is **payload offset 32**: bit 7 is charging, bits 0-6 are the percentage. The earlier `55 03 00 38 38` query returned a different info frame (52 % in the reporter's diagnostics while M HUB showed 90 %), so it is not used. Protocol from [krisgfx9/Wireless-Device-Battery-Monitor PR #1957](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor/pull/1957), recovered from M HUB's own web driver and confirmed on a G75 V2 on the same `3837:3033` receiver. Confirmed on the reporter's K99 V3 (@Kolomhon, #458): the level agreed with M HUB within its 10 % display step, and the charging bit followed the cable. The query is only sent shortly after real keyboard input, like the ATK keyboard provider: on an idle keyboard the request itself can wake the keyboard, and the wake can reach the desktop as input (#458 follow-up).

### Zaopin Z2 4K

**Connection:** 2.4 GHz 4K receiver (`3554:F527`), USB-C (`3554:F526`), and Bluetooth LE.

The 4K receiver and the mouse on USB-C both answer the same 17-byte Compx
power query (command `0x04`) on the `ff02:0002` collection of interface 1.
The Pulsar/ATK reader uses this frame from `andrewrabert/python-pulsar-mouse-tool`.

### Pulsar X2 V2 Mini, Pulsar X2 CrazyLight Mini, ATK VXE R1 SE+, VXE R1 Pro Max

**Connection:** 2.4 GHz dongle (3554:F508, 373B:1085, 3554:F58A, 3554:F505, 3710:5406) and USB cable (3554:F507, 3554:F58F, 3554:F58C, 3554:F506, 3710:3414)

17-byte big-endian frames, report id 0x08: command 0x04 asks for the power details and answers with the level in byte 6, the on-cable flag in byte 7 and millivolts in bytes 8-9, with a checksum (0x55 minus the sum of the first 16 bytes) in byte 16. From andrewrabert/python-pulsar-mouse-tool, which also backs the "HID: pulsar" driver in review for Linux; the Kysona M600 and VXE Dragonfly R1 Pro use the same protocol but their ids are not claimed here. The ATK/VXE control panel of the OpenMouse project (@openmouse/protocol, drivers/atk) lists the R1 Pro Max receiver as 3554:f58a and reads it with the same command 0x04 frame, opening the collection with usage page 0xFF02 and usage 0x0002 - the R1 Pro Max dongle has five other interface-1 collections and the first of them is not the one that answers, so that collection is now preferred; a collection whose output report cannot carry the 17-byte frame is skipped, because Windows refuses that write and the refusal looks exactly like a device that is switched off. The framing is also in G-Wolves' own web driver (mouse.xyz), whose Compx class sends the same report-0x08 frame with a checksum of 0x55 minus the sum of the payload. The R1 Pro Max is confirmed on hardware on both of its transports, by the reporter of #87: this provider read the mouse on its stock 1 kHz receiver, on the `ff02:0002` collection, and on its cable the level agreed with ATK's own panel (hub.atk.pro) with the charging flag following the cable in both directions, so the cable id (3554:F58C) is read on hardware as well rather than only claimed from their report. A frame whose checksum does not match is refused rather than shown. The X2 CrazyLight Mini (#164) was tested by its reporter on both of its transports with this same frame - the dongle and the cable both answered command 0x04 with valid checksums, the cable reporting charging - so both of its ids (3710:5406, 3710:3414) are claimed from that report; the app-side reading was confirmed on their run of this branch in #164 - the dongle read 20 %, the same raw level as their own test, and the cable read while charging.

VGN's Dragonfly 4K receiver (`3554:f505`) and the same mouse on its cable (`3554:f506`) follow the same exchange - the receiver lists six vendor pages on interface 1 with `ff02:0002` among them. The receiver is **confirmed on hardware by @TweaknFreak** ([owner confirmation](https://github.com/HeyOkay/HaloBattery/issues/323#issuecomment-6637051514)): the level it showed agreed with the vendor tool's 53 %, so the id is claimed from a working read rather than from the collection shape; the owner later [confirmed the cable and return to the receiver](https://github.com/HeyOkay/HaloBattery/issues/323#issuecomment-6704340833). The maintainer [recorded both ids as confirmed in stable 1.17.0](https://github.com/HeyOkay/HaloBattery/issues/323#issuecomment-6879098152).

The ATK X1 Pro 1K dongle (373B:101A, #230), the ATK Fire X 8K (373B:101B) and the Compx keyboard receivers (3554:FA09 - seen on the AULA F75 in #247 and the LEOBOG Hi75C Pro in #257) list the same ff02:0002 control collection and are read with the same frame; the receiver id is shared by those keyboards, so the tray name is the receiver's, not one model's. The Corsair Sabre v2 Pro Ultralight (1B1C:2B2A, #290) is the same exchange too:
its receiver publishes ff02:0002 and no ff42 at all, so it is read here rather than by
the bragi path. The Teevolution Terra Pro (3554:F523, #300) is the same exchange behind the 'RapidSync' product string, with its vendor collection on interface 2. Its 1K sibling `3554:F520` is the same reader and is now claimed too; **confirmed on hardware** by @7-1ME in #448 at 100% (4235 mV). The ATK X1 Pro is [confirmed on hardware by @gnrlk3nobi](https://github.com/HeyOkay/HaloBattery/issues/230#issuecomment-6631717770) in #230: it read immediately, the level stayed within about 5 % of ATK HUB in both states (the two apps sample at different moments, so the sign flips), and the charging state followed the cable. The keyboard receiver and the Fire X 8K are still waiting for their testers.

### Attack Shark R5 Ultra

**Connection:** 2.4 GHz receiver (373E:0047)

The R5 Ultra uses the R6/R8/M5 Ultra feature-report protocol, recovered from the
vendor WebHID driver by [mohammed-just/attack-shark-r6-cli at b343854](https://github.com/mohammed-just/attack-shark-r6-cli/blob/b3438542106999adb743858f267ddc33a167e7d7/r6ctl.py#L354-L373)
(MIT). A 64-byte feature report with report id 0 carries `02 02 00 83` at bytes
2-5; the reply starts with the `0xA1` marker and carries the battery percentage
and charging flag. Either sourced byte order is accepted only when the flag is
0/1 and the percentage is 0-100; malformed pairs cannot become a live 0% reading.
The provider sends only that read-only battery command, tries each receiver
collection in the reference's order, and sends no configuration, DPI, lighting or
firmware command. **Confirmed on hardware** by the reporter of
[#372](https://github.com/HeyOkay/HaloBattery/issues/372): the R5 read 91 % on its
2.4 GHz receiver. The X820 Ultra on its own receiver (`3151:5006`, product
`2.4G Wireless Keyboard`) reads through the ROYUAN/MonsGeek `0xF7` status query
and showed 100 %; its wired id (`3151:5002`) does not carry a battery command and
is not used. This keyboard status has no charging flag, so charging stays unknown.

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

### Glorious Model O, O-, D and D- Wireless

**Connection:** 2.4 GHz receiver (258A:2022, 2024, 2023, 2025)

Sinowealth's framed battery query, documented and confirmed on hardware by Rodrigo-200/GloriousBatteryMonitor-Go (crediting AwesomeTy18/GloriousBatteryMonitor, glorious-mouse-battery-system-tray, glorious-indicator and OpenRGB's SinowealthGMOWController): a **feature report**, report id 0, of 65 bytes (id + 64) shaped `00 | 00 00 02 02 00 83 | 00 ...` - DevSel `0x02` wireless, CmdType `0x02` battery, Func `0x83` - then a ~100 ms wait for the RF round trip and a GetFeature of the same report. The answer carries `0x83` at byte 6, the charging byte at 7 (0 = no, non-zero = on the charger) and the level at 8; status byte 1 is `A1` awake, `A2` waking, `A4` asleep. Only positively awake `A1` replies publish a percentage; request echoes and provisional statuses cannot become a false 0%, while genuine awake 0% and 100% remain valid. The receiver publishes vendor collections on `ffa0` and `ffff`; the one whose feature report is 65 bytes is tried first.

The same query, with the same DevSel, is what [korkje/mxw](https://github.com/korkje/mxw) sends to the Model O, O-, D and D- (`src/report/battery.rs`, interface 2; its `SUPPORTED_MICE` table holds the wireless/wired id pairs). Those ids are now in the provider's table - 2022/2011, 2024/2013, 2023/2012, 2025/2014 - with the Model O confirmed, the Model O- confirmed by its owner in #221 (level and charging, test build `glorious-models-test1`), and the Model D and D- **untried** on hardware.

The level is **confirmed in this app on hardware by @gumocska** (#281): the icon appeared and the percentage matched Glorious Core, tracked live for hours and, after the channel-collision fix below, with SignalRGB installed. Charging is confirmed too: the state appears when the cable goes in and clears when it comes out. The percentage stays at the value from the moment charging started and blinks, because the mouse stops answering the receiver over the cable - it is read again when the cable comes out, which is when the real level appears. Charging over a cable is read the way GloriousBatteryMonitor-Go reads it - from the **wired id appearing** (`258a:2011` for the Model O, `DeviceDatabase.cs`), not from the reply: while the mouse charges over USB it leaves the 2.4 GHz link, so the receiver goes quiet and the provider reports the wired id as charging, keeping the last level. Charging from a wall adapter gives no signal at all - the mouse is not talking to the receiver, so there is nothing to read and the icon keeps its last value.

Glorious's feature report is a **shared mailbox**: any app writing report 0 on interface 2 overwrites the answer. With SignalRGB running, its lighting frames go to that report about 28 times a second, every read-back is its status frame (`a1 00 02 08 02 00 01 ff ...`, level `0xFF`), and no reply in a 33-query capture carried `0x83` (#281). The provider tells that case apart from sleep and from its own waking state: a reply from a different exchange (no `0x83` function byte) means another app is writing to the same report, while the exchange's own `A2` waking and `A4` asleep answers are reported as no level yet. When the write itself is refused (`feature write failed`) it says another app holds the feature report and marks that reading stale, without stopping the other collections or mice in the same poll. SignalRGB's own per-device setting decides which of the two happens: with its control of the mouse switched off, both apps coexist and Halo reads normally. No app can win the race by retrying, and HID offers no exclusive claim on a feature report. One capture with Glorious Core open shows Core **suppresses** SignalRGB's writes to the mouse (no lighting frames at all while Core polls), which is why the reading survives next to Core and not next to SignalRGB alone.

Lamzu Tachi no longer borrows this provider or chooses arbitrary vendor
collections. Its separately sourced Aurora diagnostic tester is documented below;
normal Tachi polling remains held.

### SteelSeries Arctis Pro Wireless

**Connection:** `1038:1290` base station.

Preserve the existing `41 aa` status and `40 aa` battery exchange first. Only if
it receives no accepted answer is the sourced report-ID-zero form tried. A valid
off reply or a partially accepted exchange prevents that fallback, following the
review of [#389](https://github.com/HeyOkay/HaloBattery/pull/389). Levels are five
coarse steps (`0/25/50/75/100`); the protocol does not supply charging state.
The PR author observed 75% on hardware. Local validation uses fake HID, including
no reply, refused writes, valid off status and partial/malformed battery replies.

### SteelSeries Aerox 3 Wireless

**Connection:** 2.4 GHz dongle (1038:1838)

The receiver's battery query: it flags its configuration opcodes with `0x40` over their wired values, so the wired `92` stays silent and a 64-byte `00 d2 ...` is answered by a report echoing `d2`. The level byte is the charging flag in bit 7 plus a 1..21 step value (or a direct percentage above 21), as yurtemre7/steel-mouse decodes it; the interface and the product id come from alloyctl's reverse engineering of this exact id, cross-checked against the capture notes at gort818/aerox3-wireless. A level byte of 0 means off or asleep rather than empty, and the 2.4 GHz link sleeps when the mouse is idle - it wakes on the next movement, so a poll that lands on a sleeping mouse simply shows nothing. Works alongside SteelSeries GG; the CS2 Dragon Lore edition (1038:1878) shares the protocol and is included untested The Aerox 5 Wireless (1038:1852, 185C, 1860) and Aerox 9 Wireless (1038:1858, 1874) in 2.4 GHz mode use the same battery exchange in rivalcfg (`0x92` with the wireless flag `0x40`, level `(value - 1) * 5`); the level is confirmed on an Aerox 9 Wireless by @AJD00m in #79 (`d2 04 ...` = 15 %, the same as SteelSeries GG), the charging bit is not tested yet

### SteelSeries Rival 3 Wireless

**Connection:** 2.4 GHz dongle (1038:1830)

The mouse exchange on the same interface and the same `0xFFC0` configuration collection as the headsets, but not the `b0` request: a 64-byte `00 aa 01 ...` out, answered by a report carrying the level. The two references disagree about the reply and neither has been on hardware here, so both shapes are read: a reply that echoes `aa` (level in byte 1, charging in byte 3, as yurtemre7/steel-mouse reads it) and a 3-byte reply read the way flozz/rivalcfg does (level in byte 0, charging in byte 2, the same request and the same hidapi read). A leading report id byte of `0x00` is skipped, a level above 100 is refused in both shapes, and in the 3-byte shape the charging byte has to be 0 or 1 so a stray report cannot pass as a level - the diagnostics print the raw reply and say which shape was used. The collection is picked by usage page `0xFFC0` rather than by interface number, because the Rival 650 has it on interface 0 (rivalcfg issue #202). Works alongside SteelSeries GG; the Gen 2 revision (1038:1872) is included untested

### VAXEE wireless mice (3057)

**Connection:** 4K dongle (3057:2001 and the other VAXEE receiver ids) or USB cable

64-byte feature reports `0e a5 <cmd> 01 01` (report id 0x0e, header 0xa5, read 0x01, one data byte), answered by a report that echoes the command with its value in byte 5. Command `0x0b` is the battery in 5 % steps (byte 5 × 5, accepting only raw values 0–20) and `0x10` the charging flag (non-zero = on the cable). The command echo is checked, so a stale reply is refused rather than read as a level. The receiver leaves its answer in the feature buffer after about 100 ms, and a sleeping mouse can ignore the first exchange, so the pair is tried twice 300 ms apart. Matching is by vendor id and the `0xFF05` usage page rather than product id, so the dongle and a cabled mouse are both found; they share one icon, the cable is asked first, and a reading that reports charging wins. A receiver reports its own part number rather than the paired mouse's model, so a reading taken from the dongle is shown as "VAXEE mouse" rather than a part number; a cabled mouse reports its own model. While any `0xFF05` collection remains, a silent mouse keeps its last level on a greyed icon for five minutes (the same `ASLEEP_KEEP` as the other receiver providers) - losing the receiver, or the cap expiring, drops it. The cable and its dongle share one key and one icon, so two VAXEE mice connected at once are merged into a single icon. Ported from [ryanlewis/mousebatt](https://github.com/ryanlewis/mousebatt)'s `src/protocol.rs`, whose VAXEE support was tested on an XE-S behind a VXD02 4K dongle; the battery command comes from [stuffz/mouse-battery-monitor](https://github.com/stuffz/mouse-battery-monitor) and the framing from the [VAXEE Control Center](https://vcc.vaxee.co/) web driver. **Confirmed on hardware in Halo Battery** on an E1 Wireless over its receiver; other models and the cabled path are untested

### WLmouse Beast X and Beast X Mini Pro

**Connection:** 8K or 1K receiver, or USB cable

The same feature-report exchange as the Beast X Max (`00 00 02 02 00 83` out, `a1 00 02 02 00 83 <charging> <battery %>` back), on the vendor collection `ffff:0000`. The provider's receiver table lists `0xA887` (Beast X) and `0xA868` (Beast X Mini Pro); the Max is confirmed, these two are not.

### WLmouse Beast X Max

**Connection:** 8K receiver (`36A7:A880`) or USB cable (`36A7:A881`).

Use the existing report-0 feature query on `ffff:0000`. The protocol framing
and preferred collection come from
[incconutwo/mouse-battery-tray, devices.py](https://github.com/incconutwo/mouse-battery-tray/blob/main/devices.py#L80-L175).
The cable identity is confirmed by the owner's 22 November 2026 capture:
`WLMOUSE BEAST MAX`, interface 2, `ffff:0000`, reply
`00 a1 00 02 02 00 83 01 60 00 00 00` = 96% and charging through the production
transport. The owner also confirmed the updated Desktop build works both on
the cable and after unplugging it back to the receiver.

Active requests are limited to the four explicit PIDs (`A887`, `A868`, `A880`,
`A881`) on `ffff:0000`. Other collections and unlisted identities receive no
feature query; the existing passive vendor heartbeat fallback is retained.
The cable is not a receiver: its charging reading wins without querying the
silent receiver, and unplugging it restores the receiver with the same icon
and device key. Silent-device backoff is unchanged.

### Ninjutso Sora V3 (093a:eb02)

**Connection:** 2.4 GHz receiver `093a:eb02` or USB cable `093a:e010`; the
vendor collections are on usage page `0xFF01` (main) and `0xFF00` (secure); only
`0xFF01` is asked, because no source or capture shows report 6 on `0xFF00`.

The Sora V3 uses the current NinjaForce WebHID protocol, not the Sora V2
report-5 protocol. Battery and charging are two separate read commands on
feature report 6, with a 15-byte packet:

    request  12 00 00 01 00 00 00 ...   battery
    request  11 00 00 01 00 00 00 ...   charging
    reply    06 <cmd> 00 00 01 00 01 00 <value> ...

The first data byte is the level (0-100) or the charging flag. The cable is
queried first when both connections are present, so charging follows the
cable; the receiver reports the same level wirelessly. The source is
OpenMouse-Project/mouse-protocol's Ninjutso codecs and driver, derived from the
NinjaForce client; it is not marked hardware-verified there. Tested by
@Cynersi on both connections: the wireless level and the cable level matched
NinjaForce, and the cable showed charging.

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

### ATK V75X

**Connection:** 2.4 GHz receiver (`320F:5088`, product `ATK V75X 2.4G Dongle`) and USB cable (`320F:5055`, product `ATK V75X`)

Read-only battery command from [ATK HUB](https://hub.atk.pro/), Evision keyboard controller in [`keyboard-info.util-D8BvmqVT.js`](https://bpcdn.atkgear.com/hub-v3/production/3.2.29/static/keyboard-info.util-D8BvmqVT.js) (driver 3.2.29). The dedicated ATK keyboard provider (`providers/atk_keyboard.py`) handles this exchange. On vendor collection `FF1C:0092`, send output report `04` followed by 31 bytes: a little-endian request id, command `1A`, then zeroes. The input report echoes the id and command; payload bytes 7 and 8 are percentage and charge state (`1` = charging on the tested V75X; `2` is also accepted, as in ATK's driver). Reject unrelated replies, nonzero offsets and levels above 100. The product string is required because other keyboard brands share this VID/PID. No configuration handshake is needed. **Confirmed on a real V75X receiver**: three matching 59% discharging replies on 2026-10-20. Charging was subsequently verified on the same 2.4 GHz receiver: `04 01 7b 1a 00 00 00 00 3c 01 ...` reported 60% with the charging cable connected, then `04 01 7c 1a 00 00 00 00 3e 00 ...` reported 62% and not charging after unplugging. State `2` remains a vendor-documented charging variant, unverified on this unit.

On the cable the keyboard answers the same request on the same `FF1C:0092` collection: **confirmed by the PR author on a real V75X** in [#368](https://github.com/HeyOkay/HaloBattery/pull/368) on 2026-11-09, three replies `04 0n 01 1a 00 00 00 00 5b 01` (91%, charging). Cable and receiver share one icon; when both are connected the cable is read. The cable powers the keyboard, so it is read on every poll without the typing pause below. After switching back to the receiver, the last level is kept but not the charging state.

Battery queries pause after ten seconds without input from this ATK keyboard.
A Windows Raw Input listener reads device headers only, retains no key contents,
and ignores mouse input, other keyboards and vendor battery reports. While typing,
a good reading is reused for five minutes before the next request, and a failed
request is not repeated within 30 seconds. An ATK key event wakes the poll loop
only when a request is due, at most once per 30 seconds, so bursts of typing do
not trigger repeated polls of every device. While idle, the tray icon keeps its normal appearance and percentage; the right-click menu alone identifies the value as cached and polling as paused
and does not claim a current charging state. Startup waits for typing rather
than waking an idle keyboard; a failed listener also leaves queries paused.
No protocol or sleep-setting writes are introduced. Physical sleep still needs
an idle test on the keyboard; absence of battery requests alone is not a power measurement.

### Keychron Ultra-Link 8K, Keychron M5

**Connection:** 2.4 GHz receiver (3434:D028) and USB cable (3434:D048)

Keychron's vendor protocol on interface 4: `b3 06` (status), normally a 64-byte feature report, answered by a 64-byte input report `b4 06`. Byte 20 holds the level in its low seven bits and the charging flag in bit 7. On the captured `ffc1:0001` collections, a refused feature request (Ultra-Link 8K, #373) or an accepted but unanswered request (Link-KM, #270) falls back to the identical output report. From csutcliff/keychron-battery-dkms, which implements it for these two ids. The **Nape Pro** (`3434:d026`, the "Keychron Link-KM" receiver, #270) speaks the same exchange but publishes its vendor collection on **interface 1** (`ffc1:0001`), not 4, so the provider picks interface 4 first and then the `ffc1` vendor page. Only a documented battery collection is ever opened: `ffc1:0001` (interface 1 on the Link-KM, interface 4 on the Ultra-Link 8K per the #373 diagnostics) or a vendor page on interface 4 other than VIA (`ff60`), the interface the Linux module binds to; the output-report retry is made only on `ffc1:0001`. A receiver that shows only keyboard, mouse, consumer, VIA or other vendor collections is skipped without opening any of them. A single receiver id can appear twice (two Ultra-Link 8K dongles); every candidate collection is queried and each answering receiver gets its own icon, keyed on its own HID path (`keychron:<pid>:<hash>`), so a receiver keeps its key, names and history when another one sleeps, is unplugged or is plugged back in. The earlier shared key `keychron:<pid>` moves to the receiver once, through the app's key migration, when exactly one receiver of that id is plugged in and answers; with two plugged in nothing moves, because which of them owned it cannot be known. The level was confirmed on hardware: the reporter's raw capture read 99 % on an M6 8K (`63`, charging bit clear) and 99 % with bit 7 set while on a charger (`e3`), and a G4 with the same `D028` receiver read correctly on the test build in #373. The Ultra-Link 8K charging indication has not been confirmed in the app yet. On the Link-KM receiver, @cocowalla confirmed the M6 level against Keychron Launcher and the charging ring following the cable in #270. A level above 100 after masking bit 7 is refused rather than shown.

### Lofree Hyzen

**Connection:** 2.4 GHz dongle (388D:0025)

The transaction Lofree's own web driver (hyzen.lofree.tech) uses, on report 0x04 of the vendor collection 0xFF1C:0x92: start (`00 00 01`), the command, end (`00 00 02`), each acknowledged in byte 2. Command `AA` asks if the keyboard is online, command `1A` returns the level in byte 7. As in the web driver, only the dongle is read, and not while the same keyboard is on its cable; no charging state. **Unverified** - no Lofree keyboard was on hand

### Lofree Flow Lite84

**Target:** `05ac:024f`, captured product `2.4G Wireless Receiver`, control
collection `FF02:0002`. The USB ID is shared with Apple keyboards and AULA F108
receivers, so a matching ID elsewhere never authorizes a Flow request.

Source: Lofree Control HUB (`lofree.tech/home`, bundle `index-BTVblIUr.js`),
recorded in [`providers/lofree.py`](../providers/lofree.py) and
[PR #169](https://github.com/HeyOkay/HaloBattery/pull/169).
Its 17-byte output frame uses report ID `08`, keyboard flag `80` at index 5,
and a checksum whose complete-frame sum is `55`. Command `03` must report online
before command `04` supplies the percentage at index 6 and charging flag at 7.

Only that receiver product and control collection are selected. Unknown output
caps remain compatible with the previously supported path; known incompatible
caps refuse the request before opening a device. There is no fallback to another
collection or to an AULA receiver. Existing community hardware confirmation
does not represent a new hardware test of this guard.

### Logitech G502 LIGHTSPEED, G502 X PLUS

**Connection:** Lightspeed receiver (046D:C539, 046D:C547)

HID++ 2.0 on the receiver's vendor interface: the device name (feature 0x0005) and the first battery feature the device supports (0x1004 unified battery, 0x1000 battery status or 0x1001 battery voltage; the G502 LIGHTSPEED reports voltage, converted to % with the Li-ion curve used by Solaar, the G502 X PLUS the unified battery percentage). The icon follows the device's unit id (feature 0x0003). Works alongside G HUB

### Logitech G PRO X3 SUPERSTRIKE

**Connection:** LIGHTSPEED receiver (`046d:c54f`); wired mouse
`046d:c0a9`.
This is the **mouse**, not the PRO X 3 LIGHTSPEED headset.

The mouse exposes HID++ on Logitech's alternate vendor collections
`ff43:0301` (short, seven-byte reports) and `ff43:0302` (long, 20-byte reports).
For these two product IDs only, the Logitech provider treats them as the
usual short/long HID++ paths, keeping the original `ff00:0001` / `ff00:0002`
paths preferred wherever both exist. The receiver is queried through the
usual paired-device slots (1-6), while the wired mouse uses slot `0xff`.
The existing HID++ 2.0 battery feature lookup and status parsing are reused;
no new battery command or device-setting write is introduced.

**Protocol sources:** [OpenLogi PRO X3 SUPERSTRIKE receiver support
(PR #2191)](https://github.com/AprilNEA/OpenLogi/pull/2191) identifies the
`c54f` receiver and the `ff43:0302` long collection with its `ff43:0301`
short sibling on Windows. The short collection is recognized when present.
This change only maps the known collections to the existing
read-only HID++ battery requests in `providers/logitech.py`.

**Hardware reports:** in [PR #465](https://github.com/HeyOkay/HaloBattery/pull/465),
the author reported a physical PRO X3 SUPERSTRIKE under Windows on build `fffb327`: the reported
battery percentage matched Logitech G HUB; charging was detected; the tray
icon greyed out after powering the mouse off and returned on reconnect.
The author subsequently reported battery and charging on `c0a9` with the receiver physically unplugged.
After #512 merged, the same author tested the merged build again
([#504 follow-up](https://github.com/HeyOkay/HaloBattery/pull/504#issuecomment-7060984742)):
with the receiver removed and the mouse on USB-C the level still matched G HUB and the charging
animation appeared; reconnecting the receiver stopped the animation and returned the wireless
reading. The simulated status values (0/1/3) and low-battery notifications are still not
exercised on hardware. Automated tests cover collection routing, charging statuses and legacy
`ff00` precedence. `C54D` retains its existing route; it is excluded from the X3 fallback
following the scope review in PR #465.

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

### 8BitDo Ultimate 2 Wireless (XInput)

**Connection:** Bluetooth (level); 2.4 GHz receiver (`2dc8:310b`) or USB (gamepad only).

An XInput pad, so Windows.Gaming.Input owns the battery report: over the dongle it
returns its placeholder (`remain=1000`, `full=1000`), and Windows Settings shows no
battery for the device either (checked by the reporter). The receiver's vendor
collection (`ff7a:0001`) is silent - the diagnostics recorded `nothing received` - and
its XInput-facing collection `0001:0005` must never be opened: reading it makes XInput
fail with `read error` and the pad disappears on 2.4 GHz and USB (test build 3, #461).
There is therefore no level to read over the dongle, and none is invented.

Over Bluetooth the controller reports one, and the Bluetooth provider shows it - 60 % on
the reporter's unit. The XInput provider names the pad **8BitDo Ultimate 2** from the
product id in its Windows.Gaming.Input report, because Windows calls it "Xbox 360
Controller for Windows" - but only while it is the only controller connected (one XInput
slot, one report). Windows' report order is not the XInput slot order, so next to another
controller the pad keeps the generic name rather than risk renaming the other one. Its
Windows.Gaming.Input reports are deliberately left out of the trusted set: the
`remain=full=1000` placeholder would otherwise become a false 100 % (#110, #191).

### ASUS ROG Raikiri II

**Connection:** 2.4 GHz receiver (`0B05:1C92`), vendor collection `ff03:0001` on interface 4

Read-only output report `B0 12 04` (64 bytes, zero padded); the reply carries
the same header, the level at zero-based byte 5 and the charging state at byte
10. **Confirmed on the reporter's hardware** in #269 with test build 2: 70%
over wireless, then 69% with the charging ring on the USB cable; the diagnostic
probe showed the 64-byte write and `0x01` at byte 10. The XInput provider pairs
the reading to the controller's one slot and uses the ROG name, so the generic
Windows Xbox entry is not duplicated. Test 1's feature write was refused; test
2 uses output/input reports.

Source: no public project documents this query. The controller's owner
(@nickfizz0208) identified it as the ASUS vendor power query in #269 and checked
it with his own build against Armoury Crate (65% in both); the charging label of
byte 10 comes from the ASUS GearLink SDK, as he reported there.

### Fractal Design Scape

**Connection:** 2.4 GHz dongle (`36BC:0001`) or the headset on its USB-C cable (`36BC:2001`).

Both expose a 64-byte vendor collection on usage page `FF00` / usage `0001`. Output report `0x02` carries the device type and command; input report `0x02` echoes them. `GET_DEVICE_STATE` (`0x21`) is sent to the dongle type `0x11` and then the headset type `0xF1`: dongle payload byte 4 is 1 when the headset is linked, headset byte 15 is the percentage and byte 23 is 1 on the cable. From Fractal's Adjust Pro WebHID configurator. Only the state query is sent. **Unverified in this app.**

### Arbiter Studio Akitsu

**Connection:** 8K receiver or USB cable, vendor 373E: Akitsu `000A`/`0009`, Akitsu 3950 `0039`/`0038`, Akitsu Medium `0067`/`0066` (receiver/cable).

Vendor collection `FFFF:0000`, 65-byte feature report 0. A feature report with bytes 3=02, 4=02, 6=83 is followed by a feature read; status A1/A2 means answered, A0 means the mouse is asleep. Byte 7 is charging and byte 8 the percentage (both shifted when the report id is present). Read-only; no mode writes. **Unverified in this app.**

### Areson / Redragon M915, M913

**Connection:** 2.4 GHz receiver `25A7:FA07` / `FA08`.

GetPower feature request on `FF02:0002` (interface 1), 17 bytes, checksum `0x55 - sum(bytes 0..15)`. The reply arrives on `FF01:0000`, starts `09 04 00`, carries a 0-10 level in byte 6 and charging in byte 7, and the whole frame sums to `0x55`. Level maps to 10 % steps. The FA7B/FA7C family uses another scale and is not claimed. **Unverified in this app.**

### Machenike L7

**Connection:** 2.4 GHz receiver `248A:FF4C` or USB cable `248A:FF4E`; product string must be an L7.

Feature report `0x51`, command `0x06`, 21 bytes. The reply has the marker `8A 24 4E FF` at bytes 4-7, link bits in byte 10, level in byte 11 and charging in byte 12. Only this read is sent; the shared 248A vendor id is why the product string is required. **Unverified in this app.**

### MSI VERSA 300 ELITE, Clutch GM31 / GM41

**Connection:** VERSA `0DB0:9E22`; GM31 receiver `0D3C` / cable `0D3B`; GM41 receiver `0D4C` / cable `0D4B`.

VERSA and GM31 answer output request `00 0F 20 00 ...` on `FF0D:00B0` or `FF00:0001`; the reply starts `0F 20` and byte 14 is charging bit 7 plus the percentage. The GM41 only pushes `A0 <raw> 07 ...` on `FF10:0006`, mapped through MSI's curve. MSI Center's `41 80` remote-control request is deliberately not sent. **Unverified in this app.**

### MSI VERSA PRO WIRELESS and STRIKE PRO WIRELESS

**VERSA PRO WIRELESS** (`0DB0:1FC6`) and **STRIKE PRO WIRELESS**
(`0DB0:1620`) use HID interface 1, usage page `00FF`, usage `0001`.

The battery query is a 65-byte HID output report:

`00 0D B0 01 00 00 00 05` followed by 57 zero bytes.

The 65-byte response has message type `05` at byte 7, battery percentage
at byte 9, and external power state at byte 10 (`01` = external power
present, `02` = external power absent). The exact power source is not
identified. A fully charged device still reports `01` while external power
is connected; there is no separate charge-complete value. A zero battery
level may be reported during power-switch transitions with either external
power state. Zero battery levels are treated as no reading, regardless of
byte 10. The request is sent using HID output `write()`, not a feature
report.

The request and response format was verified on physical VERSA PRO WIRELESS
and STRIKE PRO WIRELESS receivers using HidSharp 2.6.4. Halo Battery polls
these devices on each poll (60 seconds by default) and maintains separate status
entries for the mouse and keyboard.

### MOONDROP GM01 Pro

**Connection:** 2.4 GHz dongle `35D8:0133`, interface 3 consumer collection; on its USB cable the headset has no control channel.

Report `0x4B`, read `0x80`: command `0x27` returns link in byte 8 and percentage in byte 9; command `0x31` is answered by the dongle with link in byte 4. On the cable the last level is kept and charging is shown, because the headset drops the radio link. **Unverified in this app.**

### Ninjutso Sora V2; Sprime PM1

**Connection:** Sora V2 receiver `1915:AE1C` / cable `AE11`; Sprime PM1 receiver `AC1C` / `AD8C`, cable `AC11` / `AC12` / `AC13`.

Collection `FFA0:0001`, feature report `0x05`, command `0x15`, 32-byte request. Reply byte 9 is the level (0 means 100), byte 10 charging, byte 11 full (Sprime), byte 12 online. Only this read is sent. **Unverified in this app.**

### GameSir G7 Pro; FlyDigi Vader Pro

**Connection:** 2.4 GHz receiver (shows up as an Xbox controller)

Windows.Gaming.Input battery report: exact percentage and charging state. XInput is the fallback (four levels only)

### GameSir G7 Pro 8K

**Connection:** 2.4 GHz dongle (`3537:10c6` for the Aimlabs edition), USB cable (`3537:10c5`) or the charging dock (the pad stays on its dongle). Requires exactly one controller in both XInput and Windows.Gaming.Input and one matching vendor collection; otherwise the existing XInput fallback is kept.

The pad is an Xbox 360 controller for Windows: XInput says "wired" and Windows.Gaming.Input returns the `1000/1000` placeholder whatever the charge, which showed as a permanent 100 %. Its second HID collection (`fff0:0040`, the one GameSir Connect uses) carries input report `0x12` with the charge state in byte 35 (`01` charging) and the level in percent in byte 36. Source: GameSir Connect 1.11.9, `proxyG7ProCE.parseGamepadState()`, and its `G7ProCE` mode table for the product ids of the other editions (Gold, Nioh 3, NBA 2K, Golden Apple - unverified). The pad does not send report `0x12` on its own (an idle pad stayed silent for minutes); it follows any request on that collection, so the read-only "read current profile" request `0F 0B` (`getReadCurrentProfileCommand`), which GameSir Connect sends when it loads the pad's profile, is written and the report arrives within 0.01 s. Only this request is sent; levels above 100 are refused. **Confirmed on hardware on Windows** by the owner on the Aimlabs edition with GameSir Connect closed: 100 / 98 / 97 % matched GameSir Connect, charging was reported on the cable and on the dock (`01 60` = charging, 96 %), idle and in game. Right after power-on the pad reported 100 % once and 98 % two minutes later: its own estimate settling.

The **GameSir Cyclone 2** (`3537:100b`, #221) exposes the same `fff0:0040` collection, and GameSir Connect's `proxyC2.parseGamepadState()` reads the charge state and level from the same bytes 35-36, so the same reader is used for it. When charging it enumerates as **`3537:0575`** with the same collection, while Windows exposes the charging identity as a generic Xbox pad (`3537:1053`); the XInput provider pairs that generic report to the GameSir collection instead of showing its `1000/1000` placeholder. The receiver was confirmed by the reporter on 2026-11-14 (85 % matching GameSir Connect); the charging identity reports an all-zero `0x12` before its real state, so the reader skips zero reports, then reads the same pad's generic `1053` collection as a fallback and keeps the Cyclone 2 name. **Confirmed on hardware** by @grimmlium on 2026-11-14: 86 %, charging, matching GameSir Connect.

### BEITONG KP40D

**Connection:** 2.4 GHz receiver (`20bc:515c`); wired and Bluetooth modes are not covered. Requires exactly one controller in both XInput and Windows.Gaming.Input and one matching receiver. With multiple controllers, the existing XInput fallback is kept; Windows.Gaming.Input enumeration order cannot identify an XInput slot.

The vendor collection on interface 1 (`00ff:0003`): a 64-byte output report `02 15` followed by zeros is answered by a report starting `02 15`, with the level in byte 2 and the charger state in byte 4 (`50` on battery, `51` charging). Source: the owner's USBPcap capture of BEITONG iControl 1.0.5 on 2026-11-10 (`beitong-battery-20261110.pcapng`). This is the controller's estimate, which rises during charging and settles after unplugging; levels above 100 and unknown charge states are refused. Only the status request is sent. The reading replaces Windows' `1000/1000` placeholder when one XInput controller and one matching receiver identify this pad. **Confirmed on hardware on Windows**, with iControl closed: receiver level, charging/unplugging transitions and wake after sleep.

### Nintendo Switch Pro Controller, Joy-Con (L) / (R)

**Connection:** Bluetooth (057E:2009, 2006, 2007)

Byte 2 of the controller's own input report: level 0-8 in steps of 2 (full, medium, low, critical, empty) and the charging bit, shown as SDL shows it (level / 8, so 100 / 75 / 50 / 25 / 0 %) with the level name in the tooltip. When Steam has put the controller in the full mode, the 0x30 reports carry the byte and nothing is written; otherwise one read-only subcommand (0x02, request device info) is sent and its 0x21 reply carries the byte. The controller mode is never changed. USB is not read (the controller charges there). **Unverified** - no Switch controller was on hand, so a level above 8 is refused rather than shown

### Sony DualSense (PS5)

**Connection:** USB or Bluetooth

Read straight from the HID input report (USB byte 53, Bluetooth full report byte 54). Over Bluetooth, see the note below the table

### Sony DualShock 4 (PS4)

**Connection:** USB cable and Bluetooth (054C:09CC)

Read straight from the HID input report: exact percentage and charging state (USB byte 30, Bluetooth full report byte 32). Over Bluetooth, see the note below the table

#### Sony battery validation and physical identity

Battery codes follow Sony's [Linux hid-playstation driver at
`666539e`](https://github.com/torvalds/linux/blob/666539eecdf4db623f5e3c3e55cf9f2dfad24aa4/drivers/hid/hid-playstation.c):
DualSense parsing at lines 1610-1638, DualShock 4 at 2550-2588. Keep the existing
10% display bins. DualSense state 2 means full independently of the level nibble;
DS4 cable level 11 means full. Fault/reserved states are unavailable, not full or
still connecting. For DS4 without a cable, refuse undefined bins 11-15 rather
than applying Linux's saturation-to-full behavior. Reports must include the
complete 64-byte USB or 78-byte Bluetooth input frame.

On Sony USB `054c:05c4`/`09cc` (DS4) and `0ce6`/`0df2` (DualSense/Edge), only the
explicit `0001:0005` gamepad collection receives one pairing-info GET_FEATURE on
its already-open handle per poll. Sources: SDL at `c8da179`:
[PS4 lines 266-277 and 317-344](https://github.com/libsdl-org/SDL/blob/c8da179691f909734cd1b124c930e49311428996/src/joystick/hidapi/SDL_hidapi_ps4.c#L266)
uses report `0x12` with a 64-byte caller buffer;
[PS5 lines 375-433](https://github.com/libsdl-org/SDL/blob/c8da179691f909734cd1b124c930e49311428996/src/joystick/hidapi/SDL_hidapi_ps5.c#L375)
uses report `0x09` with a 128-byte caller buffer. Both read the controller address
from bytes 6..1. Linux's `ps_get_report` (799-818), `dualsense_get_mac_address`
(1183-1199) and `dualshock4_get_mac_address` (2148-2166) establish the echoed report
ID and complete 20-/16-byte payloads required here. Zero/broadcast or malformed
addresses are refused. Larger caller buffers account for HID report padding.

No new pairing read is made on Bluetooth: SDL's PS5 lines 427-428 warn it can
enable enhanced reports there. Unknown collections, other vendors, the `05c5`
Strikepad and `0ba0` wireless adapter are excluded from these new reads. Existing
battery-trigger behavior and the Bluetooth opt-in remain unchanged. There is no
persistent path-to-address cache: a different pad may reuse the same USB path
between polls. The existing bounded native transport handles these reads; no new
worker/process or dependency is introduced. These new identity reads have only
offline coverage in this change, not hardware confirmation.

Canonical unique addresses combine only one USB and one Bluetooth connection of
the same model. Duplicate addresses or conflicting metadata keep every endpoint
visible. Only an actually observed MAC-formatted old key can prove a settings
migration; plain, port-based or arbitrary old serial keys cannot prove their
historical owner, even if one controller is present now. Ambiguous legacy settings
must be selected again. Anonymous clones remain port-dependent; no level, list
order or controller count is used as a physical identity.

### Steam Controller (2025) with its puck

**Connection:** wired USB `28de:1302`, or puck `28de:1304` (Proteus) / `28de:1305` (Nereid).

Passive input only. The wired identity requires `ff00:0001` and a nonnegative USB
interface. Puck controller slots use interfaces 2-5: prefer `ff00:0001`, then
another vendor page, then gamepad `0001:0005`, preserving the prior listen-only
fallback order. Sources: SDL at `742645f`,
its [device identities](https://github.com/libsdl-org/SDL/blob/742645fe283a0a724bb85da0e247c4ad14fc2329/src/joystick/controller_list.h#L682-L685),
[Triton driver selection](https://github.com/libsdl-org/SDL/blob/742645fe283a0a724bb85da0e247c4ad14fc2329/src/joystick/hidapi/SDL_hidapi_steam_triton.c#L444-L486)
and [battery structure](https://github.com/libsdl-org/SDL/blob/742645fe283a0a724bb85da0e247c4ad14fc2329/src/joystick/hidapi/steam/controller_structs.h#L687-L697).
Report `43` carries charge state then percentage; `79`/`46` signal connection
changes. BLE `1303`, the older `1142` dongle and other Valve products are excluded.
No output/feature reports or lizard-mode changes are sent.

The [PR #265 recovery](https://github.com/HeyOkay/HaloBattery/pull/265) observes
all slots within one shared five-second round-robin window, with a 0.4-second
quiet window, at most 16 handles and 4096 reports per slot. A battery/disconnect
reply ends that slot's observation; every handle closes after polling. Keys
include normalized USB instance, PID and interface, so duplicated serials do
not merge physical controllers. Legacy aliases are accepted only when unambiguous.

The prior [#58 puck confirmation](https://github.com/HeyOkay/HaloBattery/issues/58)
remains: `43 01 5f` reported 95%, `43 04 64` 100%/charge complete, with cached
readings greyed while quiet. Wired USB and the recovered multi-device behavior
have fake-device tests but were not tested on hardware in this change. A level
above 100 is refused rather than shown.

### Xbox-compatible controllers (other models)

**Connection:** USB or the Xbox wireless adapter

Any Xbox-compatible controller is read the same way as the GameSir G7 Pro: the battery level from `Windows.Gaming.Input.RawGameControllers`, with XInput's coarse level (low / medium / full) as the fallback. Controllers other than the GameSir have not been individually tested.

WGI report order is not an XInput user index. Associate a level only with one
eligible WGI report and one XInput slot; multiple pads keep their own coarse
XInput levels. An ambiguous receiver's "wired" type has no trustworthy percentage
or charging state, so it stays unknown. Observed disconnects invalidate per-slot
levels and WGI associations. XInput exposes no persistent identity here; swapping
controllers between observations without an observed disconnect cannot establish
a new physical owner. WGI nullable Int32 capacities must satisfy
`0 <= remaining <= full <= 2147483647`, with `full > 0`, rather than being clamped.

GameSir Nova 2 Lite `3537:1098` has a captured false-full WGI report:
`Discharging`, `remaining=full=1000`, `rate=None` ([owner comparison](https://github.com/HeyOkay/HaloBattery/issues/142#issuecomment-5879245780),
[exact identity and report](https://github.com/HeyOkay/HaloBattery/issues/142#issuecomment-5885746260)).
The same narrow rule appeared in [the prior provider fix](https://github.com/casparas123/HaloBattery/commit/f2e6a834dde1aa829f708a16f473b5f18718f752).
Refuse that exact report on that identity only; other models and charging/idle
reports may legitimately be full. No HID request is added for this fallback.

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

**Connection:** 2.4 GHz receiver (413C:211D, "Alienware Pro KB Gaming Receiver"), vendor collection `ff00:0001` on interface 1.

Alienware Command Center v6.14.54.0 was captured with USBPcap for #287. It sends a 64-byte **feature** report (`SET_REPORT`, **wValue 0x0300 = report id 0**) starting `0e a1 00 ...`, and the receiver answers with an input report `0e a1 <level> <state> ...` - `0x25` = 37 %, exactly what Command Center displayed in both captures. The same stream carries unsolicited state reports (`0e 05`, `0e 17`, `0e 83`, `0e 88`, `0e 89`, `0e 97`), so a reader must filter on the `a1` command rather than take the next report.

The report id is the detail that matters: hidapi treats the first byte of the buffer as the report id, so the frame must be handed over as `00 | 0e a1 ...` (65 bytes). The first build passed `0e` as the report id, the receiver ignored the writes and every poll ended in "no link (off or asleep)" - the test build's report in #287. The reply may come back with the same `0x00` in front or without it, so the mark is looked for at either position.

Byte 3 is the charger state: `0x00` on battery and `0x04` with the cable in, the only two values in the captures (the charging one is `process3`). It is reported as charging while `0x04` and as not charging otherwise; a state never seen is written to the diagnostics rather than interpreted.

**Confirmed on hardware by @notisv** on the second test build: the icon showed the level continuously (`reply: 0e a1 5c 00` = 92 %), agreed with Bluetooth, Windows and Command Center at the same time, and the charging state followed the cable in and out. They kept it running for over an hour before reporting.

### Razer BlackShark V3, BlackShark V3 Pro

**Connection:** 2.4 GHz receiver (`1532:057A` for the V3, `1532:0577` for the V3 Pro)

Both publish the same four collections on interface 5 - `000c:0001`, `ff13:0001`,
`000b:0005` and `ff14:0001` - and are read with the same frames on `ff14:0001`:
64-byte output reports, report id `0x02`, transaction byte `0x60`, class `0x21` for the
battery and `0x2a` for the charging state, with an XOR checksum in byte 62. The format
comes from OpenRazer PR #2794 and the Synapse capture in
[#292](https://github.com/HeyOkay/HaloBattery/issues/292). Three wake frames go out
once per connection, then the charging and battery queries; the collection stays open
so the headset's pushes arrive, and a query only goes out at connect, after a link-up
push, or after minutes of silence, because this dongle resets its radio link when it is
polled continuously. The V3 Pro identity was added from the identical collection shape
in [#519](https://github.com/HeyOkay/HaloBattery/issues/519). The reporter confirmed
that the test build made the headset appear in the tray and display its charging
animation on their hardware.

### Razer BlackShark V3 X HyperSpeed

**Connection:** 2.4 GHz receiver (`1532:057D`, product `BlackShark V3 X`) or USB cable (`1532:057C`, product `BlackShark V3 X USB`)

This model is **not** the BlackShark V3's `0x02`/`ff14` family and not the 2023
"PA" frames. Its protocol was reverse-engineered byte-for-byte from Synapse
captures by everyoneexe/blackshark-linux (`PROTOCOL.md`,
`blackshark-rs/src/protocol.rs`, MIT) for the Xbox edition (`1532:0A5B`). Frames
are 64-byte feature reports with report id `0x07`; the battery is a read-only
GET, class `0x07`, command `0x80`, with the level in byte 10 and a CRC of XOR
bytes 3..60 in byte 62. The PC receiver (`057D`) and the cable (`057C`) publish
the same collections on interface 0 (`000c`, `000b`, `ff90`, `ff82`). In the
#280 diagnostics `ff90:0001` refused the feature write and only `ff82:0001`
answered, on both ids, so only interface 0 `ff82:0001` is written; the cable is
tried first and the two identities share one icon. The general Razer reader
skips both ids.

**Confirmed on hardware** in [#280](https://github.com/HeyOkay/HaloBattery/issues/280)
with test build 2: the reporter saw a live level (49%). The charging state is
class `0x07`, command `0x04` read as `0x84`; its reply carries the charge flag at
byte 10 (0 on battery, 1 charging). Test build 5 fixed the flag offset and the
reporter confirmed the charging ring follows the cable and the level returns to
the wireless reading after unplugging. The query is implemented from
[openrazer PR #2899](https://github.com/openrazer/openrazer/pull/2899), which
tested both the receiver and the cable on hardware. Test build 4 added the cable
identity `057C`.

### Razer Barracuda X (2022) (2.4 GHz)

**Connection:** 2.4 GHz dongle (1532:0552, "Razer Barracuda X 2.4", Macronix bridge)

This dongle does not answer the Razer mouse request, and the headset ("PA") frames are refused
with `WriteFile: 0x00000001 Incorrect function` - the dongle bridges USB HID to an **Airoha**
chip and speaks Airoha's RACE protocol instead, framed for the Macronix bridge:

    app mode   > 01 40                    < 01 40 01 01
    request    > 01 80 <len> 50 41 <group> <seq> <body...>
    reply      < 01 80 <len> 50 49 ....... payload: [group, 0x80|seq, status, data...]

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

### ATTACKSHARK X66

**Connection:** 2.4 GHz receiver (`0C45:FEFE`, product `X66 2.4G`), interface 3, usage `FF60:0061`

The receiver uses the Sonix 33-byte dongle reports documented and tested on an AJAZZ AK980 V2 by YellowHearth1/ajazz-ak980-v2-linux (`docs/PROTOCOL.md`, `crates/ak980-core/src/device.rs`): a 33-byte output report with report id 0 and the command `20 01`, zero-padded, with the byte sum in the last byte. The reply repeats `20 01 00` and carries the level in byte 3. The first request after the radio sleeps may only wake the link; the query is still sent once per poll and never re-sent after silence or a refused write, so the next poll reads the woken link. There is no charging flag in this reply, so `charging_known=False`.

The receiver is shared by several OEM keyboards; this provider claims it only when the product string contains `X66`. **Confirmed on hardware** by @VipexArtz in #446: the receiver read 41% (the software showed about 40%) and the Bluetooth reading was 47%; the level rose while charging, as it also does in the vendor software. The reply has no charging flag, so charging stays unknown by design.

### URX Core75 (2.4 GHz)

**Connection:** 2.4 GHz dongle (36B0:3002, "URX Core75 Wireless")

The dongle exposes the keyboard's **VIA raw HID** channel: usage page `0xFF60`, usage `0x61`,
interface 1, 32-byte packets, report id 0, custom channel 0. URX HUB (its public web driver)
reads the battery as VIA custom value **7**:

    request  00 | 08 00 07 00 ...        report id 0, VIA custom get (0x08), channel 0, value 7
    reply    08 00 07 4c 00 ...           echoed header, then the level: 0x4c = 76 %

The report-id byte is required: without it the keyboard answers `ff 07 ...`. VIA's
protocol-version read (`01`) answers `01 00 0d`, i.e. version 13, which is what URX HUB
expects for this model.

**Confirmed on hardware by @TheLastGamer18** (#245): the app detected the keyboard and the
percentage matched URX HUB. The charging state is **not exposed**: a second probe run with
the charging cable in returned byte-identical replies (`08 00 07 4c ...`), so the app shows
the level only rather than inventing a charger state.

The provider also requires `URX`/`Core75` in the product string. The EVO75 shares `36B0:3002` and the same `FF60:0061` collection but is not a URX Core75 and does not answer this custom value, so it is no longer claimed. **Confirmed in #448**: the false URX icon disappeared and the EVO75 is left alone.

### Red Square Alumix TKL (RSQ-20055)

**Connection:** 2.4 GHz receiver (342D:E411, product string `RSQ_20055_2.4G`)

The receiver exposes the keyboard's config channel as usage page `0xFF60`, usage `0x61`.
Red Square's web configurator (`conf.red-square.org`) sends a 32-byte tunnel frame on
output report **7**:

    request  07 | 05 01 01 0A 07 8B 00 ...        report id 7, settings read (0x8B)
    reply    07 | 05 A1 01 0A 07 8B <sleep> <debounce> <battery> <module> ...

The level is the ninth byte after the report id (offset 8). The same read is what the
configurator calls `getDeviceSleepTime`; its model table does not draw a battery icon for
this family, but the response still carries the byte. Only this product string is queried:
`342D:E411` is shared by several Red Square models, and the receiver's DFU collection is
never opened.

The configurator's wireless-module driver has **no charging query and no charging event**
for this family, so the app reports the level and marks charging unknown. Confirmed on the
reporter's hardware by @fogius (#460): the tray level appeared. Whether the keyboard exposes
a charger state at all is being checked with the reporter.

Source: Red Square's public web configurator (`conf.red-square.org`, model table and
`GskyWirelessModuleSeries` driver). Not open source; the protocol notes above are the
independently written implementation.

### Logitech (more HID++ 2.0 devices and G-series headsets)

**Connection:** Lightspeed, Unifying or Bolt receiver

The provider reads HID++ 2.0 generically from the receiver's `ff00` vendor collections (slots 1-6), so most other HID++ 2.0 mice and keyboards on a Lightspeed, Unifying or Bolt receiver should answer the same `0x1004` unified battery request. The G-series headsets - G533, G535, G633, G635, G733, G933, G935, G PRO, G PRO X - are in the headset pid table and use feature `0x1F20` (the G535 is the exception: it has no feature table and answers its 1.0 charge register, see below). Only the models listed in the support table above are confirmed so far.

The **G535** (0AC4) is long-only: its dongle exposes just the consumer collection `000C:0001`, with no `ff00` pair, so there is no feature table to ask. Its charge register is read with a *long* report instead - `11 FF 05 0D` is answered by `10 FF 05 0D <mV hi> <mV lo> <state>` (state `01` discharging, `03` charging) - and the voltage is mapped through HeadsetControl's G535 curve. It is read *before* the HID++ 2.0 ping, because a 1.0 headset does not answer one and otherwise looks asleep. The register is answered on the `ff43:0202` collection; the consumer collection is silent, so both are tried (#339). **Confirmed on hardware** by @ti-henrq: `register 0d: 4069 mV -> 85%`, icon shown.

The **G325 LIGHTSPEED** (`046D:0B21`) does not speak HID++ at all. It uses Logitech's vendor `0x50`/`0x23` report on `ffa0:0001` (interface 3): a 64-byte output report `50 23 0b 00 03 1a 00 03 00 07 0a ...` gets an ACK (`50 23 03 ...`) and then a battery packet `50 23 0b 00 03 10 00 06 00 07 0a RAW LEVEL STATE`, with the displayed percentage at byte 12 and `0x00`/`0x02` at byte 13 for on-battery/charging. The exchange and the values are from @Platerios in [Sapd/HeadsetControl #581](https://github.com/Sapd/HeadsetControl/issues/581), cross-checked against G HUB; this app reads only that report and keeps the last level greyed when the headset is off. **Confirmed on hardware** in [#232](https://github.com/HeyOkay/HaloBattery/issues/232) with test build 2: the level matched G HUB and the charging ring followed the cable.

Devices with no feature table at all (the older HID++ 1.0 generation) are read through the old battery registers instead: a short report `10 <slot> 81 <register>` on the short collection reads register `0x0D` (a percentage with a charging status - `30` discharging, `50` recharging, `90` full) or register `0x07` (a discrete level - 7 full, 5 good, 3 low, 1 critical - beside a charging byte), the same registers and order Solaar uses. This was added for the M710 keyboard of the MK710 wireless desktop ([#159](https://github.com/HeyOkay/HaloBattery/issues/159)), built from Solaar's implementation; **confirmed on hardware** in the reporter's test-build run - the icon shows the keyboard's coarse level. The **MX Revolution** (`046D:C525`, #449) is another HID++ 1.0 device: its paired slot answers the charge register with 75%, and the receiver id is mapped to the model name because the device has no feature table to report one. **Confirmed on hardware by @breezywine**: the test build shows the MX Revolution name and mouse icon.

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

### AJAZZ AJ159 PRO

**Connection:** 8K receiver (`3151:402d`, product `AJAZZ 2.4G 8K`)

The receiver publishes `ffff:0002` on interface 2 and has no interrupt-OUT report on any collection. The AJ-series exchange is used instead: a zero-payload `0xF7` feature poll wakes the 2.4G telemetry, then `GET_FEATURE 0x05` returns the charge (`05 00 00 64 ...`, level at byte 3 on Windows). Only that layout is accepted: the report id `05` first, zero padding at bytes 1-2 and a level of 1-100 at byte 3. Any other first byte, the reference's unnumbered Linux frame and `F7`-prefixed replies are refused, and only report `0x05` is requested. The provider tries the 33-, 65- and 67-byte poll lengths reported by the reference project, reads the answer as a feature report, and never calls `read()` on this collection. The reply carries the level only, so the app deliberately shows no charging ring (`charging_known=False`). Confirmed on hardware in [#289](https://github.com/HeyOkay/HaloBattery/issues/289): the reporter's icon shows a percentage after test build 4. The Kysona Uranus (`3151:5031`) shares the transport but remains held until its own confirmation.

## 1.17.0 release exclusions and confirmations

In stable 1.17.0, the AJAZZ alternate targets `3151:402d` (AJ159 PRO) and
`3151:5031` (Kysona Uranus) were excluded before opening a HID device: the
AJ179-derived `0x10` request had no confirmed link on those targets. This is the
historical release exclusion, not the latest AJ159 result.

The AJ159 owner later [confirmed a percentage in test build 4](https://github.com/HeyOkay/HaloBattery/issues/289#issuecomment-6899703056).
The level reader is now enabled through [PR #427](https://github.com/HeyOkay/HaloBattery/pull/427).
Its sourced feature-report exchange replaces the old
AJ179-style request; the [dock follow-up](https://github.com/HeyOkay/HaloBattery/issues/289#issuecomment-6901993307)
records no charging flag, so a charging state must not be inferred. Kysona Uranus
([#251](https://github.com/HeyOkay/HaloBattery/issues/251)) remains unconfirmed and
held before opening a HID device. The captured, confirmed AJ179 V2 MAX exchange
is unchanged.

Turtle Beach Stealth 600X Gen 3 (`10f5:2232`, [#239](https://github.com/HeyOkay/HaloBattery/issues/239))
is excluded from the enabled PID allowlist until its exchange is confirmed. The
SInf/GSI and CoAP protocol tests remain; they do not establish that this model accepts
either exchange. Stealth Pro II and Stealth 700 Gen 3 remain enabled. Atlas Air
(`10f5:225e`) also remains enabled: [Vetrices confirmed](https://github.com/HeyOkay/HaloBattery/issues/310#issuecomment-6741190943)
the test build's level matches Swarm II, charging follows the cable, and the level
updates after unplugging. This confirmation applies to Atlas Air, not Stealth 600X.

Sony INZONE H9 II (`054c:0fa8`, [#294](https://github.com/HeyOkay/HaloBattery/issues/294))
is excluded before opening its serial port: compatibility with the H9 initialization
frames has not been confirmed. The sourced H9 (`054c:0e53`) exchange remains enabled.
Lamzu Tachi (`37b0:000c`, [#262](https://github.com/HeyOkay/HaloBattery/issues/262))
is excluded before opening HID: collection layout alone was the evidence for trying
the Glorious Sinowealth query. The captured Glorious exchange and the other Lamzu
providers are unchanged. These two exclusions resolve the historical fork #30 and
Lamzu half of #34 release holds; their protocol tests remain for further development.

### Phone Link selected cache

**Source:** the Windows 11 Phone Link Start-menu companion's existing local
cache; no Bluetooth, HID, GATT, network, phone command or app/service activation
is performed. The provider does not require Bluetooth and can reflect status
which Phone Link already obtained over Wi-Fi. This is not a real-time API.

File-format evidence is pinned to
[PaulBayfield/devices.bayfield.dev at 4cd6e3c](https://github.com/PaulBayfield/devices.bayfield.dev/blob/4cd6e3c4fc3b7d61837e60b933318e51b620d253/agent/src/devices/phonelink/__init__.py#L47)
and [Wireless-Device-Battery-Monitor at bbc2030](https://github.com/krisgfx9/Wireless-Device-Battery-Monitor/blob/bbc203045b185ce3f0b1fe66597830515822e849/src/drivers/phonelink.cpp#L79).
HaloBattery independently implements these file-format facts; no upstream
provider code is copied. Microsoft documents the companion's
[battery/connectivity display](https://support.microsoft.com/en-us/windows/apps/phonelink/mobile-device-in-start-menu),
not this private file format.

Two exact files under `%LOCALAPPDATA%\Packages\Microsoft.YourPhone_8wekyb3d8bbwe`
are read, with size/structure limits and at most one retry for a concurrent update:

- `LocalState\StartMenu\StartMenuCompanion.json`: root `speak` identifies the
  selected device. The observed 1.26092.119.0 profile accepts the exact English
  suffix ` - Connected to your PC`. Only the header battery row is examined:
  `body[5].columns[4].items[0].tooltip` (Image) and
  `body[5].columns[6].items[0].tooltip` (Container). Both must agree on the
  complete `Battery <0..100>% remaining` phrase, optionally followed by
  `, charging`. Other text, notification sections and media are not used.
- `LocalCache\DeviceMetadataStorage.json`: the selected name must uniquely
  match a linked Android/iOS entry's nested `Metadata.Metadata.DisplayName`.
  `Metadata.Id` is its per-device UUID; the outer `DeviceMetadatas` group key
  is shared and is never used as device identity. Windows/unknown platforms,
  duplicate names with different UUIDs and missing identity are refused.

The key is `phone_link:<canonical UUID>`. Switching selected devices never
transfers levels or preferences. The same UUID survives a display-name change;
a re-link that changes UUID is a new identity. No fuzzy Bluetooth/name merge is
performed. The companion exposes one selected battery, not all linked batteries.

File mtime is not a battery measurement timestamp or periodic heartbeat, and
metadata `LastSeenTime` is not one either. On the owner's connected phone the
file remained unchanged for about 17 minutes, so five-minute deletion was wrong.
After five minutes an accepted level is kept explicitly **last reported**,
offline/unconfirmed, with charging disabled. Only the ring's display projection
preserves that known percentage; the renderer and device connectivity are not
changed. It cannot trigger alerts or extend use-time history.

An unseen cache may seed a level only within three hours, an engineering
admission policy rather than a freshness guarantee. Beyond that, only the exact
previously admitted (UUID key, file mtime, percentage, charger) tuple survives.
Polling never refreshes that timestamp. Missing/invalid data, unlinking,
ambiguous identity or selection changes clear prior admission. Ancient or
future-dated cold caches still yield **no percentage** and an explanation.
A bare remaining-percent phrase
does not prove charger state: charging stays unknown and alerts/use-time
estimates are suppressed. Only explicit charging is accepted as known by this
initial profile. Unsupported status text is not guessed or scanned for arbitrary
percentages elsewhere in the card.

**Local field checks:** a powered-off tablet still had an old connected/46% card;
that percentage was correctly withheld. After the user connected an S24+, its
companion file was 51 seconds old, both battery fields said 42%, and the unique
matched Android UUID differed from the tablet. In the earlier composed Desktop
build, the owner also confirmed its percentage and phone glyph in the tray and
Settings with phone Bluetooth disabled and Wi-Fi available. These checks used
the same cache reader; they are not direct phone queries or a hardware test of
every prerequisite branch separately. iPhone behavior remains unverified.

Updates depend on Phone Link continuing to synchronize in the background.
During another local observation, the phone and HaloBattery both showed 93%,
but the companion cache and Start menu omitted charging status. HaloBattery
continued polling and correctly kept charging unknown. Earlier explicit charging
reports were observed, so this is not a claim that Phone Link never supports
charging. Cache polling cannot fix upstream synchronization or invent a missing
charger flag. Temporary poll-counter logging used for that check is not shipped.

Newer Windows CrossDevice can hold status only in memory. Its internal Settings
interface has a Microsoft Settings caller restriction; HaloBattery does not
activate/bypass it, scrape UI, or substitute older cached percentages. If that
is the only available source, battery status remains unavailable until a supported
companion cache is produced. Other localized card formats and iPhone charging/
disconnect behavior still need field confirmation; UI translations do not make
an unsupported cache format supported.
### MCHOSE V9 Pro

**Target:** `291d:385d`; Halo writes only vendor-page collections.
[JoaoKSS's controller at `79724bf`](https://github.com/JoaoKSS/MCHOSE_v9_PRO_Controller/blob/79724bf9c9f4405bbf08d5522f6006f5a044b257/mchose_qt.py#L146-L165)
opens that exact VID/PID, sends a 64-byte output report `55 65 01 00 ...`,
and accepts `55 65 <percentage> <status>`. Halo does not send the reference's
separate EQ query. [PR #248](https://github.com/HeyOkay/HaloBattery/pull/248)
records the level rising from 20% to 60% and charging following the cable;
the `00` charging / `02` battery status interpretation comes from that run,
not the reference's UI.
Only the reference's `hid_write` output-report transport is used. A refused
write, including "Incorrect function", ends the read without a feature-report
retry. The former retry was based on another HyperX protocol; neither the
reference nor PR #248's discussion/reviews establishes it on this target.
## Guarded integrations from pending device pull requests

These are local integrations into the current native transport. The upstream
PR's owner observations establish their stated device evidence; they are not
hardware tests of this new implementation, and do not establish every charging
transition or sibling model.

### AULA F108 Pro

**Target:** `05ac:024f`, exact product `F108Pro Dongle`, interface 3 `FF60:0061`.
The same USB ID also belongs to a Lofree receiver, so USB ID alone never permits
a write. [AulaKit](https://github.com/meitar/aula-driver/blob/985df39f8224d6f031aeafdacf1a6328f458600f/Sources/AulaKit/Hardware/WirelessAulaDevice.swift#L82-L95)
sources command `20 01`; its lines 174-185 define the additive checksum.
[PR #402](https://github.com/HeyOkay/HaloBattery/pull/402) records the Windows
33-byte output request and the owner's 33% reply. The parser accepts the complete
32-byte payload, optionally preceded by report-ID zero, checks command/padding,
checksum and range, and keeps charging unknown. Native writes must complete the
actual padded output size. Stable per-path keys keep identical receivers separate;
the old singleton key is migrated only when exactly one eligible receiver exists.
No short-request fallback is added.

### MCHOSE K7 V2 Ultra+

**Target:** receiver `3837:1018`, interface 2 `FF01:0001` only. M HUB's
`assets/main-DQZs6ZzX.js` (SHA-256
`52b25bf9cdb449b84c69f50970c1612a2e33b3c98b7caffd03c1c8e9dca1ce1a`),
`getCommandBuffer`, `readDeviceInfo` and `deviceInfoToObj`, source a 64-byte output
report `4D`, command `0900`. [PR #403](https://github.com/HeyOkay/HaloBattery/pull/403)
records the 85% reply and charge flags 0/1. Validate supported flags, command,
payload length, XOR checksum, vendor identity, zero padding and percentage.
The embedded mouse PID is not required to equal its receiver's USB PID. A zero
level means unlinked, not an empty battery. Other new receiver PIDs are held;
silence never falls back to the legacy `06` feature query.

### EWEADN X23 Pro

**Target:** receiver `04f3:026f`, interface 1 `000A:0000`. The official
[device list](https://hub.eweadn.cn/deviceJson/other.json) maps it to the YDX driver.
The text-only archived [main bundle](https://hub.eweadn.cn/ydx/web/assets/index-BvEqCbAL.js)
(`ca5d68f36d8b276a7ff4386b8fc4f478b51d22f6e2813bc0f2e025e4ecefd402`)
calls `receiveFeatureReport(5)` and decodes input report 4. The
[device view](https://hub.eweadn.cn/ydx/web/assets/DeviceView-ss1Fdo4r.js)
(`80b55838f8bb10d13f6468aacc3225fbdfb15e7f0761bca0ed425d7cc1928870`)
maps charge 1 to charging and 2 to full.

[PR #406 at ad77d41](https://github.com/HeyOkay/HaloBattery/blob/ad77d41e109814df2c19d8abbe03e6000ccaad43/providers/eweadn_ydx.py)
is the pinned Windows 31-byte-payload/report-ID collection-size declaration;
an independent raw descriptor capture is not available. Cached native HID caps
must identify exactly one input-32 and one feature-32 collection on the **same
receiver**. Legacy HIDAPI uses a bounded descriptor parser. Missing/ambiguous
roles fail closed. Drain bounded old input, GET feature 5 once, then listen for
input 4 under one shared 1.5-second budget, divided fairly between receivers.
Starting receiver order rotates, and response waiting is divided only after
setup/trigger. The shared deadline stops new work; native calls already in flight
retain their own bounded completion/cleanup timeouts. Deferred receiver readings
use a bounded, unplug-pruned cache marked offline/stale, without fresh history or
alerts. No output/SET feature/configuration query or wired `026e` path is enabled.
Link, byte types, complete length and range are checked before treating a full
flag as 100%; unknown charging remains unknown.

### Delux M900 Pro and VGN N75 PRO

[PR #407 at 675198a](https://github.com/HeyOkay/HaloBattery/pull/407) records passive
`03 3f 40 01 4a` (74%) for Delux `1d57:fa65`. The existing Inphic listener accepts
model `3F` only on that PID; it sends no command. The longer twelve-read window
is limited to FA65; FA60 keeps its previous five reads and model allowlist.

VGN N75 PRO is an exact `VGN N75 PRO 2.4G Dongle` product alias on `320f:5088`,
using the existing ATK `FF1C:0092` interface-1 output `04`/command `1A` exchange
documented under ATK V75X. No generic brand match or new command is added.
Changing between VGN receiver and ATK wired identities resets the label and
cached value; the same ATK model retains cable/receiver continuity. PR evidence
confirms level observations, not a new charging transition test.
The provider supports one active keyboard at a time: an ATK cable takes
priority, otherwise the first eligible receiver in Windows enumeration is
selected. Other receivers are not queried in that poll. VGN uses the separate
`atk_keyboard:320f:5088:vgn-n75-pro` key, while ATK keeps its existing
`atk_keyboard:320f:5088` key for both cable and receiver. Shared legacy settings
are left with ATK because their former owner cannot be proved; they are not
guessed onto VGN. Separate simultaneous-keyboard polling is not provided here.
### Passive Inphic / Attack Shark and Rapoo readers

These enabled providers send **no requests**, so their evidence concerns input
layouts and receiver identities rather than authorization for device writes.
[Inphic PR #181](https://github.com/HeyOkay/HaloBattery/pull/181) records INPHIC
HUB's parser, the driver supplied in [#160](https://github.com/HeyOkay/HaloBattery/issues/160#issuecomment-5922153086),
and receiver reports for `1d57:fa65` and `fa60` in #160/#163/#69/#228. It
distinguishes `03 <model> 40 <subcommand> <level>` from the R1/X6 ten-step
shape, and records model-specific charging/stale-value limits.
[Rapoo PR #215](https://github.com/HeyOkay/HaloBattery/pull/215) records the
vendor HUB report-7 parser and A HUB's `RapooDevice.dll` keyboard parser,
with captured `bd 01 ...` status frames and the receiver/cable IDs in
[#193](https://github.com/HeyOkay/HaloBattery/issues/193). `24ae:14a9` was
added from [#235](https://github.com/HeyOkay/HaloBattery/issues/235).
The vendor binaries/web bundles are named historical sources, not pinned
source files in this repository; do not present them as reproducibly archived.

The older **Rapoo VT9 Pro `24ae:185a`** is a separate passive family. The
[reporter's capture in #109](https://github.com/HeyOkay/HaloBattery/issues/109#issuecomment-5880593801)
contains ten interrupt-IN packets `bb b0 42 20 03 01 64` from the same USB device
whose descriptor identifies `24ae:185a`. State byte 5 is `01` discharging or
`02` charging; byte 6 is percentage. These state meanings are retained in the
[test-build source at `d5a5a5d`](https://github.com/casparas123/HaloBattery/blob/d5a5a5dfc6e93b2b26719f590e7dc65f1e3e4bdb/providers/rapoo.py#L1-L36).
Only interface 1's `ff00:0002` / `ff00:000e` collections are listened to; no
query is sent. The logical seven-byte report can carry zero padding from
Windows HID reads. Other report families, unknown states and percentages above
100 are refused. The existing 64-byte read buffer is a caller limit, not a
claim about this receiver's missing HID report descriptor. A quiet receiver
keeps its last value greyed for up to five minutes; unplugging clears it.

The capture confirms 100% discharging only. Charging and other percentages
have source-based simulated tests; the old test build received no reporter
confirmation, and this recovery has not been tested on hardware.

### Wraith W1

**Connection:** 2.4 GHz receiver `093A:522C` or USB cable `093A:622C`; the ids are shared with Incott mice.

Device-info feature report `09` / command `8F`: reply byte 3 is 18 for the W1 or 20 for the Freestyle. After that, read-only `86` button queries (0, 1, 2, 4, 3) prompt the mouse to push input report `09`, whose byte 1 is charging bit 7 plus the low 7-bit percentage. A device that does not answer 8F is left to the Incott path. Protocol from the Wraith web driver and W1 Service. **Unverified in this app.**

### Sony INZONE H9 II

**Connection:** receiver `054C:0FA8`, interface 5, collection `FF04:0001`.

The read-only audio-status request is exactly 64 bytes:

    02 0C 01 00 FC 08 96 C3 41 06 01 01 00 A2 ...

Source: [marcinjakubowski/zoneout at 49ebedb](https://github.com/marcinjakubowski/zoneout/blob/49ebedb178f10c84c7e42e0e946c0ac971d08376/src/zoneout/device.py#L139-L167)
(MIT), with the status offsets and charging values in that revision's
[SPECS.md](https://github.com/marcinjakubowski/zoneout/blob/49ebedb178f10c84c7e42e0e946c0ac971d08376/SPECS.md).
A complete report-id-2 reply has command `0x06` at byte 9, charging at byte 14
(`0` or `1`) and the level at byte 15 (`0` through `100`). Other charging values
keep the level with an unknown charging state. The source does not establish a
status-response category, sequence or checksum, so the reader does not require
an echo of the request header. The exact outgoing request is rejected as an echo.

Only the exact receiver collection is opened. Cached native report caps are
checked before I/O, with the canonical 64-byte frame retained. Draining stops
after 32 reports, reply reads after 40 attempts or one second per receiver, and
a poll has a shared three-second budget with rotation between receivers.
Independent HID-path keys prevent one receiver's level from replacing another's.
No H9 serial initialization, setting command or alternate transport is sent.

The reporter of [#387](https://github.com/HeyOkay/HaloBattery/issues/387#issuecomment-6890208267)
confirmed the level in an earlier test build. No successful raw reply was attached;
the bounded reader and its native-transport integration have offline coverage but
have not been freshly tested on H9 II hardware. Charging is source-based and its
cable transition remains unconfirmed.

### Sourced HID target review

See [HID target safety](hid-target-safety.md) for exact collection gates, packet
corrections, retained compatibility exceptions and hardware retest limits.

## Explicit diagnostic readers

See [device diagnostics](device-diagnostics.md) for the acknowledgement, exact
endpoint restrictions and known hardware limitations. These readers do not
change normal provider allowlists.

- AJ159 PRO: Aiacos/ajazz-control-center at
  `03a29f7a90dd32057ca7cd0d6e6fa8f13d5400f3`,
  `src/devices/mouse/src/aj_series.cpp` L225-264,290-312; same-target Windows
  GET05/65 reply in [#289 test4](https://github.com/HeyOkay/HaloBattery/issues/289#issuecomment-6899703056).
  Only 3151:402D interface2 FFFF:0002, a caps-sized `00 F7` feature poll,
  30 ms wait, then GET05. Numbered `05 00 00 <level>` requires 1-100;
  zero means unknown. Charging is unknown. The normal AJ159 reader is unchanged.
- PRO X 3: Sapd/HeadsetControl at
  `1d2ccd1412246d85b2f8733d739cc865ba466aab`,
  `lib/devices/logitech_gpro_x3_lightspeed.hpp` L14-24,46-64,80-93,
  `lib/devices/protocols/logitech_centurion_protocol.hpp`
  L89-145,237-275,305-325,394-448,554-670 and captured fixtures in
  `tests/test_protocols.cpp` L1589-1596,1643-1666. Only 046D:0B45 interface3
  FFA0:0001 and three 50/23 Root/bridge/battery exchanges. No FeatureSet scan,
  setup, settings, optional offline probe, recovery or X2 fallback.
- Sony H9 II uses the existing reviewed `providers/sony_h9ii.py` reader without
  changes. No response category or magic is inferred from the outgoing request.
- Endgame 4K: johanneszab/vole
  [PROTOCOL.md](https://github.com/johanneszab/vole/blob/4ba29dea54b4628df03cfe2f94f4576c124afd80/re/PROTOCOL.md)
  sections1-3,12-14 and stuffz/mouse-battery-monitor `docs/ENDGAME.md` at
  `25871cce3a8266d8d2bc05b0ca81fbb74b3a38b4`. Only 3367:1970 receiver
  or cabled 1968/1982, FF01:0002; one A1/0E identity and one A1/B4 battery
  command. Busy responses are re-read, not resent. No A0/config/settings or
  recovery exchange is used. A successful reply has no command/target echo;
  repeated agreement cannot establish freshness. The source documents that
  a failed command can leave the link wedged until a successful 0F probe.
  This tool deliberately does not add automatic recovery and remains held.
- Tachi: manufacturer's Aurora `getBatPer`/`setReport`/`getReport` in
  `https://www.xvalleyinno.top/LAMZU/static/js/index-4fe8914c.js`, SHA256
  `5895a882e4e3570131044fa610a6e300ee835446c9909721f73b1e09951dd354`;
  model table `Config/env-models.json` SHA256
  `f931e5e7c11b041f74f938aeeeaf214046fc9a64af2ca822ad5afaa06cd9128b`
  and #262 HID inventory. Dorsal at
  `55f702fc70ce852228cf2c7d20173541e4f17420`, `src/dorsal/protocol.py`
  L244-260 independently supports the mouse payload but bypasses a real USB
  receiver. Only 37B0:000C interface2 FFFF:0000, report0 plus the 64-byte
  Aurora 83 payload, one 100 ms wait and GET_FEATURE. No CompX/Glorious
  fallback, wired probes or alternate-size probing. Real receiver, charging
  and recovery validation remain outstanding.
