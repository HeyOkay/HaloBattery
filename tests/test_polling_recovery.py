"""Automatic circuit recovery with fake time/providers; no network or native I/O."""
import threading
import time
import unittest

from halo.polling import PollingRuntime
from providers.base import DeviceStatus
from test_experimental_polling import Clock, FakeProvider, Hooks


class PollingRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.hooks = Hooks()
        self.runtimes = []
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        self.hooks.idle.clear()
        for runtime in self.runtimes:
            self.assertTrue(runtime.close(0.5))

    def wait(self, runtime, predicate):
        end = time.monotonic() + 2
        while time.monotonic() < end:
            runtime.pump()
            if predicate():
                return
            time.sleep(0.001)
        self.fail("fake provider did not finish")

    def gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def circuit(self, values=None):
        provider = FakeProvider("wlmouse")
        provider.values = values or [DeviceStatus(provider.key, "mouse", 80, source="wlmouse")]
        runtime = PollingRuntime([provider], lambda _full: None, clock=self.clock,
                                 provider_scope=self.hooks.scope,
                                 transport_idle=self.hooks.transport_idle,
                                 cancel_owner=self.hooks.cancel_owner)
        self.runtimes.append(runtime)
        runtime.configure(set())
        runtime.request_poll()
        slot = runtime._slots["wlmouse:0"]
        self.wait(runtime, lambda: len(provider.calls) == 1 and not slot.running)
        self.assertTrue(runtime.consume().fresh_keys)
        provider.error = OSError(87, "native I/O failed")
        runtime.request_poll()
        for count, delay in ((1, 5), (2, 10), (3, 0)):
            self.wait(runtime, lambda: slot.failures == count and not slot.running)
            self.clock.advance(delay)
        self.assertTrue(slot.circuit)
        self.assertEqual(len(provider.calls), 4)
        batch = runtime.consume()
        self.assertFalse(batch.fresh_keys)
        self.assertFalse(batch.completed)
        self.assertTrue(all(s.polling_stale for s in batch.hid))
        return runtime, provider, slot

    def test_normal_mixed_batch_does_not_mark_deferred_values_fresh(self):
        provider = FakeProvider("eweadn_ydx")
        provider.values = [
            DeviceStatus("live", "receiver one", 47, source=provider.name),
            DeviceStatus("deferred", "receiver two", 90, online=False,
                         source=provider.name, polling_stale=True, charging_known=False),
            DeviceStatus("paused", "receiver three", 50, source=provider.name,
                         polling_paused=True),
            DeviceStatus("gone", "receiver four", None, online=False, source=provider.name),
        ]
        runtime = PollingRuntime([provider], lambda _full: None, clock=self.clock)
        self.runtimes.append(runtime)
        runtime.configure(set())
        runtime.request_poll()
        slot = runtime._slots["eweadn_ydx:0"]
        self.wait(runtime, lambda: len(provider.calls) == 1 and not slot.running)
        batch = runtime.consume()
        self.assertEqual(batch.fresh_keys, {"live", "gone"})
        self.assertEqual(batch.completed, {"eweadn_ydx"})
        self.assertEqual({status.key for status in batch.hid},
                         {"live", "deferred", "paused", "gone"})
        self.assertTrue(next(s for s in batch.hid if s.key == "deferred").polling_stale)

    def test_automatic_recovery_waits_sixty_seconds_and_uses_existing_workers(self):
        runtime, provider, slot = self.circuit()
        workers = runtime._workers
        provider.error = None
        provider.values = [DeviceStatus(provider.key, "mouse", 43, source="wlmouse")]
        self.clock.advance(59.999)
        for _ in range(30):
            runtime.request_poll()
        self.assertEqual(len(provider.calls), 4)
        self.clock.advance(0.001)
        self.wait(runtime, lambda: len(provider.calls) == 5 and not slot.running)
        batch = runtime.consume()
        self.assertEqual([(s.level, s.polling_stale) for s in batch.hid], [(43, False)])
        self.assertEqual(batch.fresh_keys, {provider.key})
        self.assertEqual(batch.completed, {"wlmouse"})
        self.assertFalse(runtime.consume().fresh_keys)
        self.assertFalse(slot.circuit)
        self.assertEqual(slot.failures, 0)
        self.assertEqual(runtime._workers, workers)
        self.assertLessEqual(runtime.stats()["peak_running"], 2)

    def test_repeated_failures_have_one_retry_per_cooldown_without_catchup(self):
        runtime, provider, slot = self.circuit()
        self.clock.advance(60)
        self.wait(runtime, lambda: slot.failures == 4 and not slot.running)
        self.assertEqual(len(provider.calls), 5)
        self.clock.advance(10000)
        self.wait(runtime, lambda: slot.failures == 5 and not slot.running)
        for _ in range(200):
            runtime.request_poll()
        self.assertEqual(len(provider.calls), 6)
        self.assertEqual(runtime.stats()["queued"], 1)
        self.assertEqual(slot.retry_at - self.clock(), 60)
        self.assertTrue(all(s.polling_stale for s in runtime.consume().hid))

    def test_empty_offline_or_unknown_results_do_not_refresh_old_cache(self):
        runtime, provider, slot = self.circuit()
        provider.error = None
        alias_calls = len(provider.alias_calls)
        variants = [[], [DeviceStatus(provider.key, "mouse", 80, online=False)],
                    [DeviceStatus(provider.key, "mouse", None)],
                    [DeviceStatus("new", "connected unknown", None)]]
        for values in variants:
            with self.subTest(values=values):
                provider.values = values
                previous = len(provider.calls)
                self.clock.advance(60)
                self.wait(runtime, lambda: len(provider.calls) == previous + 1 and not slot.running)
                batch = runtime.consume()
                old = next(s for s in batch.hid if s.key == provider.key)
                self.assertEqual(old.level, 80)
                self.assertTrue(old.polling_stale)
                self.assertFalse(batch.fresh_keys)
                self.assertFalse(batch.completed)
                self.assertFalse(batch.aliases)
                self.assertTrue(slot.circuit)
                self.assertEqual(slot.retry_at - self.clock(), 60)
        new = next(s for s in runtime.consume().hid if s.key == "new")
        self.assertTrue(new.online)
        self.assertFalse(new.polling_stale)
        self.assertEqual(len(provider.alias_calls), alias_calls)

    def test_mixed_recovery_only_marks_observed_endpoint_fresh(self):
        values = [DeviceStatus("a", "mouse a", 80), DeviceStatus("b", "mouse b", 70)]
        runtime, provider, slot = self.circuit(values)
        provider.error = None
        provider.values = [DeviceStatus("a", "mouse a", 30),
                           DeviceStatus("b", "mouse b", 70, online=False),
                           DeviceStatus("unknown", "connected", None)]
        provider.aliases = {"old-a": "a", "old-b": "b"}
        self.clock.advance(60)
        self.wait(runtime, lambda: not slot.circuit and not slot.running)
        batch = runtime.consume()
        self.assertEqual(batch.fresh_keys, {"a"})
        self.assertEqual(batch.aliases, {"old-a": "a"})
        by_key = {s.key: s for s in batch.hid}
        self.assertTrue(by_key["b"].polling_stale)
        self.assertFalse(by_key["unknown"].polling_stale)
        self.assertTrue(by_key["unknown"].online)

    def test_multicycle_unknown_recovery_cache_is_bounded_then_all_observations_survive(self):
        runtime, provider, slot = self.circuit()
        provider.error = None
        for cycle in range(4):
            provider.values = [DeviceStatus(f"unknown-{cycle}-{i}", "connected unknown", None)
                               for i in range(256)]
            calls = len(provider.calls)
            self.clock.advance(60)
            self.wait(runtime, lambda: len(provider.calls) == calls + 1 and not slot.running)
            batch = runtime.consume()
            self.assertEqual(len(batch.hid), 256)
            self.assertEqual(len({s.key for s in batch.hid}), 256)
            old = next(s for s in batch.hid if s.key == provider.key)
            self.assertEqual(old.level, 80)
            self.assertTrue(old.polling_stale)
            self.assertFalse(batch.fresh_keys)
            self.assertFalse(batch.completed)
            self.assertFalse(batch.aliases)
            self.assertTrue(slot.circuit)
        provider.values = [DeviceStatus(provider.key, "mouse", 43)] + [
            DeviceStatus(f"observed-{i}", "current endpoint", 50) for i in range(255)]
        expected_keys = {s.key for s in provider.values}
        calls = len(provider.calls)
        self.clock.advance(60)
        self.wait(runtime, lambda: len(provider.calls) == calls + 1 and not slot.running)
        batch = runtime.consume()
        self.assertEqual(len(batch.hid), 256)
        self.assertEqual({s.key for s in batch.hid}, expected_keys)
        self.assertEqual(batch.fresh_keys, expected_keys)
        self.assertEqual(next(s.level for s in batch.hid if s.key == provider.key), 43)
        self.assertFalse(slot.circuit)
        self.assertEqual(slot.failures, 0)
        self.clock.advance(120)
        runtime.pump()
        time.sleep(0.01)
        self.assertEqual(len(provider.calls), calls + 1)

    def test_recovery_deduplicates_with_observed_then_known_stale_priority(self):
        previous = [DeviceStatus("unknown-old", "unknown", None, polling_stale=True),
                    DeviceStatus("known-old", "cached", 80, polling_stale=True),
                    DeviceStatus("observed", "old reading", 90, polling_stale=True)]
        values = [DeviceStatus("observed", "unknown duplicate", None),
                  DeviceStatus("observed", "current", 43),
                  DeviceStatus("observed", "duplicate reading", 44),
                  DeviceStatus("new", "unknown", None),
                  DeviceStatus("new", "duplicate unknown", None)]
        result = PollingRuntime._recovery_values(previous, values)
        self.assertEqual([(s.key, s.level) for s in result],
                         [("observed", 43), ("known-old", 80), ("unknown-old", None), ("new", None)])
        # Deduplication is local to the recovery union, not normal provider results.
        self.assertEqual(len(PollingRuntime._statuses(values)), 5)

    def test_half_open_requires_idle_confirmation_even_without_a_lease(self):
        runtime, provider, slot = self.circuit()
        self.assertFalse(slot.lease)
        provider.error = None
        self.hooks.idle[slot.owner] = False
        self.clock.advance(60)
        for _ in range(100):
            runtime.pump()
        self.assertEqual(len(provider.calls), 4)
        saved_idle = runtime._idle
        def unavailable(_owner):
            raise RuntimeError("idle status unavailable")
        runtime._idle = unavailable
        runtime.pump()
        self.assertEqual(len(provider.calls), 4)
        runtime._idle = saved_idle
        self.hooks.idle[slot.owner] = True
        self.wait(runtime, lambda: len(provider.calls) == 5 and not slot.running)

    def test_lease_and_running_probe_prevent_replacement_or_backlog(self):
        runtime, provider, slot = self.circuit()
        workers = runtime._workers
        provider.error = None
        provider.gate = self.gate()
        self.hooks.idle[slot.owner] = False
        slot.lease = True
        self.clock.advance(60)
        runtime.pump()
        self.assertEqual(len(provider.calls), 4)
        self.hooks.idle[slot.owner] = True
        self.wait(runtime, lambda: slot.recovering and slot.running)
        for _ in range(200):
            runtime.request_poll()
        self.assertEqual(len(provider.calls), 5)
        self.assertEqual(runtime.stats()["queued"], 0)
        provider.gate.set()
        self.wait(runtime, lambda: not slot.running)
        time.sleep(0.01)
        self.assertEqual(len(provider.calls), 5)
        self.assertEqual(runtime._workers, workers)

    def test_expired_half_open_never_publishes_late_value_or_overlaps(self):
        runtime, provider, slot = self.circuit()
        provider.error = None
        provider.values = [DeviceStatus(provider.key, "mouse", 12)]
        provider.gate = self.gate()
        self.clock.advance(60)
        self.wait(runtime, lambda: slot.recovering and slot.running)
        self.clock.advance(20)
        runtime.pump()
        self.assertTrue(slot.expired)
        self.clock.advance(3600)
        for _ in range(100):
            runtime.request_poll()
        self.assertEqual(len(provider.calls), 5)
        self.hooks.idle[slot.owner] = False
        provider.gate.set()
        self.wait(runtime, lambda: not slot.running)
        batch = runtime.consume()
        self.assertEqual([s.level for s in batch.hid], [80])
        self.assertTrue(batch.hid[0].polling_stale)
        self.assertFalse(batch.fresh_keys)
        self.assertFalse(batch.completed)
        self.assertTrue(slot.lease)
        provider.gate = None
        self.hooks.idle[slot.owner] = True
        self.wait(runtime, lambda: len(provider.calls) == 6 and not slot.running)
        self.assertEqual(runtime.consume().fresh_keys, {provider.key})

    def test_disabled_or_closed_runtime_does_not_recover(self):
        for action in ("disable", "shutdown"):
            with self.subTest(action=action):
                runtime, provider, slot = self.circuit()
                if action == "disable":
                    runtime.configure({"wlmouse"})
                else:
                    runtime.stop_admission()
                self.clock.advance(120)
                for _ in range(100):
                    runtime.pump()
                    runtime.request_poll()
                self.assertEqual(len(provider.calls), 4)
                self.assertFalse(runtime.consume().fresh_keys)

    def test_manual_force_before_cooldown_and_normal_empty_results_still_work(self):
        runtime, provider, slot = self.circuit()
        provider.error = None
        self.clock.advance(20)
        runtime.request_poll(force=True)
        self.wait(runtime, lambda: len(provider.calls) == 5 and not slot.running)
        self.assertFalse(slot.circuit)
        self.assertEqual(runtime.consume().fresh_keys, {provider.key})
        provider.values = []
        runtime.request_poll()
        self.wait(runtime, lambda: len(provider.calls) == 6 and not slot.running)
        self.assertEqual(runtime.consume().hid, [])

    def test_force_during_failed_probe_keeps_one_automatic_retry(self):
        runtime, provider, slot = self.circuit()
        provider.gate = self.gate()
        self.clock.advance(60)
        self.wait(runtime, lambda: slot.recovering and slot.running)
        runtime.request_poll(force=True)
        provider.gate.set()
        self.wait(runtime, lambda: not slot.running)
        self.assertEqual(slot.failures, 1)
        self.assertEqual(runtime.stats()["queued"], 1)
        provider.error = None
        provider.gate = None
        self.clock.advance(5)
        self.wait(runtime, lambda: len(provider.calls) == 6 and not slot.running)
        self.assertEqual(runtime.consume().fresh_keys, {provider.key})


if __name__ == "__main__":
    unittest.main()
