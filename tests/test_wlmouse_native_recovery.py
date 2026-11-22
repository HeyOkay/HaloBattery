"""WLmouse feature-only receiver regression; no hardware or real native API.

The owner's 36A7:A880 interface2 FFFF:0000 reports input=0/output=0/feature=65.
The input below is the documented WLmouse A1/83 reply with synthetic levels.
"""
from types import SimpleNamespace
import unittest
from unittest import mock

import test_experimental_hid as fixtures
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from halo.native_io import Completion
from providers import wlmouse


def feature(level):
    return bytes.fromhex('00 a1 00 02 02 00 83 00') + bytes((level,)) + bytes(56)


class FeatureOnlyApi(fixtures.Api):
    def __init__(self):
        super().__init__()
        self.report, self.report_count = feature(80), 64
        self.input_reads = 0
    def create_ioctl(self, owner, code, **kwargs):
        if kwargs['output_size'] == 64:
            self.ioctls.append((code, kwargs))
            request = fixtures.Request(b'')
            request.completion = Completion(False, 87, 0, b'', False)
            return request
        return super().create_ioctl(owner, code, **kwargs)
    def create_read(self, owner, size):
        self.input_reads += 1
        raise AssertionError('feature-only collection has no input reports')


class WLmouseNativeRecoveryTests(unittest.TestCase):
    def test_sleep_does_not_issue_short_feature_or_input_reads_and_wake_recovers(self):
        api = FeatureOnlyApi()
        runtime = TransportRuntime(api=api, timeout=.5)
        metadata = fixtures.Metadata(ReportCaps(0, 0, 65))
        self.addCleanup(lambda: self.assertTrue(runtime.close(.5)))
        fake_hid = SimpleNamespace(device=lambda: HidDevice(runtime, metadata=metadata))
        info = dict(vendor_id=0x36A7, product_id=0xA880, interface_number=2,
                    usage_page=0xFFFF, usage=0, path=b'fake-wlmouse', product_string='WLMOUSE BEAST MAX 8K RECEIVER')
        clock = SimpleNamespace(time=lambda: 1000., sleep=lambda seconds: None)
        provider = wlmouse.WLmouseProvider()
        with mock.patch.object(wlmouse, 'hid', fake_hid), mock.patch.object(wlmouse, 'time', clock), \
                mock.patch.object(wlmouse.hidlist, 'enumerate', side_effect=lambda vid: [info] if vid == 0x36A7 else []):
            with runtime.provider_scope('wlmouse') as initial:
                [first] = provider.poll()
            self.assertEqual((first.level, first.online), (80, True))
            self.assertFalse(initial.failed)
            api.report = bytes(65)  # awake receiver, mouse temporarily silent
            with runtime.provider_scope('wlmouse') as quiet:
                [cached] = provider.poll()
            self.assertEqual((cached.level, cached.online, cached.polling_stale), (80, False, False))
            self.assertFalse(quiet.failed, quiet.error)
            self.assertEqual(api.input_reads, 0)
            self.assertTrue(all(options['output_size'] == 65 for _, options in api.ioctls))
            api.report = feature(79)
            with runtime.provider_scope('wlmouse') as recovered:
                [current] = provider.poll()
            self.assertEqual((current.level, current.online), (79, True))
            self.assertFalse(recovered.failed)

    def test_repeated_long_idle_and_wake_cycles_retire_every_native_handle(self):
        # Ten simulated hours exercise the original late-onset failure path;
        # fake time/native completions do not verify real hardware endurance.
        api = FeatureOnlyApi()
        runtime = TransportRuntime(api=api, timeout=.5)
        metadata = fixtures.Metadata(ReportCaps(0, 0, 65))
        self.addCleanup(lambda: self.assertTrue(runtime.close(.5)))
        fake_hid = SimpleNamespace(device=lambda: HidDevice(runtime, metadata=metadata))
        info = dict(vendor_id=0x36A7, product_id=0xA880, interface_number=2,
                    usage_page=0xFFFF, usage=0, path=b'fake-wlmouse',
                    product_string='WLMOUSE BEAST MAX 8K RECEIVER')
        now = [1000.]
        clock = SimpleNamespace(time=lambda: now[0], sleep=lambda seconds: None)
        provider = wlmouse.WLmouseProvider()
        with mock.patch.object(wlmouse, 'hid', fake_hid), mock.patch.object(wlmouse, 'time', clock), \
                mock.patch.object(wlmouse.hidlist, 'enumerate', side_effect=lambda vid: [info] if vid == 0x36A7 else []):
            for cycle in range(20):
                with self.subTest(cycle=cycle):
                    api.report = feature(80 - cycle)
                    with runtime.provider_scope('wlmouse') as awake:
                        [reading] = provider.poll()
                    self.assertEqual((reading.level, reading.online), (80 - cycle, True))
                    self.assertFalse(awake.failed, awake.error)
                    now[0] += 1800
                    api.report = bytes(65)
                    with runtime.provider_scope('wlmouse') as idle:
                        self.assertEqual(provider.poll(), [])
                    self.assertFalse(idle.failed, idle.error)
                    self.assertTrue(runtime.transport_idle('wlmouse'))
                    self.assertTrue(all(owner.closed for owner in api.owners))
                    self.assertTrue(all(request.disposed for request in api.requests))
        self.assertEqual(api.input_reads, 0)
        self.assertTrue(api.ioctls)
        self.assertTrue(all(options['output_size'] == 65 for _, options in api.ioctls))

    def test_legacy_backend_keeps_both_known_feature_read_variants(self):
        class LegacyDevice:
            def __init__(self): self.lengths = []
            def open_path(self, path): pass
            def send_feature_report(self, data): return len(data)
            def get_feature_report(self, report, length):
                self.lengths.append(length)
                return feature(80) if length == 64 else []
            def close(self): pass
        device = LegacyDevice()
        with mock.patch.object(wlmouse, 'hid', SimpleNamespace(device=lambda: device)), \
                mock.patch.object(wlmouse.time, 'sleep', return_value=None):
            self.assertEqual(wlmouse.WLmouseProvider()._read_feature(b'fake'), (80, False))
        self.assertEqual(device.lengths, [65, 64])
