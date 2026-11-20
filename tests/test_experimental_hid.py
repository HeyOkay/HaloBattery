"""Native HID adapter tests: fake HID metadata and native completions only."""
import ctypes
import os
import threading
import time
import types
import unittest

from halo.hid_transport import HidDevice, TransportRuntime, ReportCaps, _HIDP_CAPS
from halo.native_io import Completion


class Owner:
    def __init__(self):
        self.handle, self.closing, self.closed = 123, False, False
        self.close_threads = []
    def close(self):
        self.close_threads.append(threading.current_thread().name)
        self.closing = self.closed = True
        return True


class Request:
    def __init__(self, data=None, *, transferred=None, ignore_cancel=False):
        self.completion = None if data is None else Completion(True, 0,
            len(data) if transferred is None else transferred, data, False)
        self.buffer = ctypes.create_string_buffer(data or b'\0' * 128)
        self.native_addresses = {'output': ctypes.addressof(self.buffer)}
        self.started = self.cancelled = self.disposed = False
        self.ignore_cancel = ignore_cancel
    def start(self):
        if not self.cancelled:
            self.started = True
    def cancel(self):
        self.cancelled = True
        if not self.ignore_cancel and self.completion is None:
            self.completion = Completion(False, 995, 0, b'', True)
        return True
    def poll(self):
        return self.completion
    def dispose(self):
        if self.completion is None:
            return False
        self.disposed = True
        return True
    def complete(self, data):
        self.completion = Completion(True, 0, len(data), data, self.cancelled)


class Api:
    def __init__(self):
        self.owners, self.requests, self.writes, self.ioctls = [], [], [], []
        self.access = []
        self.fail_rw = False
        self.next_read = None
        self.report = b'\0abc'
        self.report_count = 3
    def open_path(self, path, **kwargs):
        self.access.append(kwargs.get('access', 'rw'))
        if self.fail_rw and not kwargs:
            raise OSError('exclusive')
        owner = Owner()
        self.owners.append(owner)
        return owner
    def create_read(self, owner, size):
        request = self.next_read or Request()
        self.next_read = None
        self.requests.append(request)
        return request
    def create_write(self, owner, data):
        self.writes.append(data)
        request = Request(b'', transferred=len(data))
        self.requests.append(request)
        return request
    def create_ioctl(self, owner, code, **kwargs):
        self.ioctls.append((code, kwargs))
        request = Request(self.report, transferred=self.report_count)
        self.requests.append(request)
        return request


