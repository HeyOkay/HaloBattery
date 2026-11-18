"""Standalone scheduler checks. Fake requests only; no hardware or network."""
from dataclasses import dataclass
import threading
import time
import unittest

from halo.io_scheduler import IOScheduler, MAX_BUFFER


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@dataclass(frozen=True)
class Completion:
    success: bool = True
    error: int = 0
    transferred: int = 2
    data: bytes = b"ok"
    cancel_requested: bool = False


class FakeRequest:
    def __init__(self, completion=None, *, cancel_completes=True, start_gate=None,
                 cancel_gate=None, dispose_gate=None, raises=None):
        self.completion = completion
        self.cancel_completes = cancel_completes
        self.start_gate, self.cancel_gate, self.dispose_gate = start_gate, cancel_gate, dispose_gate
        self.raises = dict(raises or {})
        self.calls = []
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.disposed = threading.Event()
        self.dispose_entered = threading.Event()
        self.cancel_entered = threading.Event()
        self.lock = threading.Lock()
        self.issued = False
        self.native_released = False

    def _call(self, name):
        with self.lock:
            self.calls.append((name, threading.current_thread().name))
            error = self.raises.pop(name, None)
        if error is not None:
            raise error

    def start(self):
        self._call("start")
        self.issued = True
        self.started.set()
        if self.start_gate is not None:
            self.start_gate.wait()

    def cancel(self):
        self._call("cancel")
        self.cancel_entered.set()
        if self.cancel_gate is not None:
            self.cancel_gate.wait()
        self.cancelled.set()
        if not self.issued or self.cancel_completes:
            self.completion = Completion(False, 995, 0, b"", True)
        return self.completion is not None

    def poll(self):
        self._call("poll")
        return self.completion

    def dispose(self):
        self._call("dispose")
        self.dispose_entered.set()
        if self.dispose_gate is not None:
            self.dispose_gate.wait()
        if self.completion is None:
            return False
        self.native_released = True
        self.disposed.set()
        return True

    def finish(self, data=b"late"):
        self.completion = Completion(True, 0, len(data), data, self.cancelled.is_set())

    def names(self):
        return [name for name, _ in self.calls]


class SchedulerPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.scheduler = IOScheduler(clock=self.clock, autostart=False)
        self.gates = []
        self.requests = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        for request in self.requests:
            request.finish()
        self.clock.advance(100)
        self.scheduler.close(0.5)
        self.wait_for(lambda: self.scheduler.stats()["worker_alive"] == 0, seconds=1, advance=False)

    def gate(self):
        result = threading.Event()
        self.gates.append(result)
        return result

    def request(self, *args, **kwargs):
        result = FakeRequest(*args, **kwargs)
        self.requests.append(result)
        return result

    def wait_for(self, predicate, *, seconds=1, advance=True):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.scheduler.pump()
            if predicate():
                return True
            if advance:
                self.clock.advance(0.005)
            time.sleep(0.001)
        self.fail("scheduler did not reach expected state: " + repr(self.scheduler.stats()))

    def idle(self, key):
        return not self.scheduler.snapshot()[key].busy

    def test_success_is_published_after_disposal_on_fixed_workers(self):
        request = self.request(Completion(data=b"42", transferred=2))
        generation = self.scheduler.submit("mouse", lambda: request, timeout=1)
        self.wait_for(lambda: self.idle("mouse"))
        [result] = self.scheduler.take_results()
        self.assertEqual((result.device_key, result.generation, result.status, result.data),
                         ("mouse", generation, "success", b"42"))
        self.assertTrue(result.fresh)
        self.assertFalse(result.stale)
        self.assertTrue(request.disposed.is_set())
        self.assertTrue(all(name.startswith("halo-io-worker-") for _, name in request.calls))
        self.assertLessEqual(self.scheduler.stats()["peak_running"], 2)

    def test_one_device_lease_rejects_duplicate_until_disposal(self):
        gate = self.gate()
        request = self.request(Completion(), dispose_gate=gate)
        self.scheduler.submit("device", lambda: request, timeout=1)
        self.wait_for(request.dispose_entered.is_set)
        self.assertIsNone(self.scheduler.submit("device", lambda: self.request(Completion()), timeout=1))
        self.assertTrue(self.scheduler.snapshot()["device"].busy)
        gate.set()
        self.wait_for(lambda: self.idle("device"))
        next_request = self.request(Completion())
        self.assertEqual(self.scheduler.submit("device", lambda: next_request, timeout=1), 2)
        self.wait_for(lambda: self.idle("device"))

    def test_expiration_keeps_last_good_and_late_success_never_becomes_fresh(self):
        good = self.request(Completion(data=b"71", transferred=2))
        self.scheduler.submit("device", lambda: good, timeout=1)
        self.wait_for(lambda: self.idle("device"))
        self.scheduler.take_results()
        pending = self.request(cancel_completes=False)
        generation = self.scheduler.submit("device", lambda: pending, timeout=1)
        self.assertTrue(pending.started.wait(1))
        self.clock.advance(2)
        self.scheduler.pump()
        [expired] = self.scheduler.take_results()
        self.assertEqual((expired.status, expired.generation, expired.data), ("expired", generation, b"71"))
        self.assertTrue(expired.stale)
        self.assertFalse(expired.fresh)
        self.assertTrue(self.scheduler.snapshot()["device"].quarantined)
        self.assertIsNone(self.scheduler.submit("device", lambda: self.request(), timeout=1))
        self.wait_for(pending.cancelled.is_set, advance=False)
        pending.finish(b"99")
        self.clock.advance(0.1)
        self.wait_for(lambda: self.idle("device"))
        self.assertEqual(self.scheduler.take_results(), [])
        self.assertEqual(self.scheduler.snapshot()["device"].last_good.data, b"71")
        self.assertTrue(pending.native_released)

    def test_cancel_completion_race_never_publishes_cancelled_data_as_fresh(self):
        request = self.request(cancel_completes=False)
        self.scheduler.submit("device", lambda: request, timeout=10)
        self.assertTrue(request.started.wait(1))
        self.scheduler.cancel("device")
        request.finish(b"race")
        self.wait_for(lambda: self.idle("device"))
        results = self.scheduler.take_results()
        self.assertEqual([r.status for r in results], ["cancelled"])
        self.assertFalse(any(r.fresh for r in results))
        self.assertEqual(request.names().count("cancel"), 1)

    def test_blocking_start_does_not_block_deadlines_or_the_other_worker(self):
        gate = self.gate()
        blocked = self.request(start_gate=gate, cancel_completes=False)
        self.scheduler.submit("stuck", lambda: blocked, timeout=1)
        self.assertTrue(blocked.started.wait(1))
        self.clock.advance(2)
        before = time.monotonic()
        self.scheduler.pump()
        self.assertLess(time.monotonic() - before, 0.1)
        self.assertEqual(self.scheduler.take_results()[0].status, "expired")
        self.assertFalse(blocked.native_released)
        healthy = self.request(Completion())
        self.scheduler.submit("healthy", lambda: healthy, timeout=1)
        self.wait_for(lambda: self.idle("healthy"))
        self.assertEqual(self.scheduler.snapshot()["healthy"].last_result.status, "success")
        self.assertTrue(self.scheduler.snapshot()["stuck"].busy)
        gate.set()
        blocked.finish()
        self.clock.advance(0.1)
        self.wait_for(lambda: self.idle("stuck"))
        self.assertEqual(blocked.names().count("start"), 1)

    def test_factory_returning_after_deadline_never_starts_hardware(self):
        entered, release = threading.Event(), self.gate()
        late = self.request()
        def factory():
            entered.set()
            release.wait()
            return late
        self.scheduler.submit("late-open", factory, timeout=1)
        self.assertTrue(entered.wait(1))
        self.clock.advance(2)
        self.scheduler.pump()
        self.assertTrue(self.scheduler.snapshot()["late-open"].quarantined)
        self.assertIsNone(self.scheduler.submit("late-open", lambda: late, timeout=1))
        [result] = self.scheduler.take_results()
        self.assertEqual(result.status, "expired")
        release.set()
        self.wait_for(lambda: self.idle("late-open"))
        self.assertNotIn("start", late.names())
        self.assertEqual(late.names().count("cancel"), 1)
        self.assertTrue(late.native_released)
        self.assertEqual(self.scheduler.take_results(), [])

    def test_late_factory_is_checked_even_before_coordinator_pump(self):
        entered, release = threading.Event(), self.gate()
        request = self.request()
        def factory():
            entered.set()
            release.wait()
            return request
        self.scheduler.submit("late", factory, timeout=1)
        self.assertTrue(entered.wait(1))
        self.clock.advance(2)
        release.set()
        self.wait_for(lambda: self.idle("late"))
        self.assertNotIn("start", request.names())
        self.assertEqual(self.scheduler.take_results()[0].status, "expired")

    def test_expired_queued_generation_never_calls_factory(self):
        gates = [self.gate(), self.gate()]
        blockers = [self.request(start_gate=gate, cancel_completes=False) for gate in gates]
        for index, request in enumerate(blockers):
            self.scheduler.submit("block" + str(index), lambda r=request: r, timeout=100)
        self.assertTrue(all(request.started.wait(1) for request in blockers))
        calls = []
        self.scheduler.submit("queued", lambda: calls.append("old") or self.request(Completion()), timeout=1)
        self.clock.advance(2)
        self.scheduler.pump()
        self.assertFalse(self.scheduler.snapshot()["queued"].busy)
        fresh = self.request(Completion())
        self.assertEqual(self.scheduler.submit("queued", lambda: calls.append("new") or fresh, timeout=10), 2)
        for gate in gates:
            gate.set()
        for request in blockers:
            request.finish()
        self.wait_for(lambda: self.idle("queued"))
        self.assertEqual(calls, ["new"])

    def test_two_stuck_workers_and_sixteen_devices_never_expand_pool_or_queues(self):
        gates = [self.gate(), self.gate()]
        blockers = [self.request(start_gate=gate, cancel_completes=False) for gate in gates]
        for index, request in enumerate(blockers):
            self.scheduler.submit("d" + str(index), lambda r=request: r, timeout=1)
        self.assertTrue(all(request.started.wait(1) for request in blockers))
        factories = []
        for index in range(2, 16):
            self.assertIsNotNone(self.scheduler.submit("d" + str(index),
                lambda: factories.append(True) or self.request(Completion()), timeout=1))
        self.assertIsNone(self.scheduler.submit("seventeenth", lambda: self.request(), timeout=1))
        self.clock.advance(2)
        self.scheduler.pump()
        for _ in range(100):
            self.scheduler.cancel("d0")
            self.scheduler.cancel("d1")
        stats = self.scheduler.stats()
        self.assertEqual(stats["workers"], 2)
        self.assertEqual(stats["worker_alive"], 2)
        self.assertLessEqual(stats["peak_running"], 2)
        self.assertLessEqual(stats["devices"], 16)
        self.assertLessEqual(stats["results"], 16)
        self.assertLessEqual(stats["queued"], 32)
        self.assertEqual(factories, [])
        self.assertFalse(self.scheduler.close(0.02))
        self.assertTrue(self.scheduler.stats()["closed"])

    def test_factory_and_request_exceptions_do_not_kill_or_replace_workers(self):
        def broken_factory():
            raise RuntimeError("factory failed")
        self.scheduler.submit("factory", broken_factory, timeout=1)
        self.wait_for(lambda: self.idle("factory"))
        self.assertEqual(self.scheduler.take_results()[0].status, "error")
        for method in ("start", "poll", "dispose"):
            with self.subTest(method=method):
                request = self.request(Completion(), raises={method: RuntimeError("bad " + method)})
                self.scheduler.submit(method, lambda r=request: r, timeout=1)
                self.wait_for(lambda: self.idle(method))
                self.assertFalse(self.scheduler.snapshot()[method].last_result.fresh)
                self.assertEqual(self.scheduler.stats()["worker_alive"], 2)
        healthy = self.request(Completion())
        self.scheduler.submit("healthy", lambda: healthy, timeout=1)
        self.wait_for(lambda: self.idle("healthy"))
        self.assertEqual(self.scheduler.snapshot()["healthy"].last_result.status, "success")
        self.assertFalse(self.scheduler.snapshot()["healthy"].last_result.fresh)

    def test_blocked_cancel_keeps_context_pinned_and_other_worker_usable(self):
        gate = self.gate()
        request = self.request(cancel_gate=gate)
        self.scheduler.submit("device", lambda: request, timeout=1)
        self.assertTrue(request.started.wait(1))
        self.clock.advance(2)
        self.scheduler.pump()
        self.wait_for(request.cancel_entered.is_set, advance=False)
        healthy = self.request(Completion())
        self.scheduler.submit("healthy", lambda: healthy, timeout=1)
        self.wait_for(lambda: self.idle("healthy"))
        self.assertTrue(self.scheduler.snapshot()["device"].busy)
        self.assertFalse(self.scheduler.close(0.02))
        gate.set()
        self.wait_for(lambda: self.scheduler.stats()["worker_alive"] == 0, advance=False)
        self.assertTrue(request.disposed.is_set())

    def test_blocked_dispose_is_not_cancelled_or_reused(self):
        gate = self.gate()
        request = self.request(Completion(), dispose_gate=gate)
        self.scheduler.submit("device", lambda: request, timeout=1)
        self.wait_for(request.dispose_entered.is_set)
        self.clock.advance(2)
        self.scheduler.pump()
        self.assertTrue(self.scheduler.snapshot()["device"].quarantined)
        self.assertNotIn("cancel", request.names())
        self.assertIsNone(self.scheduler.submit("device", lambda: request, timeout=1))
        self.assertFalse(self.scheduler.close(0.02))
        gate.set()
        self.wait_for(lambda: self.scheduler.stats()["worker_alive"] == 0, advance=False)
        self.assertTrue(request.native_released)
        self.assertFalse(any(r.fresh for r in self.scheduler.take_results()))

    def test_close_budget_then_eventual_cleanup_uses_same_workers(self):
        request = self.request(cancel_completes=False)
        self.scheduler.submit("device", lambda: request, timeout=100)
        self.assertTrue(request.started.wait(1))
        workers = tuple(self.scheduler._workers)
        before = time.monotonic()
        self.assertFalse(self.scheduler.close(0.02))
        self.assertLess(time.monotonic() - before, 0.25)
        self.assertTrue(self.scheduler.snapshot()["device"].busy)
        request.finish()
        # No fake-clock advance or new thread is needed for shutdown cleanup.
        self.wait_for(lambda: self.scheduler.stats()["worker_alive"] == 0, advance=False)
        self.assertEqual(tuple(self.scheduler._workers), workers)
        self.assertTrue(request.native_released)
        self.assertTrue(self.scheduler.close(0))
        self.assertIsNone(self.scheduler.submit("new", lambda: request, timeout=1))

    def test_results_coalesce_and_cache_bound_can_be_released_only_when_idle(self):
        for _ in range(5):
            request = self.request(Completion())
            self.scheduler.submit("same", lambda r=request: r, timeout=1)
            self.wait_for(lambda: self.idle("same"))
        self.assertEqual(self.scheduler.stats()["results"], 1)
        [result] = self.scheduler.take_results()
        self.assertEqual(result.generation, 5)
        self.assertTrue(self.scheduler.forget("same"))
        self.assertNotIn("same", self.scheduler.snapshot())
        pending = self.request(cancel_completes=False)
        self.scheduler.submit("busy", lambda: pending, timeout=1)
        self.assertFalse(self.scheduler.forget("busy"))

    def test_payloads_and_arguments_are_bounded(self):
        for timeout in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                self.scheduler.submit("invalid", lambda: self.request(), timeout=timeout)
        with self.assertRaises(ValueError):
            self.scheduler.submit("x" * 257, lambda: self.request(), timeout=1)
        oversized = self.request(Completion(data=b"x" * (MAX_BUFFER + 1), transferred=MAX_BUFFER + 1))
        self.scheduler.submit("oversized", lambda: oversized, timeout=1)
        self.wait_for(lambda: self.idle("oversized"))
        result = self.scheduler.snapshot()["oversized"].last_result
        self.assertFalse(result.fresh)
        self.assertLessEqual(len(result.data), MAX_BUFFER)

    def test_snapshot_caches_never_republish_a_fresh_observation(self):
        request = self.request(Completion(data=b"71", transferred=2))
        generation = self.scheduler.submit("device", lambda: request, timeout=1)
        self.wait_for(lambda: self.idle("device"))
        [fresh] = self.scheduler.take_results()
        self.assertTrue(fresh.fresh)
        for _ in range(20):
            view = self.scheduler.snapshot()["device"]
            self.assertEqual(view.last_good.data, b"71")
            self.assertEqual(view.last_good.generation, generation)
            self.assertFalse(view.last_good.fresh)
            self.assertFalse(view.last_result.fresh)
            self.scheduler.pump()
            self.assertEqual(self.scheduler.take_results(), [])

    def test_scheduler_payload_cap_matches_native_backend(self):
        # Import declarations only, without constructing WindowsApi/handles.
        from halo.native_io import MAX_BUFFER as native_limit
        self.assertEqual(MAX_BUFFER, 32768)
        self.assertEqual(MAX_BUFFER, native_limit)

    def test_cancel_exception_does_not_release_a_pending_device_or_kill_worker(self):
        request = self.request(cancel_completes=False, raises={"cancel": RuntimeError("cancel failed")})
        self.scheduler.submit("device", lambda: request, timeout=1)
        self.assertTrue(request.started.wait(1))
        self.clock.advance(2)
        self.scheduler.pump()
        self.wait_for(lambda: "cancel failed" in self.scheduler.snapshot()["device"].last_error)
        self.assertTrue(self.scheduler.snapshot()["device"].busy)
        self.assertFalse(request.native_released)
        self.assertEqual(self.scheduler.stats()["worker_alive"], 2)
        request.finish()
        self.clock.advance(0.1)
        self.wait_for(lambda: self.idle("device"))
        self.assertFalse(any(result.fresh for result in self.scheduler.take_results()))

    def test_exception_with_broken_text_does_not_kill_worker(self):
        class BrokenText(BaseException):
            def __str__(self):
                raise RuntimeError("cannot format exception")
        def factory():
            raise BrokenText()
        self.scheduler.submit("device", factory, timeout=1)
        self.wait_for(lambda: self.idle("device"))
        [result] = self.scheduler.take_results()
        self.assertEqual(result.status, "error")
        self.assertIn("exception text unavailable", result.error)
        self.assertEqual(self.scheduler.stats()["worker_alive"], 2)

    def test_optional_coordinator_handles_normal_completion_and_exits_on_close(self):
        other = IOScheduler()
        request = self.request(Completion())
        try:
            other.submit("device", lambda: request, timeout=1)
            end = time.monotonic() + 1
            while other.snapshot()["device"].busy and time.monotonic() < end:
                time.sleep(0.001)
            self.assertFalse(other.snapshot()["device"].busy)
            self.assertTrue(other.close(0.5))
            self.assertEqual(other.stats()["worker_alive"], 0)
            self.assertFalse(other.stats()["coordinator_alive"])
        finally:
            other.close(0.5)


if __name__ == "__main__":
    unittest.main()
