"""Targeted CLI diagnostics: fake readers and fake native completions only."""
from contextlib import contextmanager
import ctypes
import json
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from halo import device_diagnostics as D
from halo import device_test_adapters as A
from halo import diagnostic_guard as G
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
import test_device_diagnostics_entrypoint as entrytests
import test_experimental_hid as native
import test_aj159_native as aj159tests
import test_sony_h9ii as sonytests
import test_logitech_pro_x3 as x3tests
from providers import ajazz, sony_h9ii, logitech_centurion


TARGETS = {
    "aj159": (0x3151, 0x402D, 2, 0xFFFF, 2),
    "inzone-h9ii": (0x054C, 0x0FA8, 5, 0xFF04, 1),
    "logitech-pro-x3": (0x046D, 0x0B45, 3, 0xFFA0, 1),
}
FIELDS = ("vendor_id", "product_id", "interface_number", "usage_page", "usage")


def endpoint(device, path=b"selected-receiver", **changes):
    info = dict(zip(FIELDS, TARGETS[device]))
    info.update(path=path)
    info.update(changes)
    return info


class AdapterSelectionTests(unittest.TestCase):
    def setUp(self):
        self.reader = Mock()
        self.reader._ask_aj159.return_value = (70, True)
        self.reader._read.return_value = (60, False, True)
        self.reader._read_x3.return_value = (78, False, False)
        self.reader.poll.side_effect = AssertionError("family poll must never run")
        self.module = SimpleNamespace(hidlist=SimpleNamespace(enumerate=Mock()), RECEIVER_BUDGET=1.)
        loader = patch.object(A, "_load_provider", return_value=(self.module, self.reader))
        self.loader = loader.start()
        self.addCleanup(loader.stop)

    def probe(self, device, entries=None):
        self.module.hidlist.enumerate.return_value = entries if entries is not None else [endpoint(device)]
        tester = A.TargetedDeviceTester(device)
        return tester.probe_experimental(acknowledged=True), tester

    def assert_no_read(self):
        self.reader._ask_aj159.assert_not_called()
        self.reader._read.assert_not_called()
        self.reader._read_x3.assert_not_called()
        self.reader.poll.assert_not_called()

    def test_literal_acknowledgement_precedes_all_import_enumeration_and_readers(self):
        for device in TARGETS:
            for value in (False, None, 1, "true"):
                with self.subTest(device=device, value=value), self.assertRaises(ValueError):
                    A.TargetedDeviceTester(device).probe_experimental(acknowledged=value)
        self.loader.assert_not_called()
        self.module.hidlist.enumerate.assert_not_called()
        self.assert_no_read()

    def test_unsupported_held_targets_have_no_adapter_or_loader(self):
        for device in ("kysona", "stealth-600x", "bad"):
            with self.subTest(device=device), self.assertRaises(ValueError):
                A.TargetedDeviceTester(device)
        self.loader.assert_not_called()

    def test_single_exact_target_calls_only_its_existing_reader(self):
        for device, level, method in (("aj159", 70, "_ask_aj159"),
                                      ("inzone-h9ii", 60, "_read"),
                                      ("logitech-pro-x3", 78, "_read_x3")):
            self.reader.reset_mock()
            self.module.hidlist.enumerate.reset_mock()
            info = endpoint(device)
            # Include enabled siblings plus held targets in the fake inventory.
            entries = [dict(info, product_id=pid) for pid in
                       (0x5C2F, 0x0AF7, 0x0D58, 0x5031, 0x2232)] + [info]
            result, _ = self.probe(device, entries)
            self.assertEqual(result["percentage"], level)
            self.module.hidlist.enumerate.assert_called_once_with(TARGETS[device][0])
            for other in ("_ask_aj159", "_read", "_read_x3", "poll"):
                if other != method:
                    getattr(self.reader, other).assert_not_called()
            called = getattr(self.reader, method)
            called.assert_called_once()
            self.assertEqual(called.call_args.args[0], info if device == "aj159" else info["path"])
            if device == "inzone-h9ii":
                self.assertIsInstance(called.call_args.args[1], float)
            self.assertEqual(result["freshness"], "unverified")

    def test_wrong_identity_collection_types_or_paths_never_reach_readers(self):
        for device in TARGETS:
            entries = [None, 3, "not metadata"]
            for field in FIELDS:
                for value in (None, True, "1", -1):
                    entries.append(endpoint(device, **{field: value}))
            for path in (b"", "", b"\xff", "\ud800", b"a\0b", "a\0b", None, 12):
                entries.append(endpoint(device, path=path))
            with self.subTest(device=device):
                self.assertIsNone(self.probe(device, entries)[0])
        self.assert_no_read()

    def test_two_receivers_are_ambiguous_but_case_and_encoding_duplicates_are_one(self):
        for device in TARGETS:
            self.reader.reset_mock()
            with self.subTest(device=device):
                self.assertIsNone(self.probe(device, [endpoint(device, b"a"), endpoint(device, b"b")])[0])
                self.assert_no_read()
                self.assertIsNotNone(self.probe(device, [endpoint(device, b"Receiver"),
                                                        endpoint(device, "receiver")])[0])

    def test_no_reading_or_malformed_reader_results_never_become_valid_level(self):
        for device in TARGETS:
            method = {"aj159": "_ask_aj159", "inzone-h9ii": "_read", "logitech-pro-x3": "_read_x3"}[device]
            values = [(None, True), (True, True), (101, True), (70, False)] if device == "aj159" else [
                None, (None, False, False), (True, False, True), (-1, False, True),
                (101, False, True), (70, 0, True), (70, False, 1), (70,)]
            for value in values:
                getattr(self.reader, method).return_value = value
                with self.subTest(device=device, value=value):
                    self.assertIsNone(self.probe(device)[0])

    def test_unknown_charge_and_percentage_boundaries_are_preserved(self):
        for device in TARGETS:
            method = {"aj159": "_ask_aj159", "inzone-h9ii": "_read", "logitech-pro-x3": "_read_x3"}[device]
            for level in (0, 100):
                getattr(self.reader, method).return_value = ((level, True) if device == "aj159"
                                                           else (level, False, False))
                result, _ = self.probe(device)
                self.assertEqual(result["percentage"], level)
                self.assertIsNone(result["charging"])
                self.assertFalse(result["charging_known"])

    def test_errors_never_publish_raw_paths_reports_or_exception_text(self):
        for device in TARGETS:
            self.module.hidlist.enumerate.side_effect = OSError("PRIVATE path serial report")
            result, tester = self.probe(device)
            self.assertIsNone(result)
            self.assertNotIn("PRIVATE", " ".join(tester.diagnostics()))
        self.assert_no_read()


