"""Boundary regressions for Ahmed's original #413 mutation report.

Wire fixtures use the published reply layouts, not provider constants. No
devices, network, application startup, threads or real history files are used.
"""
import unittest

from halo.history import History
from providers import astro, hyperx_cloud3, jbl, playstation


def hyperx_reply(command, level):
    # #155's 62-byte reply: 66 89 0e d7 30 ... reads 48 percent.
    # The reader also accepts the documented 0d response ID.
    return bytes((0x66, command, 0x0E, 0xD7, level)) + bytes(57)


class PlayStationChargingTests(unittest.TestCase):
    def test_dualshock_cable_bit_controls_charging(self):
        # Existing DS4 status contract: bit 4 is cable/charging; low nibble is level.
        for status, expected in ((0x00, (0, False)), (0x07, (70, False)),
                                 (0x10, (0, True)), (0x17, (70, True)),
                                 (0x1A, (100, True))):
            with self.subTest(status=status):
                self.assertEqual(playstation.parse_ds4(status), expected)

    def test_dualsense_charge_complete_still_counts_as_charging(self):
        # State 2 with a full level must keep charging. Other state-2 level
        # combinations belong to the separate #471 parser review.
        for status, expected in ((0x05, (50, False)), (0x15, (50, True)),
                                 (0x2A, (100, True))):
            with self.subTest(status=status):
                self.assertEqual(playstation.parse_dualsense(status), expected)


class AstroRangeTests(unittest.TestCase):
    @staticmethod
    def reply(level):
        # HeadsetControl's Astro A50 Gen 5 battery layout: 02 0c 06 00 06
        # <handle> <left percent> <right percent> <dock>, padded to 64 bytes.
        return bytes((0x02, 0x0C, 0x06, 0x00, 0x06, 0x0C, level, level, 0)) + bytes(55)

    def test_zero_and_full_are_real_astro_levels(self):
        for level in (0, 50, 100):
            with self.subTest(level=level):
                self.assertEqual(astro.parse_battery(self.reply(level)), level)

    def test_well_formed_reply_cannot_publish_an_impossible_percentage(self):
        for level in (101, 255):
            for form in (bytes, bytearray, list):
                with self.subTest(level=level, form=form):
                    self.assertIsNone(astro.parse_battery(form(self.reply(level))))
        good = self.reply(50)
        for bad in (None, b"", good[:6], bytes((0x99,)) + good[1:],
                    good[:4] + bytes((0x07,)) + good[5:]):
            with self.subTest(frame=bad):
                self.assertIsNone(astro.parse_battery(bad))


class HyperXRangeTests(unittest.TestCase):
    def test_zero_and_full_are_real_readings_for_both_battery_reply_ids(self):
        for command in (0x89, 0x0D):
            for level in (0, 48, 100):
                with self.subTest(command=command, level=level):
                    self.assertEqual(hyperx_cloud3.parse_battery(hyperx_reply(command, level)), level)

    def test_well_formed_reply_cannot_publish_an_impossible_percentage(self):
        for command in (0x89, 0x0D):
            for level in (101, 255):
                for form in (bytes, bytearray, list):
                    with self.subTest(command=command, level=level, form=form):
                        self.assertIsNone(hyperx_cloud3.parse_battery(form(hyperx_reply(command, level))))


class JblRangeTests(unittest.TestCase):
    def test_zero_and_full_are_real_battery_reports(self):
        # Quantum 910 report 08 carries the percentage in its next byte.
        for level in (0, 30, 100):
            with self.subTest(level=level):
                self.assertEqual(jbl.parse_level(bytes((0x08, level))), level)

    def test_well_formed_report_cannot_publish_an_impossible_percentage(self):
        for level in (101, 255):
            for form in (bytes, bytearray, list):
                with self.subTest(level=level, form=form):
                    self.assertIsNone(jbl.parse_level(form((0x08, level))))
        for frame in (None, b"", b"\x08", b"\x09\x64", b"\x2f\x64"):
            with self.subTest(frame=frame):
                self.assertIsNone(jbl.parse_level(frame))


class RechargeThresholdTests(unittest.TestCase):
    def test_exact_three_point_rise_resets_history_but_two_point_jitter_does_not(self):
        for new_level, reset in ((78, False), (79, True), (80, True)):
            with self.subTest(new_level=new_level):
                history = History()
                for index, level in enumerate((80, 79, 78, 77, 76)):
                    history.record("mouse", level, False, True, index * 600.)
                self.assertAlmostEqual(history.rate("mouse"), 1 / 600.)
                before = [sample[:] for sample in history.devices["mouse"]["samples"]]
                history.record("mouse", new_level, False, True, 3000.)
                state = history.devices["mouse"]
                if reset:
                    self.assertEqual(state["samples"], [[0., new_level]])
                    self.assertEqual(state["use"], 0.)
                    self.assertIsNone(history.rate("mouse"))
                else:
                    self.assertEqual(state["samples"], before)
                    self.assertEqual(state["use"], 3000.)
                    self.assertIsNotNone(history.rate("mouse"))


if __name__ == "__main__":
    unittest.main()
