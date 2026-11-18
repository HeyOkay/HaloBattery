"""Hardware-free opt-in coordinator checks using existing fake instances."""
import ast
import importlib
from pathlib import Path
from contextlib import contextmanager
import threading
import time
import types
import unittest

from halo.polling import PollingRuntime, PollBatch, _CONFLICTS
from providers.base import DeviceStatus, Provider


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def advance(self, seconds):
        self.now += seconds


class Hooks:
    def __init__(self):
        self.local = threading.local()
        self.idle = {}
        self.cancelled = []
    @contextmanager
    def scope(self, owner):
        value = types.SimpleNamespace(failed=False, error="", cancelled=False, unsafe=False)
        self.local.scope = value
        self.local.owner = owner
        try:
            yield value
        finally:
            self.local.scope = None
    def transport_idle(self, owner):
        return self.idle.get(owner, True)
    def cancel_owner(self, owner):
        self.cancelled.append(owner)


class FakeProvider:
    def __init__(self, name, key=None):
        self.name = name
        self.key = key or name + ":test"
        self.values = [DeviceStatus(self.key, name + " device", 50, source=name)]
        self.calls = []
        self.entered = threading.Event()
        self.gate = None
        self.error = None
        self.on_poll = None
        self.pending = False
        self.signature_value = frozenset()
        self.signature_calls = []
        self.aliases = {}
        self.alias_calls = []
        self.alias_error = None
        self.on_aliases = None
    def poll(self):
        self.calls.append(threading.current_thread().name)
        values = list(self.values)
        self.entered.set()
        if self.gate is not None:
            self.gate.wait()
        if self.error is not None:
            raise self.error
        if self.on_poll is not None:
            return self.on_poll()
        return values
    def diagnostics(self):
        return ["cached diagnostics"]
    def legacy_key_aliases(self):
        self.alias_calls.append(threading.current_thread().name)
        if self.alias_error is not None:
            raise self.alias_error
        return self.on_aliases() if self.on_aliases is not None else self.aliases
    def connected_slots(self):
        self.signature_calls.append(threading.current_thread().name)
        return self.signature_value


class PollingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hooks = Hooks()
        self.notifications = []
        self.runtimes = []
        self.gates = []
    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.hooks.idle.clear()
        for runtime in self.runtimes:
            runtime.close(0.5)
            end = time.monotonic() + 1
            while runtime.stats()["alive"] and time.monotonic() < end:
                runtime.pump()
                time.sleep(0.001)
            self.assertEqual(runtime.stats()["alive"], 0)
    def gate(self):
        value = threading.Event()
        self.gates.append(value)
        return value
    def runtime(self, providers, **kwargs):
        value = PollingRuntime(providers, self.notifications.append, clock=self.clock,
                                    provider_scope=self.hooks.scope,
                                    transport_idle=self.hooks.transport_idle,
                                    cancel_owner=self.hooks.cancel_owner, **kwargs)
        self.runtimes.append(value)
        value.configure(set(), False)
        return value
    def wait(self, runtime, predicate):
        end = time.monotonic() + 1
        while time.monotonic() < end:
            runtime.pump()
            if predicate():
                return
            time.sleep(0.001)
        self.fail("coordinator did not reach expected state: " + repr(runtime.stats()))
    def idle(self, runtime):
        return runtime.stats()["running"] == 0 and runtime.stats()["queued"] == 0

    def test_native_work_stays_on_two_fixed_workers_and_cached_reads_are_not_fresh(self):
        provider = FakeProvider("keychron")
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual(batch.fresh_keys, {provider.key})
        self.assertEqual(batch.completed, {"keychron"})
        self.assertEqual(batch.owners[provider.key], "keychron")
        self.assertTrue(all(name.startswith("halo-provider-") for name in provider.calls))
        self.assertFalse(self.notifications[-1])
        again = runtime.consume()
        self.assertFalse(again.fresh_keys)
        self.assertFalse(again.completed)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(runtime.stats()["workers"], 2)
        self.assertLessEqual(runtime.stats()["peak_running"], 2)

    def test_requests_coalesce_to_one_followup_without_reconstructing_provider(self):
        provider = FakeProvider("keychron")
        provider.gate = self.gate()
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.assertTrue(provider.entered.wait(1))
        for _ in range(100):
            runtime.request_poll()
        self.assertEqual(len(provider.calls), 1)
        provider.gate.set()
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertEqual(len(provider.calls), 2)
        self.assertIs(runtime._slots["keychron:0"].provider, provider)

    def test_timed_out_call_retains_stale_cache_and_native_lease_after_python_returns(self):
        bad, good = FakeProvider("keychron"), FakeProvider("audeze")
        runtime = self.runtime([bad, good])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        runtime.consume()
        bad.gate = self.gate()
        bad.entered.clear()
        runtime.request_poll()
        self.assertTrue(bad.entered.wait(1))
        self.hooks.idle["keychron:0"] = False
        self.clock.advance(21)
        runtime.pump()
        batch = runtime.consume()
        stale = next(s for s in batch.hid if s.key == bad.key)
        self.assertTrue(stale.polling_stale)
        self.assertFalse(stale.online or stale.charging or stale.charging_known)
        self.assertFalse(stale.polling_paused or stale.wake_on_input)
        self.assertNotIn(bad.key, batch.fresh_keys)
        self.assertNotIn("keychron", batch.completed)
        bad.gate.set()
        self.wait(runtime, lambda: runtime.stats()["running"] == 0)
        self.clock.advance(100)
        runtime.request_poll(force=True)
        self.wait(runtime, lambda: len(good.calls) >= 3)
        self.assertEqual(len(bad.calls), 2)
        self.assertTrue(runtime._slots["keychron:0"].lease)
        self.hooks.idle["keychron:0"] = True
        self.wait(runtime, lambda: len(bad.calls) == 3)
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertIn(bad.key, runtime.consume().fresh_keys)

    def test_overlap_domains_serialize_and_unrelated_device_still_polls(self):
        razer, barracuda, keyboard = [FakeProvider(n) for n in ("razer", "barracuda", "keychron")]
        razer.gate = self.gate()
        runtime = self.runtime([razer, barracuda, keyboard])
        runtime.request_poll()
        self.assertTrue(razer.entered.wait(1))
        self.wait(runtime, keyboard.entered.is_set)
        self.assertFalse(barracuda.entered.is_set())
        self.hooks.idle["razer:0"] = False
        self.clock.advance(26)
        runtime.pump()
        razer.gate.set()
        self.wait(runtime, lambda: runtime.stats()["running"] == 0)
        self.assertFalse(barracuda.entered.is_set())
        self.hooks.idle["razer:0"] = True
        runtime.pump()
        self.wait(runtime, barracuda.entered.is_set)

    def test_scope_failure_caught_by_provider_is_not_an_authoritative_empty(self):
        provider = FakeProvider("keychron")
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        runtime.consume()
        def caught_failure():
            self.hooks.local.scope.failed = True
            self.hooks.local.scope.error = "read deadline expired"
            return []
        provider.on_poll = caught_failure
        runtime.request_poll()
        self.wait(runtime, lambda: runtime._slots["keychron:0"].failures == 1)
        batch = runtime.consume()
        self.assertEqual(batch.hid[0].level, 50)
        self.assertTrue(batch.hid[0].polling_stale)
        self.assertFalse(batch.completed)
        self.assertFalse(batch.fresh_keys)

    def test_confirmed_ordinary_error_allows_current_fallback_reading(self):
        provider = FakeProvider("wlmouse")
        runtime = self.runtime([provider])
        def report_size_fallback():
            self.hooks.local.scope.failed = True
            self.hooks.local.scope.error = "65-byte feature request refused"
            return [DeviceStatus(provider.key, "WLmouse", 71, source="wlmouse")]
        provider.on_poll = report_size_fallback
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual([s.level for s in batch.hid], [71])
        self.assertEqual(batch.fresh_keys, {provider.key})
        self.assertEqual(batch.completed, {"wlmouse"})
        self.assertFalse(batch.hid[0].polling_stale)
        self.assertEqual(runtime._slots["wlmouse:0"].failures, 0)
        self.assertFalse(runtime.consume().fresh_keys)

    def test_confirmed_error_keeps_healthy_pad_beside_connected_unknown_pad(self):
        provider = FakeProvider("playstation")
        healthy = DeviceStatus("playstation:healthy", "healthy pad", 60, source="playstation")
        unknown = DeviceStatus("playstation:unknown", "connected unknown pad", None, source="playstation")
        provider.aliases = {"old-healthy": healthy.key}
        def partial_reading():
            self.hooks.local.scope.failed = True
            self.hooks.local.scope.error = "other collection open refused"
            return [healthy, unknown]
        provider.on_poll = partial_reading
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual([s.level for s in batch.hid], [60, None])
        self.assertTrue(all(s.online and not s.polling_stale for s in batch.hid))
        self.assertEqual(batch.fresh_keys, {healthy.key, unknown.key})
        self.assertEqual(batch.completed, {"playstation"})
        self.assertEqual(batch.aliases, {"old-healthy": healthy.key})

    def test_mixed_partial_reading_cannot_bypass_unsafe_cancel_deadline_or_ownership(self):
        for failure in ("unsafe", "cancelled", "unretired", "offline-cache", "deadline"):
            with self.subTest(failure=failure):
                provider = FakeProvider("playstation")
                runtime = self.runtime([provider])
                runtime.request_poll()
                self.wait(runtime, lambda: self.idle(runtime))
                runtime.consume()
                self.hooks.idle["playstation:0"] = failure != "unretired"
                def partial():
                    scope = self.hooks.local.scope
                    scope.failed = True
                    scope.unsafe = failure == "unsafe"
                    scope.cancelled = failure == "cancelled"
                    scope.error = "other endpoint unavailable"
                    if failure == "deadline":
                        self.clock.advance(21)
                    return [DeviceStatus("healthy", "current", 60, source="playstation"),
                            DeviceStatus("unknown", "unreadable", None,
                                         online=failure != "offline-cache", source="playstation")]
                provider.on_poll = partial
                with self.assertLogs("halo_battery", level="WARNING"):
                    runtime.request_poll()
                    self.wait(runtime, lambda: runtime._slots["playstation:0"].failures == 1)
                batch = runtime.consume()
                self.assertEqual([s.level for s in batch.hid], [50])
                self.assertTrue(batch.hid[0].polling_stale)
                self.assertFalse(batch.fresh_keys)
                self.assertFalse(batch.aliases)
                self.hooks.idle["playstation:0"] = True

    def test_partial_invalid_level_remains_unknown_instead_of_clamped_full(self):
        provider = FakeProvider("audeze")
        def partial():
            self.hooks.local.scope.failed = True
            return [DeviceStatus("healthy", "current", 60, source="audeze"),
                    DeviceStatus("invalid", "bad level", 101, source="audeze")]
        provider.on_poll = partial
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual([(s.key, s.level) for s in batch.hid], [("healthy", 60), ("invalid", None)])
        self.assertTrue(all(not s.polling_stale for s in batch.hid))
        self.assertEqual(batch.fresh_keys, {"healthy", "invalid"})

    def test_ordinary_error_with_only_unknown_endpoints_remains_failed(self):
        provider = FakeProvider("playstation")
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        runtime.consume()
        def all_unknown():
            self.hooks.local.scope.failed = True
            return [DeviceStatus("unknown", "no measurement", None, source="playstation")]
        provider.on_poll = all_unknown
        with self.assertLogs("halo_battery", level="WARNING"):
            runtime.request_poll()
            self.wait(runtime, lambda: runtime._slots["playstation:0"].failures == 1)
        batch = runtime.consume()
        self.assertEqual([s.level for s in batch.hid], [50])
        self.assertTrue(batch.hid[0].polling_stale)
        self.assertFalse(batch.fresh_keys)

    def test_unsafe_or_unretired_error_never_publishes_provider_cache_as_fresh(self):
        for unsafe, cancelled, idle, online in ((True, False, True, False),
                                                (True, False, True, True),
                                                (False, True, True, True),
                                                (False, False, False, True),
                                                (False, False, True, False)):
            with self.subTest(unsafe=unsafe, cancelled=cancelled, idle=idle, online=online):
                provider = FakeProvider("wlmouse")
                runtime = self.runtime([provider])
                runtime.request_poll()
                self.wait(runtime, lambda: self.idle(runtime))
                runtime.consume()
                self.hooks.idle["wlmouse:0"] = idle
                def caught_failure():
                    scope = self.hooks.local.scope
                    scope.failed, scope.unsafe, scope.cancelled = True, unsafe, cancelled
                    scope.error = "deadline expired" if unsafe else "feature report refused"
                    return [DeviceStatus(provider.key, "cached", 80, online=online, source="wlmouse")]
                provider.on_poll = caught_failure
                with self.assertLogs("halo_battery", level="WARNING") as messages:
                    runtime.request_poll()
                    self.wait(runtime, lambda: runtime._slots["wlmouse:0"].failures == 1)
                self.assertIn("wlmouse:0", messages.output[0])
                batch = runtime.consume()
                self.assertEqual([s.level for s in batch.hid], [50])
                self.assertTrue(batch.hid[0].polling_stale)
                self.assertFalse(batch.hid[0].online)
                self.assertFalse(batch.fresh_keys)
                self.assertFalse(batch.completed)
                self.hooks.idle["wlmouse:0"] = True
                runtime.close(.5)

    def test_disable_reenable_discards_old_generation_and_uses_existing_instance(self):
        provider = FakeProvider("keychron")
        provider.gate = self.gate()
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.assertTrue(provider.entered.wait(1))
        runtime.configure({"keychron"}, False)
        self.assertEqual(runtime.consume().hid, [])
        runtime.configure(set(), False)
        provider.values = [DeviceStatus(provider.key, "new", 75, source="keychron")]
        provider.gate.set()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual([s.level for s in batch.hid], [75])
        self.assertEqual(len(provider.calls), 2)

    def test_legacy_lamzu_name_waits_for_both_protocol_completions(self):
        first, second = FakeProvider("lamzu", "lamzu:first"), FakeProvider("lamzu", "lamzu:second")
        second.gate = self.gate()
        runtime = self.runtime([first, second])
        runtime.request_poll()
        self.wait(runtime, lambda: bool(runtime._slots["lamzu:0"].statuses))
        self.assertNotIn("lamzu", runtime.consume().completed)
        second.gate.set()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual(batch.completed, {"lamzu"})
        self.assertEqual(set(batch.owners.values()), {"lamzu"})
        self.assertFalse(runtime.consume().completed)

    def test_signature_queries_are_background_and_do_not_overlap_xinput_poll(self):
        provider = FakeProvider("xinput")
        discovery_threads = []
        def discovery():
            discovery_threads.append(threading.current_thread().name)
            return frozenset(("fake HID path",))
        runtime = self.runtime([provider], signature_reader=discovery)
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertTrue(all(n.startswith("halo-provider-") for n in discovery_threads))
        self.assertEqual(runtime.signature()[0], frozenset(("fake HID path",)))
        provider.gate = self.gate()
        runtime.request_poll()
        self.assertTrue(provider.entered.wait(1))
        previous = len(provider.signature_calls)
        self.clock.advance(3)
        provider.signature_value = frozenset((1,))
        runtime.pump()
        time.sleep(0.01)
        self.assertEqual(len(provider.signature_calls), previous)
        provider.gate.set()
        self.wait(runtime, lambda: runtime.signature()[1] == frozenset((1,)))
        self.assertTrue(any(self.notifications))

    def test_backoff_and_circuit_never_create_replacement_threads(self):
        provider = FakeProvider("keychron")
        provider.error = RuntimeError("native error")
        runtime = self.runtime([provider])
        workers = runtime._workers
        runtime.request_poll()
        for count, delay in ((1, 5), (2, 10), (3, 20)):
            self.wait(runtime, lambda: runtime._slots["keychron:0"].failures == count)
            self.clock.advance(delay)
            runtime.pump()
        self.assertTrue(runtime._slots["keychron:0"].circuit)
        self.assertEqual(len(provider.calls), 3)
        runtime.request_poll()
        time.sleep(0.01)
        self.assertEqual(len(provider.calls), 3)
        provider.error = None
        runtime.request_poll(force=True)
        self.wait(runtime, lambda: len(provider.calls) == 4)
        self.assertEqual(runtime._workers, workers)

    def test_invalid_levels_are_refused_and_bad_result_stales_only_its_owner(self):
        provider = FakeProvider("keychron")
        provider.values = [DeviceStatus(str(index), "device", level, source="keychron")
                           for index, level in enumerate((None, 0, 100, 101, True))]
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertEqual([s.level for s in runtime.consume().hid], [None, 0, 100, None, None])
        provider.values = [None]
        runtime.request_poll()
        self.wait(runtime, lambda: runtime._slots["keychron:0"].failures == 1)
        self.assertTrue(all(s.polling_stale for s in runtime.consume().hid))

    def test_diagnostics_event_and_deadline_use_cached_publication(self):
        gate = self.gate()
        reads = []
        def reader():
            reads.append(threading.current_thread().name)
            gate.wait()
            return ["VID=1234 PID=5678 metadata"]
        runtime = self.runtime([], diagnostics_reader=reader)
        event = runtime.request_diagnostics()
        self.assertIs(event, runtime.request_diagnostics())
        self.wait(runtime, lambda: bool(reads))
        self.assertFalse(event.is_set())
        self.clock.advance(11)
        runtime.pump()
        self.assertTrue(event.is_set())
        gate.set()
        self.wait(runtime, lambda: runtime.stats()["running"] == 0)
        self.assertNotIn("VID=1234 PID=5678 metadata", runtime.diagnostics())
        self.assertTrue(all(n.startswith("halo-provider-") for n in reads))

    def test_expired_queued_diagnostics_never_runs_after_workers_become_free(self):
        providers = [FakeProvider("p0"), FakeProvider("p1")]
        for provider in providers:
            provider.gate = self.gate()
        calls = []
        runtime = self.runtime(providers, diagnostics_reader=lambda: calls.append(True) or [])
        runtime.request_poll()
        self.assertTrue(all(provider.entered.wait(1) for provider in providers))
        event = runtime.request_diagnostics()
        self.clock.advance(11)
        runtime.pump()
        self.assertTrue(event.is_set())
        for provider in providers:
            provider.gate.set()
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertEqual(calls, [])

    def test_ready_queue_is_fair_and_keeps_at_most_one_followup_per_provider(self):
        providers = [FakeProvider("p" + str(i)) for i in range(20)]
        providers[0].gate = self.gate()
        runtime = self.runtime(providers)
        runtime.request_poll()
        self.assertTrue(providers[0].entered.wait(1))
        for _ in range(25):
            runtime.request_poll()
        self.wait(runtime, lambda: all(provider.calls for provider in providers[1:]))
        self.assertEqual(len(providers[0].calls), 1)
        self.assertLessEqual(runtime.stats()["queued"], len(providers))
        providers[0].gate.set()
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertLessEqual(runtime.stats()["peak_running"], 2)

    def test_two_stuck_jobs_degrade_without_thread_growth_and_shutdown_is_bounded(self):
        providers = [FakeProvider("p" + str(i)) for i in range(3)]
        for provider in providers[:2]:
            provider.gate = self.gate()
        runtime = self.runtime(providers)
        runtime.request_poll()
        self.assertTrue(all(p.entered.wait(1) for p in providers[:2]))
        self.assertFalse(providers[2].entered.is_set())
        self.clock.advance(21)
        runtime.pump()
        self.assertEqual(runtime.stats()["running"], 2)
        self.assertEqual(runtime.stats()["alive"], 2)
        self.assertLessEqual(runtime.stats()["queued"], 4)
        before = time.monotonic()
        self.assertFalse(runtime.close(0.02))
        self.assertLess(time.monotonic() - before, 0.2)
        self.assertFalse(runtime.request_poll(force=True))
        for provider in providers[:2]:
            provider.gate.set()
        self.wait(runtime, lambda: runtime.stats()["alive"] == 0)
        self.assertFalse(providers[2].entered.is_set())

    def test_alias_field_preserves_four_argument_batch_and_provider_default(self):
        batch = PollBatch([], set(), set(), {})
        other = PollBatch([], set(), set(), {})
        self.assertEqual(batch.aliases, {})
        batch.aliases["old"] = "new"
        self.assertEqual(other.aliases, {})
        provider = Provider()
        provider.legacy_key_aliases()["old"] = "new"
        self.assertEqual(provider.legacy_key_aliases(), {})

    def test_alias_is_worker_cached_and_consumed_with_its_fresh_observation_once(self):
        provider = FakeProvider("keychron")
        provider.aliases = {"keychron:legacy": provider.key}
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual(batch.aliases, {"keychron:legacy": provider.key})
        self.assertEqual(batch.fresh_keys, {provider.key})
        self.assertEqual(len(provider.alias_calls), 1)
        self.assertTrue(provider.alias_calls[0].startswith("halo-provider-"))
        self.assertEqual(runtime.consume().aliases, {})
        self.assertEqual(runtime.consume().aliases, {})
        self.assertEqual(len(provider.alias_calls), 1)
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertEqual(runtime.consume().aliases, {"keychron:legacy": provider.key})
        self.assertEqual(len(provider.alias_calls), 2)

    def test_alias_target_must_be_current_online_nonstale_and_fresh(self):
        for kind in ("missing", "offline", "stale", "unrelated"):
            with self.subTest(kind=kind):
                provider = FakeProvider("keychron")
                provider.aliases = {"keychron:legacy": provider.key}
                if kind == "missing":
                    provider.values = []
                elif kind == "offline":
                    provider.values[0].online = False
                elif kind == "stale":
                    provider.values[0].polling_stale = True
                else:
                    provider.aliases = {"keychron:legacy": "another-provider:new"}
                runtime = self.runtime([provider])
                runtime.request_poll()
                self.wait(runtime, lambda: self.idle(runtime))
                self.assertEqual(runtime.consume().aliases, {})
                self.assertEqual(runtime.consume().aliases, {})

    def test_alias_does_not_replay_when_only_another_provider_refreshes(self):
        first, second = FakeProvider("keychron"), FakeProvider("audeze")
        first.aliases = {"keychron:legacy": first.key}
        runtime = self.runtime([first, second])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        self.assertEqual(runtime.consume().aliases, {"keychron:legacy": first.key})
        first.gate = self.gate()
        runtime.request_poll()
        self.wait(runtime, lambda: len(second.calls) == 2 and not runtime._slots["audeze:1"].running)
        batch = runtime.consume()
        self.assertEqual(batch.fresh_keys, {second.key})
        self.assertEqual(batch.aliases, {})

    def test_conflicting_aliases_and_ambiguous_targets_are_refused(self):
        for mode in ("one-old-two-targets", "two-old-one-target", "live-old", "duplicate-target"):
            with self.subTest(mode=mode):
                first, second = FakeProvider("keychron"), FakeProvider("audeze")
                first.aliases = {"old": first.key}
                if mode == "one-old-two-targets":
                    second.aliases = {"old": second.key}
                elif mode == "two-old-one-target":
                    first.aliases = {"old": first.key, "also-old": first.key}
                elif mode == "live-old":
                    second.values[0].key = "old"
                else:
                    second.values[0].key = first.key
                runtime = self.runtime([first, second])
                runtime.request_poll()
                self.wait(runtime, lambda: self.idle(runtime))
                self.assertEqual(runtime.consume().aliases, {})

    def test_malformed_alias_metadata_does_not_discard_good_battery_readings(self):
        malformed = [None, [], 7, {"": "keychron:test"}, {"old": ""},
                     {"old": "old"}, {"old": 7}, {7: "keychron:test"},
                     {"x" * 257: "keychron:test"}, {"old": "x" * 257},
                     {"old\x00": "keychron:test"}, {"old": "new\n"},
                     {str(index): "target" for index in range(257)},
                     {"valid-old": "keychron:test", "bad": None}]
        for metadata in malformed:
            with self.subTest(metadata=repr(metadata)[:70]):
                provider = FakeProvider("keychron")
                provider.aliases = metadata
                runtime = self.runtime([provider])
                runtime.request_poll()
                self.wait(runtime, lambda: self.idle(runtime))
                batch = runtime.consume()
                self.assertEqual(batch.aliases, {})
                self.assertEqual(batch.fresh_keys, {provider.key})
                self.assertEqual([(value.level, value.online) for value in batch.hid], [(50, True)])
                self.assertIn("reading retained", "\n".join(runtime.diagnostics()))

    def test_raising_alias_method_does_not_discard_good_battery_reading(self):
        provider = FakeProvider("keychron")
        provider.alias_error = ValueError("malformed metadata")
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.wait(runtime, lambda: self.idle(runtime))
        batch = runtime.consume()
        self.assertEqual(batch.aliases, {})
        self.assertEqual(batch.fresh_keys, {provider.key})
        self.assertEqual(batch.hid[0].level, 50)

    def test_disabled_and_late_polls_never_read_or_publish_aliases(self):
        for disable in (True, False):
            with self.subTest(disable=disable):
                provider = FakeProvider("keychron")
                provider.aliases = {"old": provider.key}
                provider.gate = self.gate()
                runtime = self.runtime([provider])
                runtime.request_poll()
                self.assertTrue(provider.entered.wait(1))
                if disable:
                    runtime.configure({"keychron"})
                else:
                    self.clock.advance(21)
                    with self.assertLogs("halo_battery", level="WARNING"):
                        runtime.pump()
                provider.gate.set()
                self.wait(runtime, lambda: runtime.stats()["running"] == 0)
                self.assertEqual(provider.alias_calls, [])
                self.assertEqual(runtime.consume().aliases, {})

    def test_cancelled_or_failed_polls_never_publish_cached_aliases(self):
        for failure in ("cancelled", "error"):
            with self.subTest(failure=failure):
                provider = FakeProvider("keychron")
                provider.aliases = {"old": provider.key}
                runtime = self.runtime([provider])
                runtime.request_poll()
                self.wait(runtime, lambda: self.idle(runtime))
                self.assertEqual(runtime.consume().aliases, {"old": provider.key})
                if failure == "cancelled":
                    def cancelled():
                        self.hooks.local.scope.cancelled = True
                        return list(provider.values)
                    provider.on_poll = cancelled
                else:
                    provider.error = OSError("device unavailable")
                with self.assertLogs("halo_battery", level="WARNING"):
                    runtime.request_poll()
                    self.wait(runtime, lambda: runtime._slots["keychron:0"].failures == 1)
                batch = runtime.consume()
                self.assertEqual(batch.aliases, {})
                self.assertFalse(batch.fresh_keys)
                self.assertTrue(batch.hid[0].polling_stale)
                self.assertEqual(len(provider.alias_calls), 1)

    def test_metadata_finishing_after_deadline_cannot_publish_aliases(self):
        provider = FakeProvider("keychron")
        entered, release = threading.Event(), self.gate()
        def delayed_metadata():
            entered.set()
            release.wait()
            return {"old": provider.key}
        provider.on_aliases = delayed_metadata
        runtime = self.runtime([provider])
        runtime.request_poll()
        self.assertTrue(entered.wait(1))
        self.clock.advance(21)
        with self.assertLogs("halo_battery", level="WARNING"):
            runtime.pump()
        release.set()
        self.wait(runtime, lambda: runtime.stats()["running"] == 0)
        self.assertEqual(runtime.consume().aliases, {})
        self.assertFalse(runtime.consume().fresh_keys)


