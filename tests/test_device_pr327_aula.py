"""Tests for the AULA devices: providers/aula.py (F87 Pro V2C keyboard) and the AULA
half of providers/lofree.py (V9 Max+ mouse). No hardware is needed.

The keyboard answers `00 AA 10 ...` with `55 10 <type> ... <level>` at byte 26 of the
33-byte report. hidapi drops the leading 0 report id on some backends, so the provider
accepts the header at byte 0 as well; the fake sends it the way Windows does (report id
first), and one test covers the shifted layout.

The mouse answers the Lofree exchange (start 01 -> command -> answer from byte 7 -> end 02)
with the charge state one byte past the level, and the receiver pushes a link-state frame
`AA 00 AA .. <state>` that says the mouse is switched off.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import aula as K          # noqa: E402
from providers import lofree as F        # noqa: E402


# --- keyboard ---------------------------------------------------------------------------

def kb_report(level=76, frame_type=0x00, shaped=True):
    """A 33-byte answer; `shaped=False` drops the leading 0 report id, as hidapi can."""
    r = [0x00, 0x55, 0x10, frame_type] + [0x00] * 22 + [level, 0x00] + [0x00] * 5
    r = r[:33]
    return r if shaped else r[1:]


class FakeKeyboard:
    def __init__(self, level=76, silent=False, answer=None):
        self.level, self.silent, self.answer = level, silent, answer
        self.writes, self.queue = [], []

    def open_path(self, path):
        self.path = path

    def write(self, data):
        self.writes.append(list(data))
        if not self.silent:
            self.queue.append(self.answer(level=self.level) if self.answer
                              else kb_report(self.level))
        return len(data)

    def read(self, n, timeout=None):
        return self.queue.pop(0) if self.queue else []

    def close(self):
        pass


def kb_entries(pid=0xFEFC, page=K.PAGE, usage=K.USAGE):
    return [{"vendor_id": K.VID, "product_id": pid, "usage_page": page, "usage": usage,
             "path": b"kb-%04x" % pid, "product_string": "AULA F87ProV2C Dongle"}]


class KeyboardParseTest(unittest.TestCase):
    def test_the_query_is_33_bytes_and_starts_with_00_aa_10(self):
        req = K.request()
        self.assertEqual(len(req), 33)
        self.assertEqual(req[:3], [0x00, 0xAA, 0x10])

    def test_the_level_is_byte_26_with_the_report_id_in_front(self):
        self.assertEqual(K.parse_level(kb_report(76)), 76)

    def test_the_shifted_layout_is_accepted_too(self):
        # report id 0 means "no report id" in HID, so hidapi may drop the leading byte
        self.assertEqual(K.parse_level(kb_report(76, shaped=False)), 76)

    def test_both_frame_types_carry_the_level(self):
        self.assertEqual(K.parse_level(kb_report(96, frame_type=0x18)), 96)
        self.assertEqual(K.parse_level(kb_report(95, frame_type=0x00)), 95)

    def test_a_zero_or_out_of_range_level_is_not_a_reading(self):
        self.assertIsNone(K.parse_level(kb_report(0)))
        self.assertIsNone(K.parse_level(kb_report(200)))

    def test_another_command_or_a_short_report_is_refused(self):
        wrong = kb_report(76)
        wrong[2] = 0x11
        self.assertIsNone(K.parse_level(wrong))
        self.assertIsNone(K.parse_level([0x00, 0x55, 0x10]))
        self.assertIsNone(K.parse_level(None))


class KeyboardPollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (K.hid, K.hidlist)
        self.kbd = FakeKeyboard(level=76)
        K.hid = types.SimpleNamespace(device=lambda: self.kbd)

    def tearDown(self):
        K.hid, K.hidlist = self._saved

    def poll(self, entries):
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        p = K.AulaProvider()
        return p.poll(), p.diagnostics()

    def test_the_keyboard_gives_one_icon_on_its_receiver(self):
        out, _ = self.poll(kb_entries())
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("aula:fefc", "AULA F87ProV2C Dongle", 76, False, True,
                           "aula", "keyboard")])
        # exactly one query, and it is the documented one
        self.assertEqual(len(self.kbd.writes), 1)
        self.assertEqual(self.kbd.writes[0][:3], [0x00, 0xAA, 0x10])

    def test_a_silent_keyboard_shows_nothing(self):
        # the receiver does not cache: no reply means the keyboard is off
        self.kbd.silent = True
        out, diag = self.poll(kb_entries())
        self.assertEqual(out, [])
        self.assertTrue(any("no answer" in line for line in diag))

    def test_the_wired_collection_is_not_claimed(self):
        # 38A6:2712 reports no battery; a 0xFF67 collection must not be written to
        out, _ = self.poll(kb_entries(pid=0x2712, page=0xFF67, usage=0x0061))
        self.assertEqual(out, [])
        self.assertEqual(self.kbd.writes, [])


# --- mouse through the Lofree exchange --------------------------------------------------

def m_report(b2, data=()):
    p = [0x00, 0x00, b2, len(data), 0x00, 0x00, 0x00] + list(data)
    return [F.REPORT_ID] + p + [0x00] * (31 - len(p))


def beat(state):
    """The pushed link-state frame: `04 AA 00 AA ... <state>` with the state at byte 7."""
    p = [0xAA, 0x00, 0xAA, 0x00, 0x00, 0x00, 0x00, state]
    return [F.REPORT_ID] + p + [0x00] * (31 - len(p))


class FakeMouse:
    def __init__(self, level=89, charge=0, silent=False, link=None):
        self.level, self.charge, self.silent, self.link = level, charge, silent, link
        self.writes, self.queue = [], []

    def open_path(self, path):
        self.path = path

    def write(self, data):
        data = list(data)
        self.writes.append(data)
        if self.silent:
            return len(data)
        b2 = data[3]
        if b2 in (F.START, F.END):
            self.queue.append(m_report(b2))
            return len(data)
        if self.link is not None:
            self.queue.append(beat(self.link))
        self.queue.append(m_report(F.CMD_BATTERY, [self.level, self.charge]))
        return len(data)

    def read(self, n, timeout=None):
        return self.queue.pop(0) if self.queue else []

    def close(self):
        pass


def mouse_entries(*pids):
    out = []
    for pid in pids:
        out.append({"vendor_id": F.AULA_VID, "product_id": pid,
                    "usage_page": F.USAGE_PAGE, "usage": F.USAGE,
                    "path": b"mouse-%04x" % pid,
                    "product_string": "AULA-8K Dongle" if pid == 0x22A0 else "AULA-V9Max+"})
    return out


class MouseTest(unittest.TestCase):
    def setUp(self):
        self._saved = (F.hid, F.hidlist)
        self.mouse = FakeMouse()
        F.hid = types.SimpleNamespace(device=lambda: self.mouse)

    def tearDown(self):
        F.hid, F.hidlist = self._saved

    def poll(self, entries, dongle=None):
        F.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        F.hid = types.SimpleNamespace(device=lambda: (dongle or self.mouse))
        p = F.LofreeProvider()
        return p.poll(), p.diagnostics()

    def test_the_mouse_answers_the_lofree_exchange_on_the_same_collection(self):
        out, _ = self.poll(mouse_entries(0x22A0))
        self.assertEqual([(s.key, s.level, s.charging, s.online, s.kind) for s in out],
                         [("aula:mouse", 89, False, True, "mouse")])
        # start, the command, then end - as the Lofree web driver does
        self.assertEqual([w[3] for w in self.mouse.writes[:3]],
                         [F.START, F.CMD_BATTERY, F.END])

    def test_the_charge_byte_is_read_one_past_the_level(self):
        self.mouse.level, self.mouse.charge = 99, 1
        out, _ = self.poll(mouse_entries(0x22A0))
        self.assertEqual([(s.level, s.charging) for s in out], [(99, True)])
        self.mouse.level, self.mouse.charge = 100, 2      # the maker counts 2 as charging
        out, _ = self.poll(mouse_entries(0x22A0))
        self.assertEqual([(s.level, s.charging) for s in out], [(100, True)])

    def test_a_zero_level_is_not_a_reading(self):
        # a receiver that was just re-plugged answers zeros until the mouse reports
        self.mouse.level = 0
        out, diag = self.poll(mouse_entries(0x22A0))
        self.assertEqual(out, [])
        self.assertTrue(any("out of range" in line for line in diag))

    def test_a_link_down_frame_marks_the_mouse_offline(self):
        # the receiver answers from its cache, so the pushed frame decides
        self.mouse.link = 0x00
        out, _ = self.poll(mouse_entries(0x22A0))
        self.assertEqual([(s.level, s.online) for s in out], [(89, False)])

    def test_a_link_up_frame_keeps_it_online(self):
        self.mouse.link = F.LINK_ON
        out, _ = self.poll(mouse_entries(0x22A0))
        self.assertEqual([(s.level, s.online) for s in out], [(89, True)])

    def test_the_dongle_and_the_cable_are_one_icon(self):
        # both ids are present; the cable is read first and answers
        cable = FakeMouse(level=91, charge=1)
        out, _ = self.poll(mouse_entries(0x22B7, 0x22A0), dongle=cable)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].key, "aula:mouse")
        self.assertEqual(len(self.mouse.writes), 0)      # the dongle was not read at all

    def test_a_dongle_of_another_vendor_is_left_alone(self):
        # the Lofree keyboard path must not open the AULA collection
        entries = [{"vendor_id": F.LOFREE_VID, "product_id": 0x0025,
                    "usage_page": F.USAGE_PAGE, "usage": F.USAGE,
                    "path": b"lofree", "product_string": "HYZEN67@Lofree"}]
        out, _ = self.poll(entries)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
