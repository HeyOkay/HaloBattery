"""Cancel/admission ordering on existing fixed workers; no native API or hardware."""
import threading
import unittest

from halo.hid_transport import TransportRuntime


class AdmissionGate:
    def __init__(self, lock):
        self.lock = lock
        self.thread = None
        self.armed = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def __enter__(self):
        if self.armed and threading.current_thread() is self.thread:
            self.armed = False
            self.entered.set()
            if not self.release.wait(2):
                raise AssertionError("admission gate timed out")
        self.lock.acquire()
        return self

    def __exit__(self, *_):
        self.lock.release()


class TransportAdmissionCancellationTests(unittest.TestCase):
    def test_cancellation_while_waiting_for_admission_never_starts_callback(self):
        runtime = TransportRuntime(timeout=1)
        gate = AdmissionGate(runtime._lock)
        runtime._lock = gate
        callbacks, outcomes, failures = [], [], []
        workers = runtime.scheduler._workers

        def provider():
            try:
                with runtime.provider_scope("cancel-race:0") as scope:
                    gate.armed = True
                    try:
                        runtime.invoke(lambda: callbacks.append(threading.current_thread().name), "shared-key")
                    except TimeoutError as error:
                        outcomes.append(str(error))
                    outcomes.append((scope.cancelled, scope.failed, scope.unsafe))
            except BaseException as error:
                failures.append(error)

        thread = gate.thread = threading.Thread(target=provider, name="cancel-admission-provider")
        try:
            thread.start()
            self.assertTrue(gate.entered.wait(2), "provider did not reach admission boundary")
            runtime.cancel_owner("cancel-race:0")
            gate.release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(callbacks, [], "cancelled scope issued a new callback")
            self.assertEqual(outcomes, ["provider transport scope cancelled", (True, True, True)])
            self.assertFalse(runtime._calls)
            self.assertEqual(runtime.scheduler.stats()["devices"], 0)
            self.assertEqual(runtime.scheduler._workers, workers)
        finally:
            gate.release.set()
            thread.join(2)
            self.assertTrue(runtime.close(.5))


if __name__ == "__main__":
    unittest.main()
