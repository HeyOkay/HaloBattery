"""Bluetooth snapshot failure and last-known readings; JSON/fake processes only."""
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest import mock

from providers import bluetooth

MAC = "AABBCCDDEEFF"
ROOT = "BTHENUM\\DEV_" + MAC + "\\7&123"
KEY = "bt:" + MAC


def snapshot(level=75, *, connected=True, pnp=True, error=None, status="OK"):
    props = []
    if level is not None:
        props.append(dict(id=ROOT, key=bluetooth.K_BAT, data=level))
    if pnp is not None:
        props.append(dict(id=ROOT, key=bluetooth.K_CONN, data=pnp))
    aep = [dict(addr=MAC, conn=connected, err=error, le=False, cls=0x0404)]
    return dict(devs=[dict(id=ROOT, name="Windows Headset", status=status)],
                props=props, aep=aep, aep_error=None)


def raw_snapshot(*args, **kwargs):
    return json.dumps(snapshot(*args, **kwargs))


def phone_snapshot(level=75, *, connected=True, pnp=True, error=None):
    data = snapshot(level, connected=connected, pnp=pnp, error=error)
    data["devs"][0]["name"] = "Phone"
    data["aep"][0]["cls"] = 0x0203     # major class 2, smartphone
    return data


def raw_phone(*args, **kwargs):
    return json.dumps(phone_snapshot(*args, **kwargs))


