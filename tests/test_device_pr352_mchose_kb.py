"""Tests for providers/mchose_kb.py - no hardware needed.

The exchange is the one MCHOSE's own web driver (M HUB, www.mchose.com.cn) uses; the command
table in its ``assets/purify.es-*.js`` bundle has

    ["getBattery", {default: {wirelessCommand: "13 4a 01 00 ...",
                              wirelessParser: batteryLevel:u8, fullStatus:bit4, chargeStatus:bit4}}]

0x13 is the report id, so the frame on the wire is ``13 4a 01`` and zeros, on the 20-byte
output report of the vendor collection (usage page 0xFF02). Captured on a K87 receiver
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
        self.assertEqual(bytes(K.REQUEST)[:6], bytes([0x13, 0x4A, 0x01, 0x00, 0x00, 0x02]))
        self.assertEqual(len(K.REQUEST), 19)

    def test_the_captured_answer_reads_the_level(self):
        self.assertEqual(K.parse_reply(reply(0x64, 0x01)), (100, 0x01))
        self.assertEqual(K.parse_reply(reply(0x2A, 0x00)), (42, 0x00))

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

    def test_another_mchose_vendor_id_is_left_alone(self):
        fake = FakeKeyboard([reply()])
        out, _ = self.poll(fake, entries(vid=0x5253))
        self.assertEqual(out, [])
        self.assertEqual(fake.sent, [])


if __name__ == "__main__":
    unittest.main()
