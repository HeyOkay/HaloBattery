"""No provider's poll() may raise when the HID layer gives nothing, an error, or odd
descriptors. One provider that raises costs the user every device of that provider for the
cycle (the poll loop logs it and moves on), so each must degrade to "no devices".

hid.enumerate and hid.device are replaced: no real HID device is touched.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers

from test_hide_rename import hb  # noqa: E402

# reads the Windows XInput DLL / Windows.Gaming.Input, not HID
NOT_HID = {"xinput"}


def odd_descriptors(vid=0, *args):
    base = {"vendor_id": vid, "product_id": 0, "path": b"", "usage_page": 0, "usage": 0,
            "product_string": None, "manufacturer_string": None, "serial_number": None,
            "interface_number": -1, "release_number": 0}
    return [dict(base), dict(base, product_id=0xFFFF, path=b"\\\\?\\hid#vid_ffff&pid_ffff#x",
                             product_string="", usage_page=0xFFFF, usage=0xFFFF, interface_number=255),
            dict(base, product_id=0x0001, product_string="電\U0001F3A7", serial_number="\ud800")]


def raising(*args, **kwargs):
    raise OSError("hidapi: enumerate failed")


class PollWithoutDevicesTests(unittest.TestCase):
    def check(self, enumerate_function, device_class):
        providers = [p for p in hb.make_providers() if p.name not in NOT_HID]
        self.assertGreater(len(providers), 40)
        with mock.patch("hid.enumerate", enumerate_function), \
                mock.patch("providers.hidlist.enumerate", enumerate_function), \
                mock.patch("hid.device", device_class):
            for provider in providers:
                with self.subTest(provider=provider.name):
                    result = provider.poll()
                    self.assertIsInstance(result, list)
                    self.assertEqual([], [r for r in result if r.level is not None and not 0 <= r.level <= 100])
                    self.assertIsInstance(provider.diagnostics(), list)

    class Unopenable:
        """What hidapi does when another app holds the device: the object exists, opening fails."""

        def open_path(self, path):
            raise OSError("cannot open")

        open = open_path

        def close(self):
            pass

    def test_no_devices(self):
        self.check(lambda *a, **k: [], self.Unopenable)

    def test_enumerate_raises(self):
        self.check(raising, self.Unopenable)

    def test_odd_descriptors_that_cannot_be_opened(self):
        self.check(odd_descriptors, self.Unopenable)


if __name__ == "__main__":
    unittest.main()
