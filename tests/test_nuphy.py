"""Tests for providers/nuphy.py. No hardware is needed."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import nuphy as N  # noqa: E402


def wh80_reply(level, charging=False):
    body = bytearray(64)
    body[0:8] = bytes([0xAA, 0x05, 0x00, 0x00, 0x01, 0x20, 0x00, 0x00])
    body[8] = level | (0x80 if charging else 0)
    body[3] = sum(body[4:]) & 0xFF
    return bytes(body)


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.opened = []
        self.written = False

    def open_path(self, path):
        self.opened.append(path)

    def write(self, data):
        self.writes.append(bytes(data))
        self.written = True
        return len(data)

    def read(self, size, timeout):
        if not self.written:
            return []
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


def info(pid, iface, page, usage, product=""):
    return {"path": f"path-{pid:04x}-{iface}-{page:04x}".encode(),
            "product_id": pid, "interface_number": iface,
            "usage_page": page, "usage": usage, "product_string": product}


class Wh80Test(unittest.TestCase):
    def test_request_is_the_documented_frame(self):
        req = N.make_wh80_request()
        self.assertEqual(req[1:9], bytes([0x55, 0x05, 0x00, 0x21, 0x01, 0x20, 0x00, 0x00]))
        self.assertEqual(len(req), 65)

    def test_reply_parses_level_and_charging(self):
        self.assertEqual(N.parse_wh80(wh80_reply(100, True)), (100, True))
        self.assertEqual(N.parse_wh80(wh80_reply(73, False)), (73, False))

    def test_bad_checksum_is_rejected(self):
        reply = bytearray(wh80_reply(73))
        reply[3] ^= 0xFF
        self.assertIsNone(N.parse_wh80(bytes(reply)))

    def test_poll_reads_the_cable(self):
        dev = FakeDevice([wh80_reply(80, True)])
        infos = [info(N.WH80_PID, 1, 0x0001, 0x0000)]
        with mock.patch.object(N.hidlist, "enumerate", return_value=infos), \
                mock.patch.object(N.hid, "device", return_value=dev):
            found = N.NuphyProvider().poll()
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].name, found[0].level, found[0].charging),
                         ("NuPhy WH80", 80, True))
        self.assertEqual(dev.writes[0][1:9], bytes([0x55, 0x05, 0x00, 0x21, 0x01, 0x20, 0x00, 0x00]))


class Halo96FrameTest(unittest.TestCase):
    def feed(self, usages, start=0.0):
        frame = N.Halo96Frame()
        out = None
        now = start
        for usage in usages:
            out = frame.consume([0x03, usage & 0xFF, usage >> 8], now)
            now += 0.05
        return out

    def test_digits_without_charging(self):
        self.assertEqual(self.feed([0x07, 0x08 + 5, 0x10 + 9, 0x00]), (89, False))

    def test_charging_prefix(self):
        self.assertEqual(self.feed([0x0F, 0x07, 0x08 + 6, 0x10 + 4, 0x00]), (100, True))

    def test_incomplete_frame_is_ignored(self):
        self.assertIsNone(self.feed([0x07, 0x08 + 5]))

    def test_old_prefix_does_not_charge(self):
        frame = N.Halo96Frame()
        frame.consume([0x03, 0x0F, 0x00], 0.0)
        frame.consume([0x03, 0x07, 0x00], 1.0)
        frame.consume([0x03, 0x08 + 5, 0x00], 1.05)
        frame.consume([0x03, 0x10 + 0, 0x00], 1.10)
        self.assertEqual(frame.consume([0x03, 0x00, 0x00], 1.15), (80, False))


class EchoDevice(FakeDevice):
    """Answers a write with whichever reply is queued."""

    def __init__(self, replies):
        super().__init__(replies)

    def write(self, data):
        super().write(data)
        return len(data)


if __name__ == "__main__":
    unittest.main()