class TargetedEntryPointTests(entrytests.DiagnosticEntryPointTests):
    """Inherit the existing startup/held-contract regression checks unchanged."""

    def setUp(self):
        super().setUp()
        retention_patch = patch.object(D, "_retained_guard", None)
        retention_patch.start()
        self.addCleanup(retention_patch.stop)
        self.guard = Mock()
        self.guard.acquire.return_value = self.guard.close.return_value = True
        guard_patch = patch.object(D, "_create_instance_guard", return_value=self.guard)
        self.guard_factory = guard_patch.start()
        self.addCleanup(guard_patch.stop)
        @contextmanager
        def scope(owner):
            self.assertIn(owner, D._OWNERS.values())
            yield SimpleNamespace(failed=False)
        self.runtime.provider_scope.side_effect = scope

    def test_new_targets_frozen_json_bypasses_profile_and_has_no_release_hold(self):
        for device in TARGETS:
            self.backend.reset_mock()
            self.factory.reset_mock()
            self.provider_factory.reset_mock()
            self.runtime.reset_mock()
            self.provider.reset_mock()
            path = self.root / (device + ".json")
            with self.subTest(device=device), self.assertRaises(SystemExit) as stopped:
                self.execute(["--diagnose-device", device, "--acknowledge-experimental",
                              "--diagnostic-output", str(path)])
            self.assertEqual(stopped.exception.code, 0)
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertFalse(result["release_hold"])
            self.assertEqual(result["freshness"], "unverified")
            self.provider_factory.assert_called_once_with(device)
            self.runtime.provider_scope.assert_called_once_with(device + "-local-tester")
            self.provider.probe_experimental.assert_called_once_with(acknowledged=True)
            self.runtime.close.assert_called_once_with(.5)

    def test_new_target_invalid_or_mixed_args_stop_before_output_backend_and_profile(self):
        for device in TARGETS:
            path = self.root / (device + ".json")
            base = ["--diagnose-device", device, "--diagnostic-output", str(path)]
            cases = [base, base + ["--acknowledge-experimental", "--transport", "receiver"],
                     base + ["--acknowledge-experimental", "--probe"],
                     base + ["--acknowledge-experimental", "--transport", "wired"]]
            for args in cases:
                with self.subTest(device=device, args=args), self.assertRaises(SystemExit) as stopped:
                    self.execute(args)
                self.assertEqual(stopped.exception.code, 2)
                self.assertFalse(path.exists())
        self.assert_no_backend()
        self.guard_factory.assert_not_called()

    def test_new_target_requires_new_output_before_guard_acquisition(self):
        existing = self.root / "existing.json"
        existing.write_text("preserve report", encoding="utf-8")
        for device in TARGETS:
            base = ["--diagnose-device", device, "--acknowledge-experimental"]
            for suffix in ([], ["--diagnostic-output", str(existing)],
                           ["--diagnostic-output", str(self.root / "missing" / "report.json")]):
                with self.subTest(device=device, suffix=suffix), self.assertRaises(SystemExit) as stopped:
                    self.execute(base + suffix)
                self.assertEqual(stopped.exception.code, 2)
        self.assert_no_backend()
        self.guard_factory.assert_not_called()
        self.assertEqual(existing.read_text(encoding="utf-8"), "preserve report")

    def test_new_target_failures_discard_even_a_successful_reader_result(self):
        for device in TARGETS:
            for failure in ("scope", "close", "close-error"):
                D._retained_guard = None
                self.guard.reset_mock()
                @contextmanager
                def scope(owner):
                    yield SimpleNamespace(failed=failure == "scope")
                self.runtime.provider_scope.side_effect = scope
                self.runtime.close.return_value = failure != "close"
                self.runtime.close.side_effect = OSError("cleanup") if failure == "close-error" else None
                result, code = D.run(device, acknowledged=True, load_backend=self.backend,
                                     create_runtime=self.factory, create_provider=self.provider_factory)
                with self.subTest(device=device, failure=failure):
                    self.assertEqual(code, 1)
                    self.assertIsNone(result["reading"])
                if failure == "scope":
                    self.guard.close.assert_called_once_with()
                    self.assertIsNone(D._retained_guard)
                else:
                    self.guard.close.assert_not_called()
                    self.assertIs(D._retained_guard, self.guard)

    def test_direct_new_target_invalid_ack_or_transport_never_loads_backend(self):
        for device in TARGETS:
            for ack, transport in ((False, None), (1, None), (True, "receiver"), (True, "wired")):
                result, code = D.run(device, acknowledged=ack, transport=transport,
                                     load_backend=self.backend, create_runtime=self.factory,
                                     create_provider=self.provider_factory)
                self.assertEqual(code, 1)
                self.assertIsNone(result["reading"])
        self.assert_no_backend()
        self.guard_factory.assert_not_called()

    def test_existing_instance_or_guard_errors_do_not_import_backend_or_provider(self):
        for failure in (False, OSError("create mutex unavailable"), RuntimeError("guard failure")):
            self.guard.acquire.side_effect = failure if isinstance(failure, Exception) else None
            self.guard.acquire.return_value = failure
            for device in TARGETS:
                result, code = D.run(device, acknowledged=True, load_backend=self.backend,
                                     create_runtime=self.factory, create_provider=self.provider_factory)
                self.assertEqual(code, 1)
                self.assertIsNone(result["reading"])
        self.assert_no_backend()
        self.assertEqual(self.guard.close.call_count, len(TARGETS) * 3)

    def test_guard_close_failure_discards_a_successful_reading(self):
        for failure in (False, OSError("close failed")):
            self.guard.close.side_effect = failure if isinstance(failure, Exception) else None
            self.guard.close.return_value = failure
            for device in TARGETS:
                D._retained_guard = None
                result, code = D.run(device, acknowledged=True, load_backend=self.backend,
                                     create_runtime=self.factory, create_provider=self.provider_factory)
                self.assertEqual(code, 1)
                self.assertIsNone(result["reading"])
                self.assertIs(D._retained_guard, self.guard)


