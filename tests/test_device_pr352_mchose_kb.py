"""Tests for providers/mchose_kb.py - no hardware needed.

The exchange is the one MCHOSE's own web driver (M HUB, www.mchose.com.cn) uses; the command
table and wireless readCommand in ``assets/main-DQZs6ZzX.js`` have

    ["getBattery", {default: {wirelessCommand: "13 4a 01 00 ...",
                              wirelessParser: batteryLevel:u8, fullStatus:bit4, chargeStatus:bit4}}]

The 19-byte template receives its additive checksum 0x5e before sendReport removes
report ID 0x13. HIDAPI's complete wire frame is 20 bytes, ``13 4a 01`` + 16 zeros +
``5e``, on the FF02:0002 collection of interface 1. Captured on a K87 receiver
(41E4:2001):

    13 4A 01 00 02 64 01 00 ... 00 C5
                     ^^  ^^        ^^ checksum = sum(bytes 0..18) & 0xFF

0x64 = 100 %, which the keyboard's own 10/10 bar display confirms. The byte after the level is
0x01 on a full keyboard that is **not** on the cable, so the provider reads the level only and
never claims the device is charging.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import mchose_kb as K  # noqa: E402


def reply(level=0x64, flags=0x01):
    """The captured answer, parameterised, with the vendor's sum in the last byte."""
    r = [0x13, 0x4A, 0x01, 0x00, 0x02, level, flags] + [0x00] * 12
    return r + [sum(r) & 0xFF]


class FakeKeyboard:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.path = None
        self.sent = []

    # hid.device API
    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def set_nonblocking(self, value):
        pass

    def write(self, data):
        self.sent.append((self.path, bytes(data)))
        return len(data)

    def read(self, size, timeout_ms=0):
        return self.answers.pop(0) if self.answers else []


def entries(vid=0x41E4, pid=0x2001):
    shape = [(0, 0x0001, 0x0006),
             (1, 0x0001, 0x0006),
             (1, 0xFF02, 0x0002),      # the command collection
             (1, 0x000C, 0x0001),
             (1, 0xFF04, 0x0002)]      # the small feature-report collection
    return [{"vendor_id": vid, "product_id": pid, "interface_number": i,
             "usage_page": p, "usage": u,
             "path": b"kb-%d-%04x-%02x" % (i, p, u),
             "product_string": "MCHOSE 2.4G Wireless"}
            for i, p, u in shape]


CMD_PATH = entries()[2]["path"]