class BluetoothProviderTests(unittest.TestCase):
    def test_charging_state_is_grouped_by_address_and_refreshes(self):
        provider = bluetooth.BluetoothProvider()
        data = snapshot()
        other_mac = "112233445566"
        other = json.loads(json.dumps(snapshot(40)).replace(MAC, other_mac))
        for name in ("devs", "props", "aep"):
            data[name].extend(other[name])
        # The level can be on the root and the charging property on a service node.
        charge = dict(id="BTHENUM\\{0000111E-0000-1000-8000-00805F9B34FB}\\7&1&0&" + MAC + "_C00000000",
                      key=bluetooth.K_CHARGE, data=1)
        data["props"].append(charge)
        statuses = {s.key: s for s in provider.handle(json.dumps(data))}
        self.assertTrue(statuses[KEY].charging)
        self.assertTrue(statuses[KEY].charging_known)
        self.assertFalse(statuses["bt:" + other_mac].charging)
        self.assertTrue(statuses["bt:" + other_mac].charging_known)
        charge["data"] = 0
        self.assertFalse(next(s for s in provider.handle(json.dumps(data)) if s.key == KEY).charging)

    def test_unknown_invalid_and_conflicting_charging_states(self):
        for values in ([2], [255], [True], [1.0], ["1"], [None], [0, 1]):
            with self.subTest(values=values):
                data = snapshot()
                data["props"].extend(dict(id=ROOT, key=bluetooth.K_CHARGE, data=v) for v in values)
                status, = bluetooth.group_devices(data, [])
                self.assertEqual(status.level, 75)
                self.assertFalse(status.charging)
                self.assertFalse(status.charging_known)

    def test_cached_level_or_lost_connection_clears_charging(self):
        for data in (snapshot(None), snapshot(connected=False), {}):
            with self.subTest(data=data):
                provider = bluetooth.BluetoothProvider()
                prop = dict(id=ROOT, key=bluetooth.K_CHARGE, data=1)
                first = snapshot()
                first["props"].append(prop)
                self.assertTrue(provider.handle(json.dumps(first))[0].charging)
                if "props" in data:
                    data["props"].append(prop)
                stale, = provider.handle(json.dumps(data))
                self.assertFalse(stale.online)
                self.assertFalse(stale.charging)
                self.assertFalse(stale.charging_known)

    def test_level_boundaries_and_invalid_values(self):
        for value in (0, 100, 55, "75"):
            with self.subTest(value=value):
                provider = bluetooth.BluetoothProvider()
                statuses = provider.parse(raw_snapshot(value))
                self.assertEqual(len(statuses), 1)
                self.assertEqual(statuses[0].level, int(value))
                self.assertTrue(statuses[0].online)
                self.assertFalse(statuses[0].polling_stale)
        for value in (-1, 101, 255, True, False, 50.9, float("nan"),
                      float("inf"), "bad", "101", "1_0", "+50", {}, []):
            with self.subTest(value=value):
                provider = bluetooth.BluetoothProvider()
                self.assertEqual(provider.parse(raw_snapshot(value)), [])
                self.assertNotIn(MAC, provider._levels)

    def test_bad_value_does_not_replace_last_valid_battery(self):
        provider = bluetooth.BluetoothProvider()
        provider.handle(raw_snapshot(75))
        for value in (-1, 101, True, 50.9, "bad"):
            with self.subTest(value=value):
                statuses = provider.handle(raw_snapshot(value))
                self.assertEqual(statuses[0].level, 75)
                self.assertEqual(provider._levels[MAC], 75)
                self.assertFalse(statuses[0].online)
                self.assertFalse(statuses[0].charging_known)
                self.assertTrue(statuses[0].polling_stale)

    def test_missing_battery_is_last_known_until_fresh_battery_returns(self):
        provider = bluetooth.BluetoothProvider()
        first = provider.handle(raw_snapshot(0))[0]
        self.assertTrue(first.online)
        cached = provider.handle(raw_snapshot(None))[0]
        self.assertEqual(cached.level, 0)
        self.assertFalse(cached.online)
        self.assertFalse(cached.charging)
        self.assertFalse(cached.charging_known)
        self.assertTrue(cached.polling_stale)
        restored = provider.handle(raw_snapshot(100))[0]
        self.assertEqual(restored.level, 100)
        self.assertTrue(restored.online)
        self.assertTrue(restored.charging_known)
        self.assertFalse(restored.polling_stale)

    def test_missing_battery_without_prior_level_never_fabricates_one(self):
        provider = bluetooth.BluetoothProvider()
        self.assertEqual(provider.handle(raw_snapshot(None)), [])
        self.assertEqual(provider._levels, {})

    def test_failed_snapshots_retain_icons_without_advancing_misses(self):
        provider = bluetooth.BluetoothProvider()
        first = provider.handle(raw_snapshot())[0]
        for raw in ("", "{", "null", "[]", "12", "{}", '{"devs":"bad"}',
                    '{"devs":[null]}', '{"devs":[],"props":[1]}',
                    '{"devs":[],"aep":[{"addr":17}]}'):
            with self.subTest(raw=raw):
                out = provider.handle(raw)
                self.assertEqual(len(out), 1)
                self.assertEqual(out[0].key, KEY)
                self.assertEqual(out[0].level, 75)
                self.assertFalse(out[0].online)
                self.assertFalse(out[0].charging_known)
                self.assertTrue(out[0].polling_stale)
                self.assertEqual(provider._misses[KEY], 0)
                self.assertIs(provider._shown[KEY], first)

    def test_malformed_fields_do_not_partially_replace_level_cache(self):
        provider = bluetooth.BluetoothProvider()
        provider.handle(raw_snapshot(75))
        malformed = snapshot(90)
        malformed["devs"][0]["name"] = 17
        out = provider.handle(json.dumps(malformed))
        self.assertEqual(out[0].level, 75)
        self.assertEqual(provider._levels, {MAC: 75})
        self.assertEqual(provider._misses[KEY], 0)

    def test_failures_between_real_misses_do_not_remove_or_reset_device(self):
        provider = bluetooth.BluetoothProvider()
        provider.handle(raw_snapshot())
        empty = json.dumps(dict(devs=[], props=[], aep=[]))
        retained = provider.handle(empty)
        self.assertEqual(provider._misses[KEY], 1)
        self.assertFalse(retained[0].online)
        for _ in range(3):
            self.assertEqual(len(provider.merge(None)), 1)
            self.assertEqual(provider._misses[KEY], 1)
        self.assertEqual(provider.handle(empty), [])
        self.assertNotIn(KEY, provider._shown)

    def test_valid_disconnection_still_removes_after_smoothing(self):
        provider = bluetooth.BluetoothProvider()
        provider.handle(raw_snapshot())
        first = provider.handle(raw_snapshot(connected=False))
        self.assertEqual(len(first), 1)
        self.assertFalse(first[0].online)
        self.assertEqual(provider.handle(raw_snapshot(connected=False)), [])

    def test_phone_connection_flap_keeps_the_live_icon_during_the_grace(self):
        now = [100.0]
        provider = bluetooth.BluetoothProvider(clock=lambda: now[0])
        first = provider.handle(raw_phone(75))[0]
        self.assertTrue(first.online)
        self.assertEqual(first.kind, "phone")

        now[0] += 1
        flap = provider.handle(raw_phone(connected=False))[0]
        self.assertTrue(flap.online)
        self.assertFalse(flap.polling_stale)
        self.assertEqual(flap.level, 75)
        self.assertFalse(flap.charging_known)
        self.assertEqual(provider._misses[KEY], 0)

        now[0] += 29
        still = provider.handle(raw_phone(connected=False))[0]
        self.assertTrue(still.online)
        self.assertFalse(still.polling_stale)

        now[0] += 1
        stale = provider.handle(raw_phone(connected=False))[0]
        self.assertFalse(stale.online)
        self.assertTrue(stale.polling_stale)
        self.assertEqual(provider._misses[KEY], 1)

    def test_phone_returning_within_the_grace_restarts_it(self):
        now = [100.0]
        provider = bluetooth.BluetoothProvider(clock=lambda: now[0])
        provider.handle(raw_phone())
        now[0] += 1
        provider.handle(raw_phone(connected=False))
        now[0] += 10
        live = provider.handle(raw_phone())[0]
        self.assertTrue(live.online)
        self.assertNotIn(KEY, provider._off_since)

        now[0] += 1
        flap = provider.handle(raw_phone(connected=False))[0]
        self.assertTrue(flap.online)
        now[0] += 29
        self.assertTrue(provider.handle(raw_phone(connected=False))[0].online)

    def test_connection_fallback_precedence_is_preserved(self):
        cases = [
            (dict(connected=False, pnp=True), False),
            (dict(connected=True, pnp=False), True),
            (dict(connected=None, pnp=True, error="timeout"), True),
            (dict(connected=None, pnp=False, error="timeout"), False),
            (dict(connected=None, pnp=None, error="timeout"), False),
            (dict(connected=None, pnp=None, error=None, status="OK"), True),
            (dict(connected=None, pnp=None, error=None, status="Error"), False),
        ]
        for options, shown in cases:
            with self.subTest(options=options):
                out = bluetooth.BluetoothProvider().parse(raw_snapshot(**options))
                self.assertEqual(bool(out), shown)

    def test_global_winrt_failure_still_uses_pnp_connection(self):
        data = snapshot()
        data["aep"] = []
        data["aep_error"] = "WinRT unavailable"
        out = bluetooth.BluetoothProvider().parse(json.dumps(data))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].online)

    def test_singleton_powershell_collections_are_still_supported(self):
        data = snapshot()
        data["devs"] = data["devs"][0]
        data["props"] = data["props"][0]
        data["aep"] = data["aep"][0]
        self.assertEqual(bluetooth.BluetoothProvider().parse(json.dumps(data))[0].level, 75)

    def test_nonzero_query_exit_is_failure_even_with_valid_stdout(self):
        provider = bluetooth.BluetoothProvider()
        completed = SimpleNamespace(returncode=1,
            stdout=raw_snapshot().encode("utf-8"), stderr=b"failed")
        with mock.patch.object(bluetooth.subprocess, "run", return_value=completed):
            self.assertIsNone(provider._query())
        self.assertTrue(any("exited with code 1" in line for line in provider.diagnostics()))

    def test_query_timeout_and_process_failure_retain_last_known(self):
        for failure in (subprocess.TimeoutExpired("powershell", 60), OSError("process failed")):
            with self.subTest(failure=failure):
                provider = bluetooth.BluetoothProvider()
                provider.handle(raw_snapshot())
                with mock.patch.object(bluetooth.sys, "platform", "win32"), \
                        mock.patch.object(bluetooth.subprocess, "run", side_effect=failure):
                    for _ in range(3):
                        out = provider.poll()
                        self.assertEqual(len(out), 1)
                        self.assertFalse(out[0].online)
                        self.assertTrue(out[0].polling_stale)
                        self.assertEqual(provider._misses[KEY], 0)


if __name__ == "__main__":
    unittest.main()
