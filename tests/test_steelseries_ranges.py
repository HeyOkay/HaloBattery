"""Literal SteelSeries range fixtures; no network or hardware.

Bounds: Sapd/HeadsetControl 62e361b085d7716805a3bf524bc9cca3761e0e15,
lib/devices/steelseries_arctis_nova_7.hpp:139-166,
steelseries_arctis_nova_5.hpp:219,
steelseries_arctis_7_plus.hpp:121, steelseries_arctis_gamebuds.hpp:80-95,
steelseries_arctis_1.hpp:63-72 and steelseries_arctis_7.hpp:69-78.
Raw-scale exceptions: torvalds/linux fe2ec83746e501645709761605c2464a44fd2929,
drivers/hid/hid-steelseries-arctis.c:144-150,183-198.
Replies are written independently of provider constants and mapping tables.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _sandbox

_sandbox.install_guards()

from providers import steelseries as S


NO_READING = (None, False, False)
BAD_PERCENTAGES = (-1, 101, 127, 255, 256, None, "50", 50.0, True)
BAD_STEPS = (-1, 5, 9, 100, 255, 256, None, "4", 4.0, True)


def replies_by_request(replies):
    """Apply the real exchange's accept callback to independently specified replies."""
    def ask(request, accept):
        for reply in replies.get(tuple(request[:2]), []):
            if accept(reply):
                return reply
        return None
    return ask


class B0Ranges(unittest.TestCase):
    def test_percentage_endpoints_and_charging(self):
        for parse, reply, offset in (
                (S.parse_nova7, [0xB0, 0x03, 0, 0x01], 2),
                (S.parse_nova5, [0xB0, 0x01, 0, 0, 0x01], 3)):
            for level in (0, 1, 99, 100):
                with self.subTest(parser=parse.__name__, level=level):
                    reply[offset] = level
                    self.assertEqual(parse(reply), (level, True, True))

    def test_invalid_percentages_are_not_clamped(self):
        for parse, reply, offset in (
                (S.parse_nova7, [0xB0, 0x03, 0, 0x01], 2),
                (S.parse_nova5, [0xB0, 0x01, 0, 0, 0x01], 3)):
            for level in BAD_PERCENTAGES:
                with self.subTest(parser=parse.__name__, level=level):
                    reply[offset] = level
                    self.assertEqual(parse(reply), NO_READING)

    def test_five_step_levels(self):
        for parse in (S.parse_nova7_discrete, S.parse_arctis7_plus):
            for raw, expected in ((0, 0), (1, 25), (2, 50), (3, 75), (4, 100)):
                with self.subTest(parser=parse.__name__, raw=raw):
                    self.assertEqual(parse([0xB0, 0x03, raw, 0x01]),
                                     (expected, True, True))

    def test_invalid_steps_are_not_clamped(self):
        for parse in (S.parse_nova7_discrete, S.parse_arctis7_plus):
            for level in BAD_STEPS:
                with self.subTest(parser=parse.__name__, level=level):
                    self.assertEqual(parse([0xB0, 0x03, level, 0x01]), NO_READING)

    def test_truncated_and_unrelated_reports(self):
        for parse, reply in (
                (S.parse_nova7, [0xB0, 0x03, 100, 0x03]),
                (S.parse_nova7_discrete, [0xB0, 0x03, 4, 0x03]),
                (S.parse_nova5, [0xB0, 0x01, 0, 100, 0x00]),
                (S.parse_arctis7_plus, [0xB0, 0x03, 4, 0x00]),
                (S.parse_gamebuds, [0xB0, 0, 0, 0x03, 0x03, 100, 100])):
            for size in range(len(reply)):
                with self.subTest(parser=parse.__name__, size=size):
                    self.assertEqual(parse(reply[:size]), NO_READING)
            with self.subTest(parser=parse.__name__, unrelated=True):
                self.assertEqual(parse([0x01] + reply[1:]), NO_READING)

    def test_gamebuds_valid_levels_include_zero(self):
        for reply, expected in (
                ([0xB0, 0, 0, 3, 3, 0, 100], 0),
                ([0xB0, 0, 0, 3, 3, 100, 100], 100),
                ([0xB0, 0, 0, 3, 3, 80, 60], 60),
                ([0xB0, 0, 0, 2, 3, 255, 100], 100),
                ([0xB0, 0, 0, 3, 2, 0, 255], 0)):
            with self.subTest(reply=reply):
                self.assertEqual(S.parse_gamebuds(reply), (expected, False, True))

    def test_each_active_bud_is_validated_before_taking_minimum(self):
        for level in BAD_PERCENTAGES:
            for reply in ([0xB0, 0, 0, 3, 3, level, 60],
                          [0xB0, 0, 0, 3, 3, 60, level],
                          [0xB0, 0, 0, 3, 2, level, 0],
                          [0xB0, 0, 0, 2, 3, 0, level]):
                with self.subTest(reply=reply):
                    self.assertEqual(S.parse_gamebuds(reply), NO_READING)

    def test_poll_does_not_publish_invalid_levels(self):
        for pid, reply in ((0x22A1, [0xB0, 3, 255, 1]),
                           (0x2202, [0xB0, 3, 5, 1]),
                           (0x2232, [0xB0, 1, 0, 101, 1]),
                           (0x220E, [0xB0, 3, 9, 1]),
                           (0x230A, [0xB0, 0, 0, 3, 3, 60, 255])):
            descriptor = {"product_id": pid, "interface_number": 3,
                          "usage_page": 0xFFC0, "usage": 1, "path": b"fake-headset"}
            with self.subTest(pid=pid), \
                    mock.patch.object(S.hidlist, "enumerate", return_value=[descriptor]), \
                    mock.patch.object(S.SteelSeriesProvider, "_read", return_value=reply):
                self.assertEqual(S.SteelSeriesProvider().poll(), [])


