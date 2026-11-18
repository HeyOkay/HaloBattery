"""Strict Aurora source contract, acknowledgement and native fake ownership."""
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import test_experimental_hid as native
from test_lamzu_sw import entry, reply
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from providers import lamzu_sw as L
from tools import probe_lamzu_tachi as T


class ParseTests(unittest.TestCase):
    def test_both_sourced_layouts_and_level_bounds(self):
        for level in (0, 75, 100):
            data = reply(level)
            self.assertEqual(L.parse_reply(data), (level, False, True))
            self.assertEqual(L.parse_reply(data[1:]), (level, False, True))
        for level in (101, 255, -1, True, None, 75.0):
            self.assertIsNone(L.parse_reply(reply(level)))

    def test_unknown_charge_is_not_invented_as_discharging(self):
        self.assertEqual(L.parse_reply(reply(50, 1)), (50, True, True))
        for flag in (2, 7, 255):
            self.assertEqual(L.parse_reply(reply(50, flag)), (50, None, False))

    def test_short_oversized_wrong_header_echo_status_and_bad_byte_types(self):
        valid = reply(75)
        for length in range(65):
            self.assertIsNone(L.parse_reply(valid[:length]))
        for data in (valid + [0], None, "x" * 65, 65, bytes(65)):
            self.assertIsNone(L.parse_reply(data))
        for at, value in ((0, 1), (1, 0xA0), (1, 0xA2), (1, 0xA4), (1, True),
                          (3, 0), (4, 3), (6, 0x81), (7, -1), (8, 101)):
            data = list(valid)
            data[at] = value
            self.assertIsNone(L.parse_reply(data))


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.provider = L.LamzuSinowealthProvider(clock=lambda: self.now, sleep=lambda _: None)
        self.device = Mock()
        self.device.get_report_lengths.return_value = {"input": 0, "output": 0, "feature": 65}
        self.device.send_feature_report.return_value = 65
        self.device.get_feature_report.return_value = reply(75)
        self.entries = [entry()]
        for context in (
            patch.object(L, "hid", types.SimpleNamespace(device=lambda: self.device)),
            patch.object(L.hidlist, "enumerate", side_effect=lambda vid: self.entries),
        ):
            context.start()
            self.addCleanup(context.stop)

    def probe(self):
        return self.provider.probe_experimental(acknowledged=True)

    def test_normal_variants_and_unacknowledged_probe_do_no_enumeration_or_open(self):
        self.assertFalse(L.ENABLED_PIDS)
        with patch.object(L.hidlist, "enumerate") as inventory, patch.object(L, "hid") as hid:
            for env in ({}, {"HALO_PROBE": "1"}):
                with patch.dict("os.environ", env):
                    self.assertEqual(self.provider.poll(), [])
            for acknowledgement in (False, None, 1, "yes"):
                with self.assertRaises(ValueError):
                    self.provider.probe_experimental(acknowledged=acknowledgement)
            inventory.assert_not_called()
            hid.device.assert_not_called()

    def test_exact_receiver_collection_only_without_glorious_or_wired_fallback(self):
        for changes in ({"vendor_id": 0x373E}, {"product_id": 5}, {"product_id": 11},
                        {"interface_number": 1}, {"usage_page": 0xFFA0, "usage": 1},
                        {"usage": 1}, {"interface_number": True}):
            self.entries = [entry(**changes)]
            self.assertIsNone(self.probe())
        self.device.open_path.assert_not_called()
        self.device.send_feature_report.assert_not_called()

    def test_multiple_receivers_refuse_before_query_and_case_duplicates_deduplicate(self):
        self.entries = [entry(), entry(path=b"other-receiver")]
        self.assertIsNone(self.probe())
        self.device.open_path.assert_not_called()
        self.entries = [entry(path=b"TACHI"), entry(path=b"tachi")]
        self.assertEqual(self.probe()["percentage"], 75)
        self.device.open_path.assert_called_once_with(b"tachi")

    def test_cached_caps_must_carry65_bytes_and_never_trigger_alternate_size_probes(self):
        for feature in (None, False, -1, 64, 32769):
            self.device.get_report_lengths.return_value = {"feature": feature}
            self.assertIsNone(self.probe())
        self.device.send_feature_report.assert_not_called()
        self.device.get_feature_report.assert_not_called()
        self.device.get_report_lengths.return_value = {"feature": 1041}
        self.assertEqual(self.probe()["percentage"], 75)
        self.device.send_feature_report.assert_called_once_with(L.make_request())
        self.device.get_feature_report.assert_called_once_with(0, 1041)

    def test_short_send_skips_get_and_disappearance_closes_without_leaking_error(self):
        self.device.send_feature_report.return_value = 64
        self.assertIsNone(self.probe())
        self.device.get_feature_report.assert_not_called()
        self.device.close.assert_called_once_with()
        self.device.send_feature_report.return_value = 65
        for operation in ("open_path", "send_feature_report", "get_feature_report"):
            self.device.reset_mock()
            getattr(self.device, operation).side_effect = OSError("PRIVATE-PATH-SERIAL")
            self.assertIsNone(self.probe())
            self.device.close.assert_called_once_with()
            self.assertNotIn("PRIVATE", " ".join(self.provider.diagnostics()))
            getattr(self.device, operation).side_effect = None

    def test_late_valid_response_is_discarded_and_never_cached_for_another_unit(self):
        self.assertEqual(self.probe()["percentage"], 75)
        self.entries = [entry(path=b"another-unit")]
        self.device.get_feature_report.return_value = reply(95, status=0xA0)
        self.assertIsNone(self.probe())
        def late(*args):
            self.now = 10
            return reply(75)
        self.device.get_feature_report.side_effect = late
        self.assertIsNone(self.probe())
        self.assertIn("budget exhausted", " ".join(self.provider.diagnostics()))

    def test_unknown_charge_remains_unknown_in_diagnostic_result(self):
        self.device.get_feature_report.return_value = reply(50, charging=7)
        result = self.probe()
        self.assertEqual((result["percentage"], result["charging"], result["charging_known"]),
                         (50, None, False))
        self.assertEqual(result["freshness"], "unverified")

    def test_source100ms_wait_occurs_before_get(self):
        sleep = Mock()
        provider = L.LamzuSinowealthProvider(clock=lambda: self.now, sleep=sleep)
        self.assertEqual(provider.probe_experimental(acknowledged=True)["percentage"], 75)
        sleep.assert_called_once_with(.100)


