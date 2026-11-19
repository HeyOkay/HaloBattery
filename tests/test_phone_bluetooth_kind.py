"""Phone pictograms use reported classes, without another Bluetooth request."""
import unittest
from providers.bluetooth import kind_from_class


class PhoneKindTests(unittest.TestCase):
    def test_classic_mobile_classes_use_phone_without_matching_names(self):
        for value in (0x0200, 0x0204, 0x0208, 0x020C, 0x5A020C):
            with self.subTest(value=value):
                self.assertEqual(kind_from_class(value, False), "phone")
        for value in (0x010C, 0x0210, 0x0214, 0x0218):
            with self.subTest(value=value):
                self.assertEqual(kind_from_class(value, False), "")

    def test_le_generic_phone_keeps_reserved_appearances_unknown(self):
        self.assertEqual(kind_from_class(0x0040, True), "phone")
        for value in (0x0041, 0x0080, None, "not a class"):
            with self.subTest(value=value):
                self.assertEqual(kind_from_class(value, True), "")

    def test_existing_accessory_classes_keep_their_pictograms(self):
        for value, le, expected in ((0x0540, False, "keyboard"),
                                     (0x0580, False, "mouse"),
                                     (0x0404, False, "headset"),
                                     (0x03C1, True, "keyboard"),
                                     (0x03C2, True, "mouse")):
            with self.subTest(value=value):
                self.assertEqual(kind_from_class(value, le), expected)
