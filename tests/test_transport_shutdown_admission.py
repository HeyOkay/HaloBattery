"""Atomic shutdown/admission with fake requests; no hardware or native APIs."""
import threading
import unittest
from halo.hid_transport import HidDevice, TransportRuntime
from test_experimental_hid import Api, Metadata, Request
from test_transport_admission_cancellation import AdmissionGate


class ClosingAttemptLock:
    def __init__(self, lock):
        self.lock, self.closer = lock, None
        self.attempted = threading.Event()
    def __enter__(self):
        if threading.current_thread() is self.closer:
            self.attempted.set()
        self.lock.acquire()
        return self
    def __exit__(self, *_):
        self.lock.release()


class ShutdownAdmissionTests(unittest.TestCase):
    def test_waiting_invoke_is_refused_after_shutdown_with_retained_device(self):
        api = Api()
        runtime = TransportRuntime(api=api, timeout=1)
        device = HidDevice(runtime, metadata=Metadata())
        request = Request(ignore_cancel=True)
        device.open_path("fake-shutdown-admission")
        owner = api.owners[-1]
        api.next_read = request
        device.set_nonblocking(True)
        self.assertEqual(device.read(64), [])
        gate = AdmissionGate(runtime._lock)
        runtime._lock = gate
        callbacks, errors, scopes = [], [], []
        workers = runtime.scheduler._workers
        def provider():
            try:
                with runtime.provider_scope("shutdown-waiter") as scope:
                    gate.armed = True
                    try:
                        runtime.invoke(lambda: callbacks.append("late"), "late-shutdown-key")
                    except OSError as error:
                        errors.append(str(error))
                    scopes.append((scope.failed, scope.unsafe))
            except BaseException as error:
                errors.append(repr(error))
        thread = gate.thread = threading.Thread(target=provider)
        try:
            thread.start()
            self.assertTrue(gate.entered.wait(2))
            self.assertFalse(runtime.close(.01))
            self.assertFalse(owner.closed)
            self.assertFalse(request.disposed)
            self.assertFalse(runtime.scheduler.stats()["closed"])
            gate.release.set(); thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(callbacks, [], "runtime admitted work after reporting shutdown")
            self.assertEqual(errors, ["native runtime is closing"])
            self.assertEqual(scopes, [(True, True)])
            self.assertEqual(runtime.scheduler._workers, workers)
        finally:
            gate.release.set(); thread.join(2)
            request.complete(b"\0finished")
            self.assertTrue(runtime.close(.5))
        self.assertTrue(owner.closed)
        self.assertTrue(request.disposed)

    def test_closing_flag_cannot_flip_during_locked_admission_before_submit(self):
        runtime = TransportRuntime(timeout=1)
        lock = ClosingAttemptLock(runtime._lock)
        runtime._lock = lock
        submitted, allow_submit, issued = threading.Event(), threading.Event(), threading.Event()
        real_submit = runtime.scheduler.submit
        observations, failures = [], []
        provider_thread = None
        def callback():
            observations.append(runtime._closing)
            issued.set()
            return "accepted before shutdown"
        def gated_submit(key, factory, *, timeout):
            if threading.current_thread() is provider_thread:
                submitted.set()
                if not allow_submit.wait(2):
                    raise AssertionError("submit gate timed out")
            generation = real_submit(key, factory, timeout=timeout)
            if threading.current_thread() is provider_thread and not issued.wait(2):
                raise AssertionError("accepted fake callback never issued")
            return generation
        def provider():
            try:
                runtime.invoke(callback, "closing-linearization")
            except OSError:
                pass  # Shutdown can cancel this already-admitted generation.
            except BaseException as error:
                failures.append(error)
        def close():
            try:
                runtime.close(.2)
            except BaseException as error:
                failures.append(error)
        runtime.scheduler.submit = gated_submit
        provider_thread = threading.Thread(target=provider)
        closer = lock.closer = threading.Thread(target=close)
        try:
            provider_thread.start()
            self.assertTrue(submitted.wait(2))
            closer.start()
            self.assertTrue(lock.attempted.wait(2))
            allow_submit.set()
            provider_thread.join(2); closer.join(2)
            self.assertFalse(provider_thread.is_alive() or closer.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(observations, [False], "closing flag changed before locked admission completed")
        finally:
            allow_submit.set()
            provider_thread.join(2)
            if closer.ident is not None:
                closer.join(2)
            runtime.scheduler.submit = real_submit
            self.assertTrue(runtime.close(.5))


if __name__ == "__main__":
    unittest.main()
