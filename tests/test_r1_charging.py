import unittest

from providers.inphic import parse_frame


class R1ChargingTest(unittest.TestCase):
    def test_wall_charger_on_receiver(self):
        for packet, result in [("0310400107", (70, False)),
                               ("0310400307", (70, True)),
                               ("0310400108", (80, False)),
                               ("031040030a", (100, True)),
                               ("0310400300", None),
                               ("03104003ff", None),
                               ("03104003", None)]:
            with self.subTest(packet=packet):
                self.assertEqual(parse_frame(bytes.fromhex(packet), (0x10,)), result)
