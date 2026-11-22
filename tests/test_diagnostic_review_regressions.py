"""Review regressions: no devices, network, real processes or Windows windows."""
import ast
from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from halo import device_diagnostics as D, device_test_adapters as A, diagnostic_guard as G
from providers import logitech_centurion as X


class OfflineOutputTests(unittest.TestCase):
    def test_real_offline_exception_produces_valid_nonempty_json_and_cleans_up(self):
        reader = X.LogitechCenturionProvider()
        reader._read_x3 = Mock(side_effect=X.Offline("PRIVATE-PATH"))
        endpoint = dict(vendor_id=0x046D, product_id=0x0B45, interface_number=3,
                        usage_page=0xFFA0, usage=1, path=b"test-only")
        runtime, guard = Mock(), Mock()
        runtime.close.return_value = guard.acquire.return_value = guard.close.return_value = True
        @contextmanager
        def scope(owner):
            yield SimpleNamespace(failed=False)
        runtime.provider_scope.side_effect = scope
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "status.json"
            with patch.object(D, "_retained_guard", None), \
                    patch.object(D, "_create_instance_guard", return_value=guard), \
                    patch.object(D, "_load_backend", return_value=object()), \
                    patch.object(D, "_create_runtime", return_value=runtime), \
                    patch.object(A, "_load_provider", return_value=(X, reader)), \
                    patch.object(X.hidlist, "enumerate", return_value=[endpoint]), \
                    patch.object(sys, "platform", "win32"), \
                    patch.object(sys, "frozen", True, create=True), redirect_stdout(io.StringIO()):
                code = D.main(["--diagnose-device", "logitech-pro-x3", "--acknowledge-experimental",
                               "--diagnostic-output", str(output)])
            text = output.read_text(encoding="utf-8")
            result = json.loads(text)
        self.assertEqual(code, 1)
        self.assertIsNone(result["reading"])
        self.assertTrue(result["release_hold"])
        self.assertIn("offline", " ".join(result["diagnostics"]))
        self.assertNotIn("PRIVATE", text)
        runtime.close.assert_called_once_with(.5)
        guard.close.assert_called_once()


class GenericProbeLeaseTests(unittest.TestCase):
    def main_globals(self):
        path = Path(__file__).resolve().parents[1] / "halo_battery.pyw"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
        env = dict(VERSION="test", PRETEND_VERSION="", sys=SimpleNamespace(argv=["app", "--probe"], stderr=io.StringIO()),
                   flyout=SimpleNamespace(enable_dpi_awareness=Mock()), probe=Mock(), log=Mock())
        exec(compile(ast.Module(body=[main], type_ignores=[]), str(path), "exec"), env)
        return env

    def test_existing_app_or_diagnostic_blocks_probe_before_any_poll(self):
        env, guard = self.main_globals(), Mock()
        guard.acquire.return_value = False
        with patch.object(G, "InstanceGuard", return_value=guard):
            with self.assertRaises(SystemExit) as failure:
                env["main"]()
        self.assertNotEqual(failure.exception.code, 0)
        self.assertIn("another diagnostic is running", env["sys"].stderr.getvalue())
        env["probe"].assert_not_called()
        guard.close.assert_called_once()

    def test_probe_retains_exclusive_lease_through_normal_return_and_native_error(self):
        for failure in (None, OSError("fake native error")):
            env, guard = self.main_globals(), Mock()
            guard.acquire.return_value = True
            env["probe"].side_effect = failure
            with patch.object(G, "InstanceGuard", return_value=guard):
                if failure:
                    with self.assertRaises(OSError):
                        env["main"]()
                else:
                    env["main"]()
            self.assertIs(env["_probe_instance_guard"], guard)
            env["probe"].assert_called_once()
            guard.close.assert_not_called()

    def test_guard_creation_failure_never_polls(self):
        env = self.main_globals()
        with patch.object(G, "InstanceGuard", side_effect=PermissionError("access denied")):
            with self.assertRaises(SystemExit) as failure:
                env["main"]()
        self.assertNotEqual(failure.exception.code, 0)
        self.assertIn("exclusive device access unavailable", env["sys"].stderr.getvalue())
        env["probe"].assert_not_called()

    def test_missing_or_unusable_console_still_fails_closed(self):
        for console in (None, Mock(write=Mock(side_effect=OSError("closed console")))):
            env, guard = self.main_globals(), Mock()
            env["sys"].stderr = console
            guard.acquire.return_value = False
            with patch.object(G, "InstanceGuard", return_value=guard), self.assertRaises(SystemExit) as failure:
                env["main"]()
            self.assertNotEqual(failure.exception.code, 0)
            env["probe"].assert_not_called()
