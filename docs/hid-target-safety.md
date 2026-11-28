# HID battery target safety

Battery requests are restricted by protocol evidence, not by a vendor/product
name or a plausible reply from an unrelated collection. Report metadata does
not authorize arbitrary new requests. This review preserves the existing
battery/charging parsers unless the named source establishes a different frame.

- Alienware Pro: `413C:211D`, interface 1, `FF00:0001` only; Command Center
  capture and [#287](https://github.com/HeyOkay/HaloBattery/issues/287).
- Astro A50 X: `FF32:0074`, from HeadsetControl
  [logitech_astro_a50.hpp](https://github.com/Sapd/HeadsetControl/blob/master/lib/devices/logitech_astro_a50.hpp).
- AULA: `0C45:FEFC`, `FF80:0001`; [#325](https://github.com/HeyOkay/HaloBattery/issues/325)
  and [#327](https://github.com/HeyOkay/HaloBattery/pull/327). Other Sonix keyboards
  sharing the vendor page are not targets.
- Corsair Void v2: interface 4, `FF42:0001`; [#176 capture](https://github.com/HeyOkay/HaloBattery/pull/176#issuecomment-6031065546).
  Bragi/XT/SE uses `FF42:0001`; legacy Void uses `FFC5:0001`, from
  HeadsetControl `corsair_virtuoso_xt.hpp` and `corsair_void_rich.hpp`.
  Probe mode keeps the sourced 64/65-byte frames, without arbitrary caps-sized
  writes or notice-channel fallback. Current XT cable and M65 routes are retained.
- Maxwell: HeadsetControl `audeze_maxwell.hpp` / `audeze_maxwell2.hpp` authorizes
  `FF13:0001`. The inferred Xbox cable `3329:4B1E` is held pending a source.
  Existing cable-first grouping and icon keys remain unchanged, including the
  owner's zero-serial cable/dongle pair. Multi-headset identity migration is
  outside this collection-safety change.
- EWEADN: vendor [pq.json](https://hub.eweadn.cn/deviceJson/pq.json) and
  [#352](https://github.com/HeyOkay/HaloBattery/pull/352) establish receiver
  `089D:062F`, `FF01:0010`. Cable `088D:062E` is held pending its own exchange.
- URX Core75: exact normalized `URX Core75 Wireless`, `36B0:3002`, interface 1,
  `FF60:0061`; [owner probe](https://github.com/HeyOkay/HaloBattery/issues/245#issuecomment-6654805476)
  establishes 32 payload bytes plus zero report ID, and
  [manufacturer firmware](https://github.com/URX-keyboard/qmk_firmware/blob/fd1af64ccd362bcc485758cb950755e5618951f4/keyboards/urx/core75/keymaps/default/keymap.c#L167-L169)
  defines 0-100%. Shared IDs or partial names do not authorize EVO75.
- WLmouse: current A880 and owner-confirmed A881 cable queries remain active on
  `FFFF:0000` ([#529](https://github.com/HeyOkay/HaloBattery/pull/529)). Native
  feature length 65 avoids invalid short reads; feature-only paths skip input
  reads and always close. Legacy HIDAPI retains its known 65/64-byte variants.
- Waizowl: OGM 8K receiver `1915:0728` and the OGM Pro V2 cable `1915:07A2`,
  `FF0A:0001` only (the `Validate` of Waizowl Hub's modules 1832 and 1954), with
  the hub's read requests alone. Other `1915` products are not targets.
- Keychron: current sourced receiver collections, Ultra-Link multi-device
  identities and conservative aliases from [#522](https://github.com/HeyOkay/HaloBattery/pull/522)
  supersede the older proposed rewrite and remain unchanged.

## ASUS shared SPEEDNOVA receiver

The existing Harpe II receiver exchange remains on interface 2, `FF01:0001`,
`mi_02&col03`, as in G-Helper `HarpeIIAce.cs` and the
[53% owner capture](https://github.com/HeyOkay/HaloBattery/pull/364).
There is no sourced paired Harpe PID, so no USB-PID-derived discriminator is invented.
A full 64-byte pair list identifying `1C12` instead selects Azoth 96 HE on
`FF00:0001` and prevents the Harpe mouse query for that receiver, even if its
keyboard collection is absent. The pair-list query is on `FF02:0001`; both use
interface 2. [#238 owner test](https://github.com/HeyOkay/HaloBattery/issues/238#issuecomment-6876578460)
confirmed Azoth 96 HE at 36% and charging transitions in the earlier test build.
This integration has offline fake-HID coverage, not a new hardware test. Keris II
Origin's established OMNI route is unchanged. Unknown/incomplete pair lists do
not authorize a keyboard query and retain the legacy captured Harpe route.

## Razer compatibility limits

`1532:0536` is the Barracuda X cable with no output endpoint ([#256](https://github.com/HeyOkay/HaloBattery/issues/256));
its `0552` receiver has a separate provider. That provider restores local mode
when a remote-mode ACK is missing or an exception occurs. BlackShark PA is
restricted to explicit known IDs and vendor candidates, not headset names.
BlackShark V3 / V3 Pro retain their separate readers.

`1532:0550` automatic battery writes are held until the exact exchange has a
primary source or capture. The [owner confirmed a working level](https://github.com/HeyOkay/HaloBattery/issues/332#issuecomment-6638842380),
but that whole-build result did not identify a successful PA or generic wire
exchange or charging transition. It is not sufficient to authorize guessed
writes. This hold does not affect the separately sourced `0552` receiver or
known BlackShark PA / V3 / MediaTek routes.

## MCHOSE K87 packet correction and retest

Manufacturer [main-DQZs6ZzX.js](https://www.mchose.com.cn/assets/main-DQZs6ZzX.js),
SHA-256 `61588300659c7802018831d78a98af1dfd7967d70a8d286bfbd3e4d424173227`,
defines 19-byte getBattery template `13 4a 01` plus 16 zeros. Its wireless wrapper
appends additive checksum `5e`, producing the complete 20-byte HIDAPI output.
Only the documented `41E4:2001` K87 and `41E4:2004` G98 Pro identities,
interface 1, `FF02:0002`, receive this shared query. Truncated or refused writes
abort; replies require the full header, length, checksum and 0-100% range.
The literal captured K87 reply ends `c5` and reports 100%; its low flag does not
mean charging. Existing G98 Pro `41E4:2004` support and high-nibble charging
semantics from [#378](https://github.com/HeyOkay/HaloBattery/issues/378) are retained.
The corrected shared request is source-verified and offline-tested,
**not yet owner-retested on K87 or G98 Pro hardware**. No additional identities
are inferred from a product name.
