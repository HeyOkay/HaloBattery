"""Tests for the single-model batch: AKG, Alienware AW920H, AULA A7Pro, PULSE Explore."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import akg as A  # noqa: E402
from providers import alienware_aw920h as W  # noqa: E402
from providers import aula_a7pro as U  # noqa: E402
from providers import pslink as P  # noqa: E402


class FakeHid:
    def __init__(self, replies=(), inputs=(), features=()):
        self.replies = list(replies)
        self.inputs = list(inputs)
        self.features = list(features)
        self.written = False

    def open_path(self, path):
        pass

    def write(self, data):
        self.written = True
        return len(data)

    def read(self, size, timeout):
        return self.replies.pop(0) if self.replies else []

    def get_input_report(self, report_id, length):
        return self.inputs.pop(0) if self.inputs else []

    def get_feature_report(self, report_id, length):
        return self.features.pop(0) if self.features else b""

    def close(self):
        pass


def run(module, provider_cls, infos, fake):
    with mock.patch.object(module.hid, "device", return_value=fake), \
            mock.patch.object(module.hidlist, "enumerate", return_value=infos):
        return provider_cls().poll()


def akg_report(level=50):
    b = bytearray(62)
    b[0], b[1], b[2] = 0x07, 9, 0x80
    b[3:12] = bytes([0x05, 0x5D, 0x05, 0x00, 0xD6, 0x0C, 0x00, 0x00, level])
    return bytes(b)


class AkgTest(unittest.TestCase):
    def test_find_level_respects_valid_window(self):
        self.assertEqual(A.find_level(akg_report(50)), 50)
        stale = bytearray(akg_report(99))
        stale[1] = 0  # valid window no longer covers the old frame
        self.assertIsNone(A.find_level(bytes(stale)))

    def test_poll(self):
        infos = [{"path": b"a", "product_id": A.PID, "interface_number": 3,
                  "usage_page": 0xFF13, "usage": 1,
                  "input_report_byte_length": 62, "output_report_byte_length": 62}]
        found = run(A, A.AkgProvider, infos, FakeHid(inputs=[akg_report(61)]))
        self.assertEqual((found[0].level, found[0].charging_known), (61, False))


def alienware_reply(level=95, charging=False):
    b = bytearray(62)
    b[0:4] = bytes([0x07, 0xC0, 0x0A, 0x09])
    b[4], b[5], b[6], b[7] = 0, 1, level, 1 if charging else 0
    b[8], b[9], b[10], b[11], b[12] = 0, 0x1C, 0x10, level, 0x02
    check = 0
    for value in b[3:13]:
        check ^= value
    b[13] = check
    return bytes(b)


class AlienwareTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(W.parse_reply(alienware_reply(95)), (95, False))
        self.assertEqual(W.parse_reply(alienware_reply(80, True)), (80, True))
        bad = bytearray(alienware_reply(95))
        bad[13] ^= 0xFF
        self.assertIsNone(W.parse_reply(bytes(bad)))

    def test_poll(self):
        infos = [{"path": b"w", "product_id": W.PID, "interface_number": 5,
                  "usage_page": 0xFF13, "usage": 1,
                  "input_report_byte_length": 62, "output_report_byte_length": 62}]
        found = run(W, W.AlienwareAw920hProvider, infos,
                    FakeHid(replies=[alienware_reply(88, True)]))
        self.assertEqual((found[0].level, found[0].charging), (88, True))


class AulaTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(U.parse_reply(bytes([0x55, 0x65, 80, 0]) + bytes(60)), (80, 0))
        self.assertIsNone(U.parse_reply(bytes([0x55, 0x65, 101, 0]) + bytes(60)))
        self.assertIsNone(U.parse_reply(bytes([0x55, 0x65, 80, 1]) + bytes(60)))

    def test_poll(self):
        infos = [{"path": b"u", "product_id": U.PID, "product_string": "AULA-A7Pro",
                  "interface_number": 0, "usage_page": 0xFF90, "usage": 1,
                  "input_report_byte_length": 64, "output_report_byte_length": 64}]
        found = run(U, U.AulaA7ProProvider, infos,
                    FakeHid(replies=[[], bytes([0x55, 0x65, 100, 0]) + bytes(60)]))
        self.assertEqual((found[0].level, found[0].charging), (100, False))
        self.assertEqual(found[0].approx, "on cable, full")


class PsLinkTest(unittest.TestCase):
    def reply(self, report_id, payload):
        b = bytearray(64)
        b[0] = report_id
        b[1:1 + len(payload)] = payload
        return bytes(b)

    def test_levels(self):
        status = bytearray(self.reply(P.STATUS_ID, b""))
        status[P.LEVELS_AT] = 0xAE
        self.assertEqual(P.parse_levels(bytes(status)), (93, 67))
        self.assertEqual(P.nibble_to_percent(15), 100)
        self.assertEqual(P.ascii_name(self.reply(P.NAME_ID, b"PULSE Explore")), "PULSE Explore")

    def test_poll(self):
        infos = [{"path": b"p", "product_id": P.PID, "interface_number": 3,
                  "usage_page": 0xFF01, "usage": 0x20, "feature_report_byte_length": 64}]
        status = bytearray(self.reply(P.STATUS_ID, b""))
        status[P.LEVELS_AT] = 0xAE
        fake = FakeHid(features=[self.reply(P.LINK_ID, b"\x01"),
                                 self.reply(P.NAME_ID, b"PULSE Explore"),
                                 bytes(status)])
        found = run(P, P.PsLinkProvider, infos, fake)
        self.assertEqual((found[0].level, found[0].charging_known), (67, False))
        self.assertEqual(found[0].approx, "L 93%, R 67%")


if __name__ == "__main__":
    unittest.main()
