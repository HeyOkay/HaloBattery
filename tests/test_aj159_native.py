"""AJ159 F7/05 protocol and fake native-scope regression, with no hardware.

Owner #289 test4 diagnostics34882030 L46-50: SET caller33, refused GET33,
then GET05/65 ->05 00 00 46 01 00 01 02 (70%). Its recorded12-byte prefix
is padded with synthetic zeros below. Independent100% and not-ready frames:
Aiacos/ajazz-control-center03a29f7a90dd32057ca7cd0d6e6fa8f13d5400f3,
capture-evidence.md L54-91; aj_series.cpp L225-264,290-312.
"""
from contextlib import contextmanager
from types import SimpleNamespace
import time
import unittest
from unittest import mock

import test_experimental_hid as native
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from halo.native_io import Completion
from halo.polling import PollingRuntime
from providers import ajazz


OWNER_70 = bytes.fromhex("05 00 00 46 01 00 01 02 00 00 00 00") + bytes(53)
SOURCE_100 = bytes.fromhex("05 00 00 64 01 01 01 02") + bytes(57)
NOT_READY = bytes.fromhex("05 00 00 00 01 01 01 02") + bytes(57)
COMPLETE = object()
ENTRY = dict(vendor_id=0x3151, product_id=0x402D, interface_number=2,
             usage_page=0xFFFF, usage=2, path=b"fake-aj159", product_string="AJAZZ 2.4G 8K")


class CapsDevice:
    """Only the cached caps API; no report descriptor API or native device."""
    def __init__(self, length=65, reply=OWNER_70, wrote=COMPLETE, error=None):
        self.length, self.reply, self.wrote, self.error = length, reply, wrote, error
        self.opened, self.sent, self.gets, self.closed = [], [], [], 0

    def open_path(self, path):
        self.opened.append(path)
        if self.error == "open":
            raise OSError("device disappeared")

    def get_report_lengths(self):
        return {"feature": self.length}

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        if self.error == "send":
            raise OSError("feature refused")
        return len(data) if self.wrote is COMPLETE else self.wrote

    def get_feature_report(self, report, length):
        self.gets.append((report, length))
        if self.error == "get":
            raise OSError("device disappeared")
        return self.reply[:length] if isinstance(self.reply, bytes) else self.reply

    def read(self, *args):
        raise AssertionError("AJ159 must not use input read")

    def write(self, *args):
        raise AssertionError("AJ179 output command is not AJ159's protocol")

    def close(self):
        self.closed += 1


