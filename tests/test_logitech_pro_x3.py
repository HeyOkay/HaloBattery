"""PRO X 3 receiver: independent captured bytes and offline native completions.

HeadsetControl 1d2ccd1412246d85b2f8733d739cc865ba466aab:
tests/test_protocols.cpp L1589-1596,1643-1666 records receiver 046d:0b45
revision 1.02, dynamic bridge 3 / battery 5, level 78, and padded 64-byte writes.
lib/devices/protocols/logitech_centurion_protocol.hpp L237-275,305-325,394-448
defines lengths, Root lookup and battery fields. Boundary/error variations below
are synthetic; these tests do not claim Windows or charging hardware validation.
"""
from types import SimpleNamespace
import unittest
from unittest import mock

import test_experimental_hid as native
from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
from halo.native_io import Completion
from providers import logitech_centurion as provider


def wire(hexadecimal):
    raw = bytes.fromhex(hexadecimal)
    return raw + bytes(64 - len(raw))


ROOT = wire('50 23 06 00 00 01 03 00 01')
ACK = wire('50 23 03 00 03 11')
LOOKUP = wire('50 23 0b 00 03 10 00 06 00 00 01 05 00 03')
BATTERY = wire('50 23 0b 00 03 10 00 06 00 05 01 4e 50 00')
REQUESTS = [
    wire('50 23 05 00 00 01 00 03'),
    wire('50 23 0a 00 03 11 00 05 00 00 01 01 04'),
    wire('50 23 08 00 03 11 00 03 00 05 01'),
]


def replies(level=78, state=0, bridge=3, index=5):
    # Vary only fields established by the named capture and Root.getFeature.
    root, ack, lookup, battery = map(bytearray, (ROOT, ACK, LOOKUP, BATTERY))
    root[6] = bridge
    ack[4] = lookup[4] = battery[4] = bridge
    lookup[11] = battery[9] = index
    battery[11], battery[13] = level, state
    return [bytes(root), bytes(ack), bytes(lookup), bytes(ack), bytes(battery)]


def info(path=b'x3-receiver-a', **changes):
    result = dict(vendor_id=0x046D, product_id=0x0B45, interface_number=3,
                  usage_page=0xFFA0, usage=1, path=path, serial_number='duplicate')
    result.update(changes)
    return result


class Device:
    def __init__(self, responses=None, counts=None):
        self.responses = list(replies() if responses is None else responses)
        self.counts = list(counts or [])
        self.writes, self.reads, self.opens = [], [], []
        self.closed = False

    def open_path(self, path):
        self.opens.append(path)

    def write(self, report):
        self.writes.append(bytes(report))
        value = self.counts.pop(0) if self.counts else 64
        if isinstance(value, Exception):
            raise value
        return value

    def read(self, size, timeout):
        self.reads.append((size, timeout))
        value = self.responses.pop(0) if self.responses else []
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True


class CapsDevice(Device):
    def __init__(self, caps, **kwargs):
        super().__init__(**kwargs)
        self.caps = caps

    def get_report_lengths(self):
        return self.caps


