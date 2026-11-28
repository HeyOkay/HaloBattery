"""Tests for the BatteryHub batch-3 providers: Niceboy, Press Play, EVGA, Piao."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import evga as E  # noqa: E402
from providers import niceboy as N  # noqa: E402
from providers import piao as P  # noqa: E402
from providers import pressplay as R  # noqa: E402


class FakeHid:
    def __init__(self, replies=(), features=()):
        self.replies = list(replies)
        self.features = list(features)
        self.written = False
        self.sent = []

    def open_path(self, path):
        pass

    def write(self, data):
        self.written = True
        self.sent.append(bytes(data))
        return len(data)

    def read(self, size, timeout):
        if not self.written:
            return []
        return self.replies.pop(0) if self.replies else []

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def get_feature_report(self, report_id, length):
        return self.features.pop(0) if self.features else b""

    def close(self):
        pass


def run(module, provider_cls, infos, fake):
    with mock.patch.object(module.hid, "device", return_value=fake), \
            mock.patch.object(module.hidlist, "enumerate", return_value=infos):
        return provider_cls().poll()


class NiceboyTest(unittest.TestCase):
    def test_millivolts_table_and_reply(self):
        self.assertEqual(N.percent_from_mv(4200), 100)
        self.assertEqual(N.percent_from_mv(4133), 90)
        self.assertEqual(N.percent_from_mv(3199), 10)
        raw = 1002
        reply = bytes([5, 0, 0xFA, 0x48, raw & 0xFF, raw >> 8, 1]) + bytes(26)
        self.assertEqual(N.parse_reply(reply), (90, True))
        self.assertIsNone(N.parse_reply(bytes([5, 0, 0xFA, 0x48, 0, 0, 0]) + bytes(26)))

    def test_poll(self):
        infos = [{"path": b"n", "product_id": N.PID, "interface_number": 0,
                  "usage_page": 1, "usage": 0,
                  "input_report_byte_length": 33, "output_report_byte_length": 33}]
        raw = 1002
        reply = bytes([5, 0, 0xFA, 0x48, raw & 0xFF, raw >> 8, 0]) + bytes(26)
        found = run(N, N.NiceboyProvider, infos, FakeHid(replies=[reply]))
        self.assertEqual((found[0].level, found[0].charging), (90, False))

    def test_asleep_keeps_last_reading_greyed(self):
        infos = [{"path": b"n", "product_id": N.PID, "interface_number": 0,
                  "usage_page": 1, "usage": 0,
                  "input_report_byte_length": 33, "output_report_byte_length": 33}]
        raw = 1002
        awake = bytes([5, 0, 0xFA, 0x48, raw & 0xFF, raw >> 8, 0]) + bytes(26)
        asleep = bytes([5, 0, 0xFA, 0x48, 0, 0, 0]) + bytes(26)
        provider = N.NiceboyProvider()
        for reply in (awake, asleep):
            with mock.patch.object(N.hid, "device", return_value=FakeHid(replies=[reply])), \
                    mock.patch.object(N.hidlist, "enumerate", return_value=infos):
                found = provider.poll()
        self.assertEqual((found[0].level, found[0].online, found[0].wake_on_input),
                         (90, False, True))


class PressPlayTest(unittest.TestCase):
    def test_parse(self):
        reply = bytearray(64)
        reply[0:3] = b"\x06\x05\x26"
        reply[13] = 87
        self.assertEqual(R.parse_reply(bytes(reply)), 87)
        self.assertIsNone(R.parse_reply(bytes([6, 5, 0x27] + [0] * 61)))

    def test_poll(self):
        infos = [{"path": b"p", "product_id": R.PID, "usage_page": 0xFFB2, "usage": 1,
                  "input_report_byte_length": 64, "output_report_byte_length": 64}]
        reply = bytearray(64)
        reply[0:3] = b"\x06\x05\x26"
        reply[13] = 64
        found = run(R, R.PressPlayProvider, infos, FakeHid(replies=[bytes(reply)]))
        self.assertEqual((found[0].level, found[0].charging_known), (64, False))


def evga_reply(status, link, level=0, charging=False, report_id=True):
    payload = bytes([status, 0, link, 2, 0, 0x83, 1 if charging else 0, level])
    return (b"\x00" + payload if report_id else payload) + bytes(64 - len(payload))


class EvgaTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(E.parse_reply(evga_reply(E.STATUS_OK, E.LINK_WIRED, 97, True),
                                       E.LINK_WIRED), (E.STATUS_OK, 97, True))
        self.assertEqual(E.parse_reply(evga_reply(E.STATUS_NOT_READY, E.LINK_WIRELESS),
                                       E.LINK_WIRELESS),
                         (E.STATUS_NOT_READY, -1, False))
        self.assertIsNone(E.parse_reply(evga_reply(E.STATUS_OK, E.LINK_WIRED, 50),
                                        E.LINK_WIRELESS))

    def test_cable_wins_and_receiver_is_not_duplicated(self):
        infos = [
            {"path": b"cable", "product_id": E.CABLE, "interface_number": 2,
             "usage_page": 0xFFFF, "usage": 0, "feature_report_byte_length": 65},
            {"path": b"rx", "product_id": E.RECEIVER, "interface_number": 2,
             "usage_page": 0xFFFF, "usage": 0, "feature_report_byte_length": 65},
        ]
        fake = FakeHid(features=[evga_reply(E.STATUS_OK, E.LINK_WIRED, 97, True)])
        found = run(E, E.EvgaProvider, infos, fake)
        self.assertEqual((len(found), found[0].level, found[0].charging), (1, 97, True))

    def test_receiver_asleep_keeps_last_reading_greyed(self):
        infos = [{"path": b"rx", "product_id": E.RECEIVER, "interface_number": 2,
                  "usage_page": 0xFFFF, "usage": 0, "feature_report_byte_length": 65}]
        provider = E.EvgaProvider()
        for reply in (evga_reply(E.STATUS_OK, E.LINK_WIRELESS, 56),
                      evga_reply(E.STATUS_ASLEEP, E.LINK_WIRELESS)):
            with mock.patch.object(E.hid, "device", return_value=FakeHid(features=[reply])), \
                    mock.patch.object(E.hidlist, "enumerate", return_value=infos):
                found = provider.poll()
        self.assertEqual((found[0].level, found[0].online, found[0].wake_on_input),
                         (56, False, True))


class PiaoTest(unittest.TestCase):
    def battery_reply(self, value):
        frame = bytearray(64)
        frame[0], frame[1], frame[2], frame[3] = 3, 0x50, 0, 1
        frame[5], frame[6] = 0x81, value
        return bytes(frame)

    def test_parse_data_and_battery(self):
        self.assertEqual(P.parse_data(self.battery_reply(0xE4), P.CMD_BATTERY, P.SUB_BATTERY),
                         b"\xE4")
        self.assertEqual(P.parse_battery(b"\xE4"), (100, True))
        self.assertEqual(P.parse_battery(b"\x4B"), (75, False))
        self.assertIsNone(P.parse_battery(b"\xFF"))

    def test_poll(self):
        infos = [{"path": b"p", "product_id": P.WIRELESS, "usage_page": 0xFF00,
                  "usage": 1, "feature_report_byte_length": 64}]
        link = bytearray(64)
        link[0], link[1], link[2], link[3] = 3, 0x50, 0, 1
        link[5] = 0x47
        link[7] = 1
        found = run(P, P.PiaoProvider, infos,
                    FakeHid(features=[bytes(link), self.battery_reply(0xE4)]))
        self.assertEqual((found[0].level, found[0].charging), (100, True))

    def test_link_down_keeps_last_reading_greyed(self):
        infos = [{"path": b"p", "product_id": P.WIRELESS, "usage_page": 0xFF00,
                  "usage": 1, "feature_report_byte_length": 64}]
        link = bytearray(64)
        link[0], link[1], link[2], link[3] = 3, 0x50, 0, 1
        link[5] = 0x47
        provider = P.PiaoProvider()
        awake = bytearray(link)
        awake[7] = 1
        for features in ([bytes(awake), self.battery_reply(0x4B)], [bytes(link)]):
            with mock.patch.object(P.hid, "device", return_value=FakeHid(features=features)), \
                    mock.patch.object(P.hidlist, "enumerate", return_value=infos):
                found = provider.poll()
        self.assertEqual((found[0].level, found[0].online, found[0].wake_on_input),
                         (75, False, True))


if __name__ == "__main__":
    unittest.main()