class Metadata:
    def __init__(self, caps=ReportCaps(65, 64, 65)):
        self.report_caps, self.features = caps, []
        self.fail_feature = False
    def caps(self, owner, guard):
        guard()
        return self.report_caps
    def send_feature(self, owner, data):
        self.features.append(data)
        return not self.fail_feature
    def string(self, owner, name):
        return name + ' test'


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.api, self.metadata = Api(), Metadata()
        self.runtime = TransportRuntime(api=self.api, timeout=.5)
        self.device = HidDevice(self.runtime, metadata=self.metadata)
        self.device.open_path(b'fake-path')
    def tearDown(self):
        self.assertTrue(self.runtime.close(.5), self.runtime.diagnostics())
    def test_caps_layout_and_fallback(self):
        self.assertEqual(ctypes.sizeof(_HIDP_CAPS), 64)
        self.device.close()
        self.api.fail_rw = True
        self.device.open_path('feature-only')
        self.assertEqual(self.api.access[-2:], ['rw', 0])
    def test_padding_and_original_feature_count(self):
        self.assertEqual(self.device.write([0, 2, 3]), 64)
        self.assertEqual(self.api.writes[-1], b'\0\2\3' + bytes(61))
        self.assertEqual(self.device.send_feature_report([0, 7]), 2)
        self.assertEqual(self.metadata.features[-1], b'\0\7' + bytes(63))
        self.assertEqual(self.device.write([]), -1)
        self.assertEqual(self.device.send_feature_report([]), -1)
        self.assertEqual(self.device.error(), 'feature write data is empty')
    def test_feature_and_input_report_zero_id_count(self):
        self.assertEqual(self.device.get_feature_report(0, 4), [0, 97, 98, 99])
        self.assertEqual(self.api.ioctls[-1][1]['input_data'], b'\0'*4)
        self.api.report, self.api.report_count = b'\5abc', 4
        self.assertEqual(self.device.get_input_report(5, 4), [5, 97, 98, 99])
        self.assertTrue(self.api.requests[-1].disposed)
    def test_nonblocking_read_reuses_one_native_request(self):
        self.device.set_nonblocking(True)
        self.assertEqual(self.device.read(64), [])
        request = self.api.requests[-1]
        self.assertFalse(request.cancelled)
        self.assertEqual(self.device.read(64), [])
        self.assertEqual(len(self.api.requests), 1)
        request.complete(b'\0\x11\x22')
        self.assertEqual(self.device.read(1), [17])
        self.assertTrue(request.disposed)
        self.assertIsNone(self.device._read)
    def test_finite_read_timeout_preserves_read_then_close_drains(self):
        self.assertEqual(self.device.read(64, 5), [])
        request = self.api.requests[-1]
        self.assertFalse(request.cancelled)
        self.device.close()
        self.assertTrue(request.cancelled and request.disposed)
        self.assertTrue(self.api.owners[-1].closed)
        self.assertTrue(all('halo-io-worker' in name for name in self.api.owners[-1].close_threads))
    def test_timeout_keeps_owner_quarantined_until_native_completion(self):
        request = Request(ignore_cancel=True)
        self.api.next_read = request
        self.runtime.timeout = .04
        with self.runtime.provider_scope('test') as scope:
            with self.assertRaises(TimeoutError):
                self.device.read(64)
        self.assertTrue(scope.failed)
        self.assertTrue(scope.unsafe)
        self.assertFalse(self.runtime.transport_idle('test'))
        cancel_deadline = time.monotonic() + .5
        while not request.cancelled and time.monotonic() < cancel_deadline:
            time.sleep(.002)
        self.assertTrue(request.cancelled)
        self.assertFalse(request.disposed)
        self.assertFalse(self.api.owners[-1].closed)
        request.complete(b'\0x')
        deadline = time.monotonic() + 1
        while not self.runtime.transport_idle('test') and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.runtime.transport_idle('test'))
        self.assertTrue(request.disposed)
        self.assertIsNone(self.device._read)
    def test_cached_results_not_forgotten_by_parallel_idle_checks(self):
        stop = threading.Event()
        def inspect():
            while not stop.is_set():
                self.runtime.transport_idle('another')
                time.sleep(.0002)
        thread = threading.Thread(target=inspect)
        thread.start()
        try:
            for number in range(40):
                self.assertEqual(self.runtime.invoke(lambda n=number: n, 'test-result', .5), number)
        finally:
            stop.set(); thread.join()
    def test_failed_feature_marks_provider_scope(self):
        self.metadata.fail_feature = True
        with self.runtime.provider_scope('test') as scope:
            self.assertEqual(self.device.send_feature_report([0, 7]), -1)
        self.assertEqual(self.device.error(), 'feature write failed')
        self.assertTrue(scope.failed)
        self.assertFalse(scope.unsafe)
        self.metadata.fail_feature = False
        self.assertEqual(self.device.send_feature_report([0, 7]), 2)
        self.assertEqual(self.device.error(), 'Success')

    def test_string_error_is_cached_and_success_clears_it(self):
        original = self.metadata.string
        fault = OSError(31, 'product string unavailable')
        def fail_string(*args):
            raise fault
        self.metadata.string = fail_string
        with self.assertRaises(OSError) as raised:
            self.device.get_product_string()
        self.assertIs(raised.exception, fault)
        self.assertEqual(self.device.error(), str(fault))
        self.metadata.string = original
        self.assertEqual(self.device.get_product_string(), 'Product test')
        self.assertEqual(self.device.error(), 'Success')

    def test_error_is_cached_and_safe_before_open_and_after_close(self):
        from unittest.mock import patch
        unopened = HidDevice(self.runtime, metadata=Metadata())
        self.assertEqual(self.device.write([]), -1)
        with patch.object(self.runtime, 'invoke', side_effect=AssertionError('error performed I/O')):
            self.assertEqual(unopened.error(), 'Success')
            self.assertEqual(self.device.error(), 'write data is empty')
            self.assertEqual(self.device.error(), 'write data is empty')
        self.device.close()
        self.assertEqual(self.device.error(), 'Success')
        with patch.object(self.runtime, 'invoke', side_effect=AssertionError('closed error performed I/O')):
            self.device.close()
            self.assertEqual(self.device.error(), 'Success')

    def test_failed_write_diagnostic_is_text_and_success_clears_it(self):
        original = self.api.create_write
        fault = OSError(31, 'driver refused the output report')
        def fail_write(*args, **kwargs):
            raise fault
        self.api.create_write = fail_write
        with self.runtime.provider_scope('failed-write') as scope:
            self.assertEqual(self.device.write([0, 7]), -1)
        self.assertTrue(scope.failed)
        self.assertEqual(self.device.error(), str(fault))
        self.assertIsInstance(self.device._last_error, str)
        self.assertFalse(any(isinstance(value, BaseException) for value in vars(self.device).values()))
        self.api.create_write = original
        self.assertEqual(self.device.write([0, 7]), 64)
        self.assertEqual(self.device.error(), 'Success')

    def test_write_refused_with_incorrect_function_is_recognisable(self):
        """hyperx_cloud3 retries a refused write as a feature report only when
        error() names Windows error 1 the way hidapi does."""
        from providers import hyperx_cloud3
        original = self.api.create_write
        def refused(owner, data):
            request = original(owner, data)
            request.completion = Completion(False, 1, 0, b'', False)
            return request
        self.api.create_write = refused
        with self.runtime.provider_scope('refused-write') as scope:
            self.assertEqual(self.device.write([0, 7]), -1)
        self.assertTrue(scope.failed)
        self.assertFalse(scope.unsafe)
        text = self.device.error().lower()
        self.assertIn('0x00000001', text)
        self.assertTrue(any(marker in text for marker in hyperx_cloud3._FEATURE_ONLY), text)
        self.api.create_write = original
        self.assertEqual(self.device.write([0, 7]), 64)
        self.assertEqual(self.device.error(), 'Success')

    def test_read_timeout_longer_than_transport_limit_ends_empty(self):
        """hyperx_cloud_flight reads with 2000 ms. A read whose own timeout is longer
        than the transport limit (0.5 s here) ends empty, like hidapi, not as a failure."""
        with self.runtime.provider_scope('slow-read') as scope:
            self.assertEqual(self.device.read(64, 700), [])
        self.assertFalse(scope.failed)

    def test_feature_exception_keeps_identity_and_diagnostic(self):
        original = self.metadata.send_feature
        fault = OSError(5, 'feature collection access denied')
        def fail_feature(*args):
            raise fault
        self.metadata.send_feature = fail_feature
        with self.assertRaises(OSError) as raised:
            self.device.send_feature_report([0, 7])
        self.assertIs(raised.exception, fault)
        self.assertEqual(self.device.error(), str(fault))
        self.metadata.send_feature = original
        self.assertEqual(self.device.send_feature_report([0, 7]), 2)
        self.assertEqual(self.device.error(), 'Success')

    def test_report_error_retains_context_and_original_cause(self):
        original = self.api.create_ioctl
        for method, kind in [(self.device.get_feature_report, 'feature'),
                             (self.device.get_input_report, 'input')]:
            with self.subTest(report=kind):
                fault = OSError(31, 'collection report unavailable')
                def fail_report(*args, **kwargs):
                    raise fault
                self.api.create_ioctl = fail_report
                with self.assertRaises(OSError) as raised:
                    method(0, 4)
                self.assertIs(raised.exception.__cause__, fault)
                self.assertEqual(self.device.error(), str(raised.exception))
                self.assertIn('HID ' + kind + ' read', self.device.error())
                self.assertIn('collection report unavailable', self.device.error())
                self.api.create_ioctl = original
                self.assertEqual(method(0, 4), [0, 97, 98, 99])
                self.assertEqual(self.device.error(), 'Success')

    def test_read_error_is_cached_and_next_fresh_read_clears_it(self):
        request = Request()
        request.completion = Completion(False, 31, 0, b'', False)
        self.api.next_read = request
        with self.assertRaises(OSError) as raised:
            self.device.read(64)
        self.assertEqual(self.device.error(), str(raised.exception))
        self.assertTrue(request.disposed)
        self.api.next_read = Request(b'\0ok')
        self.assertEqual(self.device.read(64), [111, 107])
        self.assertEqual(self.device.error(), 'Success')

    def test_open_error_keeps_native_cause_and_retry_clears_it(self):
        self.device.close()
        original = self.api.open_path
        fault = OSError(5, 'collection is unavailable')
        def fail_open(*args, **kwargs):
            raise fault
        self.api.open_path = fail_open
        with self.assertRaisesRegex(OSError, 'open failed') as raised:
            self.device.open_path(b'unavailable-collection')
        self.assertIs(raised.exception.__cause__, fault)
        self.assertEqual(self.device.error(), str(fault))
        self.api.open_path = original
        self.device.open_path(b'recovered-collection')
        self.assertEqual(self.device.error(), 'Success')

    def test_feature_deadline_still_raises_and_retains_owner_until_completion(self):
        entered, release = threading.Event(), threading.Event()
        def blocked_feature(*args):
            entered.set()
            release.wait(1)
            return True
        self.metadata.send_feature = blocked_feature
        self.runtime.timeout = .03
        owner = self.api.owners[-1]
        try:
            with self.runtime.provider_scope('feature-deadline') as scope:
                with self.assertRaises(TimeoutError) as raised:
                    self.device.send_feature_report([0, 7])
                self.assertTrue(entered.is_set())
                self.assertEqual(self.device.error(), str(raised.exception))
                self.device.close()
            self.assertTrue(scope.failed)
            self.assertTrue(scope.unsafe)
            self.assertFalse(owner.closed)
            self.assertFalse(self.runtime.transport_idle('feature-deadline'))
        finally:
            release.set()
        deadline = time.monotonic() + 1
        while not self.runtime.transport_idle('feature-deadline') and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(owner.closed)
        self.assertTrue(self.runtime.transport_idle('feature-deadline'))
    def test_closed_shape_and_limits(self):
        with self.assertRaises(ValueError):
            self.device.write(bytes(32769))
        self.device.close()
        self.device.close()
        with self.assertRaisesRegex(ValueError, 'not open'):
            self.device.read(10)
    def test_install_preserves_shared_enumeration_and_bound_factory(self):
        calls = []
        module = types.SimpleNamespace(device=object, enumerate=lambda v,p: calls.append((v,p)) or [{'path': b'x'}])
        self.runtime.install(module)
        self.assertEqual(module.enumerate(1,2), [{'path': b'x'}])
        self.assertEqual(calls, [(1,2)])
        self.assertIsInstance(module.device(), HidDevice)
        self.assertIn('Native workers: 2; report limit: 32768 bytes', self.runtime.diagnostics())

    def test_concurrent_enumeration_serializes_without_false_busy_failure(self):
        entered, release = threading.Event(), threading.Event()
        calls, results, errors = [], [], []
        def enumerate_original(vendor, product):
            calls.append(vendor)
            if vendor == 1:
                entered.set()
                release.wait(.5)
            return [vendor]
        module = types.SimpleNamespace(device=object, enumerate=enumerate_original)
        self.runtime.install(module)
        def discover(number):
            try:
                with self.runtime.provider_scope(str(number)) as scope:
                    results.append(module.enumerate(number, 0))
                if scope.failed:
                    errors.append(scope.error)
            except Exception as exc:
                errors.append(str(exc))
        first = threading.Thread(target=discover, args=(1,))
        second = threading.Thread(target=discover, args=(2,))
        first.start()
        self.assertTrue(entered.wait(.5))
        second.start()
        time.sleep(.015)
        release.set()
        first.join(1); second.join(1)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(calls, [1, 2])
        self.assertEqual(results, [[1], [2]])

    def test_failed_native_close_does_not_release_group_lease(self):
        owner = self.api.owners[-1]
        release = threading.Event()
        original = owner.close
        owner.close = lambda: original() if release.is_set() else False
        self.runtime.timeout = .03
        with self.runtime.provider_scope('closing'):
            with self.assertRaises(TimeoutError):
                self.device.close()
        self.assertIn('native invocation', self.device.error())
        self.assertFalse(self.runtime.transport_idle('closing'))
        self.assertFalse(owner.closed)
        release.set()
        deadline = time.monotonic() + 1
        while not self.runtime.transport_idle('closing') and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.runtime.transport_idle('closing'))
        self.assertTrue(owner.closed)

    def test_expiry_after_completion_still_runs_abort_cleanup(self):
        from halo.hid_transport import _Invocation
        owner = Owner()
        invocation = _Invocation(self.runtime, lambda: 7)
        invocation.abort_finalizers.append(owner.close)
        invocation.start()
        self.assertEqual(invocation.poll().status, 'success')
        invocation.deadline = time.monotonic() - 1
        self.assertTrue(invocation.dispose())
        self.assertTrue(owner.closed)
        self.assertTrue(invocation.cancelled.is_set())

    def test_parallel_first_opens_share_one_backend_instance(self):
        from unittest.mock import patch
        runtime = TransportRuntime(timeout=.5)
        devices = [HidDevice(runtime, metadata=Metadata()) for _ in range(2)]
        created, errors = [], []
        barrier = threading.Barrier(2)
        def construct():
            time.sleep(.02)  # Model WinDLL initialization releasing the GIL.
            api = Api()
            created.append(api)
            return api
        def open_one(index):
            try:
                barrier.wait(.5)
                devices[index].open_path(('parallel-' + str(index)).encode())
            except Exception as exc:
                errors.append(exc)
        try:
            with patch('halo.hid_transport.WindowsApi', side_effect=construct):
                threads = [threading.Thread(target=open_one, args=(i,)) for i in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(1)
                self.assertFalse(any(thread.is_alive() for thread in threads))
                self.assertEqual(errors, [])
                self.assertEqual(len(created), 1)
                self.assertIs(devices[0].api, devices[1].api)
                self.assertIs(devices[0].api, created[0])
        finally:
            self.assertTrue(runtime.close(.5))

    def test_finally_close_while_query_quarantined_closes_after_late_completion(self):
        request = Request(ignore_cancel=True)
        self.api.next_read = request
        self.runtime.timeout = .03
        owner = self.api.owners[-1]
        with self.runtime.provider_scope('late-query'):
            try:
                with self.assertRaises(TimeoutError):
                    self.device.read(64)
            finally:
                self.device.close()
        self.assertFalse(owner.closed)
        self.assertFalse(self.runtime.transport_idle('late-query'))
        request.complete(b'\0late')
        deadline = time.monotonic() + 1
        while not self.runtime.transport_idle('late-query') and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.runtime.transport_idle('late-query'))
        self.assertTrue(owner.closed)
        self.assertTrue(request.disposed)
        self.assertIsNone(self.device._owner)
        self.assertEqual(len(self.runtime._devices), 0)

    def test_idle_cancelled_scope_still_closes_its_handle(self):
        owner = self.api.owners[-1]
        with self.runtime.provider_scope('idle-cancel') as scope:
            self.runtime.cancel_owner('idle-cancel')
            self.device.close()
        self.assertTrue(scope.failed)
        self.assertTrue(owner.closed)
        self.assertIsNone(self.device._owner)
        self.assertEqual(len(self.runtime._devices), 0)

    def test_cancel_racing_queued_cleanup_does_not_drop_close(self):
        release = threading.Event()
        entered = [threading.Event(), threading.Event()]
        failures = []
        def block(index):
            try:
                self.runtime.invoke(lambda: (entered[index].set(), release.wait(1)),
                                    'occupied-' + str(index), .5)
            except Exception as exc:
                failures.append(exc)
        blockers = [threading.Thread(target=block, args=(i,)) for i in range(2)]
        for thread in blockers:
            thread.start()
        self.assertTrue(all(event.wait(.5) for event in entered))
        closed = threading.Event()
        def close_device():
            try:
                with self.runtime.provider_scope('queued-close'):
                    self.device.close()
                closed.set()
            except Exception as exc:
                failures.append(exc)
        closer = threading.Thread(target=close_device)
        closer.start()
        deadline = time.monotonic() + .3
        while time.monotonic() < deadline:
            with self.runtime._lock:
                queued = self.runtime._calls.get(self.device._key)
                if queued is not None and queued[2].cleanup_only:
                    break
            time.sleep(.001)
        self.assertIsNotNone(queued)
        self.runtime.cancel_owner('queued-close')
        release.set()
        for thread in blockers + [closer]:
            thread.join(1)
        self.assertFalse(any(thread.is_alive() for thread in blockers + [closer]))
        self.assertEqual(failures, [])
        self.assertTrue(closed.is_set())
        self.assertTrue(self.api.owners[-1].closed)
        self.assertEqual(len(self.runtime._devices), 0)

    def test_expired_unstarted_close_retains_owner_and_retries_without_stacking(self):
        self.device.close()
        with self.runtime.provider_scope('queued-owner'):
            self.device.open_path(b'owned-queued-fixture')
        owner = self.api.owners[-1]
        release = threading.Event()
        entered = [threading.Event(), threading.Event()]
        failures = []
        def block(index):
            try:
                self.runtime.invoke(lambda: (entered[index].set(), release.wait(1)),
                                    'blocked-' + str(index), .8)
            except Exception as exc:
                failures.append(exc)
        threads = [threading.Thread(target=block, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        self.assertTrue(all(event.wait(.5) for event in entered))
        self.runtime.timeout = .035
        try:
            with self.runtime.provider_scope('queued-owner'):
                with self.assertRaises(TimeoutError):
                    self.device.close()
            # Several queued cleanup deadlines pass while both workers remain
            # inside their old callbacks. No replacement thread or job stack.
            deadline = time.monotonic() + .13
            while time.monotonic() < deadline:
                self.assertFalse(self.runtime.transport_idle('queued-owner'))
                self.assertFalse(owner.closed)
                stats = self.runtime.scheduler.stats()
                self.assertLessEqual(stats['devices'], 3)
                self.assertLessEqual(stats['active'], 3)
                self.assertLessEqual(stats['queued'], 1)
                self.assertEqual(len(self.runtime.scheduler._workers), 2)
                time.sleep(.005)
        finally:
            release.set()
            for thread in threads:
                thread.join(1)
        self.assertEqual(failures, [])
        deadline = time.monotonic() + 1
        while not self.runtime.transport_idle('queued-owner') and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.runtime.transport_idle('queued-owner'))
        self.assertTrue(owner.closed)
        self.assertIsNone(self.device._owner)
        self.assertEqual(len(self.runtime._devices), 0)
        self.assertLessEqual(self.runtime.scheduler.stats()['peak_running'], 2)

    def test_confirmed_report_error_allows_safe_successful_fallback(self):
        original = self.api.create_ioctl
        def fail_report(*args, **kwargs):
            request = Request()
            request.completion = Completion(False, 31, 0, b'', False)
            self.api.requests.append(request)
            return request
        self.api.create_ioctl = fail_report
        with self.runtime.provider_scope('fallback') as scope:
            with self.assertRaises(OSError):
                self.device.get_feature_report(0, 65)
            self.assertTrue(self.api.requests[-1].disposed)
            self.assertFalse(scope.unsafe)
            self.api.create_ioctl = original
            self.assertEqual(self.device.get_feature_report(0, 4), [0, 97, 98, 99])
        self.assertTrue(scope.failed)
        self.assertFalse(scope.unsafe)
        self.assertFalse(scope.cancelled)
        self.assertTrue(self.runtime.transport_idle('fallback'))

    def test_busy_admission_marks_scope_unsafe(self):
        entered, release = threading.Event(), threading.Event()
        failures = []
        def occupy():
            try:
                self.runtime.invoke(lambda: (entered.set(), release.wait(.5)), 'busy-key', .8)
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=occupy)
        thread.start()
        self.assertTrue(entered.wait(.5))
        try:
            with self.runtime.provider_scope('blocked-admission') as scope:
                with self.assertRaises(OSError):
                    self.runtime.invoke(lambda: 1, 'busy-key')
            self.assertTrue(scope.failed)
            self.assertTrue(scope.unsafe)
        finally:
            release.set()
            thread.join(1)
        self.assertEqual(failures, [])

    def test_shutdown_false_keeps_pending_context_then_eventually_cleans(self):
        request = Request(ignore_cancel=True)
        self.api.next_read = request
        self.device.set_nonblocking(True)
        self.assertEqual(self.device.read(64), [])
        self.assertFalse(self.runtime.close(.03))
        self.assertFalse(self.api.owners[-1].closed)
        self.assertFalse(request.disposed)
        request.complete(b'\0x')
        deadline = time.monotonic() + 1
        while not self.api.owners[-1].closed and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.api.owners[-1].closed)
        self.assertTrue(request.disposed)

    def test_late_open_is_closed_before_retirement_and_never_published(self):
        self.device.close()
        entered, release = threading.Event(), threading.Event()
        original = self.api.open_path
        def delayed(path, **kwargs):
            entered.set()
            release.wait(1)
            return original(path, **kwargs)
        self.api.open_path = delayed
        self.runtime.timeout = .03
        error = []
        def open_late():
            try:
                with self.runtime.provider_scope('late'):
                    self.device.open_path(b'late-path')
            except OSError as exc:
                error.append(exc)
        thread = threading.Thread(target=open_late)
        thread.start()
        self.assertTrue(entered.wait(.5))
        thread.join(.2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(error)
        self.assertFalse(self.runtime.transport_idle('late'))
        release.set()
        deadline = time.monotonic() + 1
        while not self.runtime.transport_idle('late') and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(self.runtime.transport_idle('late'))
        self.assertIsNone(self.device._owner)
        self.assertTrue(self.api.owners[-1].closed)

    def test_caps_failure_closes_owned_handle(self):
        self.device.close()
        self.metadata.report_caps = ReportCaps(32769, 1, 1)
        with self.assertRaises(ValueError):
            self.device.open_path('oversize-caps')
        self.assertTrue(self.api.owners[-1].closed)
        self.assertIsNone(self.device._owner)


@unittest.skipUnless(os.name == 'nt', 'real local Windows pipes')
class NamedPipeAdapterTests(unittest.TestCase):
    def test_real_read_write_persistent_slot_and_native_cleanup(self):
        from tools.prototypes.native_io_fixtures import NamedPipePair, wait_completion
        from halo.native_io import WindowsApi
        pair = NamedPipePair()
        api = WindowsApi()
        class PipeApi:
            def open_path(self, path):
                return api.own_handle(pair.take_server())
            def __getattr__(self, name):
                return getattr(api, name)
        runtime = TransportRuntime(api=PipeApi())
        device = HidDevice(runtime, metadata=Metadata(ReportCaps(65, 64, 65)))
        try:
            device.open_path(b'local-fixture-only')
            device.set_nonblocking(True)
            self.assertEqual(device.read(64), [])
            retained = device._read
            self.assertEqual(device.read(64), [])
            self.assertIs(device._read, retained)
            peer_write = pair.track(api.create_write(api.borrow_handle(pair.client), b'\0abc'))
            peer_write.start()
            self.assertTrue(wait_completion(peer_write).success)
            self.assertTrue(peer_write.dispose())
            deadline = time.monotonic() + 1
            data = []
            while not data and time.monotonic() < deadline:
                data = device.read(64)
            self.assertEqual(data, [97, 98, 99])
            peer_read = pair.track(api.create_read(api.borrow_handle(pair.client), 64))
            peer_read.start()
            self.assertEqual(device.write([0, 8, 9]), 64)
            self.assertEqual(wait_completion(peer_read).data, b'\0\x08\x09' + bytes(61))
            self.assertTrue(peer_read.dispose())
            self.assertEqual(device.read(64, 5), [])
            device.close()
            self.assertEqual(api.live_request_count, 0)
            self.assertTrue(runtime.close(.5))
        finally:
            self.assertTrue(runtime.close(.5))
            self.assertTrue(pair.close())


if __name__ == '__main__':
    unittest.main()