class ProX3Tests(unittest.TestCase):
    def poll(self, devices, entries=None, reader=None):
        devices = list(devices) if isinstance(devices, (tuple, list)) else [devices]
        entries = [info()] if entries is None else entries
        reader = reader or provider.LogitechCenturionProvider()
        factory = mock.Mock(side_effect=devices)
        with mock.patch.object(provider, 'hid', SimpleNamespace(device=factory)), \
                mock.patch.object(provider.hidlist, 'enumerate', return_value=entries):
            result = reader.poll()
        return result, factory

    def test_captured_78_uses_exact_three_read_requests_without_features_or_setup(self):
        device = Device()
        [status], _ = self.poll(device)
        self.assertEqual((status.level, status.charging, status.charging_known), (78, False, True))
        self.assertEqual((status.online, status.source, status.kind), (True, 'logitech', 'headset'))
        self.assertEqual(device.writes, REQUESTS)
        self.assertEqual(device.reads, [(64, 100)] * 5)
        self.assertTrue(device.closed)

    def test_zero_100_and_source_charging_states(self):
        for level in (0, 100):
            for state, charging in ((0, False), (1, True), (2, True)):
                with self.subTest(level=level, state=state):
                    [status], _ = self.poll(Device(replies(level, state)))
                    self.assertEqual((status.level, status.charging, status.charging_known),
                                     (level, charging, True))

    def test_absent_or_unknown_charging_keeps_valid_level_but_unknown_state(self):
        for response in (
                wire('50 23 09 00 03 10 00 04 00 05 01 4e'),
                wire('50 23 0a 00 03 10 00 05 00 05 01 4e 50'),
                wire('50 23 0b 00 03 10 00 06 00 05 01 4e 50 03'),
                wire('50 23 0b 00 03 10 00 06 00 05 01 4e 50 ff')):
            with self.subTest(response=response[:14]):
                [status], _ = self.poll(Device(replies()[:-1] + [response]))
                self.assertEqual((status.level, status.charging, status.charging_known), (78, False, False))

    def test_wrong_targets_never_open_even_if_enabled_table_is_widened(self):
        wrong = [info(vendor_id=0x1234), info(interface_number=2), info(interface_number=None),
                 info(usage_page=0xFF13), info(usage=2), info(product_id=0x0B46),
                 info(path=None), info(path=b'')]
        with mock.patch.object(provider, 'ENABLED_PIDS', frozenset((0x0AF7, 0x0B45, 0x0B46))):
            result, factory = self.poll([], wrong)
        self.assertEqual(result, [])
        factory.assert_not_called()

    def test_short_or_refused_writes_never_read_and_close(self):
        for count in (-1, 0, 1, 63, 65, None, True, 64.0, OSError('disappeared')):
            with self.subTest(count=count):
                device = Device(counts=[count])
                result, _ = self.poll(device)
                self.assertEqual(result, [])
                self.assertEqual(device.writes, REQUESTS[:1])
                self.assertEqual(device.reads, [])
                self.assertTrue(device.closed)

    def test_disappearance_at_each_exchange_preserves_no_fake_result(self):
        for responses, count in (([OSError('gone')], 1),
                                 ([ROOT, OSError('gone')], 2),
                                 ([ROOT, ACK, LOOKUP, OSError('gone')], 3)):
            with self.subTest(count=count):
                device = Device(responses)
                self.assertEqual(self.poll(device)[0], [])
                self.assertEqual(len(device.writes), count)
                self.assertTrue(device.closed)

    def test_malformed_battery_frames_are_refused_without_fallback(self):
        bad = [b'', b'garbage', BATTERY[:13], bytes(64),
               wire('51 0b 00 03 10 00 06 00 05 01 4e 50 00'),
               wire('50 24 0b 00 03 10 00 06 00 05 01 4e 50 00'),
               wire('50 23 3e 00 03 10 00 06 00 05 01 4e 50 00'),
               wire('50 23 01 00 03 10 00 06 00 05 01 4e 50 00'),
               wire('50 23 0b 01 03 10 00 06 00 05 01 4e 50 00'),
               wire('50 23 0b 00 04 10 00 06 00 05 01 4e 50 00'),
               wire('50 23 0b 00 03 10 00 05 00 05 01 4e 50 00'),
               wire('50 23 0b 00 03 10 00 06 01 05 01 4e 50 00'),
               wire('50 23 0b 00 03 10 00 06 00 06 01 4e 50 00'),
               wire('50 23 0b 00 03 10 00 06 00 05 02 4e 50 00'),
               wire('50 23 08 00 03 10 00 03 00 05 01'),
               wire('50 23 0b 00 03 10 00 06 00 05 01 65 50 00'),
               wire('50 23 0b 00 03 10 00 06 00 05 01 ff 50 00'),
               [0x50, 0x23, 0x0b, 0, 3, 0x10, 0, 6, 0, 5, 1, '78', 0x50, 0],
               [0x50, 0x23, 0x0b, 0, 3, 0x10, 0, 6, 0, 5, 1, True, 0x50, 0],
               [0x50, 0x23, 0x0b, 0, 3, 0x10, 0, 6, 0, 5, 1, -1, 0x50, 0],
               [0x50, 0x23, 0x0b, 0, 3, 0x10, 0, 6, 0, 5, 1, 256, 0x50, 0]]
        for response in bad:
            with self.subTest(response=response):
                device = Device(replies()[:-1] + [response])
                self.assertEqual(self.poll(device)[0], [])
                self.assertEqual(device.writes, REQUESTS)
                self.assertLessEqual(len(device.reads), 11)

    def test_wrong_root_echo_truncated_or_missing_features_stop_discovery(self):
        for response in (wire('50 23 06 00 00 02 03 00 01'),
                         wire('50 23 06 00 01 01 03 00 01'),
                         wire('50 23 04 00 00 01 03'),
                         wire('50 23 06 00 00 01 00 00 01')):
            with self.subTest(response=response[:9]):
                device = Device([response])
                self.assertEqual(self.poll(device)[0], [])
                self.assertEqual(device.writes, REQUESTS[:1])
        for response in (wire('50 23 0b 00 03 10 00 06 00 00 01 00 00 03'),
                         wire('50 23 09 00 03 10 00 04 00 00 01 05')):
            device = Device([ROOT, ACK, response])
            self.assertEqual(self.poll(device)[0], [])
            self.assertEqual(device.writes, REQUESTS[:2])

    def test_ack_only_and_connection_event_do_not_become_battery_or_trigger_extra_query(self):
        for suffix in ([ACK], [wire('50 23 05 00 03 00 00 00')],
                       [wire('50 23 08 00 03 10 00 03 00 ff 05')]):
            device = Device(replies()[:-1] + suffix)
            self.assertEqual(self.poll(device)[0], [])
            self.assertEqual(device.writes, REQUESTS)
            self.assertTrue(device.closed)

    def test_repair_rediscovers_changed_indexes_without_sharing_x2_cache(self):
        reader = provider.LogitechCenturionProvider()
        reader._features[b'x2'] = (3, {0x0104: 4})
        first, second = Device(), Device(replies(43, bridge=7, index=9))
        [old], _ = self.poll(first, reader=reader)
        [current], _ = self.poll(second, reader=reader)
        self.assertEqual((old.level, current.level), (78, 43))
        self.assertEqual(old.key, current.key)
        self.assertEqual(second.writes[0], REQUESTS[0])
        self.assertEqual(second.writes[1], wire('50 23 0a 00 07 11 00 05 00 00 01 01 04'))
        self.assertEqual(second.writes[2], wire('50 23 08 00 07 11 00 03 00 09 01'))
        self.assertEqual(reader._features, {b'x2': (3, {0x0104: 4})})

    def test_two_receivers_with_duplicate_serials_have_separate_stable_path_keys(self):
        reader = provider.LogitechCenturionProvider()
        result, _ = self.poll([Device(), Device(replies(43))],
                              [info(b'X3-A'), info(b'X3-B')], reader)
        self.assertEqual([s.level for s in result], [78, 43])
        self.assertNotEqual(result[0].key, result[1].key)
        [again], _ = self.poll(Device(), [info(b'x3-a')], reader)
        self.assertEqual(result[0].key, again.key)
        self.assertEqual(reader.legacy_key_aliases(), {})

    def test_duplicate_enumeration_per_receiver_sends_only_three_requests(self):
        device = Device()
        result, factory = self.poll(device, [info(b'X3-A'), info(b'x3-a')])
        self.assertEqual([s.level for s in result], [78])
        self.assertEqual(factory.call_count, 1)
        self.assertEqual(device.writes, REQUESTS)

    def test_caps_must_carry_canonical_input_and_output_before_any_request(self):
        for caps in ({'input': 0, 'output': 64}, {'input': 64, 'output': 63},
                     {'input': 63, 'output': 64}, {'input': 32769, 'output': 64},
                     {'input': 64, 'output': 32769}, {'input': True, 'output': 64},
                     {'input': '64', 'output': 64}, {'input': 64}, None):
            with self.subTest(caps=caps):
                device = CapsDevice(caps)
                self.assertEqual(self.poll(device)[0], [])
                self.assertEqual((device.writes, device.reads), ([], []))
                self.assertTrue(device.closed)