class TargetedNativeTests(unittest.TestCase):
    def setUp(self):
        retention_patch = patch.object(D, "_retained_guard", None)
        retention_patch.start()
        self.addCleanup(retention_patch.stop)
        guard = Mock()
        guard.acquire.return_value = guard.close.return_value = True
        guard_patch = patch.object(D, "_create_instance_guard", return_value=guard)
        guard_patch.start()
        self.addCleanup(guard_patch.stop)

    def test_existing_native_readers_keep_their_exact_sourced_commands(self):
        class SonyApi(native.Api):
            ready = False
            def create_read(self, owner, size):
                request = native.Request(sonytests.STATUS if self.ready else b"")
                self.ready = False
                self.requests.append(request)
                return request
            def create_write(self, owner, data):
                self.ready = True
                return super().create_write(owner, data)
        class X3Api(native.Api):
            def __init__(self):
                super().__init__()
                self.responses = list(x3tests.replies())
            def create_read(self, owner, size):
                request = native.Request(self.responses.pop(0))
                self.requests.append(request)
                return request
        cases = [("aj159", ajazz, aj159tests.AJ159NativeApi, ReportCaps(0, 0, 65), 70),
                 ("inzone-h9ii", sony_h9ii, SonyApi, ReportCaps(64, 64, 0), 60),
                 ("logitech-pro-x3", logitech_centurion, X3Api, ReportCaps(64, 64, 0), 78)]
        for device, module, api_class, caps, expected in cases:
            api = api_class()
            runtime = TransportRuntime(api=api, timeout=.5)
            self.addCleanup(runtime.close, .5)
            metadata = native.Metadata(caps)
            with patch.object(runtime, "install"), \
                    patch.object(module, "hid", SimpleNamespace(device=lambda: HidDevice(runtime, metadata=metadata))), \
                    patch.object(module.hidlist, "enumerate", return_value=[endpoint(device)]):
                result, code = D.run(device, acknowledged=True, load_backend=lambda: object(),
                                     create_runtime=lambda: runtime)
            with self.subTest(device=device):
                self.assertEqual(code, 0, result)
                self.assertEqual(result["reading"]["percentage"], expected)
                self.assertTrue(all(owner.closed for owner in api.owners))
                self.assertTrue(all(request.disposed for request in api.requests))
                if device == "aj159":
                    self.assertEqual(metadata.features, [bytes.fromhex("00 f7") + bytes(63)])
                    self.assertEqual(api.writes, [])
                    self.assertEqual(len(api.ioctls), 1)
                    self.assertEqual(api.ioctls[0][1]["input_data"], b"\x05" + bytes(64))
                else:
                    self.assertEqual(api.writes, [sonytests.REQUEST] if device == "inzone-h9ii" else x3tests.REQUESTS)
                    self.assertEqual((api.ioctls, metadata.features), ([], []))

    def test_scope_cancellation_before_native_admission_prevents_requests(self):
        for device, module, provider_class in (("aj159", ajazz, ajazz.AjazzProvider),
                                                ("inzone-h9ii", sony_h9ii, sony_h9ii.SonyH9IIProvider),
                                                ("logitech-pro-x3", logitech_centurion, logitech_centurion.LogitechCenturionProvider)):
            api = native.Api()
            runtime = TransportRuntime(api=api, timeout=.5)
            self.addCleanup(runtime.close, .5)
            def load(_device):
                runtime.cancel_owner(D._OWNERS[_device])
                return module, provider_class()
            with patch.object(runtime, "install"), patch.object(A, "_load_provider", side_effect=load), \
                    patch.object(module, "hid", SimpleNamespace(device=lambda: HidDevice(runtime, metadata=native.Metadata()))), \
                    patch.object(module.hidlist, "enumerate", return_value=[endpoint(device)]):
                result, code = D.run(device, acknowledged=True, load_backend=lambda: object(),
                                     create_runtime=lambda: runtime)
            with self.subTest(device=device):
                self.assertEqual(code, 1)
                self.assertIsNone(result["reading"])
                self.assertEqual((api.owners, api.requests, api.writes, api.ioctls), ([], [], [], []))

    def test_pending_aj159_get_is_cancelled_reaped_and_not_published(self):
        class Api(aj159tests.AJ159NativeApi):
            def __init__(self):
                super().__init__()
                self.created = threading.Event()
            def create_ioctl(self, owner, code, **kwargs):
                request = native.Request()
                self.requests.append(request)
                self.created.set()
                return request
        api = Api()
        runtime = TransportRuntime(api=api, timeout=.5)
        self.addCleanup(runtime.close, .5)
        metadata = native.Metadata(ReportCaps(0, 0, 65))
        results = []
        with patch.object(runtime, "install"), \
                patch.object(ajazz, "hid", SimpleNamespace(device=lambda: HidDevice(runtime, metadata=metadata))), \
                patch.object(ajazz.hidlist, "enumerate", return_value=[endpoint("aj159")]):
            worker = threading.Thread(target=lambda: results.append(D.run("aj159", acknowledged=True,
                load_backend=lambda: object(), create_runtime=lambda: runtime)), daemon=True)
            worker.start()
            self.assertTrue(api.created.wait(2), "fake native GET was not created")
            runtime.cancel_owner(D._OWNERS["aj159"])
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results[0][1], 1)
        self.assertIsNone(results[0][0]["reading"])
        self.assertEqual(api.writes, [])
        self.assertEqual(len(api.requests), 1)
        self.assertTrue(api.requests[0].cancelled)
        self.assertTrue(api.requests[0].disposed)
        self.assertTrue(all(owner.closed for owner in api.owners))


