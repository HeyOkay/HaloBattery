"""Standalone acknowledgement and fake native ownership; no hardware/processes."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import test_experimental_hid as native
from test_endgame_4k import ORIGINAL_ID, LEVEL75, entry
from halo import device_diagnostics as D
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from providers import endgame_4k as E, endgame4k_protocol as P
from tools import probe_endgame_4k as T


class TesterTests(unittest.TestCase):
    def setUp(self):
        self.guard = Mock()
        self.guard.acquire.return_value = self.guard.close.return_value = True
        for context in (patch.object(D, "_create_instance_guard", return_value=self.guard),
                        patch.object(D, "_retained_guard", None)):
            context.start()
            self.addCleanup(context.stop)

    def test_missing_acknowledgement_transport_or_help_never_loads_native_backend(self):
        with patch.object(T, "_load_backend") as backend, \
                patch.object(T, "_create_runtime") as factory, redirect_stderr(io.StringIO()), \
                redirect_stdout(io.StringIO()):
            for args in ([], ["--transport", "receiver"], ["--acknowledge-experimental"],
                         ["--help"]):
                with self.assertRaises(SystemExit):
                    T.main(args)
            self.assertEqual(T.run("receiver")[1], 1)
            backend.assert_not_called()
            factory.assert_not_called()

    def test_non_windows_refuses_before_backend(self):
        with patch.object(T.sys, "platform", "linux"), patch.object(T, "_load_backend") as backend, \
                redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            T.main(["--acknowledge-experimental", "--transport", "receiver"])
        backend.assert_not_called()

    def run_fake(self, *, failed=False, clean=True, error=None):
        self.runtime, self.provider = Mock(), Mock()
        if isinstance(clean, BaseException):
            self.runtime.close.side_effect = clean
        else:
            self.runtime.close.return_value = clean
        @contextmanager
        def scope(owner):
            self.assertEqual(owner, "endgame-4k-local-tester")
            yield types.SimpleNamespace(failed=failed)
        self.runtime.provider_scope.side_effect = scope
        self.provider.probe_experimental.return_value = {"percentage": 75, "freshness": "unverified"}
        self.provider.diagnostics.return_value = ["experimental; reply freshness unverified"]
        if error is not None:
            self.provider.probe_experimental.side_effect = error
        with patch.object(D, "_retained_guard", None), \
                patch.object(T, "_load_backend", return_value=object()), \
                patch.object(T, "_create_runtime", return_value=self.runtime) as factory, \
                patch.object(T, "_create_provider", return_value=self.provider), \
                patch.object(T.sys, "platform", "win32"), redirect_stdout(io.StringIO()) as out:
            code = T.main(["--acknowledge-experimental", "--transport", "receiver"])
        factory.assert_called_once_with()
        self.runtime.install.assert_called_once()
        self.provider.probe_experimental.assert_called_once_with("receiver", acknowledged=True)
        self.runtime.close.assert_called_once_with(.5)
        return code, json.loads(out.getvalue())

    def test_explicit_tester_uses_one_runtime_scope_and_marks_unverified(self):
        code, output = self.run_fake()
        self.assertEqual(code, 0)
        self.assertTrue(output["experimental"])
        self.assertTrue(output["release_hold"])
        self.assertEqual(output["freshness"], "unverified")
        self.assertEqual(output["reading"]["percentage"], 75)

    def test_failed_scope_or_pending_cleanup_discards_even_valid_looking_reading(self):
        for options in ({"failed": True}, {"clean": False}, {"clean": OSError("PRIVATE-CLEANUP")}):
            code, output = self.run_fake(**options)
            self.assertEqual(code, 1)
            self.assertIsNone(output["reading"])
            self.assertNotIn("PRIVATE", json.dumps(output))

    def test_device_exception_text_is_never_printed(self):
        code, output = self.run_fake(error=OSError("PRIVATE-HID-PATH-SERIAL"))
        self.assertEqual(code, 1)
        self.assertIsNone(output["reading"])
        self.assertNotIn("PRIVATE", json.dumps(output))


class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.api = native.Api()
        self.metadata = native.Metadata(ReportCaps(0, 0, 1041))
        self.runtime = TransportRuntime(api=self.api, timeout=.12)
        self.provider = E.Endgame4kProvider()
        self.release = threading.Event()
        self.devices = []
        original_send = self.metadata.send_feature
        def send(owner, data):
            result = original_send(owner, data)
            self.api.report = ORIGINAL_ID if data[1] == 14 else LEVEL75
            self.api.report_count = len(self.api.report)
            return result
        self.metadata.send_feature = send
        def device():
            dev = HidDevice(self.runtime, metadata=self.metadata)
            self.devices.append(dev)
            return dev
        for context in (
            patch.object(E, "hid", types.SimpleNamespace(device=device)),
            patch.object(E.hidlist, "enumerate", return_value=[entry()]),
            patch.object(E, "Reader", side_effect=lambda dev: P.Reader(dev, sleep=lambda _: None)),
        ):
            context.start()
            self.addCleanup(context.stop)

    def tearDown(self):
        self.release.set()
        self.assertTrue(self.runtime.close(.5), self.runtime.diagnostics())

    def probe(self):
        return self.provider.probe_experimental("receiver", acknowledged=True)

    def test_actual_adapter_large_caps_matches_hidapi_without_a0_or_other_requests(self):
        with self.runtime.provider_scope("endgame-local") as scope:
            result = self.probe()
        self.assertFalse(scope.failed)
        self.assertEqual(result["percentage"], 75)
        self.assertEqual([frame[:4] for frame in self.metadata.features],
                         [bytes.fromhex("a1 0e 00 00"), bytes.fromhex("a1 b4 00 00")])
        self.assertTrue(all(len(frame) == 1041 for frame in self.metadata.features))
        self.assertTrue(all(not any(frame[4:]) for frame in self.metadata.features))
        self.assertEqual(len(self.api.ioctls), 2)
        for code, options in self.api.ioctls:
            self.assertEqual(options["input_data"], b"\xa1" + bytes(63))
            self.assertEqual(options["output_size"], 64)
        self.assertEqual(self.api.writes, [])
        self.assertTrue(all(owner.closed for owner in self.api.owners))

    def test_blocked_send_is_bounded_and_path_remains_owned_until_it_returns(self):
        def blocked(owner, data):
            self.metadata.features.append(data)
            self.release.wait(2)
            return True
        self.metadata.send_feature = blocked
        started = time.monotonic()
        with self.runtime.provider_scope("endgame-local") as scope:
            self.assertIsNone(self.probe())
        self.assertTrue(scope.failed)
        self.assertLess(time.monotonic() - started, 1)
        self.assertFalse(self.runtime.transport_idle("endgame-local"))
        self.assertFalse(self.api.owners[0].closed)
        replacement = HidDevice(self.runtime, metadata=self.metadata)
        with self.assertRaises(OSError):
            replacement.open_path(b"receiver")
        self.assertEqual(len(self.api.owners), 1)
        self.assertEqual(len(self.metadata.features), 1)
        self.assertEqual(self.api.ioctls, [])
        self.release.set()
        self.wait_idle()
        self.assertTrue(self.api.owners[0].closed)

    def test_pending_get_cannot_publish_late_success_or_release_ownership_early(self):
        pending = native.Request(ignore_cancel=True)
        self.api.create_ioctl = Mock(return_value=pending)
        with self.runtime.provider_scope("endgame-local") as scope:
            self.assertIsNone(self.probe())
        self.assertTrue(scope.failed)
        self.assertFalse(self.runtime.transport_idle("endgame-local"))
        self.assertFalse(self.api.owners[0].closed)
        self.assertEqual(len(self.metadata.features), 1)
        pending.complete(ORIGINAL_ID)
        self.wait_idle()
        self.assertTrue(self.api.owners[0].closed)

    def test_blocked_open_and_close_keep_native_ownership_and_never_publish(self):
        # Existing fixed native workers perform both simulations, with no
        # disposable children or replacement worker threads.
        original_open = self.api.open_path
        def blocked_open(*args, **kwargs):
            self.release.wait(2)
            return original_open(*args, **kwargs)
        self.api.open_path = blocked_open
        with self.runtime.provider_scope("endgame-local") as scope:
            self.assertIsNone(self.probe())
        self.assertTrue(scope.failed)
        self.assertEqual(self.metadata.features, [])
        self.release.set()
        self.wait_idle()
        self.assertTrue(all(owner.closed for owner in self.api.owners))
        self.release.clear()
        def open_with_blocked_close(*args, **kwargs):
            owner = original_open(*args, **kwargs)
            original_close = owner.close
            def close():
                self.release.wait(2)
                return original_close()
            owner.close = close
            return owner
        self.api.open_path = open_with_blocked_close
        with self.runtime.provider_scope("endgame-local") as scope:
            with self.assertRaises(OSError):
                self.probe()
        self.assertTrue(scope.failed)
        self.assertFalse(self.runtime.transport_idle("endgame-local"))
        self.release.set()
        self.wait_idle()
        self.assertTrue(all(owner.closed for owner in self.api.owners))

    def wait_idle(self):
        until = time.monotonic() + 1
        while not self.runtime.transport_idle("endgame-local") and time.monotonic() < until:
            time.sleep(.002)
        self.assertTrue(self.runtime.transport_idle("endgame-local"))


if __name__ == "__main__":
    unittest.main()
