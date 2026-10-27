"""Tests for providers/jabra.py. No hardware and no Jabra Direct needed.

The replies are the ones a Jabra Link 380 (0b0e:24c7) with an Evolve2 65 sent back
to the provider's own queries: the name "Jabra Evolve2 65" and battery `04 15` (21 %).
The collection shape is that dongle's: interface 3 with the telephony headset
(000b:0005), the vendor GNP collection (ff00:0001) and consumer control (000c:0001).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import jabra as J  # noqa: E402


def frame(hexstr):
    b = bytes.fromhex(hexstr)
    return b + bytes(J.FRAME_SIZE - len(b))


# replies as captured, to seq 0x20 (name) and 0x21 (battery)
NAME_EVOLVE2_65 = frame("05 00 04 20 d7 02 00 10 4a 61 62 72 61 20 45 76 6f 6c 76 65 32 20 36 35")
BATTERY_21 = frame("05 00 04 21 ca 12 02 04 15 0e 8f")


def battery(seq, flags, level):
    return frame(f"05 00 04 {seq:02x} ca 12 02 {flags:02x} {level:02x} 0e 8f")


def name(seq, text):
    raw = text.encode()
    return bytes([0x05, 0x00, 0x04, seq, 0xC0 | (7 + len(raw)), 0x02, 0x00, len(raw)]) + raw \
        + bytes(J.FRAME_SIZE - 8 - len(raw))


def nak(seq):
    return frame(f"05 00 04 {seq:02x} c7 fe 05")


class FakeDongle:
    """Answers each written query with the next reply whose seq matches; noise first."""

    def __init__(self, replies, noise=()):
        self.replies = list(replies)
        self.noise = list(noise)
        self.written = []

    def read(self):
        if self.noise:
            return list(self.noise.pop(0))
        if not self.written or not self.replies:
            return []
        return list(self.replies.pop(0))


def fake_hid(cols):
    class FakeDevice:
        def open_path(self, path):
            self.c = cols[path]

        def write(self, buf):
            self.c.written.append(bytes(buf))
            return len(buf)

        def read(self, n, timeout):
            return self.c.read()

        def close(self):
            pass
    return types.SimpleNamespace(device=FakeDevice)


def link380_entries(pid=0x24C7):
    shape = [(0x000B, 0x0005), (0xFF00, 0x0001), (0x000C, 0x0001)]
    return [{"product_id": pid, "interface_number": 3, "usage_page": p, "usage": u,
             "path": b"jabra-%04x-%d" % (p, n), "product_string": "Jabra Link 380"}
            for n, (p, u) in enumerate(shape)]


GNP_PATH = b"jabra-ff00-1"


class ParseTest(unittest.TestCase):
    def test_the_request_frames(self):
        self.assertEqual(bytes(J.make_request(0x21, J.CLASS_STATUS, J.OP_BATTERY)),
                         frame("05 04 00 21 46 12 02"))
        self.assertEqual(bytes(J.make_request(0x20, J.CLASS_DEVINFO, J.OP_NAME)),
                         frame("05 04 00 20 46 02 00"))
        self.assertEqual(len(J.make_request(0, 0, 0)), 64)

    def test_the_captured_replies_decode(self):
        self.assertEqual(J.parse_name(J.reply_payload(NAME_EVOLVE2_65, 0x20, 0x02, 0x00)),
                         "Jabra Evolve2 65")
        self.assertEqual(J.parse_battery(J.reply_payload(BATTERY_21, 0x21, 0x12, 0x02)),
                         (21, False))

    def test_charging_is_flag_bit_0(self):
        p = J.reply_payload(battery(0x30, 0x05, 80), 0x30, 0x12, 0x02)
        self.assertEqual(J.parse_battery(p), (80, True))

    def test_other_frames_are_not_the_reply(self):
        self.assertIsNone(J.reply_payload(BATTERY_21, 0x22, 0x12, 0x02))     # other seq
        self.assertIsNone(J.reply_payload(BATTERY_21, 0x21, 0x02, 0x00))     # other command
        self.assertIsNone(J.reply_payload(frame("05 04 00 21 46 12 02"), 0x21, 0x12, 0x02))
        self.assertIsNone(J.reply_payload(frame("03 01 00"), 0x21, 0x12, 0x02))
        self.assertIsNone(J.reply_payload(b"", 0x21, 0x12, 0x02))

    def test_a_nak_is_an_empty_payload(self):
        self.assertEqual(J.reply_payload(nak(0x21), 0x21, 0x12, 0x02), b"")
        self.assertIsNone(J.parse_battery(b""))

    def test_a_level_above_100_is_refused(self):
        self.assertIsNone(J.parse_battery(bytes([0x00, 0xC8])))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (J.hid, J.hidlist)
        self.provider = J.JabraProvider()

    def tearDown(self):
        J.hid, J.hidlist = self._saved

    def poll(self, entries, cols):
        J.hid = fake_hid(cols)
        J.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return self.provider.poll()

    def test_the_headset_is_read_through_the_vendor_collection(self):
        entries = link380_entries()
        dongle = FakeDongle([battery(0x20, 0x04, 21), name(0x21, "Jabra Evolve2 65")])
        cols = {e["path"]: dongle if e["path"] == GNP_PATH else None for e in entries}
        out = self.poll(entries, cols)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("jabra:24c7", "Jabra Evolve2 65", 21, False, True, "jabra", "headset")])
        self.assertEqual(dongle.written, [frame("05 04 00 20 46 12 02"),
                                          frame("05 04 00 21 46 02 00")])

    def test_the_name_is_asked_once(self):
        entries = link380_entries()
        dongle = FakeDongle([battery(0x20, 0x04, 21), name(0x21, "Jabra Evolve2 65"),
                             battery(0x22, 0x05, 22)])
        cols = {GNP_PATH: dongle}
        self.poll(entries, cols)
        out = self.poll(entries, cols)
        self.assertEqual((out[0].name, out[0].level, out[0].charging),
                         ("Jabra Evolve2 65", 22, True))
        self.assertEqual(len(dongle.written), 3)

    def test_events_before_the_reply_are_skipped(self):
        entries = link380_entries()
        dongle = FakeDongle([battery(0x20, 0x04, 55), name(0x21, "Jabra Evolve2 85")],
                            noise=[frame("05 00 04 7f c8 12 10 01 00")])
        out = self.poll(entries, {GNP_PATH: dongle})
        self.assertEqual((out[0].name, out[0].level), ("Jabra Evolve2 85", 55))

    def test_a_switched_off_headset_shows_no_icon(self):
        entries = link380_entries()
        self.assertEqual(self.poll(entries, {GNP_PATH: FakeDongle([nak(0x20)])}), [])
        self.assertEqual(self.poll(entries, {GNP_PATH: FakeDongle([])}), [])

    def test_no_name_falls_back_to_the_dongle(self):
        entries = link380_entries()
        dongle = FakeDongle([battery(0x20, 0x00, 70), nak(0x21)])
        out = self.poll(entries, {GNP_PATH: dongle})
        self.assertEqual(out[0].name, "Jabra headset (Jabra Link 380)")

    def test_other_jabra_devices_are_left_alone(self):
        entries = link380_entries(pid=0x0E40)      # an Evolve2 40 on its own cable
        self.assertEqual(self.poll(entries, {}), [])


class RegistrationTest(unittest.TestCase):
    def test_the_provider_is_registered(self):
        import providers
        self.assertIs(providers.JabraProvider, J.JabraProvider)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "halo_battery.pyw"), encoding="utf-8") as f:
            src = f.read()
        body = src.split("def make_providers(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("JabraProvider()", body)
        self.assertEqual(src.count("JabraProvider()"), 1)
        self.assertIn('"%s":' % J.JabraProvider.name, src)


if __name__ == "__main__":
    unittest.main()
