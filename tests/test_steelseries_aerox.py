"""Tests for the Aerox mice in providers/steelseries.py. No hardware is needed.

The Aerox 3, 5 and 9 Wireless share one battery exchange on their 2.4 GHz receiver
(rivalcfg builds all three wireless profiles the same way; the level is confirmed on an
Aerox 9 Wireless in #79): 00 d2 out, a 64-byte
report echoing d2 back, the level byte after the echo. The fake receiver below
answers that exchange, and the tests run the real provider code against it.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import steelseries as S  # noqa: E402

AEROX_5_9 = {
    0x1852: "SteelSeries Aerox 5 Wireless",
    0x185C: "SteelSeries Aerox 5 Wireless Destiny 2 Edition",
    0x1860: "SteelSeries Aerox 5 Wireless Diablo IV Edition",
    0x1858: "SteelSeries Aerox 9 Wireless",
    0x1874: "SteelSeries Aerox 9 Wireless WOW Edition",
}


class FakeReceiver:
    """Answers 00 d2 with d2 <level byte>; anything else gets no reply."""

    def __init__(self, level_byte):
        self.level_byte = level_byte
        self.writes = []
        self.queue = []

    def device_class(self):
        rx = self

        class FakeDevice:
            def open_path(self, path):
                pass

            def write(self, data):
                data = list(data)
                rx.writes.append(data)
                if data[:2] == [0x00, 0xD2]:
                    rx.queue.append([0xD2, rx.level_byte] + [0] * 62)
                return len(data)

            def read(self, n, timeout=None):
                return rx.queue.pop(0) if rx.queue else []

            def close(self):
                pass

        return FakeDevice


def issue_79_entries(pid):
    # the collections of the Aerox 9 Wireless in the diagnostics of issue #79
    shape = [(0, 0x0001, 0x02), (1, 0x0001, 0x06), (2, 0x000C, 0x01),
             (3, 0xFFC0, 0x01), (4, 0xFFC1, 0x01), (5, 0x0001, 0x06)]
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": b"if%d" % i, "product_string": "SteelSeries Aerox 9 Wireless"}
            for i, p, u in shape]


class AeroxTest(unittest.TestCase):
    def setUp(self):
        self._saved = (S.hid, S.hidlist)

    def tearDown(self):
        S.hid, S.hidlist = self._saved

    def poll(self, pid, level_byte):
        rx = FakeReceiver(level_byte)
        S.hid = types.SimpleNamespace(device=rx.device_class())
        entries = issue_79_entries(pid)
        S.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return S.SteelSeriesProvider().poll(), rx

    def test_aerox_9_issue_79(self):
        res, rx = self.poll(0x1858, 0x10)           # step 16 -> (16 - 1) * 5 = 75 %
        self.assertEqual([(r.name, r.level, r.charging, r.kind) for r in res],
                         [("SteelSeries Aerox 9 Wireless", 75, False, "mouse")])
        self.assertEqual(rx.writes[0], [0x00, 0xD2] + [0x00] * 62)

    def test_real_aerox_9_reply_issue_79(self):
        # hardware: @AJD00m's Aerox 9 Wireless answered this 8-byte report, and
        # SteelSeries GG showed 15 % at the same time
        self.assertEqual(S.parse_aerox3([0xD2, 0x04, 0x00, 0x2E, 0x34, 0x00, 0x00, 0x00]),
                         (15, False, True))

    def test_charging_bit(self):
        res, _ = self.poll(0x1858, 0x80 | 21)
        self.assertEqual([(r.level, r.charging) for r in res], [(100, True)])

    def test_level_zero_is_off_not_empty(self):
        res, _ = self.poll(0x1858, 0x00)
        self.assertEqual(res, [])

    def test_literal_step_percent_and_charging_boundaries(self):
        for payload, expected in (
                ("d2 00", (None, False, False)),
                ("d2 80", (None, False, False)),
                ("d2 01", (0, False, True)),
                ("d2 81", (0, True, True)),
                ("d2 15", (100, False, True)),
                ("d2 95", (100, True, True)),
                ("d2 64", (100, False, True)),
                ("d2 e4", (100, True, True)),
                ("d2 65", (None, False, False)),
                ("d2 e5", (None, False, False)),
                ("d2 7f", (None, False, False)),
                ("d2 ff", (None, False, False))):
            frame = bytes.fromhex(payload)
            with self.subTest(payload=payload):
                self.assertEqual(S.parse_aerox3(frame), expected)
                self.assertEqual(S.parse_aerox3(b"\x00" + frame), expected)

    def test_invalid_level_is_not_emitted_and_next_good_reply_recovers(self):
        rx = FakeReceiver(0x65)
        S.hid = types.SimpleNamespace(device=rx.device_class())
        S.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: issue_79_entries(0x1858))
        provider = S.SteelSeriesProvider()
        for value in (0x65, 0xE5, 0xFF):
            with self.subTest(value=value):
                rx.level_byte = value
                self.assertEqual(provider.poll(), [])
        rx.level_byte = 0x8B  # valid charging flag + step 11 = 50%
        self.assertEqual([(s.level, s.charging) for s in provider.poll()], [(50, True)])

    def test_every_aerox_5_and_9_model_uses_the_d2_exchange(self):
        for pid, name in AEROX_5_9.items():
            self.assertEqual(S.MOUSE_MODELS[pid], (name, S.parse_aerox3), hex(pid))
            self.assertEqual(S.MOUSE_EXCHANGE[pid], (S.AEROX_REQUEST, S.AEROX_ECHO), hex(pid))
            res, rx = self.poll(pid, 0x0B)          # step 11 -> 50 %
            self.assertEqual([(r.name, r.level) for r in res], [(name, 50)], hex(pid))
            self.assertTrue(all(w[:2] == [0x00, 0xD2] for w in rx.writes), hex(pid))

    def test_rivalcfg_level_formula_for_all_steps(self):
        # rivalcfg: level = ((data[1] & ~0x80) - 1) * 5, charging = data[1] & 0x80
        for step in range(1, 22):
            for chg in (0, 0x80):
                level, charging, online = S.parse_aerox3([0xD2, step | chg])
                self.assertEqual((level, charging, online), ((step - 1) * 5, bool(chg), True))


if __name__ == "__main__":
    unittest.main()
