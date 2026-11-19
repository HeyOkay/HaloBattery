# Original mutation regression mapping

The six survivors in Ahmed's original [#413](https://github.com/HeyOkay/HaloBattery/issues/413)
mutation report describe six faulty behaviors, not six new test methods.
The mapping below records their current-source checks and publication scope.

| Original survivor | Fault introduced | Current regression | Publication |
| --- | --- | --- | --- |
| M3 | Invert the DS4 cable/charging bit | `BatteryCodeTests.test_dualshock_normal_boundaries_and_documented_full` in `tests/test_playstation_battery_validation.py` | Prerequisite [#471](https://github.com/HeyOkay/HaloBattery/pull/471) |
| M4 | Omit charging for DualSense charge-complete state | `BatteryCodeTests.test_dualsense_normal_bins_and_explicit_charge_complete` in the same file | Prerequisite #471 |
| M5 | Remove HyperX's 0..100 range check | `HyperXRangeTests.test_well_formed_reply_cannot_publish_an_impossible_percentage` in `tests/test_original_mutation_regressions.py` | [#477](https://github.com/HeyOkay/HaloBattery/pull/477) |
| M6 | Remove JBL's 0..100 range check | `JblRangeTests.test_well_formed_report_cannot_publish_an_impossible_percentage` in the same file | #477 |
| M9 | Change the history recharge threshold from `>=` to `>` | `RechargeThresholdTests.test_exact_three_point_rise_resets_history_but_two_point_jitter_does_not` in the same file | #477 |
| M12 | Remove Astro's 0..100 range check | `AstroCollectionTests.test_truncated_garbage_and_out_of_range_replies_do_not_show_a_level` in `tests/test_astro.py` | Parallel [#472](https://github.com/HeyOkay/HaloBattery/pull/472), not a #477 prerequisite |

The five methods added in `test_original_mutation_regressions.py` cover M5,
M6 and M9: two positive-boundary methods, two impossible-value methods, and
one exact recharge-threshold method. M3 and M4 already have prerequisite
coverage. M12 belongs to the separate HID request safety work; #477 alone
does not add that Astro test or claim to close all six survivors.

The rewritten DualSense parser has an explicit `charge == 2` branch. The
equivalent M4 mutation changes only that branch's `return 100, True` to
`return 100, False`, preserving its percentage. This is a documented semantic
translation, not the unchanged historical patch.

The original historical score on the old source is owner-supplied and has
not been reproduced by this PR. Test counts and mutation results from other
source snapshots are not exact-tip validation of #477. No device or network
test is implied by the fixture/source records.

Independent inbound fixtures apply to the files changed in #477. Some unchanged
tests still construct replies with provider constants; there is no
repository-wide independent-fixture guarantee.
