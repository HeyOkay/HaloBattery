"""Exact Core75 target and wire capture; fake devices/completions only.

#245 owner attachments34418960/34425637: productURX Core75 Wireless,
36B0:3002 iface1 FF60:61, zeroRID output33, input32:0800074c+zeros.
#448 attachment34958359: EVO75 2.4G shares IDs/collection/manufacturer.
Maker firmware fd1af64 keymaps/default/keymap.c167-169 defines0..100.
"""
from types import SimpleNamespace
import unittest
from unittest import mock

import test_experimental_hid as native
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from providers import urx


CAPTURED = bytes.fromhex('08 00 07 4c') + bytes(28)
REQUEST = bytes.fromhex('00 08 00 07') + bytes(29)


def info(**changes):
    return dict(dict(vendor_id=0x36B0, product_id=0x3002, interface_number=1,
                     usage_page=0xFF60, usage=0x61, product_string='URX Core75 Wireless',
                     manufacturer_string='RDMCTMZT', path=b'core75'), **changes)


class Device:
    def __init__(self, frames=None, count=33):
        self.frames = [CAPTURED] if frames is None else list(frames)
        self.count = count
        self.opened, self.writes, self.reads = [], [], []
        self.closed = False

    def open_path(self, path):
        self.opened.append(path)

    def write(self, data):
        self.writes.append(bytes(data))
        if isinstance(self.count, Exception):
            raise self.count
        return self.count

    def read(self, size, timeout):
        self.reads.append((size, timeout))
        result = self.frames.pop(0) if self.frames else []
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


class UrxTargetSafetyTests(unittest.TestCase):
    def poll(self, device=None, entries=None, reader=None):
        device = device or Device()
        reader = reader or urx.UrxProvider()
        factory = mock.Mock(return_value=device)
        with mock.patch.object(urx, 'hid', SimpleNamespace(device=factory)), \
                mock.patch.object(urx.hidlist, 'enumerate', return_value=[info()] if entries is None else entries):
            result = reader.poll()
        return result, factory

    def test_captured_target_retains_key_level_and_unknown_charging(self):
        device = Device()
        [status], _ = self.poll(device)
        self.assertEqual((status.key, status.name, status.level, status.online),
                         ('urx:3002', 'URX Core75', 76, True))
        self.assertEqual((status.kind, status.charging, status.charging_known), ('keyboard', False, False))
        self.assertEqual(device.opened, [b'core75'])
        self.assertEqual(device.writes, [REQUEST])
        self.assertEqual(device.reads, [(33, 250)])
        self.assertTrue(device.closed)

    def test_normalized_case_and_whitespace_are_not_new_product_aliases(self):
        for name in ('URX Core75 Wireless', ' urx core75 wireless ', '\tURX  Core75\tWireless\r\n'):
            with self.subTest(name=name):
                [status], _ = self.poll(entries=[info(product_string=name)])
                self.assertEqual(status.level, 76)

    def test_evo_unknown_and_partial_names_never_create_a_device_or_cached_status(self):
        for name in ('EVO75 2.4G', 'URX', 'Core75', 'URX Core65 Wireless',
                     'Other Core75', 'URX Core75 Wireless SE', '', None, 75):
            with self.subTest(name=name):
                reader = urx.UrxProvider()
                reader._last = (76, urx.time.time())
                result, factory = self.poll(entries=[info(product_string=name)], reader=reader)
                self.assertEqual(result, [])
                factory.assert_not_called()

    def test_wrong_identity_and_collections_do_no_io(self):
        for changes in (dict(vendor_id=0x1234), dict(product_id=0x3130), dict(vendor_id=None),
                        dict(product_id='0x3002'), dict(interface_number=2), dict(interface_number=None),
                        dict(interface_number=1.0), dict(usage_page=0xFF00), dict(usage=0x60)):
            with self.subTest(changes=changes):
                device = Device()
                result, factory = self.poll(device, [info(**changes)])
                factory.assert_not_called()
                self.assertEqual(device.writes, [])
                self.assertTrue(all(value.level is None for value in result))
                with mock.patch.object(urx, 'hid', SimpleNamespace(device=factory)):
                    self.assertIsNone(urx.UrxProvider()._ask(info(**changes)))
                factory.assert_not_called()

    def test_mixed_enumeration_selects_only_actual_core75(self):
        device = Device()
        entries = [info(product_string='EVO75 2.4G', path=b'evo'),
                   info(interface_number=2, path=b'wrong-iface'), info()]
        [status], _ = self.poll(device, entries)
        self.assertEqual(status.level, 76)
        self.assertEqual(device.opened, [b'core75'])
        self.assertEqual(device.writes, [REQUEST])

    def test_rejected_evo_cannot_reuse_a_previously_read_core75_cache(self):
        reader = urx.UrxProvider()
        [fresh], _ = self.poll(reader=reader)
        self.assertEqual(fresh.level, 76)
        result, factory = self.poll(entries=[info(product_string='EVO75 2.4G')], reader=reader)
        self.assertEqual(result, [])
        factory.assert_not_called()
        [asleep], _ = self.poll(Device([]), reader=reader)
        self.assertEqual((asleep.level, asleep.online, asleep.charging_known), (76, False, False))

    def test_refused_and_short_writes_do_not_read(self):
        for count in (-1, 0, 1, 32, 34, None, True, 33.0, OSError('disappeared')):
            with self.subTest(count=count):
                device = Device(count=count)
                [status], _ = self.poll(device)
                self.assertIsNone(status.level)
                self.assertEqual(device.reads, [])
                self.assertTrue(device.closed)

    def test_disappearance_retains_only_offline_known_cache(self):
        reader = urx.UrxProvider()
        self.poll(reader=reader)
        device = Device([OSError('gone')])
        [status], _ = self.poll(device, reader=reader)
        self.assertEqual((status.level, status.online, status.charging_known), (76, False, False))
        self.assertTrue(device.closed)

    def test_source_zero_100_and_both_report_id_forms(self):
        for level in (0, 76, 100):
            frame = bytes((8, 0, 7, level)) + bytes(28)
            for data in (frame, b'\0' + frame):
                with self.subTest(level=level, length=len(data)):
                    self.assertEqual(urx.parse_reply(data), (level, False))

    def test_bad_frames_never_become_readings(self):
        for data in (CAPTURED[:4], CAPTURED[:31], b'\0' + CAPTURED[:31],
                     bytes.fromhex('ff 07') + bytes(30),
                     bytes.fromhex('08 01 07 4c') + bytes(28),
                     bytes.fromhex('08 00 06 4c') + bytes(28),
                     bytes.fromhex('08 00 07 65') + bytes(28),
                     [8, 0, 7, True] + [0] * 28,
                     [8, 0, 7, '76'] + [0] * 28,
                     [8, 0, 7, -1] + [0] * 28):
            with self.subTest(data=data):
                self.assertIsNone(urx.parse_reply(data))


