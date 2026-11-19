"""Literal PlayStation status/report fixtures independent of provider constants."""
import unittest
from types import SimpleNamespace
from unittest import mock

from providers import playstation


class Clock:
    def __init__(self):
        self.now = 1000.0
    def time(self):
        return self.now
    def sleep(self, duration):
        self.now += duration


class BatteryCodeTests(unittest.TestCase):
    def test_dualshock_normal_boundaries_and_documented_full(self):
        for raw, expected in ((0x00, (0, False)), (0x0a, (100, False)),
                              (0x10, (0, True)), (0x1a, (100, True)), (0x1b, (100, True))):
            with self.subTest(raw=raw):
                self.assertEqual(playstation.parse_ds4(raw), expected)

    def test_dualshock_errors_and_undefined_values_are_not_percentages(self):
        for raw in (0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x1c, 0x1d, 0x1e, 0x1f):
            with self.subTest(raw=raw):
                self.assertIsNone(playstation.parse_ds4(raw))

    def test_dualsense_normal_bins_and_explicit_charge_complete(self):
        for raw, expected in ((0x00, (0, False)), (0x0a, (100, False)),
                              (0x10, (0, True)), (0x1a, (100, True)),
                              (0x20, (100, True)), (0x2a, (100, True)), (0x2f, (100, True))):
            with self.subTest(raw=raw):
                self.assertEqual(playstation.parse_dualsense(raw), expected)

    def test_dualsense_error_and_reserved_states_are_not_battery_measurements(self):
        for raw in (0xa0, 0xa5, 0xaf, 0xb0, 0xb5, 0xbf, 0xf0, 0xf5, 0xff,
                    0x30, 0x41, 0x52, 0x63, 0x74, 0x85, 0x96, 0xc7, 0xd8, 0xe9):
            with self.subTest(raw=raw):
                self.assertIsNone(playstation.parse_dualsense(raw))

    def test_dualsense_undefined_level_bins_do_not_saturate_to_full(self):
        for raw in (0x0b, 0x0c, 0x0d, 0x0e, 0x0f, 0x1b, 0x1c, 0x1d, 0x1e, 0x1f):
            with self.subTest(raw=raw):
                self.assertIsNone(playstation.parse_dualsense(raw))

    def test_non_byte_inputs_are_refused(self):
        for parser in (playstation.parse_ds4, playstation.parse_dualsense):
            for value in (None, True, False, "10", 10.0, -1, 256, b"x"):
                with self.subTest(parser=parser.__name__, value=value):
                    self.assertIsNone(parser(value))


class BatteryReportTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.packets = []
        self.features = []
        self.closed = 0
        owner = self
        class Device:
            def open_path(self, path):
                pass
            def set_nonblocking(self, value):
                pass
            def get_feature_report(self, report_id, length):
                owner.features.append((report_id, length))
                return []
            def read(self, _length):
                owner.clock.now += .001
                return owner.packets.pop(0) if owner.packets else []
            def close(self):
                owner.closed += 1
        self.patchers = [mock.patch.object(playstation, "hid", SimpleNamespace(device=Device)),
                         mock.patch.object(playstation, "time", self.clock)]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.provider = playstation.PlayStationProvider()

    @staticmethod
    def report(dualsense, bluetooth, status):
        # Source report positions/sizes expressed as literal independent fixtures:
        # DS4 USB01/status30, BT11/status32; DualSense USB01/status53, BT31/status54.
        size = 78 if bluetooth else 64
        value = [0] * size
        value[0] = (0x31 if dualsense else 0x11) if bluetooth else 0x01
        offset = (54 if bluetooth else 53) if dualsense else (32 if bluetooth else 30)
        value[offset] = status
        return value

    def read(self, dualsense, bluetooth):
        path = rb"\\?\hid#vid&0002054c_pid&0ce6#controller" if bluetooth else b"usb-test-controller"
        return self.provider._read(path, dualsense)

    def test_valid_literal_full_reports_preserve_zero_and_full(self):
        for dualsense in (False, True):
            for bluetooth in (False, True):
                for status, expected in ((0x00, (0, False)), (0x0a, (100, False)), (0x1a, (100, True))):
                    with self.subTest(dualsense=dualsense, bluetooth=bluetooth, status=status):
                        self.packets = [self.report(dualsense, bluetooth, status)]
                        self.assertEqual(self.read(dualsense, bluetooth), expected)
        # Existing USB calibration triggers remain exactly those two sourced reads.
        self.assertTrue(all(request in ((2, 64), (5, 64)) for request in self.features))
        self.assertEqual(self.closed, 12)

    def test_error_report_does_not_blind_a_following_valid_report(self):
        for dualsense, bad in ((False, 0x1e), (True, 0xaf)):
            for bluetooth in (False, True):
                with self.subTest(dualsense=dualsense, bluetooth=bluetooth):
                    self.packets = [self.report(dualsense, bluetooth, bad),
                                    self.report(dualsense, bluetooth, 0x05)]
                    self.assertEqual(self.read(dualsense, bluetooth), (50, False))

    def test_error_and_garbage_reports_never_become_full(self):
        for dualsense in (False, True):
            for bluetooth in (False, True):
                with self.subTest(dualsense=dualsense, bluetooth=bluetooth):
                    self.packets = [self.report(dualsense, bluetooth, 0xff),
                                    self.report(dualsense, bluetooth, 0x1f)]
                    self.assertIsNone(self.read(dualsense, bluetooth))

    def test_truncated_reports_with_a_status_byte_are_refused(self):
        for dualsense in (False, True):
            for bluetooth in (False, True):
                report = self.report(dualsense, bluetooth, 0x0a)
                offset = (54 if bluetooth else 53) if dualsense else (32 if bluetooth else 30)
                for length in (0, 1, offset, offset + 1, len(report) - 1):
                    with self.subTest(dualsense=dualsense, bluetooth=bluetooth, length=length):
                        self.packets = [report[:length]]
                        self.assertIsNone(self.read(dualsense, bluetooth))

    def test_provider_publishes_unknown_instead_of_full_for_error_only_reports(self):
        for bluetooth in (False, True):
            with self.subTest(bluetooth=bluetooth):
                path = b"vid&0002054c_pid&0ce6#fake-controller" if bluetooth else b"usb-fake-controller"
                descriptor = {"vendor_id": 0x054c, "product_id": 0x0ce6, "path": path,
                              "serial_number": "AABBCCDDEEFF" if bluetooth else "",
                              "usage_page": 1, "usage": 5, "interface_number": 3,
                              "product_string": "Wireless Controller"}
                self.packets = [self.report(True, bluetooth, 0xaf)]
                with mock.patch.object(playstation, "hidlist", SimpleNamespace(enumerate=lambda vid: [descriptor])):
                    values = self.provider.poll()
                self.assertEqual(len(values), 1)
                self.assertIsNone(values[0].level)
                self.assertFalse(values[0].charging)

    def test_bluetooth_validation_never_adds_feature_requests(self):
        for dualsense in (False, True):
            self.packets = [self.report(dualsense, True, 0xff)]
            self.assertIsNone(self.read(dualsense, True))
        self.assertEqual(self.features, [])


if __name__ == "__main__":
    unittest.main()
