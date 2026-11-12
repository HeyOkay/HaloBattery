"""Stable release holds must stop before opening a HID device. No hardware."""
import unittest
import types
from unittest import mock

from providers import ajazz, endgame_4k, turtlebeach, sony_inzone, lamzu_sw, logitech_centurion


class StableDevicePolicyTests(unittest.TestCase):
    def test_aj159_and_kysona_are_not_opened_even_with_valid_vendor_collections(self):
        entries = [{"product_id": pid, "usage_page": 0xFFFF, "usage": 2,
                    "path": b"held", "interface_number": 2}
                   for _, pid, _ in ajazz.ALT_TARGETS]
        with mock.patch.object(ajazz.hidlist, "enumerate",
                               side_effect=lambda vid=0: entries if vid == 0x3151 else []), \
             mock.patch.object(ajazz.hid, "device") as device:
            self.assertEqual(ajazz.AjazzProvider().poll(), [])
            device.assert_not_called()

    def test_stealth_600x_is_not_opened(self):
        entries = [{"product_id": 0x2232, "usage_page": 0xFF13, "usage": 1,
                    "path": b"held", "serial_number": "test"}]
        with mock.patch.object(turtlebeach.hidlist, "enumerate", return_value=entries), \
             mock.patch.object(turtlebeach.hid, "device") as device:
            provider = turtlebeach.TurtleBeachProvider()
            self.assertEqual(provider.poll(), [])
            device.assert_not_called()
            self.assertTrue(any("release hold" in line for line in provider.diagnostics()))

    def test_endgame_4k_is_not_opened_with_both_required_collections(self):
        entries = [{"product_id": 0x1970, "usage_page": page, "usage": 2,
                    "path": path, "interface_number": 1}
                   for page, path in ((0xFF02, b"feature"), (0xFF01, b"input"))]
        with mock.patch.object(endgame_4k.hidlist, "enumerate", return_value=entries), \
             mock.patch.object(endgame_4k.hid, "device") as device:
            provider = endgame_4k.Endgame4kProvider()
            self.assertEqual(provider.poll(), [])
            device.assert_not_called()
            self.assertTrue(any("release hold" in line for line in provider.diagnostics()))

    def test_h9_ii_does_not_open_its_serial_port(self):
        port = types.SimpleNamespace(vid=0x054C, pid=0x0FA8, device="COM9")
        # Replace the optional modules themselves: this policy must be exercised
        # even when pyserial is not installed, rather than skipped at import time.
        ports = types.SimpleNamespace(comports=mock.Mock(return_value=[port]))
        serial = types.SimpleNamespace(Serial=mock.Mock())
        with mock.patch.object(sony_inzone, "list_ports", ports), \
             mock.patch.object(sony_inzone, "serial", serial):
            provider = sony_inzone.SonyInzoneProvider()
            self.assertEqual(provider.poll(), [])
            serial.Serial.assert_not_called()
            self.assertTrue(any("release hold" in line for line in provider.diagnostics()))

    def test_tachi_does_not_open_its_vendor_collection(self):
        entries = [{"product_id": 0x000C, "usage_page": 0xFFA0, "usage": 1,
                    "path": b"held", "interface_number": 1}]
        with mock.patch.object(lamzu_sw.hidlist, "enumerate", return_value=entries), \
             mock.patch.object(lamzu_sw.hid, "device") as device:
            provider = lamzu_sw.LamzuSinowealthProvider()
            self.assertEqual(provider.poll(), [])
            device.assert_not_called()
            self.assertTrue(any("release hold" in line for line in provider.diagnostics()))

    def test_pro_x_3_does_not_open_its_centurion_collection(self):
        entries = [{"product_id": 0x0B45, "usage_page": 0xFFA0, "usage": 1,
                    "path": b"held", "interface_number": 3}]
        with mock.patch.object(logitech_centurion.hidlist, "enumerate", return_value=entries), \
             mock.patch.object(logitech_centurion.hid, "device") as device:
            provider = logitech_centurion.LogitechCenturionProvider()
            self.assertEqual(provider.poll(), [])
            device.assert_not_called()
            self.assertTrue(any("release hold" in line for line in provider.diagnostics()))

    def test_confirmed_pro_x_2_is_still_polled(self):
        entries = [{"product_id": 0x0AF7, "usage_page": 0xFFA0, "usage": 1,
                    "path": b"prox2", "interface_number": 3}]
        with mock.patch.object(logitech_centurion.hidlist, "enumerate", return_value=entries):
            provider = logitech_centurion.LogitechCenturionProvider()
            with mock.patch.object(provider, "_read", return_value=(64, True)) as read:
                [status] = provider.poll()
                read.assert_called_once_with(b"prox2")
                self.assertEqual((status.level, status.charging), (64, True))

    def test_confirmed_atlas_air_is_still_polled(self):
        entries = [{"product_id": 0x225E, "usage_page": 0xFF13, "usage": 1,
                    "path": b"atlas", "serial_number": "test"}]
        with mock.patch.object(turtlebeach.hidlist, "enumerate", return_value=entries):
            provider = turtlebeach.TurtleBeachProvider()
            with mock.patch.object(provider, "_read_status", return_value=(60, "Atlas", True)) as read:
                [status] = provider.poll()
                read.assert_called_once_with(b"atlas", 0x225E)
                self.assertEqual((status.level, status.charging), (60, True))


if __name__ == "__main__":
    unittest.main()
