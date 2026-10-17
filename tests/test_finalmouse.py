"""Tests for providers/finalmouse.py. No hardware is needed.

The fake dongle answers the way Finalmouse's XPanel expects the ULX dongle to:
output report 04 <length> <0x80 | command> <payload length> <payload> out, input
report 05 <length> <command> <payload length> <payload> back.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import finalmouse as F  # noqa: E402


def frame(cmd, payload):
    return [F.IN_REPORT, 2 + len(payload), cmd, len(payload)] + list(payload) + [0] * 8


class FakeDongle:
    """answers: {command: payload or None}. None = no reply to that command."""

    def __init__(self, answers, noise=()):
        self.answers = answers
        self.noise = list(noise)
        self.queue = []
        self.writes = []

    def device_class(self):
        dongle = self

        class FakeDevice:
            def open_path(self, path):
                self.path = path

            def write(self, data):
                data = list(data)
                dongle.writes.append((self.path, data))
                cmd = data[2] & 0x7F
                dongle.queue += dongle.noise
                if dongle.answers.get(cmd) is not None:
                    dongle.queue.append(frame(cmd, dongle.answers[cmd]))
                return len(data)

            def read(self, n, timeout=None):
                return dongle.queue.pop(0) if dongle.queue else []

            def close(self):
                pass

        return FakeDevice


def entry(pid, page, path, usage=1):
    return {"product_id": pid, "interface_number": 1, "usage_page": page, "usage": usage,
            "path": path, "product_string": "UltralightX dongle"}


ULX = [entry(0x0100, 0x0001, b"mouse", 2), entry(0x0100, 0xFF00, b"vendor")]


class FinalmouseTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = (F.hid, F.hidlist, F.TIMEOUT)
        F.TIMEOUT = 0.02

    def tearDown(self):
        F.hid, F.hidlist, F.TIMEOUT = self._saved

    def use(self, answers, entries=ULX, noise=()):
        self.dongle = FakeDongle(answers, noise)
        F.hid = types.SimpleNamespace(device=self.dongle.device_class())
        F.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return F.FinalmouseProvider()

    def commands(self):
        return [data[2] & 0x7F for _, data in self.dongle.writes]


class RequestTests(unittest.TestCase):
    def test_request_framing(self):
        """Same framing as the settings writes ulx_reverse captured (04 03 92 01 01)."""
        self.assertEqual(F.request(18, [1])[:6], [0x04, 0x03, 0x92, 0x01, 0x01, 0x00])
        r = F.request(F.CMD_BATTERY_STATUS)
        self.assertEqual(r[:4], [0x04, 0x02, 0xA6, 0x00])
        self.assertEqual(len(r), 64)

    def test_parse_frame(self):
        self.assertEqual(F.parse_frame([0x05, 0x03, 0x12, 0x01, 0x01, 0]), (0x12, [0x01]))
        self.assertEqual(F.parse_frame([0x05, 0x03, 0x92, 0x01, 0x01]), (0x12, [0x01]))
        self.assertIsNone(F.parse_frame([0x04, 0x03, 0x12, 0x01, 0x01]))    # not report 05
        self.assertIsNone(F.parse_frame([0x05, 0x05, 0x26, 0x03, 0x50]))    # cut short
        self.assertIsNone(F.parse_frame([]))

    def test_voltage_curve(self):
        """XPanel's own curve."""
        self.assertEqual(F.volts_to_percent(4400), 100)
        self.assertEqual(F.volts_to_percent(4380), 100)
        self.assertEqual(F.volts_to_percent(3880), 50)
        self.assertEqual(F.volts_to_percent(3810), 38)
        self.assertEqual(F.volts_to_percent(2900), 0)
        self.assertIsNone(F.volts_to_percent(0))
        self.assertIsNone(F.volts_to_percent(0xFFFF))