class FakeKernel:
    """In-memory named-object existence and pointer-sized handle ownership."""

    def __init__(self):
        self.handles = set()
        self.error = 0
        self.next_handle = 2 ** 40
        self.CreateMutexW = Mock(side_effect=self.create)
        self.GetLastError = Mock(side_effect=lambda: self.error)
        self.set_error = Mock(side_effect=lambda value: setattr(self, "error", value))
        self.CloseHandle = Mock(side_effect=self.close)

    def create(self, security, owner, name):
        self.error = 183 if self.handles else 0
        self.next_handle += 1
        self.handles.add(self.next_handle)
        return self.next_handle

    def close(self, handle):
        self.handles.remove(handle)
        return True


def guard_for(kernel):
    return G.InstanceGuard(kernel=kernel, set_error=kernel.set_error, get_error=kernel.GetLastError)


class DiagnosticGuardTests(unittest.TestCase):
    def setUp(self):
        retention_patch = patch.object(D, "_retained_guard", None)
        retention_patch.start()
        self.addCleanup(retention_patch.stop)

    def test_every_diagnostic_rejects_existing_instance_before_backend_or_target_creation(self):
        for device in D._DEVICES:
            transport = "receiver" if device == "endgame-4k" else None
            guard = Mock()
            guard.acquire.return_value = False
            guard.close.return_value = True
            backend, runtime, provider = Mock(), Mock(), Mock()
            result, code = D.run(device, transport=transport, acknowledged=True,
                                 create_guard=lambda: guard, load_backend=backend,
                                 create_runtime=runtime, create_provider=provider)
            with self.subTest(device=device):
                self.assertEqual(code, 1)
                self.assertIsNone(result["reading"])
                guard.acquire.assert_called_once_with()
                guard.close.assert_called_once_with()
                backend.assert_not_called()
                runtime.assert_not_called()
                provider.assert_not_called()

    def test_held_diagnostic_pending_cleanup_blocks_another_diagnostic(self):
        for device in ("endgame-4k", "lamzu-tachi"):
            D._retained_guard = None
            kernel = FakeKernel()
            guard = guard_for(kernel)
            runtime, provider = Mock(), Mock()
            @contextmanager
            def scope(owner):
                yield SimpleNamespace(failed=False)
            runtime.provider_scope.side_effect = scope
            runtime.close.return_value = False
            provider.probe_experimental.return_value = {"percentage": 75}
            provider.diagnostics.return_value = []
            result, code = D.run(device, transport="receiver" if device == "endgame-4k" else None,
                                 acknowledged=True, create_guard=lambda: guard,
                                 load_backend=lambda: object(), create_runtime=lambda: runtime,
                                 create_provider=lambda: provider)
            with self.subTest(device=device):
                self.assertEqual(code, 1)
                self.assertIsNone(result["reading"])
                self.assertIs(D._retained_guard, guard)
                self.assertEqual(len(kernel.handles), 1)
                backend, factory = Mock(), Mock()
                other = "lamzu-tachi" if device == "endgame-4k" else "endgame-4k"
                _, repeated_code = D.run(other, transport="wired" if other == "endgame-4k" else None,
                                         acknowledged=True, load_backend=backend, create_guard=factory)
                self.assertEqual(repeated_code, 1)
                backend.assert_not_called()
                factory.assert_not_called()

    def test_pointer_sized_signatures_and_exact_existing_instance_name(self):
        kernel = FakeKernel()
        guard = guard_for(kernel)
        self.assertIs(kernel.CreateMutexW.restype, ctypes.c_void_p)
        self.assertEqual(kernel.CreateMutexW.argtypes, (ctypes.c_void_p, ctypes.c_int32, ctypes.c_wchar_p))
        self.assertEqual(kernel.CloseHandle.argtypes, (ctypes.c_void_p,))
        self.assertIs(kernel.GetLastError.restype, ctypes.c_uint32)
        self.assertTrue(guard.acquire())
        kernel.set_error.assert_called_once_with(0)
        kernel.GetLastError.assert_called_once_with()
        kernel.CreateMutexW.assert_called_once_with(None, False, "Global\\HaloBattery_single_instance")
        self.assertTrue(guard.close())
        self.assertGreater(kernel.CloseHandle.call_args.args[0], 2 ** 32)
        self.assertEqual(kernel.handles, set())
        self.assertTrue(guard.close())
        kernel.CloseHandle.assert_called_once()

    def test_concurrent_attempts_are_rejected_until_first_handle_closes(self):
        kernel = FakeKernel()
        first, second, third = [guard_for(kernel) for _ in range(3)]
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        self.assertTrue(second.close())
        self.assertFalse(third.acquire())
        self.assertTrue(third.close())
        self.assertTrue(first.close())
        fourth = guard_for(kernel)
        self.assertTrue(fourth.acquire())
        self.assertTrue(fourth.close())

    def test_creation_failure_and_private_error_query_exception_close_owned_handles(self):
        for failure in ("null", "query", "create"):
            kernel = FakeKernel()
            if failure == "null":
                kernel.CreateMutexW.side_effect = None
                kernel.CreateMutexW.return_value = 0
            elif failure == "query":
                kernel.GetLastError.side_effect = OSError("API error")
            else:
                kernel.CreateMutexW.side_effect = OSError("API error")
            guard = guard_for(kernel)
            with self.subTest(failure=failure), self.assertRaises(OSError):
                guard.acquire()
            self.assertTrue(guard.close())
            self.assertEqual(kernel.handles, set())
            self.assertEqual(kernel.CloseHandle.call_count, 1 if failure == "query" else 0)

    def test_fresh_creation_does_not_reuse_a_stale_last_error(self):
        kernel = FakeKernel()
        kernel.error = 183
        def create_without_setting_error(*args):
            kernel.next_handle += 1
            kernel.handles.add(kernel.next_handle)
            return kernel.next_handle
        kernel.CreateMutexW.side_effect = create_without_setting_error
        guard = guard_for(kernel)
        self.assertTrue(guard.acquire())
        self.assertTrue(guard.close())

    def test_failed_close_retains_handle_for_explicit_retry(self):
        kernel = FakeKernel()
        guard = guard_for(kernel)
        self.assertTrue(guard.acquire())
        kernel.CloseHandle.side_effect = None
        kernel.CloseHandle.return_value = False
        self.assertFalse(guard.close())
        self.assertEqual(len(kernel.handles), 1)
        kernel.CloseHandle.side_effect = kernel.close
        self.assertTrue(guard.close())
        self.assertEqual(kernel.handles, set())

    def test_guard_is_retained_until_process_exit_if_native_cleanup_is_unconfirmed(self):
        for failure in (False, OSError("native cleanup")):
            D._retained_guard = None
            kernel = FakeKernel()
            guard = guard_for(kernel)
            runtime = Mock()
            @contextmanager
            def scope(owner):
                yield SimpleNamespace(failed=False)
            runtime.provider_scope.side_effect = scope
            def cleanup(timeout):
                contender = guard_for(kernel)
                self.assertFalse(contender.acquire())
                self.assertTrue(contender.close())
                if isinstance(failure, Exception):
                    raise failure
                return failure
            runtime.close.side_effect = cleanup
            provider = Mock()
            provider.probe_experimental.return_value = {"percentage": 70}
            provider.diagnostics.return_value = []
            result, code = D.run("aj159", acknowledged=True, load_backend=lambda: object(),
                                 create_runtime=lambda: runtime, create_provider=lambda: provider,
                                 create_guard=lambda: guard)
            with self.subTest(failure=failure):
                self.assertEqual(code, 1)
                self.assertIsNone(result["reading"])
                self.assertEqual(len(kernel.handles), 1)
                self.assertIs(D._retained_guard, guard)
                kernel.CloseHandle.assert_called_once()  # rejected contender only
                backend, factory = Mock(), Mock()
                repeated, repeated_code = D.run("aj159", acknowledged=True, load_backend=backend,
                                                create_guard=factory)
                self.assertEqual(repeated_code, 1)
                self.assertIsNone(repeated["reading"])
                backend.assert_not_called()
                factory.assert_not_called()
                replacement = guard_for(kernel)
                self.assertFalse(replacement.acquire())
                self.assertTrue(replacement.close())

    def test_successful_native_cleanup_releases_guard_after_runtime_close(self):
        kernel = FakeKernel()
        guard = guard_for(kernel)
        runtime = Mock()
        @contextmanager
        def scope(owner):
            yield SimpleNamespace(failed=False)
        runtime.provider_scope.side_effect = scope
        def close(timeout):
            self.assertEqual(len(kernel.handles), 1)
            return True
        runtime.close.side_effect = close
        provider = Mock()
        provider.probe_experimental.return_value = {"percentage": 70}
        provider.diagnostics.return_value = []
        result, code = D.run("aj159", acknowledged=True, load_backend=lambda: object(),
                             create_runtime=lambda: runtime, create_provider=lambda: provider,
                             create_guard=lambda: guard)
        self.assertEqual(code, 0)
        self.assertEqual(result["reading"]["percentage"], 70)
        self.assertEqual(kernel.handles, set())
        self.assertIsNone(D._retained_guard)


if __name__ == "__main__":
    unittest.main()