class AJ159ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.provider = ajazz.AjazzProvider()
        self.device = CapsDevice()
        self.entries = [dict(ENTRY)]
        self.factory = mock.Mock(side_effect=lambda: self.device)
        self.patches = [mock.patch.object(ajazz, "hid", SimpleNamespace(device=self.factory)),
                        mock.patch.object(ajazz.hidlist, "enumerate",
                                          side_effect=lambda vid: self.entries if vid == 0x3151 else []),
                        mock.patch.object(ajazz, "time", SimpleNamespace(
                            time=lambda: self.now, sleep=mock.Mock()))]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_captured_level_and_unknown_charging_use_one_sourced_exchange(self):
        [status] = self.provider.poll()
        self.assertEqual((status.key, status.level, status.online, status.charging_known),
                         ("ajazz:402d", 70, True, False))
        self.assertFalse(status.charging)
        self.assertEqual(self.device.sent, [bytes.fromhex("00 f7") + bytes(63)])
        self.assertEqual(self.device.gets, [(0x05, 65)])
        ajazz.time.sleep.assert_called_once_with(0.03)
        self.assertEqual(self.device.closed, 1)

    def test_caps_select_physical_size_without_descriptor_or_length_trials(self):
        for length in (4, 33, 65, 67, 32768):
            with self.subTest(length=length):
                self.device = CapsDevice(length=length)
                [status] = self.provider.poll()
                self.assertEqual(status.level, 70)
                self.assertEqual(self.device.sent, [bytes.fromhex("00 f7") + bytes(length - 2)])
                self.assertEqual(self.device.gets, [(0x05, length)])
                self.assertEqual(self.device.closed, 1)

    def test_legacy_hidapi_uses_the_confirmed_sixty_five_byte_shape(self):
        self.device.get_report_lengths = None
        self.assertEqual(self.provider.poll()[0].level, 70)
        self.assertEqual(self.device.sent, [bytes.fromhex("00 f7") + bytes(63)])
        self.assertEqual(self.device.gets, [(0x05, 65)])

    def test_invalid_caps_never_guess_a_length_or_send_a_request(self):
        for length in (0, 1, 3, 32769, True, 65.0, "65", None):
            with self.subTest(length=length):
                self.device = CapsDevice(length=length)
                self.assertIsNone(self.provider.poll()[0].level)
                self.assertEqual(self.device.sent, [])
                self.assertEqual(self.device.gets, [])
                self.assertEqual(self.device.closed, 1)

    def test_incomplete_or_noninteger_set_count_prevents_get(self):
        for wrote in (-1, 0, 64, 66, None, True, 65.0, "65"):
            with self.subTest(wrote=wrote):
                self.device = CapsDevice(wrote=wrote)
                self.assertIsNone(self.provider.poll()[0].level)
                self.assertEqual(self.device.gets, [])
                self.assertEqual(len(self.device.sent), 1)
                self.assertEqual(self.device.closed, 1)

    def test_disappearance_and_refusal_close_without_alternative_probes(self):
        self.assertEqual(self.provider.poll()[0].level, 70)
        for stage in ("open", "send", "get"):
            with self.subTest(stage=stage):
                self.device = CapsDevice(error=stage)
                [status] = self.provider.poll()
                self.assertEqual(status.level, 70)
                self.assertFalse(status.online)
                self.assertFalse(status.charging_known)
                self.assertEqual(self.device.closed, 1)
                self.assertLessEqual(len(self.device.sent), 1)
                self.assertLessEqual(len(self.device.gets), 1)
        self.entries = []
        self.factory.reset_mock()
        self.assertEqual(self.provider.poll(), [])
        self.factory.assert_not_called()

    def test_sleep_cache_expiry_and_wake_preserve_key_without_charging_claim(self):
        self.assertEqual(self.provider.poll()[0].level, 70)
        self.device.reply = NOT_READY
        [sleeping] = self.provider.poll()
        self.assertEqual((sleeping.key, sleeping.level, sleeping.online, sleeping.charging_known),
                         ("ajazz:402d", 70, False, False))
        self.now += 301
        self.assertIsNone(self.provider.poll()[0].level)
        self.device.reply = SOURCE_100
        [awake] = self.provider.poll()
        self.assertEqual((awake.key, awake.level, awake.online, awake.charging_known),
                         ("ajazz:402d", 100, True, False))

    def test_exact_target_collection_and_interface_guard_before_open(self):
        changes = [dict(vendor_id=0x9999), dict(product_id=0x5031), dict(product_id=0x4027),
                   dict(interface_number=0), dict(interface_number=1), dict(interface_number=-1),
                   dict(usage_page=0xFF00), dict(usage=1), dict(usage=0)]
        for change in changes:
            with self.subTest(change=change):
                self.entries = [dict(ENTRY, **change)]
                self.provider.poll()
                self.factory.assert_not_called()

    def test_only_numbered_status_bytes_with_a_valid_level_are_accepted(self):
        self.assertEqual(ajazz.parse_aj159_status(OWNER_70), 70)
        self.assertEqual(ajazz.parse_aj159_status(SOURCE_100), 100)
        self.assertEqual(ajazz.parse_aj159_status(bytes.fromhex("05 00 00 01")), 1)
        bad = [None, [], b"", 70, "05000046", [5, 0, 0, "70"], [5, 0, 0, True],
               [5, 0, 0, 70.0], [5, 0, 0, 256], [5, 0, 0, -1],
               NOT_READY, bytes(65), bytes.fromhex("05 00 00 65"), bytes.fromhex("05 00 00 ff"),
               bytes.fromhex("05 ad 04 01"), bytes.fromhex("05 00 01 46"),
               bytes.fromhex("f7 00 46"), bytes.fromhex("99 00 46"),
               bytes.fromhex("00 05 00 00 46"), bytes.fromhex("00 00 46")]
        bad += [OWNER_70[:length] for length in range(4)]
        for frame in bad:
            with self.subTest(frame=frame):
                self.assertIsNone(ajazz.parse_aj159_status(frame))
        self.device.reply = [5, 0, 0, "70"]
        self.assertIsNone(self.provider.poll()[0].level)