class PollTests(FinalmouseTestCase):
    def test_battery_status(self):
        p = self.use({36: [1], 38: [80, 0x2C, 0x10], 37: [0]})
        [st] = p.poll()
        self.assertEqual((st.key, st.name, st.level, st.charging, st.online, st.kind),
                         ("finalmouse:0100", "Finalmouse UltralightX", 80, False, True, "mouse"))
        self.assertEqual(self.commands(), [36, 38, 37])
        self.assertEqual({path for path, _ in self.dongle.writes}, {b"vendor"})

    def test_charging(self):
        [st] = self.use({36: [1], 38: [42, 0, 0x10], 37: [1]}).poll()
        self.assertEqual((st.level, st.charging), (42, True))

    def test_voltage_fallback(self):
        """Firmware that does not answer battery status: the voltage is converted."""
        [st] = self.use({36: [1], 5: [0x28, 0x0F], 37: [0]}).poll()     # 3880 mV
        self.assertEqual(st.level, 50)
        self.assertEqual(self.commands(), [36, 38, 5, 37])

    def test_real_dongle_without_battery_status(self):
        """The replies a 361D:0100 dongle actually gave, byte for byte.

        Its firmware never answers command 38, so every reading comes from the voltage:
        0x1064 = 4196 mV, which is 78% on XPanel's curve. Captured 2026-10-16 on the
        dongle whose product string is 'UltralightX dongle'.
        """
        [st] = self.use({36: [0x01], 5: [0x64, 0x10], 37: [0x00]}).poll()
        self.assertEqual((st.level, st.charging, st.online), (78, False, True))
        self.assertEqual(self.commands(), [36, 38, 5, 37])

    def test_real_dongle_charging(self):
        """The same dongle with the mouse on its cable: 0x10B4 = 4276 mV, 88%.

        Read high on purpose - that is the charging voltage, not charge gained, and
        XPanel reads the same firmware through the same curve. Captured 2026-10-16.
        """
        [st] = self.use({36: [0x01], 5: [0xB4, 0x10], 37: [0x01]}).poll()
        self.assertEqual((st.level, st.charging, st.online), (88, True, True))

    def test_state_of_charge_above_100_uses_the_voltage(self):
        [st] = self.use({36: [1], 38: [0xFF, 0, 0], 5: [0x28, 0x0F], 37: [0]}).poll()
        self.assertEqual(st.level, 50)

    def test_no_link_state_reply_still_reads(self):
        [st] = self.use({38: [70, 0, 0x10], 37: [0]}).poll()
        self.assertEqual(st.level, 70)

    def test_other_reports_are_skipped(self):
        noise = [[0x01, 0x00, 0x05, 0x00], [0x05, 0x03, 0x12, 0x01, 0x01, 0]]
        [st] = self.use({36: [1], 38: [61, 0, 0x10], 37: [0]}, noise=noise).poll()
        self.assertEqual(st.level, 61)

    def test_mouse_off_shows_last_level_greyed(self):
        answers = {36: [1], 38: [80, 0, 0x10], 37: [0]}
        p = self.use(answers)
        p.poll()
        answers[36] = [0]
        [st] = p.poll()
        self.assertEqual((st.level, st.online), (80, False))
        self.assertEqual(self.commands()[-1], 36, "nothing asked after 'not connected'")
        self.assertTrue(st.wake_on_input,
                        "a sleeping mouse is awake by the time its movement lands, so the "
                        "tray may poll at once rather than wait out the interval (#202)")

    def test_mouse_off_from_the_start(self):
        self.assertEqual(self.use({36: [0]}).poll(), [])

    def test_last_level_expires(self):
        answers = {36: [1], 38: [80, 0, 0x10], 37: [0]}
        p = self.use(answers)
        p.poll()
        answers[36] = [0]
        key = "finalmouse:0100"
        lvl, chg, _ = p._last[key]
        p._last[key] = (lvl, chg, 0.0)
        self.assertEqual(p.poll(), [])

    def test_needs_the_vendor_collection(self):
        p = self.use({36: [1], 38: [80, 0, 0x10]}, entries=[entry(0x0100, 0x0001, b"mouse", 2)])
        self.assertEqual(p.poll(), [])
        self.assertEqual(self.dongle.writes, [])

    def test_mouse_on_cable_is_not_polled(self):
        p = self.use({36: [1], 38: [80, 0, 0x10]}, entries=[entry(0x0102, 0xFF00, b"cable")])
        self.assertEqual(p.poll(), [])
        self.assertEqual(self.dongle.writes, [])


if __name__ == "__main__":
    unittest.main()
