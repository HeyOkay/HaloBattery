"""Frozen/source diagnostic entry points stop before normal startup and native I/O."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from halo import device_diagnostics as D
from tools import probe_endgame_4k, probe_lamzu_tachi


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "halo_battery.pyw"
ENTRY_CODE = compile(ENTRY.read_text(encoding="utf-8"), str(ENTRY), "exec")


class NormalStartupReached(Exception):
    pass


class DiagnosticEntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="halo-diagnostic-entry-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = Mock(return_value=object())
        self.runtime = Mock()
        self.runtime.close.return_value = True
        @contextmanager
        def scope(owner):
            self.assertIn(owner, ("endgame-4k-local-tester", "lamzu-tachi-local-tester"))
            yield types.SimpleNamespace(failed=False)
        self.runtime.provider_scope.side_effect = scope
        self.factory = Mock(return_value=self.runtime)
        self.provider = Mock()
        self.provider.probe_experimental.return_value = {"percentage": 75, "freshness": "unverified"}
        self.provider.diagnostics.return_value = ["experimental; freshness unverified"]
        self.provider_factory = Mock(return_value=self.provider)

    def execute(self, args, *, stdout=None, mkdir_error=None):
        real_import = __import__
        def guarded_import(name, *positional, **keywords):
            if (name == "providers" or name.startswith("providers.")
                    or name in ("hid", "pystray", "halo.hid_backend", "halo.hid_transport")):
                raise AssertionError("unexpected backend/provider/UI import: " + name)
            return real_import(name, *positional, **keywords)
        with patch.object(D, "_load_backend", self.backend), \
                patch.object(D, "_create_runtime", self.factory), \
                patch.object(D, "_create_provider", self.provider_factory), \
                patch.object(D.sys, "frozen", True, create=True), \
                patch.object(D.sys, "executable", str(self.root / "HaloBattery.exe")), \
                patch.object(D.sys, "argv", ["HaloBattery.exe", *args]), \
                patch.object(D.sys, "platform", "win32"), \
                patch("os.makedirs", side_effect=mkdir_error or AssertionError("profile startup reached")), \
                patch("logging.handlers.RotatingFileHandler", side_effect=AssertionError("log opened")), \
                patch("os.startfile", side_effect=AssertionError("editor opened"), create=True), \
                patch("builtins.__import__", side_effect=guarded_import), \
                patch("threading.Thread.start", side_effect=AssertionError("extra worker started")), \
                redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            exec(ENTRY_CODE, {"__name__": "__main__", "__file__": str(ENTRY)})

    def assert_no_backend(self):
        self.backend.assert_not_called()
        self.factory.assert_not_called()
        self.provider_factory.assert_not_called()

    def test_standalone_short_and_long_help_exit_before_profile_backend_or_ui(self):
        for args in (["-h"], ["--help"]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as stopped:
                self.execute(args)
            self.assertEqual(stopped.exception.code, 0)
        self.assert_no_backend()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_missing_ack_help_invalid_or_mixed_arguments_never_touch_output_or_backend(self):
        path = self.root / "report.json"
        cases = [
            (["--diagnose-device", "endgame-4k", "--transport", "receiver"], 2),
            (["--diagnose-device", "lamzu-tachi"], 2),
            (["--diagnose-device", "lamzu-tachi", "--ack"], 2),
            (["--diagnose-devicee", "lamzu-tachi"], 2),
            (["--diagnos-device", "lamzu-tachi"], 2),
            (["--diagnose", "lamzu-tachi"], 2),
            (["--diagnostic-outpu", str(path)], 2),
            (["--acknowledge-experimenta"], 2),
            (["--acknowledge-experimental-extra"], 2),
            (["--acknowledge"], 2),
            (["--diagnose-device", "endgame-4k", "--help"], 0),
            (["--help"], 0),
            (["--acknowledge-experimental"], 2),
            (["--diagnose-device", "bad", "--acknowledge-experimental"], 2),
            (["--diagnose-device", "endgame-4k", "--acknowledge-experimental"], 2),
            (["--diagnose-device", "lamzu-tachi", "--transport", "wired",
              "--acknowledge-experimental"], 2),
            (["--diagnose-device", "lamzu-tachi", "--acknowledge-experimental", "--probe"], 2),
        ]
        for args, code in cases:
            with self.subTest(args=args), self.assertRaises(SystemExit) as stopped:
                self.execute([*args, "--diagnostic-output", str(path)])
            self.assertEqual(stopped.exception.code, code)
            self.assertFalse(path.exists())
        self.assert_no_backend()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_frozen_requires_explicit_available_new_output_before_backend(self):
        existing = self.root / "existing.json"
        existing.write_text("retain existing report", encoding="utf-8")
        base = ["--diagnose-device", "lamzu-tachi", "--acknowledge-experimental"]
        for suffix in ([], ["--diagnostic-output", str(existing)],
                       ["--diagnostic-output", str(self.root / "missing" / "report.json")],
                       ["--diagnostic-output", str(self.root)]):
            with self.subTest(suffix=suffix), self.assertRaises(SystemExit) as stopped:
                self.execute(base + suffix)
            self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(existing.read_text(encoding="utf-8"), "retain existing report")
        self.assert_no_backend()

    def test_diagnostic_family_typo_alone_cannot_fall_through_to_profile_startup(self):
        # No correctly spelled diagnostic switch accompanies these requests:
        # an exact-options-only dispatcher would enter the real app startup.
        for args in (["--diagnose-devicee", "lamzu-tachi"],
                     ["--diagnos-device", "lamzu-tachi"],
                     ["--diagnose", "lamzu-tachi"],
                     ["--diagnostic-outpu", str(self.root / "absent.json")],
                     ["--acknowledge-experimenta"],
                     ["--acknowledge-experimental-extra"],
                     ["--acknowledge"]):
            with self.subTest(args=args), self.assertRaises(SystemExit) as stopped:
                self.execute(args)
            self.assertEqual(stopped.exception.code, 2)
        self.assert_no_backend()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_acknowledged_frozen_commands_share_one_runner_and_bypass_normal_startup(self):
        for device, suffix, arguments in (
            ("endgame-4k", ["--transport", "receiver"], ("receiver",)),
            ("endgame-4k", ["--transport", "wired"], ("wired",)),
            ("lamzu-tachi", [], ()),
        ):
            path = self.root / f"{device}-{len(list(self.root.iterdir()))}.json"
            for value in (self.backend, self.factory, self.provider_factory,
                          self.runtime, self.provider):
                value.reset_mock()
            with self.subTest(device=device, suffix=suffix), self.assertRaises(SystemExit) as stopped:
                self.execute(["--diagnose-device", device, *suffix,
                              "--acknowledge-experimental", "--diagnostic-output", str(path)])
            self.assertEqual(stopped.exception.code, 0)
            result = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual((result["experimental"], result["release_hold"], result["freshness"]),
                             (True, True, "unverified"))
            self.backend.assert_called_once_with()
            self.factory.assert_called_once_with()
            self.provider_factory.assert_called_once_with(device)
            self.runtime.install.assert_called_once_with(self.backend.return_value)
            self.provider.probe_experimental.assert_called_once_with(*arguments, acknowledged=True)
            self.runtime.close.assert_called_once_with(.5)

    def test_normal_flags_do_not_invoke_diagnostic_factories(self):
        # Stop at the first ordinary profile step, before real log/HID/providers.
        # Existing provider tests separately prove both held poll() paths do no I/O.
        for args in ([], ["--probe"], ["--finish-update"],
                     ["--pretend-version", "1.2.3"], ["--legacy-native-io"]):
            with self.subTest(args=args), self.assertRaises(NormalStartupReached):
                self.execute(args, mkdir_error=NormalStartupReached())
        self.assert_no_backend()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_source_wrappers_keep_api_and_reuse_shared_runner(self):
        result = ({"reading": None}, 1)
        with patch.object(D, "run", return_value=result) as shared:
            self.assertEqual(probe_endgame_4k.run("receiver", acknowledged=True), result)
            self.assertEqual(shared.call_args.args, ("endgame-4k",))
            self.assertEqual(shared.call_args.kwargs["transport"], "receiver")
            self.assertIs(shared.call_args.kwargs["create_provider"], probe_endgame_4k._create_provider)
            self.assertEqual(probe_lamzu_tachi.run(acknowledged=True), result)
            self.assertEqual(shared.call_args.args, ("lamzu-tachi",))
            self.assertIs(shared.call_args.kwargs["create_provider"], probe_lamzu_tachi._create_provider)

    def test_no_ack_direct_runner_rejects_all_non_true_values_before_factories(self):
        for device, transport in (("endgame-4k", "receiver"), ("lamzu-tachi", None)):
            for acknowledgement in (False, None, 1, "true"):
                with self.subTest(device=device, acknowledgement=acknowledgement):
                    result, code = D.run(device, transport=transport, acknowledged=acknowledgement,
                                         load_backend=self.backend, create_runtime=self.factory,
                                         create_provider=self.provider_factory)
                    self.assertEqual(code, 1)
                    self.assertIsNone(result["reading"])
        self.assert_no_backend()

    def test_standard_build_does_not_install_a_diagnostic_qa_hook(self):
        recipe = (ROOT / "tools" / "build_exe.py").read_text(encoding="utf-8")
        self.assertIn("runtime_hooks=[]", recipe)
        self.assertNotIn("diagnostic_qa", recipe)
        self.assertNotIn("HALO_DIAGNOSTIC_QA", Path(D.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