class AJ159NativeApi(native.Api):
    def __init__(self):
        super().__init__()
        self.report, self.report_count = OWNER_70, 65
        self.error = None

    def create_ioctl(self, owner, code, **kwargs):
        if kwargs["output_size"] != 65 or kwargs["input_data"] != b"\x05" + bytes(64):
            raise AssertionError("unexpected report ID or deliberately invalid feature length")
        if isinstance(self.error, TimeoutError):
            raise self.error
        if self.error:
            self.ioctls.append((code, kwargs))
            request = native.Request(bytes(65))
            request.completion = Completion(False, 87, 0, b"", False)
            return request
        return super().create_ioctl(owner, code, **kwargs)

    def create_read(self, owner, size):
        raise AssertionError("feature-only AJ159 must not read input")

    def create_write(self, owner, data):
        raise AssertionError("AJ179 output query must not reach AJ159")


class AJ159NativeScopeTests(unittest.TestCase):
    def setUp(self):
        self.api = AJ159NativeApi()
        self.native = TransportRuntime(api=self.api, timeout=.5)
        self.metadata = native.Metadata(ReportCaps(0, 0, 65))
        self.provider = ajazz.AjazzProvider()
        self.polling = None
        self.patches = [mock.patch.object(ajazz, "hid", SimpleNamespace(
                            device=lambda: HidDevice(self.native, metadata=self.metadata))),
                        mock.patch.object(ajazz.hidlist, "enumerate",
                                          side_effect=lambda vid: [dict(ENTRY)] if vid == 0x3151 else []),
                        mock.patch.object(ajazz, "time", SimpleNamespace(
                            time=lambda: 1000., sleep=lambda seconds: None))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        if self.polling is not None:
            self.assertTrue(self.polling.close(.5))
        self.assertTrue(self.native.close(.5))
        for patch in reversed(self.patches):
            patch.stop()

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.polling.pump()
            if predicate():
                return
            time.sleep(.001)
        self.fail("fake native poll did not finish")

    def test_ordinary_silence_does_not_poison_scope_and_wake_reads_again(self):
        for report, count, want in ((OWNER_70, 65, (70, True)),
                                    (NOT_READY, 65, (70, False)),
                                    (bytes(65), 64, (70, False)),
                                    (SOURCE_100, 65, (100, True))):
            # HIDAPI counts a leading zero report ID separately from transferred.
            self.api.report, self.api.report_count = report, count
            with self.native.provider_scope("ajazz") as scope:
                [status] = self.provider.poll()
            self.assertEqual((status.level, status.online), want)
            self.assertFalse(status.charging_known)
            self.assertFalse(scope.failed, scope.error)
            self.assertFalse(scope.unsafe)
        self.assertEqual(self.metadata.features, [bytes.fromhex("00 f7") + bytes(63)] * 4)
        self.assertEqual(len(self.api.ioctls), 4)

    def failed_poll(self, error):
        scopes = []
        @contextmanager
        def tracked(owner):
            with self.native.provider_scope(owner) as scope:
                scopes.append(scope)
                yield scope
        self.polling = PollingRuntime([self.provider], lambda full: None, clock=lambda: 0.,
                                      provider_scope=tracked, transport_idle=self.native.transport_idle,
                                      cancel_owner=self.native.cancel_owner)
        self.polling.configure(set())
        self.polling.request_poll()
        slot = self.polling._slots["ajazz:0"]
        self.wait(lambda: bool(slot.statuses) and not slot.running)
        self.assertEqual(self.polling.consume().fresh_keys, {"ajazz:402d"})
        if error == "set":
            self.metadata.fail_feature = True
        else:
            self.api.error = error
        self.polling.request_poll()
        self.wait(lambda: slot.failures == 1 and not slot.running)
        batch = self.polling.consume()
        self.assertEqual([(s.level, s.polling_stale, s.online) for s in batch.hid], [(70, True, False)])
        self.assertFalse(batch.fresh_keys)
        self.assertFalse(batch.completed)
        self.assertTrue(scopes[-1].failed)
        return scopes[-1]

    def test_actual_get_error_is_failed_and_stale_instead_of_cached_fresh(self):
        scope = self.failed_poll(True)
        self.assertIn("report=5, buffer=65, collection=65", scope.error)

    def test_actual_get_timeout_preserves_unsafe_and_stale(self):
        scope = self.failed_poll(TimeoutError("native deadline exceeded"))
        self.assertTrue(scope.unsafe)

    def test_actual_set_refusal_is_stale_and_never_reads_old_status(self):
        scope = self.failed_poll("set")
        self.assertIn("feature write failed", scope.error)
        self.assertEqual(len(self.api.ioctls), 1)  # only the initial successful poll


if __name__ == "__main__":
    unittest.main()