class DeclaredIdentityOverlapTests(unittest.TestCase):
    def test_every_declared_protocol_overlap_has_a_scheduling_conflict(self):
        # Read actual tables, without constructing or polling a provider. This
        # guards future PID additions as well as today's five overlap groups.
        # Windows controller APIs have no direct shared HID command collection.
        regular = {
            "audeze": ("AUDEZE_VID", ("KNOWN",)), "hyperx_alpha2": ("HYPERX_VID", ("PID",)),
            "hyperx_cloud3": ("HP_VID", ("PIDS",)), "keychron": ("KEYCHRON_VID", ("PIDS",)),
            "jbl": ("JBL_VID", ("PIDS",)), "playstation": ("SONY_VID", ("KNOWN",)),
            "eightbitdo": ("VENDOR_8BITDO", ("KNOWN",)), "barracuda": ("VID", ("PID",)),
            "barracuda_x": ("VID", ("PID",)), "nintendo": ("NINTENDO_VID", ("KNOWN",)),
            "aula": ("VID", ("PID",)), "aula_f108": ("VID", ("PID",)), "astro": ("ASTRO_VID", ("PIDS",)),
            "am_infinity": ("VID", ("PID",)), "steelseries_elite": ("STEELSERIES_VID", ("PIDS",)),
            "logitech_centurion": ("LOGITECH_VID", ("PIDS",)), "hyperx_cloud3s": ("HP_VID", ("PIDS",)),
            "nzxt": ("NZXT_VID", ("PIDS",)), "steam": ("VENDOR_VALVE", ("PUCKS",)),
            "endgame_we": ("VID", ("PID",)), "dareu": ("VID", ("PIDS",)),
            "eweadn_ydx": ("VID", ("PID",)),
            "shield": ("VID", ("PID",)), "inphic": ("VID", ("PIDS",)),
            "turtlebeach": ("TURTLEBEACH_VID", ("KNOWN",)), "skullcandy": ("SKULLCANDY_VID", ("KNOWN",)),
            "rapoo": ("RAPOO_VID", ("PIDS",)), "irocks": ("VID", ("PIDS",)),
            "sony_inzone": ("VID", ("ENABLED_PIDS",)), "sony_h9ii": ("VID", ("PID",)),
            "finalmouse": ("FINALMOUSE_VID", ("DONGLES",)),
            "hyperx_alpha_wireless": ("HP_VID", ("PID",)), "jabra": ("JABRA_VID", ("PIDS",)),
            "hyperx_cloud_flight": ("VID", ("PIDS",)), "monsgeek": ("MEASURED_VID", ("DONGLES",)),
            "mchose_kb": ("VENDOR", ("PRODUCTS",)), "endgame_4k": ("VID", ("PIDS",)),
            "plantronics": ("VID", ("PIDS",)), "hyperx_ngenuity": ("HYPERX_VID", ("PIDS",)),
            "alienware": ("VID", ("PID",)), "lamzu_sw": ("VID", ("PID",)),
            "urx": ("VID", ("PID",)), "steelseries": ("STEELSERIES_VID", ("MODELS", "MOUSE_MODELS", "CLASSIC_MODELS")),
            "glorious": ("VID", ("PID", "WIRED_PIDS")), "atk_keyboard": ("VID", ("PID", "WIRED_PID")),
            "dji": ("VID", ("PID",)), "gwolves": ("GWOLVES_VID", ("RECEIVER", "WIRED", "MODELS")),
        }
        module = lambda name: importlib.import_module("providers." + name)
        pairs = lambda vid, values: {(vid, pid) for pid in values}
        found = {}
        for name, (vid_name, tables) in regular.items():
            source = module(name)
            identities = set()
            for table in tables:
                values = getattr(source, table)
                identities |= pairs(getattr(source, vid_name), (values,) if type(values) is int else values)
            found[name] = identities
        for name, constant in (("razer", "RAZER_VID"), ("logitech", "LOGITECH_VID"), ("vaxee", "VAXEE_VID")):
            found[name] = {(getattr(module(name), constant), None)}
        source = module("wlmouse")
        found["wlmouse"] = {(vid, None) for vid in source.WLMOUSE_VIDS}
        source = module("mchose")
        found["mchose"] = ({(vid, None) for vid in source.MCHOSE_VIDS}
                            | {(source.G7_VID, source.G7_PID)} | pairs(source.A5_VID, source.A5_PIDS))
        source = module("hyperx")
        found["hyperx"] = pairs(source.HYPERX_VID, source.PIDS) | pairs(source.KINGSTON_VID, source.KINGSTON_PIDS)
        source = module("pulsar")
        found["pulsar"] = ({(vid, pid) for vid, pids in source.PIDS.items() for pid in pids}
                            | pairs(source.SONIX_VID, source.SONIX_PIDS) | set(module("atk_bitmouse").PIDS))
        source = module("asus")
        found["asus"] = pairs(source.ASUS_VID, set(source.KNOWN) | {source.OMNI_PID, source.SPEEDNOVA_PID,
                               source.HARPE_II_CABLE, source.HEADSET_PID, source.HEADSET_WIRED_PID})
        source = module("corsair")
        found["corsair"] = pairs(source.CORSAIR_VID, set(source.PIDS) | set(source.BRAGI_PIDS) | set(source.VOID_PIDS)
                                 | {source.HS80_USB_PID, source.HS80_CHARGING_CABLE_PID, 0x0A6B})
        source = module("lofree")
        found["lofree"] = (pairs(source.LOFREE_VID, set(source.DONGLES) | set(source.WIRED_OF.values()))
                            | pairs(source.AULA_VID, source.AULA_PIDS) | {(source.FLOW_VID, source.FLOW_PID)})
        source = module("mchose_v9")
        found["mchose_v9"] = {(vid, pid) for vid, pids in source.V9_PIDS.items() for pid in pids}
        source = module("lamzu")
        found["lamzu"] = pairs(source.LAMZU_VID, source.PIDS) | pairs(source.COMPX_VID, source.ATLANTIS_PIDS)
        source = module("ajazz")
        found["ajazz"] = {(source.VID, source.PID), (source.WIRED_VID, source.WIRED_PID)} | set(source.ENABLED_ALT_TARGETS)
        found["atk_nearlink"] = pairs(0x373B, module("atk_nearlink").PIDS)
        source = module("eweadn")
        found["eweadn"] = {(source.RECEIVER_VID, source.RECEIVER_PID), (source.WIRED_VID, source.WIRED_PID)}
        # Ensure an added provider cannot silently fall outside this source audit.
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / "halo_battery.pyw").read_text(encoding="utf-8"))
        factory = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "make_providers")
        classes = {n.func.id for n in ast.walk(factory) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id.endswith("Provider")}
        exports = ast.parse((root / "providers" / "__init__.py").read_text(encoding="utf-8"))
        mapping = {alias.name: node.module for node in exports.body if isinstance(node, ast.ImportFrom)
                   for alias in node.names}
        self.assertEqual({mapping[name] for name in classes}, set(found) | {"xinput"})
        overlaps = set()
        rows = list(found.items())
        for index, (name, identities) in enumerate(rows):
            for other, candidates in rows[index + 1:]:
                if any(v == w and (p is None or q is None or p == q)
                       for v, p in identities for w, q in candidates):
                    overlaps.add(frozenset((name, other)))
        self.assertEqual(overlaps, _CONFLICTS)


if __name__ == "__main__":
    unittest.main()