class ClassicRanges(unittest.TestCase):
    def test_arctis1_percentage_bounds(self):
        for level in (0, 1, 99, 100) + BAD_PERCENTAGES:
            with self.subTest(level=level):
                ask = replies_by_request({(6, 0x12): [[6, 0x12, 0, level]]})
                expected = (level, False, True) if type(level) is int and 0 <= level <= 100 else NO_READING
                self.assertEqual(S.exchange_arctis1(ask), expected)

    def test_arctis7_percentage_bounds_and_zero_offline(self):
        for level in (0, 1, 99, 100) + BAD_PERCENTAGES:
            with self.subTest(level=level):
                ask = replies_by_request({(6, 0x18): [[6, 0x18, level, 0]]})
                expected = (level, False, True) if type(level) is int and 0 < level <= 100 else NO_READING
                self.assertEqual(S.exchange_arctis7(ask), expected)

    def test_arctis7_2018_preserves_documented_overreport_cap(self):
        for raw, expected in ((0, 0), (100, 100), (101, 100), (104, 100), (255, 100)):
            with self.subTest(raw=raw):
                ask = replies_by_request({(6, 0x14): [[6, 0x14, 3, 0]],
                                          (6, 0x18): [[6, 0x18, raw, 0]]})
                self.assertEqual(S.exchange_arctis7_2018(ask), (expected, False, True))

    def test_arctis9_preserves_raw_scale_endpoint_saturation(self):
        for raw, expected in ((0, 0), (99, 0), (100, 0), (127, 50),
                              (154, 100), (155, 100), (255, 100)):
            with self.subTest(raw=raw):
                ask = replies_by_request({(0, 0x20): [[0xAA, 1, 0, raw, 1]]})
                self.assertEqual(S.exchange_arctis9(ask), (expected, True, True))

    def test_raw_scale_fields_still_require_bytes(self):
        for raw in (-1, 256, None, "104", 104.0, True):
            with self.subTest(raw=raw):
                ask7 = replies_by_request({(6, 0x14): [[6, 0x14, 3, 0]],
                                           (6, 0x18): [[6, 0x18, raw, 0]]})
                ask9 = replies_by_request({(0, 0x20): [[0xAA, 1, 0, raw, 0]]})
                self.assertEqual(S.exchange_arctis7_2018(ask7), NO_READING)
                self.assertEqual(S.exchange_arctis9(ask9), NO_READING)

    def test_pro_wireless_five_steps(self):
        for raw, expected in ((0, 0), (1, 25), (2, 50), (3, 75), (4, 100)):
            with self.subTest(raw=raw):
                ask = replies_by_request({(0x41, 0xAA): [[4]], (0x40, 0xAA): [[raw]]})
                self.assertEqual(S.exchange_pro_wireless(ask), (expected, False, True))

    def test_pro_wireless_invalid_steps(self):
        for raw in BAD_STEPS:
            with self.subTest(raw=raw):
                ask = replies_by_request({(0x41, 0xAA): [[4]], (0x40, 0xAA): [[raw]]})
                self.assertEqual(S.exchange_pro_wireless(ask), NO_READING)

    def test_nova_pro_nine_step_bounds(self):
        for raw, expected in ((0, 0), (1, 12), (4, 50), (7, 87), (8, 100)):
            with self.subTest(raw=raw):
                reply = [0, 0, 0, 0, 0, 0, raw, 0, 0, 0, 0, 0, 0, 0, 0, 8]
                ask = replies_by_request({(6, 0xB0): [reply]})
                self.assertEqual(S.exchange_nova_pro(ask), (expected, False, True))

    def test_nova_pro_invalid_steps(self):
        for raw in (-1, 9, 100, 255, None, "8", 8.0, True):
            with self.subTest(raw=raw):
                reply = [0, 0, 0, 0, 0, 0, raw, 0, 0, 0, 0, 0, 0, 0, 0, 8]
                self.assertEqual(S.exchange_nova_pro(replies_by_request({(6, 0xB0): [reply]})),
                                 NO_READING)

    def test_classic_truncated_replies(self):
        for exchange, request, reply in (
                (S.exchange_arctis1, (6, 0x12), [6, 0x12, 0, 100]),
                (S.exchange_arctis7, (6, 0x18), [6, 0x18, 100, 0]),
                (S.exchange_arctis9, (0, 0x20), [0xAA, 1, 0, 154, 0]),
                (S.exchange_nova_pro, (6, 0xB0), [0, 0, 0, 0, 0, 0, 8, 0, 0, 0, 0, 0, 0, 0, 0, 8])):
            for size in range(len(reply)):
                with self.subTest(exchange=exchange.__name__, size=size):
                    self.assertEqual(exchange(replies_by_request({request: [reply[:size]]})),
                                     NO_READING)


