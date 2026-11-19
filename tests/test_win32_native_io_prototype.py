"""Standalone Windows transport integration, using local pipes and synthetic I/O.

No HID/USB/network API is used. Pipe cancellation proves Windows transport
mechanics only; a real HID driver may fail to honor cancellation.
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def until(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("prototype fixture did not reach expected state")
        time.sleep(0.001)


@unittest.skipUnless(os.name == "nt", "Windows overlapped I/O prototype")
class Win32NativeIOIntegrationTests(unittest.TestCase):
    def setUp(self):
        from halo.native_io import WindowsApi
        from tools.prototypes.native_io_fixtures import NamedPipePair
        self.api = WindowsApi()
        self.pair = NamedPipePair()
        self.addCleanup(self.pair.close)

    def read(self, size=64):
        return self.pair.track(self.api.create_read(self.pair.server, size))

    def write(self, payload):
        return self.pair.track(self.api.create_write(self.pair.client, payload))

    def completion(self, request):
        from tools.prototypes.native_io_fixtures import wait_completion
        return wait_completion(request)

    def test_real_overlapped_read_write_success(self):
        payload = b"pipe battery fixture\x00\xff"
        read, write = self.read(), self.write(payload)
        read.start()
        self.assertIsNone(read.poll())
        write.start()
        sent, received = self.completion(write), self.completion(read)
        self.assertTrue(sent.success)
        self.assertEqual(sent.transferred, len(payload))
        self.assertTrue(received.success)
        self.assertEqual(received.data, payload)
        self.assertEqual(read.result_bytes(), payload)
        self.assertTrue(write.dispose())
        self.assertTrue(read.dispose())
        self.assertEqual(self.api.live_request_count, 0)

    def test_open_path_reads_a_temporary_regular_file_without_hid(self):
        import tempfile
        sys.path.insert(0, str(ROOT / "tests"))
        import _sandbox
        from halo.native_io import GENERIC_READ
        with tempfile.TemporaryDirectory(prefix="native_open_", dir=_sandbox.root()) as directory:
            path = Path(directory) / "fixture.bin"
            path.write_bytes(b"regular-file fixture")
            owner = self.api.open_path(path, access=GENERIC_READ)
            request = self.api.create_read(owner, 64)
            try:
                request.start()
                result = self.completion(request)
                self.assertTrue(result.success)
                self.assertEqual(result.data, b"regular-file fixture")
                self.assertTrue(request.dispose())
                self.assertTrue(owner.close())
                self.assertTrue(owner.closed)
            finally:
                if request.poll() is None:
                    request.cancel()
                    self.completion(request)
                request.dispose()
                owner.close()

    def test_cancel_io_ex_targets_only_one_overlapped_on_same_handle(self):
        from halo.native_io import ERROR_OPERATION_ABORTED
        cancelled, surviving = self.read(), self.read()
        cancelled.start()
        surviving.start()
        self.assertIsNone(cancelled.poll())
        self.assertIsNone(surviving.poll())
        self.assertNotEqual(cancelled.native_addresses["overlapped"],
                            surviving.native_addresses["overlapped"])
        self.assertTrue(cancelled.cancel())
        self.assertEqual(self.completion(cancelled).error, ERROR_OPERATION_ABORTED)
        self.assertIsNone(surviving.poll())
        write = self.write(b"surviving exact request")
        write.start()
        self.assertTrue(self.completion(write).success)
        result = self.completion(surviving)
        self.assertTrue(result.success)
        self.assertEqual(result.data, b"surviving exact request")
        self.assertFalse(result.cancel_requested)
        self.assertTrue(cancelled.dispose())
        self.assertTrue(surviving.dispose())
        self.assertTrue(write.dispose())
        self.assertEqual(self.api.live_request_count, 0)

    def test_pending_native_storage_stays_pinned_until_cancel_completion(self):
        from halo.native_io import ERROR_OPERATION_ABORTED
        read = self.read()
        read.start()
        addresses = read.native_addresses
        self.assertNotEqual(addresses["allocation"], 0)
        self.assertNotEqual(addresses["overlapped"], 0)
        self.assertNotEqual(addresses["output"], 0)
        self.assertNotEqual(addresses["event"], 0)
        self.assertIsNone(read.poll())
        self.assertFalse(read.dispose())
        self.assertEqual(read.native_addresses, addresses)
        self.assertEqual(self.api.live_request_count, 1)
        self.assertTrue(read.cancel())
        received = self.completion(read)
        self.assertFalse(received.success)
        self.assertEqual(received.error, ERROR_OPERATION_ABORTED)
        self.assertTrue(received.cancel_requested)
        # Terminal observation is not disposal; storage remains valid.
        self.assertEqual(read.native_addresses, addresses)
        self.assertTrue(read.dispose())
        self.assertTrue(all(value == 0 for value in read.native_addresses.values()))
        self.assertEqual(self.api.live_request_count, 0)

    def test_success_versus_cancel_race_has_only_valid_terminal_outcomes(self):
        from tools.prototypes.native_io_fixtures import NamedPipePair, wait_completion
        from halo.native_io import ERROR_OPERATION_ABORTED
        for index in range(20):
            with self.subTest(index=index), NamedPipePair() as pair:
                read = pair.track(self.api.create_read(pair.server, 64))
                write = pair.track(self.api.create_write(pair.client, b"race"))
                read.start()
                if index % 2:
                    read.cancel()
                    write.start()
                else:
                    write.start()
                    read.cancel()
                received, sent = wait_completion(read), wait_completion(write)
                self.assertTrue(sent.success)
                self.assertIn(received.error, (0, ERROR_OPERATION_ABORTED))
                if received.success:
                    self.assertEqual(received.data, b"race")
                else:
                    self.assertEqual(received.error, ERROR_OPERATION_ABORTED)
                self.assertTrue(read.dispose())
                self.assertTrue(write.dispose())
        self.assertEqual(self.api.live_request_count, 0)

    def test_handle_close_is_deferred_until_terminal_request_disposal(self):
        owner = self.api.own_handle(self.pair.take_server())
        read = self.pair.track(self.api.create_read(owner, 64))
        read.start()
        self.assertFalse(owner.close())
        self.assertFalse(owner.closed)
        self.assertEqual(owner.active_requests, 1)
        self.assertFalse(read.dispose())
        read.cancel()
        self.completion(read)
        self.assertFalse(owner.closed)
        self.assertTrue(read.dispose())
        self.assertTrue(owner.closed)
        self.assertEqual(owner.active_requests, 0)

    def test_issue_failure_is_not_polled_before_native_outcome_is_known(self):
        read = self.read()
        entered, release = threading.Event(), threading.Event()
        def failed_issue(*_):
            entered.set()
            release.wait(2)
            ctypes.set_last_error(87)
            return False
        thread = threading.Thread(target=read.start, daemon=True)
        with mock.patch.object(self.api._dll, "ReadFile", side_effect=failed_issue), \
                mock.patch.object(self.api._dll, "GetOverlappedResult") as probe:
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertIsNone(read.poll())
                self.assertEqual(read.state, "issuing")
                self.assertFalse(read.dispose())
                probe.assert_not_called()
            finally:
                release.set()
                thread.join(2)
            result = read.poll()
            self.assertFalse(result.success)
            self.assertEqual(result.error, 87)
            probe.assert_not_called()
        self.assertTrue(read.dispose())

    def test_deferred_owner_close_does_not_publish_disposal_early(self):
        owner = self.api.own_handle(self.pair.take_server())
        read = self.pair.track(self.api.create_read(owner, 64))
        read.start()
        self.assertFalse(owner.close())
        read.cancel()
        self.completion(read)
        entered, release = threading.Event(), threading.Event()
        original_close = self.api._dll.CloseHandle
        def held_close(handle):
            if handle == owner.handle:
                entered.set()
                release.wait(2)
            return original_close(handle)
        disposed = []
        thread = threading.Thread(target=lambda: disposed.append(read.dispose()), daemon=True)
        with mock.patch.object(self.api._dll, "CloseHandle", side_effect=held_close):
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertNotEqual(read.state, "disposed")
                self.assertFalse(read.dispose())
                self.assertEqual(self.api.live_request_count, 1)
                self.assertFalse(owner.closed)
            finally:
                release.set()
                thread.join(2)
        self.assertEqual(disposed, [True])
        self.assertEqual(read.state, "disposed")
        self.assertTrue(owner.closed)
        self.assertEqual(self.api.live_request_count, 0)

    def test_scheduler_actual_pipe_completion_releases_device_lease(self):
        from halo.io_scheduler import IOScheduler
        scheduler = IOScheduler()
        self.addCleanup(scheduler.close)
        generation = scheduler.submit("pipe",
            lambda: self.read(), timeout=1.0)
        self.assertIsNotNone(generation)
        until(lambda: self.api.live_request_count == 1)
        self.assertIsNone(scheduler.submit("pipe", lambda: self.read(), timeout=1.0))
        write = self.write(b"scheduler reply")
        write.start()
        self.assertTrue(self.completion(write).success)
        self.assertTrue(write.dispose())
        until(lambda: not scheduler.snapshot()["pipe"].busy)
        result = scheduler.snapshot()["pipe"].last_good
        self.assertEqual(result.data, b"scheduler reply")
        self.assertFalse(result.fresh)
        published = scheduler.take_results()
        self.assertTrue(any(item.device_key == "pipe" and item.fresh for item in published))
        self.assertEqual(scheduler.take_results(), [])
        self.assertEqual(self.api.live_request_count, 0)

    def test_synthetic_uncancellable_late_result_never_replaces_fresh_cache(self):
        from halo.io_scheduler import IOScheduler
        from tools.prototypes.native_io_fixtures import DelayedUncancellableOperation
        scheduler = IOScheduler()
        self.addCleanup(scheduler.close)
        good = DelayedUncancellableOperation(b"fresh")
        good.completed.set()
        self.assertIsNotNone(scheduler.submit("device", lambda: good, timeout=1.0))
        until(lambda: not scheduler.snapshot()["device"].busy)
        last_good = scheduler.snapshot()["device"].last_good
        delayed = DelayedUncancellableOperation(b"obsolete")
        self.assertIsNotNone(scheduler.submit("device", lambda: delayed, timeout=0.01))
        until(lambda: scheduler.snapshot()["device"].expired)
        self.assertTrue(scheduler.snapshot()["device"].busy)
        self.assertFalse(delayed.disposed)
        self.assertIsNone(scheduler.submit("device", lambda: delayed, timeout=1.0))
        delayed.completed.set()
        until(lambda: not scheduler.snapshot()["device"].busy)
        self.assertTrue(delayed.disposed)
        cached = scheduler.snapshot()["device"].last_good
        self.assertEqual(cached.data, last_good.data)
        self.assertEqual(cached.generation, last_good.generation)
        self.assertFalse(cached.fresh)
        self.assertEqual(last_good.data, b"fresh")

    def test_fixture_cleanup_keeps_handles_if_disposal_is_unconfirmed(self):
        from tools.prototypes import native_io_fixtures as fixtures
        class DeferredDisposal:
            allowed = False
            def poll(self):
                return object()
            def dispose(self):
                return self.allowed
        request = DeferredDisposal()
        self.pair.track(request)
        server, client = self.pair.server, self.pair.client
        before = len(fixtures._PINNED_CONNECTIONS)
        try:
            with mock.patch.object(self.pair.kernel, "CloseHandle") as close:
                self.assertFalse(self.pair.close())
                close.assert_not_called()
            self.assertEqual(len(fixtures._PINNED_CONNECTIONS), before + 1)
            self.assertEqual(fixtures._PINNED_CONNECTIONS[-1][:2], (server, client))
        finally:
            # This synthetic terminal request can now confirm cleanup; no
            # unresolved native I/O is freed by this test's recovery.
            request.allowed = True
            self.pair.server, self.pair.client = server, client
            if len(fixtures._PINNED_CONNECTIONS) > before:
                fixtures._PINNED_CONNECTIONS.pop()
            self.assertTrue(self.pair.close())

    def test_process_exits_with_two_genuinely_blocked_native_daemon_workers(self):
        script = ROOT / "tools" / "prototypes" / "measure_native_io.py"
        started = time.monotonic()
        completed = subprocess.run(
            [getattr(sys, "_base_executable", sys.executable), "-B", str(script),
             "--lifecycle-child"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=8.0,
            creationflags=0x08000000)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertTrue(report["native_waits_still_uncompleted"])
        self.assertFalse(report["close_returned"])
        self.assertLess(report["close_elapsed_seconds"], 0.8)
        self.assertEqual(report["stats"]["workers"], 2)
        self.assertLess(time.monotonic() - started, 8.0)


if __name__ == "__main__":
    unittest.main()