class NativeProX3Tests(unittest.TestCase):
    def test_native_ack_then_silence_stays_clean_and_reuses_one_pending_input(self):
        class Api(native.Api):
            def __init__(self):
                super().__init__()
                self.responses = [ROOT, ACK]
                self.read_calls = 0

            def create_read(self, owner, size):
                self.read_calls += 1
                request = native.Request(self.responses.pop(0)) if self.responses else native.Request()
                self.requests.append(request)
                return request

        api = Api()
        runtime = TransportRuntime(api=api, timeout=.5)
        metadata = native.Metadata(ReportCaps(64, 64, 0))
        try:
            with mock.patch.object(provider, 'hid', SimpleNamespace(
                    device=lambda: HidDevice(runtime, metadata=metadata))), \
                    mock.patch.object(provider.hidlist, 'enumerate', return_value=[info()]):
                with runtime.provider_scope('logitech_centurion') as scope:
                    self.assertEqual(provider.LogitechCenturionProvider().poll(), [])
            self.assertFalse(scope.failed, scope.error)
            self.assertEqual(api.writes, REQUESTS[:2])
            self.assertEqual(api.read_calls, 3)  # root, ACK, one reused silent read
            self.assertTrue(all(r.disposed for r in api.requests))
            self.assertTrue(all(o.closed for o in api.owners))
        finally:
            self.assertTrue(runtime.close(.5))

    def test_native_input_error_is_reported_by_transport_scope(self):
        class Api(native.Api):
            def create_read(self, owner, size):
                request = native.Request(b'')
                request.completion = Completion(False, 87, 0, b'', False)
                self.requests.append(request)
                return request

        api = Api()
        runtime = TransportRuntime(api=api, timeout=.5)
        metadata = native.Metadata(ReportCaps(64, 64, 0))
        try:
            with mock.patch.object(provider, 'hid', SimpleNamespace(
                    device=lambda: HidDevice(runtime, metadata=metadata))), \
                    mock.patch.object(provider.hidlist, 'enumerate', return_value=[info()]):
                with runtime.provider_scope('logitech_centurion') as scope:
                    self.assertEqual(provider.LogitechCenturionProvider().poll(), [])
            self.assertTrue(scope.failed)
            self.assertIn('read error', scope.error)
            self.assertEqual(api.writes, REQUESTS[:1])
            self.assertTrue(all(r.disposed for r in api.requests))
        finally:
            self.assertTrue(runtime.close(.5))

    def test_native_caps_padding_full_counts_and_input_sizes(self):
        for size in (64, 65):
            with self.subTest(size=size):
                class Api(native.Api):
                    def __init__(self):
                        super().__init__()
                        self.responses = list(replies())
                        self.read_sizes = []

                    def create_read(self, owner, length):
                        self.read_sizes.append(length)
                        request = native.Request(self.responses.pop(0).ljust(size, b'\0'))
                        self.requests.append(request)
                        return request

                api = Api()
                runtime = TransportRuntime(api=api, timeout=.5)
                metadata = native.Metadata(ReportCaps(size, size, 0))
                device = HidDevice(runtime, metadata=metadata)
                try:
                    with mock.patch.object(provider, 'hid', SimpleNamespace(device=lambda: device)), \
                            mock.patch.object(provider.hidlist, 'enumerate', return_value=[info()]):
                        with runtime.provider_scope('logitech_centurion') as scope:
                            [status] = provider.LogitechCenturionProvider().poll()
                    self.assertEqual(status.level, 78)
                    self.assertFalse(scope.failed, scope.error)
                    self.assertEqual(api.writes, [r.ljust(size, b'\0') for r in REQUESTS])
                    self.assertEqual(api.read_sizes, [size] * 5)
                    self.assertEqual((api.ioctls, metadata.features), ([], []))
                    self.assertTrue(all(r.disposed for r in api.requests))
                    self.assertTrue(all(o.closed for o in api.owners))
                finally:
                    self.assertTrue(runtime.close(.5))

    def test_native_short_padded_completion_stops_before_input(self):
        class Api(native.Api):
            def create_write(self, owner, data):
                self.writes.append(data)
                request = native.Request(b'', transferred=64)  # caps requires 65
                self.requests.append(request)
                return request

            def create_read(self, owner, size):
                raise AssertionError('must not read after incomplete padded output')

        api = Api()
        runtime = TransportRuntime(api=api, timeout=.5)
        metadata = native.Metadata(ReportCaps(65, 65, 0))
        try:
            with mock.patch.object(provider, 'hid', SimpleNamespace(
                    device=lambda: HidDevice(runtime, metadata=metadata))), \
                    mock.patch.object(provider.hidlist, 'enumerate', return_value=[info()]):
                with runtime.provider_scope('logitech_centurion'):
                    self.assertEqual(provider.LogitechCenturionProvider().poll(), [])
            self.assertEqual(api.writes, [REQUESTS[0] + b'\0'])
        finally:
            self.assertTrue(runtime.close(.5))


if __name__ == '__main__':
    unittest.main()