class TesterTests(unittest.TestCase):
    def test_no_acknowledgement_help_or_wrong_platform_never_loads_backend(self):
        with patch.object(T, "_load_backend") as backend, patch.object(T, "_create_runtime") as factory, \
                redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            for args in ([], ["--help"]):
                with self.assertRaises(SystemExit):
                    T.main(args)
            self.assertEqual(T.run()[1], 1)
            with patch.object(T.sys, "platform", "linux"), self.assertRaises(SystemExit):
                T.main(["--acknowledge-experimental"])
            backend.assert_not_called()
            factory.assert_not_called()

    def run_fake(self, *, failed=False, clean=True, error=None):
        runtime, provider = Mock(), Mock()
        if isinstance(clean, BaseException):
            runtime.close.side_effect = clean
        else:
            runtime.close.return_value = clean
        @contextmanager
        def scope(owner):
            self.assertEqual(owner, "lamzu-tachi-local-tester")
            yield types.SimpleNamespace(failed=failed)
        runtime.provider_scope.side_effect = scope
        provider.probe_experimental.return_value = {"percentage": 75, "freshness": "unverified"}
        provider.probe_experimental.side_effect = error
        provider.diagnostics.return_value = ["real receiver behavior unverified"]
        with patch.object(T, "_load_backend", return_value=object()), \
                patch.object(T, "_create_runtime", return_value=runtime) as factory, \
                patch.object(T, "_create_provider", return_value=provider), \
                patch.object(T.sys, "platform", "win32"), redirect_stdout(io.StringIO()) as output:
            code = T.main(["--acknowledge-experimental"])
        factory.assert_called_once_with()
        runtime.install.assert_called_once()
        provider.probe_experimental.assert_called_once_with(acknowledged=True)
        runtime.close.assert_called_once_with(.5)
        return code, json.loads(output.getvalue())

    def test_only_explicit_ack_uses_one_runtime_and_outputs_no_normal_fresh_claim(self):
        code, result = self.run_fake()
        self.assertEqual(code, 0)
        self.assertTrue(result["experimental"])
        self.assertTrue(result["release_hold"])
        self.assertEqual(result["freshness"], "unverified")

    def test_failed_scope_pending_cleanup_or_private_error_discard_reading(self):
        for options in ({"failed": True}, {"clean": False},
                        {"clean": OSError("PRIVATE-CLOSE")}, {"error": OSError("PRIVATE-DEVICE")}):
            code, result = self.run_fake(**options)
            self.assertEqual(code, 1)
            self.assertIsNone(result["reading"])
            self.assertNotIn("PRIVATE", json.dumps(result))


