# Original mutation regression mapping

Ahmed's original [#413](https://github.com/HeyOkay/HaloBattery/issues/413) report
listed six surviving mutations on pre-release `5271cc9`. The tests below cover
those six one-line faults against the current production contracts; they do not
reproduce the historical score on the old snapshot.

All listed methods are in `tests/test_original_mutation_regressions.py`.

| Original survivor | Fault introduced | Regression |
| --- | --- | --- |
| M3 | Invert the DS4 cable/charging bit | `PlayStationChargingTests.test_dualshock_cable_bit_controls_charging` |
| M4 | Omit charging for DualSense charge-complete state | `PlayStationChargingTests.test_dualsense_charge_complete_still_counts_as_charging` |
| M5 | Remove HyperX's 0..100 range check | `HyperXRangeTests.test_well_formed_reply_cannot_publish_an_impossible_percentage` |
| M6 | Remove JBL's 0..100 range check | `JblRangeTests.test_well_formed_report_cannot_publish_an_impossible_percentage` |
| M9 | Change the history recharge threshold from `>=` to `>` | `RechargeThresholdTests.test_exact_three_point_rise_resets_history_but_two_point_jitter_does_not` |
| M12 | Remove Astro's 0..100 range check | `AstroRangeTests.test_well_formed_reply_cannot_publish_an_impossible_percentage` |

The five original #477 methods for HyperX, JBL and history are retained. Four
additional methods cover the current Sony charging contract and Astro's valid
and invalid levels. The already-integrated Cloud III poll test also rejects 101;
the dedicated HyperX checks additionally cover both reply IDs, 255 and buffer
representations. No new device request, parser policy or controller identity is
needed to exercise any of the six original mutations. M4 uses the unchanged
membership mutation `(0x1, 0x2)` to `(0x1,)`, not a rewrite-specific translation.
The current DualSense parser retains its level nibble in state 2; these tests
preserve that behavior rather than adopt #471's forced-full branch.

## Integration boundary

[#477](https://github.com/HeyOkay/HaloBattery/pull/477) originally carried #471
production changes and tests as prerequisites. This integration preserves the
current pre-release production files instead. The new identity/validation
suites `test_playstation_identity.py`, `test_playstation_battery_validation.py`,
`test_wgi_nova2_placeholder.py`, `test_wgi_validation.py` and
`test_xinput_identity.py` remain with #471: their migration rules, refusal of
Sony error/reserved bins and stricter report lengths are separate behavior
changes. The existing pre-release PlayStation tests are preserved unchanged.
The narrow M3/M4 checks above retain the useful original charging coverage
without importing those unresolved identity/parser changes. Likewise, M12
checks the existing Astro parser without importing #472's collection changes.

Independent inbound fixtures apply to the 28 existing test files changed in
#477. iRocks and both HyperX Cloud II request expectations are also pinned to
literal frames. Some unchanged tests elsewhere still derive fixtures from
provider constants; this is not a repository-wide independence guarantee.
The source records distinguish protocol/descriptor evidence from hardware
confirmation. All tests use fake devices or pure parsers; no hardware/network
validation or new device-compatibility claim is implied.
