"""The standard-HID reader: layout, scaling, charge flags and report parsing."""
import ctypes
import unittest

from halo import hidp


class LayoutTests(unittest.TestCase):
    def test_value_caps_matches_the_windows_header(self):
        # Windows' parser writes these structures; a wrong size would misalign every
        # entry after the first and turn the usage pages into garbage.
        self.assertEqual(ctypes.sizeof(hidp._VALUE_CAPS), 72)
        self.assertEqual(hidp._VALUE_CAPS.UsagePage.offset, 0)
        self.assertEqual(hidp._VALUE_CAPS.ReportID.offset, 2)
        self.assertEqual(hidp._VALUE_CAPS.LinkCollection.offset, 6)
        self.assertEqual(hidp._VALUE_CAPS.LogicalMin.offset, 40)
        self.assertEqual(hidp._VALUE_CAPS.UsageMin.offset, 56)

    def test_caps_matches_the_windows_header(self):
        self.assertEqual(ctypes.sizeof(hidp._HIDP_CAPS), 64)
        self.assertEqual(hidp._HIDP_CAPS.FeatureReportByteLength.offset, 8)
        self.assertEqual(hidp._HIDP_CAPS.NumberFeatureValueCaps.offset, 60)


class ScalingTests(unittest.TestCase):
    def test_a_zero_to_hundred_value_is_the_level(self):
        self.assertEqual(hidp.scale_value(64, 0, 100), 64)

    def test_a_wider_logical_range_is_scaled(self):
        self.assertEqual(hidp.scale_value(128, 0, 255), 50)

    def test_the_minimum_matters(self):
        self.assertEqual(hidp.scale_value(1, 1, 5), 0)

    def test_apple_reports_a_percentage_over_a_255_range(self):
        # HID_BATTERY_QUIRK_PERCENT in Linux: the value is already a percentage
        self.assertEqual(hidp.scale_value(87, 0, 255, 0x05AC), 87)

    def test_the_apple_quirk_does_not_apply_above_100(self):
        self.assertEqual(hidp.scale_value(128, 0, 255, 0x05AC), 50)

    def test_nonsense_is_refused(self):
        self.assertIsNone(hidp.scale_value(300, 0, 0))
        self.assertIsNone(hidp.scale_value(150, 0, 0))


class ChargeFlagTests(unittest.TestCase):
    def test_charging(self):
        self.assertEqual(hidp.charge_flags({0x44}), (True, False))

    def test_full_is_charging_too(self):
        self.assertEqual(hidp.charge_flags({0x46}), (True, True))

    def test_ac_present_means_charging(self):
        self.assertEqual(hidp.charge_flags({0xD0}), (True, False))

    def test_ac_present_while_discharging_is_not_charging(self):
        self.assertEqual(hidp.charge_flags({0xD0, 0x45}), (False, False))

    def test_nothing_is_not_charging(self):
        self.assertEqual(hidp.charge_flags(set()), (False, False))


class FakeHid:
    """Enough of hid.dll for _read_report: one 0-255 level usage in report 3."""

    def __init__(self, level=128):
        self.level = level
        self.reads = 0

    def HidP_GetValueCaps(self, report_type, caps, used, pp):
        caps[0].UsagePage = 0x06
        caps[0].UsageMin = 0x20
        caps[0].ReportID = 3
        caps[0].LinkCollection = 1
        caps[0].LogicalMin = 0
        caps[0].LogicalMax = 255
        used._obj.value = 1
        return hidp.HIDP_SUCCESS

    def HidP_GetUsageValue(self, report_type, page, link, usage, value, pp, report, length):
        value._obj.value = self.level
        return hidp.HIDP_SUCCESS

    def HidP_MaxUsageListLength(self, report_type, page, pp):
        return 0

    def HidD_GetFeature(self, handle, report, length):
        self.reads += 1
        return True

    HidD_GetInputReport = HidD_GetFeature


class ReportTests(unittest.TestCase):
    def test_a_standard_level_is_read_and_scaled(self):
        fake = FakeHid(128)
        got = hidp._read_report(fake, ctypes.c_void_p(1), ctypes.c_void_p(2),
                                hidp.HIDP_FEATURE, 8, 1, 0)
        self.assertIsNotNone(got)
        self.assertEqual(got.level, 50)
        self.assertFalse(got.charging)
        self.assertFalse(got.full)
        self.assertFalse(got.charging_known)
        self.assertEqual(fake.reads, 1, "one feature report, no writes")

    def test_a_failed_feature_read_is_dropped(self):
        class Refusing(FakeHid):
            def HidD_GetFeature(self, handle, report, length):
                return False

        self.assertIsNone(hidp._read_report(Refusing(), ctypes.c_void_p(1),
                                            ctypes.c_void_p(2),
                                            hidp.HIDP_FEATURE, 8, 1, 0))

    def test_no_matching_usage_reads_nothing(self):
        class Other(FakeHid):
            def HidP_GetValueCaps(self, report_type, caps, used, pp):
                caps[0].UsagePage = 0xFF42
                caps[0].UsageMin = 1
                caps[0].LogicalMax = 255
                used._obj.value = 1
                return hidp.HIDP_SUCCESS

        self.assertIsNone(hidp._read_report(Other(), ctypes.c_void_p(1),
                                            ctypes.c_void_p(2),
                                            hidp.HIDP_FEATURE, 8, 1, 0))


if __name__ == "__main__":
    unittest.main()