class UrxNativePaddingTests(unittest.TestCase):
    def test_cached_native_caps_pad_only_the_sourced_frame_and_check_full_count(self):
        for size in (33, 65):
            with self.subTest(size=size):
                api = native.Api()
                api.next_read = native.Request(b'\0' + CAPTURED + bytes(size - 33))
                runtime = TransportRuntime(api=api, timeout=.5)
                metadata = native.Metadata(ReportCaps(size, size, 0))
                try:
                    with mock.patch.object(urx, 'hid', SimpleNamespace(
                            device=lambda: HidDevice(runtime, metadata=metadata))), \
                            mock.patch.object(urx.hidlist, 'enumerate', return_value=[info()]):
                        with runtime.provider_scope('urx') as scope:
                            [status] = urx.UrxProvider().poll()
                    self.assertEqual(status.level, 76)
                    self.assertFalse(scope.failed, scope.error)
                    self.assertEqual(api.writes, [REQUEST.ljust(size, b'\0')])
                    self.assertEqual((api.ioctls, metadata.features), ([], []))
                    self.assertTrue(all(o.closed for o in api.owners))
                    self.assertTrue(all(r.disposed for r in api.requests))
                finally:
                    self.assertTrue(runtime.close(.5))

    def test_native_caps_smaller_than_canonical_report_refuse_before_write(self):
        api = native.Api()
        runtime = TransportRuntime(api=api, timeout=.5)
        metadata = native.Metadata(ReportCaps(33, 32, 0))
        try:
            with mock.patch.object(urx, 'hid', SimpleNamespace(
                    device=lambda: HidDevice(runtime, metadata=metadata))), \
                    mock.patch.object(urx.hidlist, 'enumerate', return_value=[info()]):
                [status] = urx.UrxProvider().poll()
            self.assertIsNone(status.level)
            self.assertEqual(api.writes, [])
        finally:
            self.assertTrue(runtime.close(.5))

    def test_native_partial_padded_write_never_reads(self):
        class Api(native.Api):
            def create_write(self, owner, data):
                self.writes.append(data)
                request = native.Request(b'', transferred=33)  # caps requires65
                self.requests.append(request)
                return request

            def create_read(self, owner, size):
                raise AssertionError('no input after partial output')

        api = Api()
        runtime = TransportRuntime(api=api, timeout=.5)
        metadata = native.Metadata(ReportCaps(65, 65, 0))
        try:
            with mock.patch.object(urx, 'hid', SimpleNamespace(
                    device=lambda: HidDevice(runtime, metadata=metadata))), \
                    mock.patch.object(urx.hidlist, 'enumerate', return_value=[info()]):
                [status] = urx.UrxProvider().poll()
            self.assertIsNone(status.level)
            self.assertEqual(api.writes, [REQUEST + bytes(32)])
        finally:
            self.assertTrue(runtime.close(.5))


if __name__ == '__main__':
    unittest.main()
