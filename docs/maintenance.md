# Community consolidation and follow-up register

The historical acceptance record below was prepared for 1.17.0. Development is
consolidated in **HeyOkay/HaloBattery**, on `pre-release`.
[Release #370](https://github.com/HeyOkay/HaloBattery/pull/370) was merged and
published as stable 1.17.0 on 2026-11-12. The #413 follow-ups are proposed through
separate pull requests into `pre-release`. Checked tracking items record local
verification; consult the linked pull requests for integration and release status.
Weekly promotion is a [manual release review](../.github/CONTRIBUTING.md#weekly-manual-release-review).

The historical [fork handover register](https://github.com/ahmedkhursheed23/HaloBattery/issues/39)
contains the issue-by-issue baseline through upstream #357. Its statements about pending
PRs and project location are historical; upstream #363 imports the community history.
Code in `pre-release` is not necessarily in a stable release or confirmed on hardware.

## 1.17.0 packaged-app acceptance (2026-11-13)

The maintainer tested the public rolling installer for candidate `db0516e` in
Windows Sandbox and reported successful completion of:

- Clean installation, the packaged tray menu, preference changes and restart.
- Reinstallation of the same candidate with preferences retained.
- Uninstallation with settings retained, reinstallation restoring those settings,
  and uninstallation with settings deleted.
- Migration from the official community 1.16.0 ZIP to the upstream 1.17.0 installer,
  with preferences preserved and no errors reported.

The installer log records success. Before/after configuration snapshots preserve
the saved preferences; `last_version` changes from `1.16.0` to `1.17.0` and
`update_repo` changes from `ahmedkhursheed23/HaloBattery` to `HeyOkay/HaloBattery`.
This verifies ZIP-to-installer migration, not an upgrade from an earlier installer.
Sandbox had no physical devices, so this adds no hardware-compatibility claim;
the contributor evidence and remaining limitations below still apply.

Casparas approved the final [#370](https://github.com/HeyOkay/HaloBattery/pull/370)
candidate, which was merged as `a20391e2a630eddf4ce8b1dc00a9a40e342aa2fc`.
The acceptance above applies to that release; it does not validate later #413
changes or replace the remaining device-specific tests below.

## Confirmed follow-ups

| Thread | Evidence and disposition |
| --- | --- |
| [#368](https://github.com/HeyOkay/HaloBattery/pull/368) | The author confirmed ATK V75X battery/charging on its receiver and USB cable, including three 91% charging replies over USB. Receiver support was already integrated; the adapted PR adds cable preference and 300/30-second polling limits for 1.17.0. Fake-HID/activity regressions cover timing and transport changes. Physical keyboard sleep and power consumption remain unverified. |
| [#310](https://github.com/HeyOkay/HaloBattery/issues/310#issuecomment-6741190943) | Vetrices confirmed Atlas Air level agrees with Swarm II, charging follows the cable, and percentage updates after unplugging. Enabled in 1.17.0; this does not confirm Stealth 600X. |
| [#74](https://github.com/HeyOkay/HaloBattery/issues/74) | Reporter confirmed AJ179 V2 Max works after the fork update. |
| [#231](https://github.com/HeyOkay/HaloBattery/issues/231) | Reporter confirmed OMNI Keris II Ace and Azoth. Recorded in protocol notes. |
| [#271](https://github.com/HeyOkay/HaloBattery/issues/271#issuecomment-6636438669) | Reporter compared the charging fix with G HUB and confirmed agreement. |
| [#277](https://github.com/HeyOkay/HaloBattery/issues/277#issuecomment-6555678753) | Reporter confirmed 11 days of Cobra Pro sleep/wake recovery on the Pro Type Ultra test2 build. Later maintainer text overlooks this result; preserve the positive evidence and compare that build before claiming a fresh regression. |
| [#332](https://github.com/HeyOkay/HaloBattery/issues/332#issuecomment-6638842380) | Reporter confirmed Barracuda X 2.4 level reads on the fork. This does not verify every Barracuda bridge or the separate charging inference. |
| [#340](https://github.com/HeyOkay/HaloBattery/issues/340#issuecomment-6669940555) | Reporter confirmed Morph 96 battery accuracy. Its separate Maono PD100W request remains open; it is wireless, contrary to the earlier response. |
| [fork #12](https://github.com/ahmedkhursheed23/HaloBattery/issues/12) | XM2we owner confirmed level, charging, reconnection and sleep/wake; they closed the report. Do not conflate XM2we with the 4K models. |
| [#287](https://github.com/HeyOkay/HaloBattery/issues/287), [#328](https://github.com/HeyOkay/HaloBattery/issues/328), [#245](https://github.com/HeyOkay/HaloBattery/issues/245) | Alienware keyboard, Plantronics BT600 and URX Core75 confirmations are carried over from fork PRs #44, #45 and #47. URX charging remains unknown. |

## Open investigations and release holds

| Thread | Current evidence and next action |
| --- | --- |
| [#321](https://github.com/HeyOkay/HaloBattery/issues/321), [#324](https://github.com/HeyOkay/HaloBattery/issues/324), [fork #48](https://github.com/ahmedkhursheed23/HaloBattery/issues/48) | Normal polling stays held. The old XM2we-derived request has been replaced locally with the pinned Vole/stuffz A1 identity/battery contract, available only through an explicit acknowledged diagnostic tester, now reachable from the packaged EXE as well as source (see the protocol notes). Equal samples do not prove reply freshness; persistent status-08 and detection-loss reports still need resolution. No reset, pairing or guessed recovery commands are enabled. Confirmed XM2we support is unchanged. |
| [#343](https://github.com/HeyOkay/HaloBattery/issues/343), [#298](https://github.com/HeyOkay/HaloBattery/issues/298) | Historical PRO X 2-style `51` probes were negative. A separate local X3 route now follows HeadsetControl's exact `046d:0b45` `50 23` read-only Root/bridge/battery exchange, dynamically resolving the feature indices. The pinned upstream author tested Ubuntu receiver firmware 1.02; Windows integration and charging transitions are not newly hardware-verified here. Confirmed PRO X 2 behavior is unchanged. A packaged `--diagnose-device logitech-pro-x3` check now selects only the exact X3 endpoint and refuses concurrent HaloBattery access; see [opt-in diagnostics](features.md#opt-in-device-diagnostics). |
| [#289](https://github.com/HeyOkay/HaloBattery/issues/289), [#251](https://github.com/HeyOkay/HaloBattery/issues/251) | AJ159 test4 supersedes the old negative test2: owner diagnostics show GET05/65 returning70% after SET0/F7. The local exact3151:402d/interface2 FFFF:2 port uses that independently sourced Aiacos exchange and native feature lengths; charging is unknown. Kysona3151:5031 remains unopened because product-table membership does not prove that exchange. AJ179 is unchanged. A packaged `--diagnose-device aj159` check now targets the sourced AJ159 endpoint alone, including when an AJ179 is connected; it refuses concurrent HaloBattery access. |
| [#239](https://github.com/HeyOkay/HaloBattery/issues/239) | Stealth 600X Gen 3 remains unconfirmed and is excluded from 1.17.0 before opening HID. Atlas Air from the same fork PR #23 now has reporter confirmation (above) and remains enabled with Stealth Pro II and Stealth 700 Gen 3. |
| [#294](https://github.com/HeyOkay/HaloBattery/issues/294), [#387](https://github.com/HeyOkay/HaloBattery/issues/387) | H9 II remains excluded from H9 serial initialization and now uses a separate bounded HID provider on exact `054c:0fa8`, interface 5 `FF04:0001`, following zoneout's pinned read-only audio-status query. The reporter confirmed the level in an earlier Windows test build; this strict implementation and charging transitions still need hardware verification. H9 serial behavior remains unchanged. |
| [#262](https://github.com/HeyOkay/HaloBattery/issues/262) | Normal Tachi polling stays held. The official Aurora model table and getBatPer source now establish the read-only83 command on the owner-recorded exact37b0:000c/interface2 FFFF:0 collection. An acknowledged diagnostic tester, available from the packaged EXE or source, uses that path only, with no Glorious fallback. Dorsal's real-firmware emulator bypasses the physical receiver, and no owner result after test2 verifies that transport or charging. |
| [#238](https://github.com/HeyOkay/HaloBattery/issues/238) | Latest diagnostics still use 1.15.0. Retest OMNI Keris II Origin with upstream pre-release. Azoth 96 HE uses SPEEDNOVA 1ad0; that shared receiver id does not prove the Harpe mouse protocol applies to this keyboard. Cetra Open 1cac is a separate unsupported device. |
| [#351](https://github.com/HeyOkay/HaloBattery/issues/351), [fork #41](https://github.com/ahmedkhursheed23/HaloBattery/issues/41) | X11 and G800 Bluetooth success is reported. G800's 2.4 GHz side still needs a receiver diagnostic/capture. Keep the combined request partially open. |
| [#82](https://github.com/HeyOkay/HaloBattery/issues/82), [#297](https://github.com/HeyOkay/HaloBattery/issues/297) | Old WARG follow-up was never confirmed; the later Compx path is integrated. Request a current pre-release result in #297 instead of treating the closed multi-device thread as proof of success. |
| [#347](https://github.com/HeyOkay/HaloBattery/pull/347) | VXE R1 S owner reports the same NK NANO receiver and offers testing. Do not infer a model-specific confirmation until they run the pre-release. |
| [#199](https://github.com/HeyOkay/HaloBattery/issues/199) | OMNI is integrated; ROG Pelta remains separate and unsupported. |
| [#360](https://github.com/HeyOkay/HaloBattery/issues/360) | F87 Pro report uses 3554:fa09, already in the Compx keyboard table. Retest this exact keyboard on pre-release before adding a duplicate reader or closing the request. |
| [#362](https://github.com/HeyOkay/HaloBattery/issues/362) | G98 V3 uses 3837:1015 and does not answer the existing MCHOSE query. Reporter offers a HUB capture; obtain that known exchange before implementing support. |
| [#358](https://github.com/HeyOkay/HaloBattery/issues/358) | Rapoo/MosArt report provides new IDs, not a verified battery exchange. Needs vendor evidence. |
| [#357](https://github.com/HeyOkay/HaloBattery/issues/357), [#361](https://github.com/HeyOkay/HaloBattery/issues/361), [fork #11](https://github.com/ahmedkhursheed23/HaloBattery/issues/11) | Widget/percentage requests remain product work. `status.json` is an existing integration point, not a built-in widget. |

## Stable-release boundary

No version bump, stable tag or promotion to `main` is part of this consolidation.
Separate per-build pre-releases remain for testing; the historical rolling release is preserved. Resolve or explicitly exclude
the experimental protocol holds above before a future stable promotion. The fork now carries the canonical 1.17.0 files as its latest bridge release,
so its installed 1.16.0 updater can offer the official package. That exact automatic
upgrade route still needs a real installed-copy check; this local follow-up publishes
neither a new bridge nor a stable release.


## Closed-issue review for #413 (2026-11-17)

The full discussions were reread before changing their tracking state. An
unanswered test build is not treated as reporter confirmation.

| Thread | Corrected disposition |
| --- | --- |
| [#93](https://github.com/HeyOkay/HaloBattery/issues/93) | Reopened: the optional single-icon mode has no implementation. This remains a feature request. |
| [#105](https://github.com/HeyOkay/HaloBattery/issues/105) | Reopened for retest: current code includes the original Hitscan receiver and HSK Pro ACE; the old reply saying HSK is unsupported is outdated. |
| [#109](https://github.com/HeyOkay/HaloBattery/issues/109) | Reopened: recover the captured VT9 Pro BB reader and obtain confirmation from the packaged build. Capture decoding alone is not an app test. |
| [#82](https://github.com/HeyOkay/HaloBattery/issues/82) | Reopened: Hyzen was confirmed; retest WARG on the later CompX path and keep Steam recovery distinct from existing puck confirmation. |
| [#70](https://github.com/HeyOkay/HaloBattery/issues/70) | Remains closed as not planned, rather than completed: repeated reporter tests found only a constant receiver value, not battery information. |
| [#13](https://github.com/HeyOkay/HaloBattery/issues/13) | Reopened: Logitech and Nova 5 were confirmed, but the exact Keychron V3 Ultra still needs a retest after the output-report correction. |

No new hardware testing or maintainer comments were added by this review.