class ParseTest(unittest.TestCase):
    def test_the_request_frame(self):
        # Pinned manufacturer wire frame, not derived from provider constants.
        expected = bytes.fromhex("13 4a 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 5e")
        self.assertEqual(K.REQUEST, expected)
        self.assertEqual(len(K.REQUEST), 20)
        self.assertEqual(K.REQUEST[-1], sum(K.REQUEST[:-1]) & 0xff)

    def test_the_captured_answer_reads_the_level(self):
        self.assertEqual(K.parse_reply(reply(0x64, 0x01)), (100, 0x01))
        self.assertEqual(K.parse_reply(reply(0x2A, 0x00)), (42, 0x00))

    def test_the_charge_nibble_is_the_high_nibble(self):
        self.assertIs(K.parse_charging(0x00), False)
        self.assertIs(K.parse_charging(0x10), True)
        self.assertIs(K.parse_charging(0x01), False)  # K87 full, not on the cable
        self.assertIsNone(K.parse_charging(0x20))

    def test_a_reply_that_does_not_echo_the_command_is_refused(self):
        wrong = reply()
        wrong[1] = 0x48
        self.assertIsNone(K.parse_reply(wrong))
        wrong = reply()
        wrong[0] = 0x12
        self.assertIsNone(K.parse_reply(wrong))

    def test_a_level_above_100_is_refused(self):
        self.assertIsNone(K.parse_reply(reply(0x65)))

    def test_a_short_reply_is_refused(self):
        self.assertIsNone(K.parse_reply([0x13, 0x4A, 0x01]))
        self.assertIsNone(K.parse_reply(None))


    def test_boundary_levels_are_valid_only_in_complete_matching_frames(self):
        for level in (0, 100):
            with self.subTest(level=level):
                self.assertEqual(K.parse_reply(reply(level)), (level, 1))

    def test_truncated_corrupt_and_unrelated_frames_are_refused(self):
        captured = bytes.fromhex("13 4a 01 00 02 64 01 00 00 00 00 00 00 00 00 00 00 00 00 c5")
        self.assertEqual(K.parse_reply(captured), (100, 1))
        for length in range(20):
            with self.subTest(length=length):
                self.assertIsNone(K.parse_reply(captured[:length]))
        for index in (0, 1, 2, 3, 5, 19):
            corrupt = bytearray(captured)
            corrupt[index] ^= 1
            with self.subTest(index=index):
                self.assertIsNone(K.parse_reply(corrupt))
        for malformed in (None, 7, "13 4a", {}, [None] * 20, [-1] * 20, [256] * 20):
            with self.subTest(malformed=malformed):
                self.assertIsNone(K.parse_reply(malformed))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (K.hid, K.hidlist)

    def tearDown(self):
        K.hid, K.hidlist = self._saved

    def poll(self, fake, ents):
        K.hid = types.SimpleNamespace(device=lambda: fake)
        K.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        p = K.MchoseKbProvider()
        return p.poll(), p.diagnostics()

    def test_the_keyboard_is_read_on_the_command_collection(self):
        fake = FakeKeyboard([reply(0x64, 0x01)])
        out, diag = self.poll(fake, entries())
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("mchose_kb:2001", "MCHOSE K87", 100, False, True,
                           "mchose_kb", "keyboard")])
        self.assertEqual({p for p, _ in fake.sent}, {CMD_PATH})

    def test_a_full_keyboard_that_is_not_plugged_in_is_not_called_charging(self):
        # the captured flag was 0x01 with the keyboard full and off the cable
        fake = FakeKeyboard([reply(0x64, 0x01)])
        out, _ = self.poll(fake, entries())
        self.assertFalse(out[0].charging)

    def test_a_silent_keyboard_keeps_an_icon_with_no_level(self):
        fake = FakeKeyboard()
        out, _ = self.poll(fake, entries())
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)

    def test_the_g98_pro_uses_the_same_command_collection(self):
        fake = FakeKeyboard([reply(0x64, 0x01)])
        out, _ = self.poll(fake, entries(pid=0x2004))
        self.assertEqual([(s.key, s.name, s.level) for s in out],
                         [("mchose_kb:2004", "MCHOSE G98 Pro", 100)])
        self.assertEqual({p for p, _ in fake.sent}, {CMD_PATH.replace(b"2001", b"2004")})

    def test_the_g98_pro_charge_flag_is_read(self):
        fake = FakeKeyboard([reply(0x32, 0x10)])
        out, _ = self.poll(fake, entries(pid=0x2004))
        self.assertEqual((out[0].level, out[0].charging, out[0].charging_known),
                         (50, True, True))

    def test_a_full_off_cable_keyboard_is_not_charging(self):
        fake = FakeKeyboard([reply(0x64, 0x01)])
        out, _ = self.poll(fake, entries(pid=0x2004))
        self.assertEqual((out[0].level, out[0].charging, out[0].charging_known),
                         (100, False, True))

    def test_the_g75_pro_on_the_compx_receiver_is_read(self):
        # captured on a G75 Pro: 13 4A 01 00 02 2F 01 00 .. 00 90 (47 %)
        captured = bytes.fromhex("13 4a 01 00 02 2f 01 00 00 00 00 00 00 00 00 00 00 00 00 90")
        fake = FakeKeyboard([list(captured)])
        ents = entries(vid=0x3554, pid=0xFA09)
        out, _ = self.poll(fake, ents)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in out],
                         [("mchose_kb:3554fa09", "MCHOSE G75 Pro", 47, False, True, "keyboard")])
        self.assertEqual(fake.sent, [(ents[2]["path"], K.REQUEST)])

    def test_a_silent_compx_receiver_gets_no_ghost_icon(self):
        # the receiver id is shared with AULA / LEOBOG keyboards read by providers/pulsar.py
        fake = FakeKeyboard()
        out, _ = self.poll(fake, entries(vid=0x3554, pid=0xFA09))
        self.assertEqual(out, [])

    def test_a_g75_that_falls_asleep_keeps_its_level_greyed(self):
        fake = FakeKeyboard([reply(0x2F, 0x01)])
        ents = entries(vid=0x3554, pid=0xFA09)
        K.hid = types.SimpleNamespace(device=lambda: fake)
        K.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        p = K.MchoseKbProvider()
        self.assertEqual(p.poll()[0].level, 47)
        out = p.poll()
        self.assertEqual([(s.key, s.level, s.online) for s in out],
                         [("mchose_kb:3554fa09", 47, False)])

    def test_other_compx_receivers_are_left_alone(self):
        for vid, pid in ((0x3554, 0xFA0A), (0x3554, 0xF508), (0x41E4, 0xFA09)):
            with self.subTest(vid=vid, pid=pid):
                fake = FakeKeyboard([reply()])
                out, _ = self.poll(fake, entries(vid=vid, pid=pid))
                self.assertEqual(out, [])
                self.assertEqual(fake.sent, [])

    def test_another_mchose_vendor_id_is_left_alone(self):
        fake = FakeKeyboard([reply()])
        out, _ = self.poll(fake, entries(vid=0x5253))
        self.assertEqual(out, [])
        self.assertEqual(fake.sent, [])


    def test_only_the_sourced_product_and_exact_command_collection_are_written(self):
        for changes in ({"product_id": 0x2002}, {"usage_page": 0xff04},
                        {"usage": 1}, {"interface_number": 2}):
            ents = [dict(e, **changes) for e in entries()]
            with self.subTest(changes=changes):
                fake = FakeKeyboard([reply()])
                _out, _diag = self.poll(fake, ents)
                self.assertEqual(fake.sent, [])

    def test_short_or_refused_writes_do_not_consume_a_stale_input_reply(self):
        class Refused(FakeKeyboard):
            def __init__(self, result):
                super().__init__([reply()])
                self.result, self.reads, self.closed = result, 0, False
            def write(self, data):
                self.sent.append((self.path, bytes(data)))
                return self.result
            def read(self, *args, **kwargs):
                self.reads += 1
                return super().read(*args, **kwargs)
            def close(self):
                self.closed = True
        for result in (-1, 0, 19, None):
            with self.subTest(result=result):
                fake = Refused(result)
                out, diag = self.poll(fake, entries())
                self.assertIsNone(out[0].level)
                self.assertFalse(out[0].online)
                self.assertEqual(fake.reads, 0)
                self.assertTrue(fake.closed)
                self.assertTrue(any("write refused/short" in line for line in diag))


if __name__ == "__main__":
    unittest.main()
