"""Edge cases for the pure battery-reply parsers of a sample of providers.

A bad or truncated device reply must never raise inside a poll, and must never turn
into a wrong number: a reading is either None or a level from 0 to 100. No hardware.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import asus, aula, corsair, jabra, logitech as L, pulsar, steelseries  # noqa: E402

# what a HID read can hand back, whatever the device: nothing, a stub, or filler
GARBAGE = {
    "empty bytes": b"",
    "empty list": [],
    "one zero": [0],
    "one 0xFF": [0xFF],
    "two bytes": [0xB0, 0x03],
    "all zero": [0] * 64,
    "all 0xFF": [0xFF] * 64,
    "ramp": list(range(64)),
    "bytes all 0xFF": b"\xff" * 64,
}


def sane_level(test, level):
    test.assertTrue(level is None or (isinstance(level, int) and 0 <= level <= 100), level)


class LogitechTests(unittest.TestCase):
    def test_voltage_curve_knots_and_bounds(self):
        for mv, expected in ((L.VOLTAGE_CURVE[0][0], 100), (L.VOLTAGE_CURVE[0][0] + 1, 100),
                             (65535, 100), (3500, 0), (3499, 0), (0, 0), (-5, 0),
                             (3646, 5), (3579, 2)):
            with self.subTest(mv=mv):
                self.assertEqual(L.voltage_to_percent(mv), expected)

    def test_voltage_curve_is_monotonic_and_in_range(self):
        previous = -1
        for mv in range(0, 5000):
            level = L.voltage_to_percent(mv)
            self.assertTrue(0 <= level <= 100)
            self.assertGreaterEqual(level, previous, mv)
            previous = level

    def test_voltage_reply_flags(self):
        # 0x1001: bytes 0-1 are the millivolts, bit 7 of byte 2 says "charging"
        full = [0x10, 0x5A, 0x00]                     # 4186 mV
        for flags, charging in ((0x00, False), (0x80, True), (0x81, True), (0x7F, False)):
            with self.subTest(flags=flags):
                self.assertEqual(L.parse_battery(L.F_VOLTAGE, [0x10, 0x5A, flags]), (100, charging, ""))
        self.assertEqual(L.parse_battery(L.F_VOLTAGE, full), (100, False, ""))

    def test_charge_complete_only_for_charging_with_low_bits_one(self):
        for flags, expected in ((0x81, True), (0x80, False), (0x01, False), (0x83, False), (0x82, False),
                                (0xFF, False)):
            with self.subTest(flags=flags):
                self.assertIs(L.charge_complete(L.F_VOLTAGE, [0x10, 0x5A, flags]), expected)
        self.assertFalse(L.charge_complete(L.F_UNIFIED, [0x10, 0x5A, 0x81]))   # only 0x1001 has it

    def test_voltage_that_is_not_a_battery_is_not_a_level(self):
        for p in ([0, 0, 0], [0x09, 0xC3, 0x80], [0x00, 0x01, 0x00]):             # under 2500 mV
            with self.subTest(p=p):
                self.assertEqual(L.parse_battery(L.F_VOLTAGE, p), (None, False, ""))
        # a filler reply is 65535 mV, not a full, charging battery
        for feature in (L.F_VOLTAGE, L.F_ADC):
            with self.subTest(feature=hex(feature)):
                self.assertEqual(L.parse_battery(feature, [0xFF, 0xFF, 0xFF]), (None, False, ""))

    def test_unified_percentage_and_flags(self):
        table = (([50, 0, 0], (50, False, "")), ([100, 0, 1], (100, True, "")),
                 ([1, 0, 4], (1, True, "")), ([0, 8, 0], (90, False, "about 90% (full)")),
                 ([0, 1, 2], (5, True, "about 5% (critical)")),
                 ([0, 3, 0], (None, False, "")),          # an unknown level flag
                 ([0, 0, 0], (None, False, "")), ([101, 0, 0], (None, False, "")),
                 ([0xFF, 0xFF, 0xFF], (None, False, "")))
        for p, expected in table:
            with self.subTest(p=p):
                self.assertEqual(L.parse_battery(L.F_UNIFIED, p), expected)

    def test_status_feature_range(self):
        for p, expected in (([0, 0, 0], (None, False, "")), ([100, 0, 2], (100, True, "")),
                            ([101, 0, 0], (None, False, "")), ([0xFF] * 3, (None, False, ""))):
            with self.subTest(p=p):
                self.assertEqual(L.parse_battery(L.F_STATUS, p), expected)

    def test_adc_headset_not_connected_is_none(self):
        self.assertEqual(L.parse_battery(L.F_ADC, [0x0F, 0xA0, 0x00]), (None, False, ""))
        level, charging, _ = L.parse_battery(L.F_ADC, [0x0F, 0xA0, 0x03])
        self.assertEqual((level, charging), (L.voltage_to_percent(0x0FA0), True))

    def test_unknown_feature_and_garbage_padding_never_raise_or_overflow(self):
        for feature in (0x0000, 0x1234, L.F_UNIFIED, L.F_STATUS, L.F_VOLTAGE, L.F_ADC):
            for name, fill in (("zero", 0x00), ("ff", 0xFF), ("0x80", 0x80)):
                with self.subTest(feature=hex(feature), fill=name):
                    level, charging, approx = L.parse_battery(feature, [fill] * 16)
                    sane_level(self, level)
                    self.assertIsInstance(charging, bool)
                    self.assertIsInstance(approx, str)

    def test_register_replies(self):
        table = ((L.REG_BATTERY_CHARGE, [50, 0, 0x50], (50, True, "")),
                 (L.REG_BATTERY_CHARGE, [100, 0, 0x90], (100, True, "")),
                 (L.REG_BATTERY_CHARGE, [60, 0, 0x30], (60, False, "")),
                 (L.REG_BATTERY_CHARGE, [0, 0, 0x50], (None, False, "")),
                 (L.REG_BATTERY_CHARGE, [101, 0, 0x50], (None, False, "")),
                 (L.REG_BATTERY_STATUS, [7, 0x00, 0], (90, False, "about 90% (full)")),
                 (L.REG_BATTERY_STATUS, [1, 0x21, 0], (5, True, "about 5% (critical)")),
                 (L.REG_BATTERY_STATUS, [9, 0x00, 0], (None, False, "")),
                 (0x99, [50, 0, 0], (None, False, "")))
        for reg, p, expected in table:
            with self.subTest(reg=hex(reg), p=p):
                self.assertEqual(L.parse_register_battery(reg, p), expected)
        for reg in (L.REG_BATTERY_CHARGE, L.REG_BATTERY_STATUS):
            level, _, _ = L.parse_register_battery(reg, [0xFF] * 16)
            self.assertIsNone(level)


class SteelSeriesTests(unittest.TestCase):
    PARSERS = (steelseries.parse_nova7, steelseries.parse_nova7_discrete, steelseries.parse_nova5,
               steelseries.parse_arctis7_plus, steelseries.parse_gamebuds)

    def test_garbage_never_raises_and_stays_in_range(self):
        for parser in self.PARSERS:
            for name, reply in GARBAGE.items():
                with self.subTest(parser=parser.__name__, reply=name):
                    level, charging, online = parser(reply)
                    sane_level(self, level)
                    self.assertIsInstance(charging, bool)
                    self.assertIsInstance(online, bool)

    def test_every_truncation_of_a_valid_reply_is_safe(self):
        valid = {steelseries.parse_nova7: [0xB0, 0x03, 80, 0x01, 0, 0, 0, 0],
                 steelseries.parse_nova5: [0xB0, 0x01, 0, 80, 0x01, 0, 0, 0],
                 steelseries.parse_arctis7_plus: [0xB0, 0x00, 3, 0x01, 0, 0, 0, 0],
                 steelseries.parse_gamebuds: [0xB0, 0, 0, 3, 3, 70, 60, 0]}
        for parser, frame in valid.items():
            self.assertIsNotNone(parser(frame)[0])
            for n in range(len(frame)):
                with self.subTest(parser=parser.__name__, length=n):
                    level, _, online = parser(frame[:n])
                    self.assertTrue(level is None or n >= 4)
                    sane_level(self, level)

    def test_levels_above_100_are_clamped_not_shown(self):
        self.assertEqual(steelseries.parse_nova7([0xB0, 0x03, 0xFF, 0x01]), (100, True, True))
        self.assertEqual(steelseries.parse_nova5([0xB0, 0x01, 0, 0xFF, 0x00]), (100, False, True))
        self.assertEqual(steelseries.parse_arctis7_plus([0xB0, 0x00, 0xFF, 0x00]), (100, False, True))
        self.assertEqual(steelseries.parse_nova7_discrete([0xB0, 0x03, 0xFF, 0x01]), (100, True, True))

    def test_headset_off_is_offline_not_a_stale_level(self):
        self.assertEqual(steelseries.parse_nova7([0xB0, 0x03, 55, 0x00]), (None, False, False))
        self.assertEqual(steelseries.parse_arctis7_plus([0xB0, 0x01, 3, 0x00]), (None, False, False))
        self.assertEqual(steelseries.parse_nova5([0xB0, 0x02, 0, 55, 0]), (None, False, False))

    def test_gamebuds_both_buds_in_case_is_none(self):
        self.assertEqual(steelseries.parse_gamebuds([0xB0, 0, 0, 0, 0, 70, 60]), (None, False, False))
        self.assertEqual(steelseries.parse_gamebuds([0xB0, 0, 0, 3, 0, 70, 60]), (70, False, True))
        self.assertEqual(steelseries.parse_gamebuds([0xB0, 0, 0, 3, 3, 70, 60]), (60, False, True))


class CorsairTests(unittest.TestCase):
    @staticmethod
    def bragi(value, shape="dark"):
        lo, hi = value & 0xFF, value >> 8
        head = ([corsair.BRAGI_ROUTE_CHILD, corsair.BRAGI_CMD_GET, 0x00, lo, hi] if shape == "dark"
                else [corsair.BRAGI_ROUTE_CHILD, corsair.BRAGI_ROUTE_CHILD, corsair.BRAGI_CMD_GET,
                      0x00, lo, hi])
        return head + [0] * (corsair.BRAGI_MSG_SIZE - len(head))

    def test_bragi_levels_and_limits(self):
        for shape in ("dark", "virtuoso"):
            for value, expected in ((765, 76), (1000, 100), (10, 1), (9, 0), (1, 0), (0, None),
                                    (1001, None), (0xFFFF, None)):
                with self.subTest(shape=shape, value=value):
                    self.assertEqual(corsair.parse_bragi(self.bragi(value, shape)), expected)

    def test_bragi_charge_states(self):
        self.assertIs(corsair.parse_bragi_charge(self.bragi(1)), True)
        self.assertIs(corsair.parse_bragi_charge(self.bragi(2)), False)
        for value in (0, 3, 0xFFFF):
            self.assertIsNone(corsair.parse_bragi_charge(self.bragi(value)))

    def test_bragi_garbage_and_truncation(self):
        for name, reply in dict(GARBAGE, none=None).items():
            with self.subTest(reply=name):
                level = corsair.parse_bragi(reply)
                sane_level(self, level)
                self.assertIn(corsair.parse_bragi_charge(reply), (None, True, False))
        frame = self.bragi(765)
        for n in range(0, 5):                       # shorter than any answer
            self.assertIsNone(corsair.parse_bragi(frame[:n]), n)
        self.assertIsNone(corsair.parse_bragi_raw([0x00] * 64))          # wrong route
        # a report id in front (65 bytes) is skipped
        self.assertEqual(corsair.parse_bragi([0x00] + frame), 76)

    def test_legacy_level(self):
        def reply(value, size=33):
            r = [0] * size
            r[corsair.LEVEL_INDEX], r[corsair.LEVEL_INDEX + 1] = value & 0xFF, value >> 8
            return r
        for value, expected in ((765, 76), (1000, 100), (0, None), (1001, None), (0xFFFF, None)):
            with self.subTest(value=value):
                self.assertEqual(corsair.parse_level(reply(value)), expected)
        for name, r in dict(GARBAGE, none=None).items():
            with self.subTest(reply=name):
                sane_level(self, corsair.parse_level(r))
        self.assertIsNone(corsair.parse_level([0] * (corsair.LEVEL_INDEX + 1)))   # one byte short


class PulsarTests(unittest.TestCase):
    @staticmethod
    def reply(level, size=65, command=pulsar.SONIX_BATTERY_COMMAND):
        r = [0] * size
        r[2:5] = command
        r[pulsar.SONIX_LEVEL_INDEX] = level
        return r

    def test_levels(self):
        for level, expected in ((0, 0), (1, 1), (100, 100), (101, None), (255, None)):
            with self.subTest(level=level):
                self.assertEqual(pulsar.parse_sonix_power(self.reply(level)), expected)

    def test_other_command_or_short_reply_is_none(self):
        self.assertIsNone(pulsar.parse_sonix_power(self.reply(50, command=(0x08, 0x81, 0x02))))
        for n in range(0, pulsar.SONIX_LEVEL_INDEX + 1):
            self.assertIsNone(pulsar.parse_sonix_power(self.reply(50)[:n]), n)
        for name, r in dict(GARBAGE, none=None).items():
            with self.subTest(reply=name):
                sane_level(self, pulsar.parse_sonix_power(r))


class AsusTests(unittest.TestCase):
    def test_garbage_for_every_parser(self):
        for name, r in GARBAGE.items():
            r = list(r)
            with self.subTest(reply=name):
                for scale in (asus.PERCENT, asus.STEPS):
                    reading = asus.parse_reply(r, scale)
                    self.assertTrue(reading is None or 0 <= reading[0] <= 100)
                reading = asus.parse_omni_mouse(r)
                self.assertTrue(reading is None or 0 <= reading[0] <= 100)
                for byte in (4, 8, 63):
                    reading = asus.parse_omni_keyboard(r, byte)
                    self.assertTrue(reading is None or 0 <= reading[0] <= 100)
                headset = asus.parse_headset_reply(r)
                self.assertTrue(headset is None or 0 <= headset[0] <= 100)
                self.assertIsInstance(asus.parse_pairs(r), list)

    def echo_reply(self, level, charging=0, report_id=False):
        r = [0] * 16
        r[0:2] = asus.ECHO
        r[asus.LEVEL_BYTE] = level
        r[asus.CHARGING_BYTE] = charging
        return ([0x00] + r) if report_id else r

    def test_reply_levels_and_standby(self):
        for level, charging, expected in ((50, 0, (50, False, "")), (100, 1, (100, True, "")),
                                          (0, 1, (0, True, "")), (0, 0, None),      # standby, not empty
                                          (101, 0, None), (255, 1, None)):
            for report_id in (False, True):
                with self.subTest(level=level, charging=charging, report_id=report_id):
                    self.assertEqual(asus.parse_reply(self.echo_reply(level, charging, report_id),
                                                      asus.PERCENT), expected)

    def test_reply_in_steps(self):
        self.assertEqual(asus.parse_reply(self.echo_reply(4), asus.STEPS), (100, False, "about 100%"))
        self.assertEqual(asus.parse_reply(self.echo_reply(3, 1), asus.STEPS),
                         (75, True, "about 75%, charging"))
        self.assertIsNone(asus.parse_reply(self.echo_reply(5), asus.STEPS))

    def test_truncated_reply_is_none(self):
        frame = self.echo_reply(50)
        for n in range(0, asus.CHARGING_BYTE + 1):
            self.assertIsNone(asus.parse_reply(frame[:n], asus.PERCENT), n)

    def test_pair_list_stops_at_the_first_empty_slot_and_ignores_a_truncated_tail(self):
        r = list(asus.OMNI_PAIRS) + [0] * 60
        self.assertEqual(asus.parse_pairs(r), [])
        r = list(asus.OMNI_PAIRS) + [0] * 60
        r[asus.OMNI_FIRST_SLOT:asus.OMNI_FIRST_SLOT + 2] = [0xCE, 0x1A]
        self.assertEqual(asus.parse_pairs(r), [0x1ACE])
        self.assertEqual(asus.parse_pairs(r[:asus.OMNI_FIRST_SLOT + 1]), [])   # half a pid
        self.assertEqual(asus.parse_pairs([]), [])

    def test_headset_reply_known_garbage_is_none(self):
        for r in ([], [0xFF], [0xFF, 0x1B], [0x00] * 64, [0xFF] * 64,
                  [0xFF, asus.HEADSET_ERROR[0], asus.HEADSET_ERROR[1]] + [0] * 61):
            with self.subTest(r=r[:4]):
                self.assertIsNone(asus.parse_headset_reply(r))

    def test_headset_reply_truncation_never_raises(self):
        frame = [0] * 64
        frame[0], frame[1] = asus.HEADSET_REPORT_ID, asus.HEADSET_HEADER
        frame[asus.HEADSET_VOLTAGE_LSB], frame[asus.HEADSET_VOLTAGE_MSB] = 0xA0, 0x0F   # 4000 mV
        self.assertEqual(asus.parse_headset_reply(frame)[0], 75)
        for n in range(len(frame)):
            with self.subTest(length=n):
                reading = asus.parse_headset_reply(frame[:n])
                self.assertTrue(reading is None or 0 <= reading[0] <= 100)


class AulaTests(unittest.TestCase):
    @staticmethod
    def frame(level, lead):
        r = [0] * 33
        r[lead], r[lead + 1] = aula.HEADER, aula.CMD_BATTERY
        r[25 + lead] = level
        return r

    def test_both_report_id_layouts(self):
        for lead in (0, 1):
            for level, expected in ((1, 1), (57, 57), (100, 100), (0, None), (101, None), (255, None)):
                with self.subTest(lead=lead, level=level):
                    self.assertEqual(aula.parse_level(self.frame(level, lead)), expected)

    def test_garbage_and_truncation(self):
        for name, r in dict(GARBAGE, none=None).items():
            with self.subTest(reply=name):
                sane_level(self, aula.parse_level(r))
        frame = self.frame(80, 0)
        for n in range(0, 26):
            self.assertIsNone(aula.parse_level(frame[:n]), n)


class JabraTests(unittest.TestCase):
    def test_battery_payload(self):
        for payload, expected in ((None, None), (b"", None), (b"\x01", None), (b"\x00\x00", (0, False)),
                                  (b"\x01\x64", (100, True)), (b"\x00\x65", None), (b"\xff\xff", None),
                                  (b"\xff\x32trailing", (50, True)), (b"\x02\x32", (50, False))):
            with self.subTest(payload=payload):
                self.assertEqual(jabra.parse_battery(payload), expected)

    def test_name_payload(self):
        for payload, expected in ((None, None), (b"", None), (b"\x00abc", None),
                                  (b"\x09abc", None),                       # claims more than it has
                                  (b"\x03abc", "abc"), (b"\x02abc", "ab"), (b"\x03   ", None),
                                  (b"\x03\xff\xfe!", "��!")):
            with self.subTest(payload=payload):
                self.assertEqual(jabra.parse_name(payload), expected)

    def test_reply_payload_short_or_foreign_frames_are_not_replies(self):
        for r in (None, [], [0x05], [0x05, 0, 4, 0x20, 0xC0]):
            with self.subTest(r=r):
                self.assertIsNone(jabra.reply_payload(r, 0x20, jabra.CLASS_STATUS, jabra.OP_BATTERY))
        nak = [jabra.REPORT_ID, 0x00, jabra.DST_HEADSET, 0x20, jabra.FLAG_REPLY, jabra.CLASS_NAK]
        self.assertEqual(jabra.reply_payload(nak, 0x20, jabra.CLASS_STATUS, jabra.OP_BATTERY), b"")
        self.assertIsNone(jabra.reply_payload(nak, 0x21, jabra.CLASS_STATUS, jabra.OP_BATTERY))


if __name__ == "__main__":
    unittest.main()