class MouseRanges(unittest.TestCase):
    def test_rival_percentage_bounds_in_both_reply_layouts(self):
        for raw in (0, 1, 99, 100) + BAD_PERCENTAGES:
            replies = [[0xAA, raw, 0, 1], [0, 0xAA, raw, 0, 1]]
            # An unnumbered no-echo 0% is ambiguous with a leading report ID.
            # The existing prefix policy is independent of battery validation.
            if raw != 0:
                replies.append([raw, 0, 1])
            for reply in replies:
                with self.subTest(reply=reply):
                    expected = (raw, True, True) if type(raw) is int and 0 <= raw <= 100 else NO_READING
                    self.assertEqual(S.parse_rival3(reply), expected)

    def test_aerox_steps_percentages_and_charging(self):
        for raw, expected in ((1, (0, False, True)), (21, (100, False, True)),
                              (22, (22, False, True)), (100, (100, False, True)),
                              (129, (0, True, True)), (149, (100, True, True)),
                              (228, (100, True, True))):
            with self.subTest(raw=raw):
                self.assertEqual(S.parse_aerox3([0xD2, raw]), expected)

    def test_aerox_invalid_raw_bytes_and_levels(self):
        for raw in (0, 101, 127, 128, 229, 255, -1, 256, None, "21", 21.0, True):
            with self.subTest(raw=raw):
                self.assertEqual(S.parse_aerox3([0xD2, raw]), NO_READING)


if __name__ == "__main__":
    unittest.main()
