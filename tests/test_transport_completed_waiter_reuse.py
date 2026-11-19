"""Old callers must not remove or cancel a newer owner of the same native key.

Event gates exercise the real fixed scheduler with fake callbacks; no device or
native API is opened. Both regressions fail before the ownership guards.
"""
import threading
import time
import unittest

import _sandbox
import halo.hid_transport as transport


class CompletedWaiterReuseTests(unittest.TestCase):
    def setUp(self):
        self.runtime = transport.TransportRuntime(timeout=2)
        self.workers = self.runtime.scheduler._workers
        self.threads = []
        self.release = []
        self.errors = {}
        self.outcomes = {}

    def tearDown(self):
        for event in self.release:
            event.set()
        for thread in self.threads:
            thread.join(3)
        self.assertFalse(any(thread.is_alive() for thread in self.threads))
        self.assertEqual(self.runtime.scheduler._workers, self.workers)
        self.assertTrue(self.runtime.close(.5))

    def caller(self, owner, callback, *, timeout=None):
        def run():
            try:
                with self.runtime.provider_scope(owner):
                    self.outcomes[owner] = self.runtime.invoke(callback, "collection", timeout)
            except BaseException as error:
                self.errors[owner] = error
        thread = threading.Thread(target=run, name="fixture-" + owner + "-caller")
        self.threads.append(thread)
        return thread

    def new_call(self):
        entered, release = threading.Event(), threading.Event()
        self.release.append(release)
        def callback():
            entered.set()
            if not release.wait(3):
                raise AssertionError("replacement callback release timed out")
            return "replacement-success"
        thread = self.caller("replacement", callback)
        thread.start()
        self.assertTrue(entered.wait(2))
        with self.runtime._lock:
            invocation = self.runtime._calls["collection"][2]
        return thread, release, invocation

    def test_old_success_cannot_forget_live_replacement_owner(self):
        captured, release = threading.Event(), threading.Event()
        self.release.append(release)
        real_snapshot = self.runtime.scheduler.snapshot
        old = self.caller("old", lambda: "old-success")
        def snapshot():
            states = real_snapshot()
            state = states.get("collection")
            if (threading.current_thread() is old and not captured.is_set()
                    and state is not None and not state.busy
                    and state.last_result is not None and state.last_result.status == "success"):
                captured.set()
                if not release.wait(3):
                    raise AssertionError("old cached-success gate timed out")
            return states
        self.runtime.scheduler.snapshot = snapshot
        old.start()
        self.assertTrue(captured.wait(2))
        new, new_release, invocation = self.new_call()
        release.set()
        old.join(2)
        self.assertFalse(old.is_alive())
        self.assertEqual(self.outcomes.get("old"), "old-success")
        self.assertTrue(real_snapshot()["collection"].busy)
        with self.runtime._lock:
            current = self.runtime._calls.get("collection")
        self.assertIsNotNone(current, "old success removed the replacement owner's call")
        self.assertIs(current[2], invocation)
        self.assertFalse(self.runtime.transport_idle("replacement"))
        new_release.set()
        new.join(2)
        self.assertEqual(self.outcomes.get("replacement"), "replacement-success")
        self.assertFalse(self.errors)

    def test_old_deadline_cannot_cancel_live_replacement_generation(self):
        retired, release = threading.Event(), threading.Event()
        self.release.append(release)
        real_await = self.runtime._await
        real_snapshot = self.runtime.scheduler.snapshot
        real_cancel = self.runtime.scheduler.cancel
        cancelled_by_old = []
        old = self.caller("old", lambda: "old-success", timeout=.08)
        def await_gate(key, generation, invocation, timeout):
            if threading.current_thread() is old:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    state = real_snapshot().get(key)
                    if state is not None and not state.busy and state.last_result is not None:
                        break
                    time.sleep(.001)
                else:
                    raise AssertionError("old callback did not retire")
                retired.set()
                if not release.wait(3):
                    raise AssertionError("old pre-consumption gate timed out")
            return real_await(key, generation, invocation, timeout)
        def cancel(key):
            if threading.current_thread() is old:
                cancelled_by_old.append(key)
            return real_cancel(key)
        self.runtime._await = await_gate
        self.runtime.scheduler.cancel = cancel
        old.start()
        self.assertTrue(retired.wait(2))
        new, new_release, invocation = self.new_call()
        release.set()
        old.join(2)
        self.assertFalse(old.is_alive())
        self.assertIsInstance(self.errors.get("old"), TimeoutError)
        self.assertEqual(cancelled_by_old, [], "old deadline cancelled a replacement generation")
        self.assertFalse(invocation.cancelled.is_set())
        new_release.set()
        new.join(2)
        self.assertEqual(self.outcomes.get("replacement"), "replacement-success")
        self.assertNotIn("replacement", self.errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
