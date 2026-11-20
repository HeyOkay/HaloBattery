"""Tests for providers/gs3075t.py - no hardware needed.

Frame layout from #379, measured on a GANSS GS3075T:
  host  AA 10 18 00 ... (32 bytes)
  kbd   55 10 18 ... level at byte 25, charge 0x02 at byte 26 while charging.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import gs3075t as G  # noqa: E402


def reply(level=85, charge=0x00, shift=0):
    r = [0x55, 0x10, 0x18] + [0x00] * 22 + [level, charge] + [0x00] * 5
    return ([0x00] + r) if shift else r


class FakeKeyboard:
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.sent = []
        self.path = None

    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def write(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        return self.answers.pop(0) if self.answers else []


def entries(vid=G.VID, pid=G.PID):
    return [{"vendor_id": vid, "product_id": pid, "interface_number": 3,
             "usage_page": G.PAGE, "usage": G.USAGE,
             "path": b"gs-%04x" % pid, "product_string": "GS3075T 2.4G"}]


class ParseTest(unittest.TestCase):
    def test_the_request_is_32_bytes_and_starts_aa_10_18(self):
        self.assertEqual(len(G.request()), G.REPORT_LEN)
        self.assertEqual(bytes(G.request()[:3]), b"\xaa\x10\x18")

    def test_hidapi_gets_the_zero_report_id_first(self):
        self.assertEqual(G.REPORT_ID, 0x00)
        self.assertEqual(G.write_frame(), [0x00] + G.request())
        self.assertEqual(len(G.write_frame()), G.REPORT_LEN + 1)

    def test_the_level_and_charge_are_read(self):
        self.assertEqual(G.parse_reply(reply(85, 0x00)), (85, False))
        self.assertEqual(G.parse_reply(reply(42, 0x02)), (42, True))
        self.assertEqual(G.parse_reply(reply(100, 0x01)), (100, True))

    def test_a_shifted_report_id_is_accepted(self):
        self.assertEqual(G.parse_reply(reply(77, 0x02, shift=1)), (77, True))

    def test_a_bad_frame_is_refused(self):
        self.assertIsNone(G.parse_reply([]))
        self.assertIsNone(G.parse_reply([0x54, 0x10, 0x18] + [0] * 29))
        self.assertIsNone(G.parse_reply(reply(101)))
        self.assertIsNone(G.parse_reply(reply(0)))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (G.hid, G.hidlist)

    def tearDown(self):
        G.hid, G.hidlist = self._saved

    def poll(self, fake, ents):
        G.hid = types.SimpleNamespace(device=lambda: fake)
        G.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        p = G.Gs3075tProvider()
        return p.poll(), p.diagnostics()

    def test_the_keyboard_is_read_on_the_vendor_collection(self):
        fake = FakeKeyboard([reply(85, 0x02)])
        out, _ = self.poll(fake, entries())
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("gs3075t:fefe", "GS3075T 2.4G", 85, True, True,
                           "gs3075t", "keyboard")])
        self.assertEqual(fake.sent, [bytes(G.write_frame())])

    def test_a_silent_first_read_does_not_stop_a_later_answer(self):
        fake = FakeKeyboard([[], reply(90, 0x00)])
        out, _ = self.poll(fake, entries())
        self.assertEqual(out[0].level, 90)

    def test_no_answer_reports_nothing(self):
        out, _ = self.poll(FakeKeyboard([]), entries())
        self.assertEqual(out, [])

    def test_another_sonix_product_is_left_alone(self):
        out, _ = self.poll(FakeKeyboard([reply()]), entries(pid=0xFEFC))
        self.assertEqual(out, [])

    def test_a_fefe_receiver_of_another_board_gets_no_command(self):
        # The same 0C45:FEFE FF60:0061 receiver ships with the ATTACKSHARK X66 (#451).
        for product in ("X66 2.4G", "", None):
            with self.subTest(product=product):
                ents = entries()
                ents[0]["product_string"] = product
                fake = FakeKeyboard([reply()])
                out, diag = self.poll(fake, ents)
                self.assertEqual(out, [])
                self.assertEqual(fake.sent, [])
                self.assertIsNone(fake.path)
                self.assertTrue(any("not a GS3075T" in line for line in diag))

    def test_a_silent_keyboard_gets_one_write_per_poll(self):
        fake = FakeKeyboard([])
        out, _ = self.poll(fake, entries())
        self.assertEqual(out, [])
        self.assertEqual(fake.sent, [bytes(G.write_frame())])

    def test_a_refused_write_is_not_repeated(self):
        class Refusing(FakeKeyboard):
            def write(self, data):
                self.sent.append(bytes(data))
                return -1

            def read(self, size, timeout_ms=0):
                raise AssertionError("no read after a refused write")

        fake = Refusing([reply()])
        out, diag = self.poll(fake, entries())
        self.assertEqual(out, [])
        self.assertEqual(len(fake.sent), 1)
        self.assertTrue(any("refused" in line for line in diag))


if __name__ == "__main__":
    unittest.main()
