"""Tests for MCHOSE V9 Pro 2 and VGN OMNISOUND reader protocols."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import mchose_v9pro2 as M  # noqa: E402
from providers import vgn as V  # noqa: E402


class FakeHid:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.written = False

    def open_path(self, path):
        pass

    def write(self, data):
        self.written = True
        return len(data)

    def read(self, size, timeout):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


def run(module, provider_cls, infos, fake):
    with mock.patch.object(module.hid, "device", return_value=fake), \
            mock.patch.object(module.hidlist, "enumerate", return_value=infos):
        return provider_cls().poll()


def mchose_reply(body, notify=False):
    f = [0xAA, 0x01, 0x01, 1 + len(body), 0x00, 0x04 if notify else 0x01,
         0x22 if notify else 0x0B] + list(body)
    check = f[2] ^ f[3] ^ f[4] ^ f[5]
    for i in range(f[3]):
        check ^= f[6 + i]
    b = [0x55] + f + [check]
    return bytes(b + [0] * (64 - len(b)))


class MchoseTest(unittest.TestCase):
    def test_level_and_charging(self):
        frame = mchose_reply([1, 0x80 | 77, 0])
        self.assertEqual(M.parse_reply(frame), ("level", 77, True))
        self.assertEqual(M.parse_reply(mchose_reply([1, 42, 0])), ("level", 42, False))

    def test_off_and_checksum(self):
        self.assertEqual(M.parse_reply(mchose_reply([0, 0, 0])), ("off", 0, False))
        bad = bytearray(mchose_reply([1, 50, 0]))
        bad[10] ^= 0xFF
        self.assertIsNone(M.parse_reply(bytes(bad)))

    def test_notification(self):
        self.assertEqual(M.parse_reply(mchose_reply([1, 0x80 | 33, 0], notify=True)),
                         ("level", 33, True))

    def test_poll(self):
        infos = [{"path": b"m", "product_id": M.PID, "usage_page": 0xFF07,
                  "usage": 0x0222, "input_report_byte_length": 64,
                  "output_report_byte_length": 64}]
        found = run(M, M.MchoseV9Pro2Provider, infos,
                    FakeHid(replies=[[], mchose_reply([1, 0x80 | 88, 0])]))
        self.assertEqual((found[0].level, found[0].charging), (88, True))


class VgnTest(unittest.TestCase):
    def test_parse_numbered_and_unnumbered(self):
        self.assertEqual(V.parse_reply(bytes([0x55, 0x65, 76])), 76)
        self.assertEqual(V.parse_reply(bytes([0x65, 76])), 76)
        self.assertIsNone(V.parse_reply(bytes([0x55, 0x65, 101])))

    def test_poll(self):
        infos = [{"path": b"v", "product_id": V.PID, "usage_page": 0x000C,
                  "usage": 0x0001, "input_report_byte_length": 32,
                  "output_report_byte_length": 64}]
        found = run(V, V.VgnProvider, infos,
                    FakeHid(replies=[[], bytes([0x55, 0x65, 64])]))
        self.assertEqual((found[0].level, found[0].charging_known), (64, False))
        self.assertFalse(found[0].charging)


if __name__ == "__main__":
    unittest.main()
