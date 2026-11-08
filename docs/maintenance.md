# Community consolidation and follow-up register

Reviewed 2026-11-08. Development is consolidated in **HeyOkay/HaloBattery**, on
`pre-release`. `main` and the stable release remain unchanged. Weekly promotion is a
[manual release review](../.github/CONTRIBUTING.md#weekly-manual-release-review).

The historical [fork handover register](https://github.com/ahmedkhursheed23/HaloBattery/issues/39)
contains the issue-by-issue baseline through upstream #357. Its statements about pending
PRs and project location are historical; upstream #363 imports the community history.
Code in `pre-release` is not necessarily in a stable release or confirmed on hardware.

## Confirmed follow-ups

| Thread | Evidence and disposition |
| --- | --- |
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
| [#321](https://github.com/HeyOkay/HaloBattery/issues/321), [#324](https://github.com/HeyOkay/HaloBattery/issues/324), [fork #48](https://github.com/ahmedkhursheed23/HaloBattery/issues/48) | Current Endgame 4K reader fails with `short send (-1)`. nqble has a separate corrected reader and original-model confirmation on 2026-11-08. Review the forthcoming correction PR against its public sources and model-specific tests; do not advertise the current reader as working. [Public correction status](https://github.com/nqble/HaloBattery/blob/main/docs/endgame/4k-status.md). |
| [#343](https://github.com/HeyOkay/HaloBattery/issues/343), [#298](https://github.com/HeyOkay/HaloBattery/issues/298) | Two recent PRO X 3 logs remain negative: no receiver feature reply; probe output/feature writes return -1. Matching collections to PRO X 2 did not establish protocol compatibility. Need report descriptors and a G HUB battery capture; keep unconfirmed. |
| [#289](https://github.com/HeyOkay/HaloBattery/issues/289), [#251](https://github.com/HeyOkay/HaloBattery/issues/251) | AJ159 test2 still reports no link; all five collections refuse the write. Trying the fifth collection fixes a coverage bug but does not establish support. Need vendor capture before claiming AJ159/Kysona works. These paths came from fork #31, explicitly held in the historical register. |
| [#239](https://github.com/HeyOkay/HaloBattery/issues/239), [#310](https://github.com/HeyOkay/HaloBattery/issues/310) | Turtle Beach experimental paths from fork #23 remain a stable-release hold pending source review and reporter confirmation. |
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
The rolling pre-release build remains for testing. Resolve or exclude the experimental
protocol holds above before the first manual stable promotion. Existing installed fork
builds still check the fork; users must install the canonical build manually unless a
separate bridge release is coordinated. Do not silently rewrite historical fork releases.
