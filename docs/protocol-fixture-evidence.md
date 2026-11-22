# Independent protocol fixture evidence

## Additional enabled-provider source records

These records fill documentation omissions; they do not enable devices or change
requests. A named upstream implementation establishes its command and product
contract, not hardware verification of every Windows transport. Remaining proof
gaps are listed explicitly below.

### DAREU A950 (Compx)

**Targets:** `260d:1074` and `260d:1084`; command collection `ff02:0002`,
reply collection `ff01:0000`. [MousePower at `684b5aa`](https://github.com/vamyane/MousePower/blob/684b5aa70e36952bac1cda094291c2ccd39c1e34/src/mousepower/core/providers/dareu_compx.py#L1-L34)
lists both PIDs and the separate feature/input-channel contract. Its
[`_send`](https://github.com/vamyane/MousePower/blob/684b5aa70e36952bac1cda094291c2ccd39c1e34/src/mousepower/core/providers/dareu_compx.py#L135-L173)
sends the 17-byte feature report `08 04 00 ... <checksum>`, with a checksum
of `55 - sum(bytes 0..15)`. Input report `09` echoes command `04`; byte 6 is
percentage and byte 7 charging. [PR #212](https://github.com/HeyOkay/HaloBattery/pull/212)
records a `260d:1074` hardware reply at 90%; `1084` is sourced, not locally verified.

### HyperX Cloud Alpha Wireless

**Target:** `03f0:098d`, Windows collection `ff43:0202` on interface 3.
[HeadsetControl at `d4fb352`](https://github.com/Sapd/HeadsetControl/blob/d4fb352721a715aefc07968be30225cc5a7e554f/lib/devices/hyperx_cloud_alpha_wireless.hpp#L23-L121)
lists that product and sends 31-byte output reports `21 bb <selector>`:
`03` reads connection, `0c` charging, `0b` percentage, with the value at
reply byte 3. [PR #318](https://github.com/HeyOkay/HaloBattery/pull/318)
records the Windows descriptor/collection selection and the second reference
implementation; it explicitly reports fake-device testing, without a successful
hardware run. No feature-report fallback is part of this provider's contract.

### HyperX Cloud Flight Wireless

**Targets:** `0951:16c4` and `0951:1723`. The request is the 20-byte output
report `21 ff 05 00 ...`; a 15- or 20-byte reply carries big-endian millivolts
at bytes 3-4. The command, both PIDs, charging threshold `100b` and discharge
curve are in [HeadsetControl at `1d2ccd1`](https://github.com/Sapd/HeadsetControl/blob/1d2ccd1412246d85b2f8733d739cc865ba466aab/lib/devices/hyperx_cloud_flight.hpp#L23-L121).
Halo selects usage `0303` on `ff53` or `ff90`. The owner's
[original #233 diagnostics](https://github.com/user-attachments/files/33141582/diagnostics.txt)
independently establish `0951:16c4`, interface 3, `ff53:0303`, alongside three
other collections; this is descriptor/target evidence, not a successful reply.
The `1723`/`ff90:0303` choice is recorded in [PR #346](https://github.com/HeyOkay/HaloBattery/pull/346),
whose local probe detected that receiver but received no battery answer.
The inspected HeadsetControl class establishes the command and both PIDs but
does not override its base class's default interface selector with these Windows
usage pairs. No independent `1723` descriptor/capture was recovered in this audit.
While charging, no percentage is invented from the elevated voltage.

### NVIDIA SHIELD Controller (2017)

**Target:** `0955:7214`, USB or Bluetooth. NVIDIA's upstream Linux driver at
[`8fa7292`](https://github.com/torvalds/linux/blob/8fa7292fee5c5240402371ea89ab285ec856c916/drivers/hid/hid-nvidia-shield.c#L48-L184)
defines 33-byte host-command reports: output `04 <command> 00 ...`, input
`03 <command> 00 <payload>`. Halo sends battery `0x07`, charger `0x3a`, and board
information `0x10`; the last supplies the serial used across transports. The
[request builder and sender](https://github.com/torvalds/linux/blob/8fa7292fee5c5240402371ea89ab285ec856c916/drivers/hid/hid-nvidia-shield.c#L228-L233)
and [USB identifiers](https://github.com/torvalds/linux/blob/8fa7292fee5c5240402371ea89ab285ec856c916/drivers/hid/hid-ids.h#L1064-L1065)
establish the bytes and target. Battery capacity is report byte 14; charger
state `2` means charging. [PR #250](https://github.com/HeyOkay/HaloBattery/pull/250)
records Windows 11 hardware checks over both transports, including charging
and serial-based deduplication; it does not claim two physical pads were tested.

### Finalmouse UltralightX

**Target:** dongle `361d:0100`, vendor page `ff00`; the firmware-only cable
identity is not queried. The independent [OpenMouse protocol at `5a4f643`](https://github.com/OpenMouse-Project/mouse-protocol/blob/5a4f6433da3d70865d008874c7055016b336c197/src/finalmouse/index.ts#L333-L417)
documents XPanel's output report `04`, its 63-byte payload
`<2+payload-length> <80|command> <payload-length> <payload>`, input report
`05`, and commands `0x24` link, `0x26` percentage, `0x05` voltage and `0x25` charging.
Its [device binding](https://github.com/OpenMouse-Project/mouse-protocol/blob/5a4f6433da3d70865d008874c7055016b336c197/src/drivers/finalmouse/hid.ts#L27-L84)
establishes the dongle PID and vendor collection. [PR #284](https://github.com/HeyOkay/HaloBattery/pull/284)
records real on-battery and cable replies: this firmware never answered `38`,
so voltage supplied the level. Voltage rises during charging and can overstate
the estimate; that observation is not evidence for changing the sourced curve.

### LAMZU Thorn / Atlantis-generation CompX protocol

**Targets in Halo:** `3554:f510` receiver and `3554:f50f` cable, `ff02:0002`.
These use a separate protocol from the Maya X feature-report section above.
[OpenMouse's catalog and collection contract at `5a4f643`](https://github.com/OpenMouse-Project/mouse-protocol/blob/5a4f6433da3d70865d008874c7055016b336c197/src/lamzu/atlantis.ts#L32-L55)
and its [product table](https://github.com/OpenMouse-Project/mouse-protocol/blob/5a4f6433da3d70865d008874c7055016b336c197/src/lamzu/atlantis.ts#L203-L224)
cover both IDs. Its imported [CompX codec](https://github.com/OpenMouse-Project/mouse-protocol/blob/5a4f6433da3d70865d008874c7055016b336c197/src/pulsar/index.ts#L1-L34)
defines report `08`, 16 payload bytes, command `04` and the checksum including
the report ID. Battery data is percentage, charging, then big-endian millivolts.
OpenMouse verifies an Atlantis cable, while Halo's [PR #348](https://github.com/HeyOkay/HaloBattery/pull/348)
records the Thorn receiver and cable reading 95%, charging on USB. Those are
separate hardware claims; the shared catalog cannot distinguish every model
behind these IDs.

### Jabra Link 380 / Link 390

**Targets:** `0b0e:24c7`, `24c8`, `24c9`, `24ca`, `24e9`, `2e57`, vendor
collection `ff00:0001`. [jabridge at `22952e2`](https://github.com/Watchdog0x/jabridge/blob/22952e25f645198da089565b4b3b285e4b2be123/cmd/jabridge/jabraApi.go#L750-L762)
lists these dongles. Its [GN Protocol builder](https://github.com/Watchdog0x/jabridge/blob/22952e25f645198da089565b4b3b285e4b2be123/cmd/jabridge/jabraApi.go#L392-L406)
establishes `05 <destination> 00 <sequence> <40|length> <class> <operation>`;
length is six header bytes plus payload. Battery is class `12`, operation `02`,
with flags then percentage; [flag bit 0 means charging](https://github.com/Watchdog0x/jabridge/blob/22952e25f645198da089565b4b3b285e4b2be123/cmd/jabridge/jabraApi.go#L1813-L1825).
Name is class `02`, operation `00`. [PR #337](https://github.com/HeyOkay/HaloBattery/pull/337)
records destination `04`, the Windows collection and a Link 380/Evolve2 65
hardware name/battery exchange. Charging was not observed set in that run.
The reference's 63-byte GNP buffer is not necessarily the physical HID report
length: its [transport wrapper](https://github.com/Watchdog0x/jabridge/blob/22952e25f645198da089565b4b3b285e4b2be123/internal/firmware/control_hidraw.go#L77-L101)
re-encodes the message to the descriptor's output length, including the report
ID. Its [tests cover both 63- and 64-byte report-5 layouts](https://github.com/Watchdog0x/jabridge/blob/22952e25f645198da089565b4b3b285e4b2be123/internal/firmware/control_hidraw_test.go#L13-L26).
Halo's 64-byte Windows frame therefore does not by itself contradict the sourced
GNP exchange. The successful Link 380 run is evidence for that device, not a
measurement of every supported dongle's descriptor.

### ATK / VXE NK NANO BITMOUSE receiver

**Target:** `373b:1216`, `ff05:0001`. [OpenMouse's pinned BITMOUSE codec](https://github.com/OpenMouse-Project/mouse-protocol/blob/5a4f6433da3d70865d008874c7055016b336c197/src/bitmouse/index.ts#L1-L91)
supplies report `08`, marker `72`, a 63-byte payload, additive checksum,
target `1` for the mouse behind a receiver, command `0x07` level and `0x0f` charging,
and request/reply lengths `2`/`1`. The encoder is in that file at lines 143-162.
Its current product table does **not** list `1216`: same-target evidence is
the [captured exchange in atk-tool PR #15](https://github.com/noosxe/atk-tool/pull/15)
and Halo's [PR #350](https://github.com/HeyOkay/HaloBattery/pull/350)/[#291](https://github.com/HeyOkay/HaloBattery/issues/291)
hardware runs. The former capture establishes the level request on that receiver;
the latter records separate level/charging replies and cable transitions.
The capture's byte 3 is a sequence, not a charging flag; bytes beyond the
declared payload are not a sourced voltage reading. No cable PID is claimed here.

### ATK X1 Nearlink: versioned Evision vendor source

**Targets:** `373b:108b` cable and `373b:108c` receiver. ATK HUB's
[versioned 3.2.28 entry](https://bpcdn.atkgear.com/hub-v3/production/3.2.28/index.html)
loads `index-DhYI8H5N.js`; its source graph contains
[`demoMode.store-DQmSowxd.js`](https://bpcdn.atkgear.com/hub-v3/production/3.2.28/static/demoMode.store-DQmSowxd.js),
SHA-256 `d7f81a1faa5f52c401476144448cf4379c1e164e90c865e4ce694fca20527b64`.
Its Evision catalog lists decimal VID `14139`, PIDs `4235`/`4236`, wired8K/dongle8K.
Class `Ho` defaults to report `04`, selects `05` for wireless, and calls
`fV`/`lc.power_info=26`. `Xn.BUFFER_LEN=63`; class `kV` stamps an incrementing
little-endian request code in bytes 0-1. The command is byte 2 and the remaining
payload is zero. Replies yield percentage/charging at payload bytes 7-8.
The [shared buffer class](https://bpcdn.atkgear.com/hub-v3/production/3.2.28/static/keyboard-info.util-DeoOkE9N.js)
and [output transport](https://bpcdn.atkgear.com/hub-v3/production/3.2.28/static/base-hid-device-Cgv5YaFg.js)
establish allocation, command offset and `sendReport` behavior.

WebHID's permission filter is `ffb2:0001` for both IDs; that filter is not a
Windows per-collection write-path declaration. The receiver report `05` on
`ffb3:0001` and cable report `04` on `ffb2:0001` are independently recorded
in [PR #279's hardware follow-up](https://github.com/HeyOkay/HaloBattery/pull/279#issuecomment-6321137102)
and its [retained literal-frame tests](https://github.com/mspw11/HaloBattery/blob/4c94e626402eb075fc1d8a207d3e28312e6ad455/tests/test_atk_nearlink.py#L51-L52).
The versioned source and SHA-256 now make the command/product provenance
reproducible; no new query or collection fallback was introduced.
