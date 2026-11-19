"""Astro A50 Gen 5 collection safety, using fake HID handles only.

Collection and frame evidence: HeadsetControl logitech_astro_a50.hpp and
HaloBattery #31's #22 dump (046d:0b1c, ff32:0074). No hardware/network.
"""
import unittest
from unittest import mock

from providers import astro as A


def entry(path, page, usage, *, iface=8, pid=0x0B1C):
    return dict(path=path, vendor_id=0x046D, product_id=pid,
                usage_page=page, usage=usage, interface_number=iface)


class FakeDevice:
    def __init__(self, reply=None, *, disappears=False):
        self.reply = [0x02, 0x0C, 0x06, 0x00, 0x06, 0x00, 73, 73, 1] if reply is None else reply
        self.disappears = disappears
        self.opened = []
        self.writes = []
        self.closed = False

    def open_path(self, path):
        self.opened.append(path)
        if self.disappears:
            raise OSError("receiver disappeared")

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms):
        return list(self.reply)

    def close(self):
        self.closed = True


class AstroCollectionTests(unittest.TestCase):
    def poll(self, entries, device):
        provider = A.AstroProvider()
        with mock.patch.object(A.hidlist, "enumerate", return_value=entries), \
                mock.patch.object(A.hid, "device", return_value=device) as create:
            result = provider.poll()
        return result, provider.diagnostics(), create

    def test_exact_usage_pair_receives_only_the_sourced_battery_frame(self):
        device = FakeDevice()
        result, _, _ = self.poll([entry(b"consumer", 0x000C, 1),
                                  entry(b"control", 0xFF32, 0x0074, iface=12)], device)
        self.assertEqual(device.opened, [b"control"])
        self.assertEqual(device.writes, [bytes([0x02, 0x0C, 0x03, 0x00, 0x06, 0x0C]) + bytes(58)])
        self.assertEqual([(s.key, s.level, s.charging) for s in result], [("astro:0b1c", 73, True)])
        self.assertTrue(device.closed)

    def test_missing_control_pair_never_opens_or_writes_another_collection(self):
        for page, usage in ((0xFF32, 1), (0xFF32, None), (0xFF00, 0x0074),
                            (0x000C, 1), (None, None)):
            with self.subTest(page=page, usage=usage):
                device = FakeDevice()
                result, diag, create = self.poll([entry(b"other", page, usage)], device)
                self.assertEqual(result, [])
                create.assert_not_called()
                self.assertEqual(device.opened, [])
                self.assertEqual(device.writes, [])
                self.assertTrue(any("not opening HID" in line for line in diag))

    def test_unknown_product_never_uses_the_matching_vendor_collection(self):
        device = FakeDevice()
        result, _, create = self.poll([entry(b"other-model", 0xFF32, 0x0074, pid=0xFFFF)], device)
        self.assertEqual(result, [])
        create.assert_not_called()

    def test_disappearing_control_does_not_fall_back_to_another_path(self):
        device = FakeDevice(disappears=True)
        result, _, _ = self.poll([entry(b"control", 0xFF32, 0x0074),
                                  entry(b"other", 0x000C, 1)], device)
        self.assertEqual(result, [])
        self.assertEqual(device.opened, [b"control"])
        self.assertEqual(device.writes, [])

    def test_truncated_garbage_and_out_of_range_replies_do_not_show_a_level(self):
        for reply in ([], [0x02], [0x02, 0x0C, 0x06, 0, 0x06],
                      [0x02, 0x0C, 0x06, 0, 0x06, 0, 101, 101, 0],
                      [0xFF] * 64):
            with self.subTest(reply=reply):
                device = FakeDevice(reply)
                result, _, _ = self.poll([entry(b"control", 0xFF32, 0x0074)], device)
                self.assertEqual(result, [])
                self.assertEqual(device.opened, [b"control"])


if __name__ == "__main__":
    unittest.main()
