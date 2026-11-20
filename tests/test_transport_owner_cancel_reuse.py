"""Old-owner cancellation must not invalidate a replacement owner at a reused key."""
import threading
import unittest
from halo.hid_transport import TransportRuntime


class OwnerCancellationReuseTests(unittest.TestCase):
    def test_delayed_old_cancellation_cannot_cancel_new_owner_after_key_forget(self):
        runtime = TransportRuntime(timeout=2)
        old_entered, old_release, old_done = threading.Event(), threading.Event(), threading.Event()
        new_entered, new_release = threading.Event(), threading.Event()
        cancel_entered, cancel_release = threading.Event(), threading.Event()
        real_cancel = runtime.scheduler.cancel
        outcomes, failures, threads = {}, [], []
        cancel_thread = None
        workers = runtime.scheduler._workers

        def callback(name, entered, release):
            entered.set()
            if not release.wait(2):
                raise AssertionError("fake callback release timed out")
            return name

        def old_provider():
            try:
                with runtime.provider_scope("old-owner"):
                    try:
                        runtime.invoke(lambda: callback("old", old_entered, old_release), "reused-key")
                    except OSError:
                        pass
            except BaseException as error:
                failures.append(error)
            finally:
                old_done.set()

        def new_provider():
            try:
                with runtime.provider_scope("new-owner") as scope:
                    outcomes["new"] = runtime.invoke(lambda: callback("new", new_entered, new_release), "reused-key")
                    outcomes["cancelled"] = scope.cancelled
                    outcomes["failed"] = scope.failed
            except BaseException as error:
                failures.append(error)

        def delayed_cancel(key):
            if threading.current_thread() is cancel_thread:
                cancel_entered.set()
                if not cancel_release.wait(2):
                    raise AssertionError("old-owner cancellation gate timed out")
            return real_cancel(key)

        runtime.scheduler.cancel = delayed_cancel
        try:
            old = threading.Thread(target=old_provider)
            threads.append(old); old.start()
            self.assertTrue(old_entered.wait(2))
            old_generation = runtime.scheduler.snapshot()["reused-key"].generation
            cancel_thread = threading.Thread(target=runtime.cancel_owner, args=("old-owner",))
            threads.append(cancel_thread); cancel_thread.start()
            self.assertTrue(cancel_entered.wait(2))
            # Drive key reuse while cancellation is delayed only if admission is
            # available. A serialized implementation prevents that interleaving.
            admission_available = runtime._lock.acquire(blocking=False)
            if admission_available:
                runtime._lock.release()
            else:
                cancel_release.set()
            old_release.set()
            self.assertTrue(old_done.wait(2))
            self.assertNotIn("reused-key", runtime.scheduler.snapshot())
            new = threading.Thread(target=new_provider)
            threads.append(new); new.start()
            self.assertTrue(new_entered.wait(2))
            self.assertEqual(runtime.scheduler.snapshot()["reused-key"].generation, old_generation)
            cancel_release.set(); cancel_thread.join(2)
            new_release.set(); new.join(2)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(failures, [])
            self.assertEqual(outcomes, {"new": "new", "cancelled": False, "failed": False})
            self.assertEqual(runtime.scheduler._workers, workers)
        finally:
            old_release.set(); new_release.set(); cancel_release.set()
            for thread in threads:
                thread.join(2)
            runtime.scheduler.cancel = real_cancel
            self.assertTrue(runtime.close(.5))


if __name__ == "__main__":
    unittest.main()
