"""Normal startup guard checks; no real app, kernel mutex or HID access."""
import ast
import ctypes
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from halo import diagnostic_guard as G
from test_targeted_device_diagnostics import FakeKernel


class StartupInstanceGuardTests(unittest.TestCase):
    def setUp(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "halo_battery.pyw").read_text(encoding="utf-8"))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("single_instance", "main")]
        self.kernel = FakeKernel()
        self.guard_type = G.InstanceGuard
        self.factory = Mock(side_effect=self.new_guard)
        self.env = dict(sys=SimpleNamespace(platform="win32", argv=["app"], stderr=None),
                        log=Mock(), _app_instance_guard=None, VERSION="test", PRETEND_VERSION="",
                        flyout=Mock(), updates=Mock(), App=Mock(), set_app_id=Mock(),
                        verification_stop_file=Mock(return_value=True))
        self.env["updates"].parse_finish_args.return_value = None
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "startup_guard_test", "exec"), self.env)
        # Also contain the old implementation for the before-fix regression run.
        for context in (patch.object(G, "InstanceGuard", self.factory),
                        patch.object(ctypes, "windll", SimpleNamespace(kernel32=self.kernel), create=True)):
            context.start()
            self.addCleanup(context.stop)

    def new_guard(self):
        return self.guard_type(kernel=self.kernel, set_error=self.kernel.set_error,
                               get_error=self.kernel.GetLastError)

    def test_creation_denied_stops_before_app_initialization(self):
        self.kernel.CreateMutexW.side_effect = lambda *args: 0
        self.kernel.error = 5
        self.env["main"]()
        self.env["set_app_id"].assert_not_called()
        self.env["App"].assert_not_called()
        self.assertIsNone(self.env["_app_instance_guard"])
        self.env["log"].error.assert_called_once()

    def test_guard_constructor_failure_is_private_and_stops_startup(self):
        self.factory.side_effect = PermissionError("PRIVATE-PATH")
        self.env["main"]()
        self.env["App"].assert_not_called()
        self.assertNotIn("PRIVATE", str(self.env["log"].mock_calls))
        self.env["log"].error.assert_called_once()

    def test_acquisition_error_is_private(self):
        guard = Mock()
        guard.acquire.side_effect = OSError("PRIVATE-NATIVE-ERROR")
        self.factory.side_effect = None
        self.factory.return_value = guard
        self.assertFalse(self.env["single_instance"]())
        self.assertNotIn("PRIVATE", str(self.env["log"].mock_calls))
        self.assertIsNone(self.env["_app_instance_guard"])

    def test_existing_diagnostic_closes_only_the_rejected_handle(self):
        diagnostic = self.new_guard()
        self.assertTrue(diagnostic.acquire())
        handles = set(self.kernel.handles)
        self.assertFalse(self.env["single_instance"]())
        self.assertEqual(self.kernel.handles, handles)
        self.kernel.CloseHandle.assert_called_once()
        self.assertIsNone(self.env["_app_instance_guard"])
        diagnostic.close()

    def test_success_retains_one_pointer_sized_handle_and_is_idempotent(self):
        self.assertTrue(self.env["single_instance"]())
        guard = self.env["_app_instance_guard"]
        self.assertIsNotNone(guard)
        self.assertGreater(next(iter(self.kernel.handles)), 2 ** 32)
        self.assertTrue(self.env["single_instance"]())
        self.factory.assert_called_once()
        self.kernel.CloseHandle.assert_not_called()
        guard.close()

    def test_non_windows_keeps_existing_no_guard_behavior(self):
        self.env["sys"].platform = "linux"
        self.assertTrue(self.env["single_instance"]())
        self.factory.assert_not_called()

    def test_rejected_handle_cleanup_failure_never_admits_startup(self):
        diagnostic = self.new_guard()
        self.assertTrue(diagnostic.acquire())
        self.kernel.CloseHandle.side_effect = None
        self.kernel.CloseHandle.return_value = False
        self.env["main"]()
        self.env["App"].assert_not_called()
        self.assertIsNone(self.env["_app_instance_guard"])
        self.env["log"].error.assert_called_once()

    def test_app_error_does_not_drop_successful_ownership(self):
        self.env["App"].return_value.run.side_effect = RuntimeError("synthetic app failure")
        with self.assertRaises(RuntimeError):
            self.env["main"]()
        self.assertIsNotNone(self.env["_app_instance_guard"])
        self.kernel.CloseHandle.assert_not_called()
        self.env["_app_instance_guard"].close()