class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.api = native.Api()
        self.metadata = native.Metadata(ReportCaps(0, 0, 65))
        self.runtime = TransportRuntime(api=self.api, timeout=.12)
        self.release = threading.Event()
        self.provider = L.LamzuSinowealthProvider(sleep=lambda _: None)
        self.api.report, self.api.report_count = bytes(reply(75)), 64
        for context in (
            patch.object(L, "hid", types.SimpleNamespace(
                device=lambda: HidDevice(self.runtime, metadata=self.metadata))),
            patch.object(L.hidlist, "enumerate", return_value=[entry()]),
        ):
            context.start()
            self.addCleanup(context.stop)

    def tearDown(self):
        self.release.set()
        self.assertTrue(self.runtime.close(.5), self.runtime.diagnostics())

    def probe(self):
        return self.provider.probe_experimental(acknowledged=True)

    def test_actual_native_adapter65_byte_zero_id_request_and_reply(self):
        with self.runtime.provider_scope("tachi-test") as scope:
            result = self.probe()
        self.assertFalse(scope.failed)
        self.assertEqual(result["percentage"], 75)
        self.assertEqual(self.metadata.features,
                         [bytes.fromhex("00 00 00 02 02 00 83") + bytes(58)])
        self.assertEqual(len(self.api.ioctls), 1)
        options = self.api.ioctls[0][1]
        self.assertEqual(options["input_data"], bytes(65))
        self.assertEqual(options["output_size"], 65)
        self.assertEqual(self.api.writes, [])
        self.assertTrue(self.api.owners[0].closed)

    def test_larger_cached_windows_buffer_uses_one_get_and_actual_canonical_count(self):
        self.metadata.report_caps = ReportCaps(0, 0, 129)
        def get(owner, code, **options):
            self.api.ioctls.append((code, options))
            # Driver reports64 bytes with zero RID retained, so the adapter
            # returns65. Allocate the full129-byte Windows buffer in the fake.
            return native.Request(bytes(reply(75)).ljust(129, b"\x00"), transferred=64)
        self.api.create_ioctl = get
        with self.runtime.provider_scope("tachi-test") as scope:
            result = self.probe()
        self.assertFalse(scope.failed)
        self.assertEqual(result["percentage"], 75)
        self.assertEqual(len(self.api.ioctls), 1)
        self.assertEqual(self.api.ioctls[0][1]["input_data"], bytes(129))
        self.assertEqual(self.api.ioctls[0][1]["output_size"], 129)
        self.assertEqual(self.metadata.features,
                         [(bytes.fromhex("00 00 00 02 02 00 83") + bytes(58)).ljust(129, b"\x00")])
        # A full-length response is not silently truncated into a 65-byte one.
        self.api.create_ioctl = lambda *args, **kwargs: native.Request(
            bytes(reply(75)).ljust(129, b"\x00"), transferred=128)
        with self.runtime.provider_scope("tachi-test"):
            self.assertIsNone(self.probe())

    def test_pending_get_keeps_owner_and_discards_late_success(self):
        pending = native.Request(ignore_cancel=True)
        self.api.create_ioctl = Mock(return_value=pending)
        with self.runtime.provider_scope("tachi-test") as scope:
            self.assertIsNone(self.probe())
        self.assertTrue(scope.failed)
        self.assertFalse(self.runtime.transport_idle("tachi-test"))
        self.assertFalse(self.api.owners[0].closed)
        replacement = HidDevice(self.runtime, metadata=self.metadata)
        with self.assertRaises(OSError):
            replacement.open_path(b"tachi")
        self.assertEqual(len(self.api.owners), 1)
        # A genuine late Windows success: zero RID retained in the output,
        # driver byte count64, so the adapter's zero-RID adjustment yields65.
        pending.buffer.raw = bytes(reply(75)).ljust(len(pending.buffer), b"\x00")
        pending.complete(bytes(reply(75))[:-1])
        self.wait_idle()
        self.assertTrue(self.api.owners[0].closed)

    def test_blocked_close_cannot_publish_even_valid_battery(self):
        original_open = self.api.open_path
        def open_device(*args, **kwargs):
            owner = original_open(*args, **kwargs)
            original_close = owner.close
            def close():
                self.release.wait(2)
                return original_close()
            owner.close = close
            return owner
        self.api.open_path = open_device
        with self.runtime.provider_scope("tachi-test") as scope, self.assertRaises(OSError):
            self.probe()
        self.assertTrue(scope.failed)
        self.assertFalse(self.runtime.transport_idle("tachi-test"))
        self.release.set()
        self.wait_idle()
        self.assertTrue(self.api.owners[0].closed)

    def wait_idle(self):
        until = time.monotonic() + 1
        while not self.runtime.transport_idle("tachi-test") and time.monotonic() < until:
            time.sleep(.002)
        self.assertTrue(self.runtime.transport_idle("tachi-test"))


if __name__ == "__main__":
    unittest.main()
