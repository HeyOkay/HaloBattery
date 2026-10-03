"""Tests for providers/flydigi.py: the Vader 5 Pro's own battery channel. No hardware.

The protocol is SDL's (SDL_hidapi_flydigi.c): a 32-byte report protocol on the pad's
0xFFA0 vendor collection. A write of ``5A A5 01`` (first byte zeroed for the Vader 5)
is answered by a report whose byte 11 carries the battery - high nibble the state
(0 on battery, 1 charging, 2 charged), low nibble the level step, x20. The case from
#191: a Vader 5 Pro on its 2.4 GHz dongle whose XInput type says "wired" and whose
Windows.Gaming.Input report is the constant remain=full=1000 placeholder. SDL's
check-architecture request goes first, then the info request; the fake returns the
padded length for every short write, which is what Windows hidapi does (the first
test build mistook that for a failed write and never read the answer).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from providers import flydigi  # noqa: E402

PATH = b"\\\\?\\HID#VID_37D7&PID_2401&MI_01#7&2a3b4c5d&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"


def info_frame(state=0, step=3, device=130, connection=1, firmware=(0x41, 0x71),
               numbered=True):
    """The device info answer as the pad frames one: 32 bytes, id first when numbered."""
    body = bytearray(31)
    body[0], body[1], body[2] = 0x5A, 0xA5, 0x01
    body[5] = device
    body[6] = connection
    body[11] = (state << 4) | step
    body[15], body[16] = firmware
    if numbered:
        return bytes([0x03]) + bytes(body)
    return bytes(body) + b"\x00"


# an input report from the pad's own stream, which shares the collection
INPUT = bytes([0x03, 0x5A, 0xA5, 0xEF]) + bytes(28)


class FakePad:
    """Serves queued reports once something was written; records the writes."""

    def __init__(self, frames=(), padded=True, fail=False, answer_on=2):
        self.queue = list(frames)
        self.written = []
        self.padded = padded   # Windows pads a short write and returns the length
        self.fail = fail
        self.answer_on = answer_on   # the 1-based write the pad answers to (#1 = arch)
        self.closed = False

    def open_path(self, path):
        pass

    def write(self, data):
        self.written.append(bytes(data))
        if self.fail:
            return -1
        return 33 if self.padded else len(data)

    def read(self, size, timeout_ms):
        if len(self.written) < self.answer_on:   # nothing arrives before the request
            return []
        return list(self.queue.pop(0)) if self.queue else []

    def close(self):
        self.closed = True


class FlydigiTests(unittest.TestCase):
    def setUp(self):
        self.pad = FakePad()
        self.infos = []
        for p in (mock.patch.object(flydigi.hid, "device", lambda: self.pad),
                  mock.patch.object(flydigi.hidlist, "enumerate", lambda vid=0: list(self.infos))):
            p.start()
            self.addCleanup(p.stop)
        self.diag = []

    def read(self):
        return flydigi.read_battery(PATH, self.diag, "FlyDigi Vader 5 Pro")

    # ---- the reply ------------------------------------------------------
    def test_the_info_reply_carries_the_level_and_state(self):
        self.pad.queue = [info_frame(state=0, step=3)]
        r = self.read()
        self.assertEqual(r, flydigi.Reading(60, False, "", "FlyDigi Vader 5 Pro"))
        self.assertTrue(self.pad.closed)
        joined = "\n".join(self.diag)
        self.assertIn("info reply", joined)
        self.assertIn("device=130", joined)
        self.assertIn("battery byte=0x03", joined)

    def test_each_state_maps_to_level_and_charging(self):
        for state, step, level, charging, approx in ((0, 0, 0, False, ""),
                                                     (1, 2, 40, True, "charging"),
                                                     (2, 0, 100, True, "fully charged")):
            with self.subTest(state=state, step=step):
                self.diag = []
                self.pad = FakePad(frames=[info_frame(state=state, step=step)])
                r = self.read()
                self.assertEqual((r.level, r.charging, r.approx), (level, charging, approx))

    def test_the_unnumbered_reply_parses_too(self):
        self.pad.queue = [info_frame(state=0, step=5, numbered=False)]
        self.assertEqual(self.read().level, 100)

    def test_the_pads_input_stream_is_skipped(self):
        self.pad.queue = [INPUT, INPUT, info_frame(state=0, step=4)]
        self.assertEqual(self.read().level, 80)

    def test_an_out_of_range_reply_is_refused(self):
        for state, step in ((3, 0), (0, 6), (0, 9), (0xF, 0xF)):
            with self.subTest(state=state, step=step):
                self.diag = []
                self.pad = FakePad(frames=[info_frame(state=state, step=step)])
                self.assertIsNone(self.read())
                self.assertIn("unexpected battery byte", "\n".join(self.diag))

    # ---- the write ------------------------------------------------------
    def test_the_architecture_check_comes_first_then_the_info_request(self):
        self.pad.queue = [info_frame()]
        self.read()
        self.assertEqual(self.pad.written[0], b"\x00\x5a\xa5\x07\x00\x00")
        self.assertEqual(self.pad.written[1], b"\x00\x5a\xa5\x01\x02\x00")

    def test_no_answer_tries_every_write_shape(self):
        self.pad.queue = []
        self.assertIsNone(self.read())
        self.assertEqual(self.pad.written, [flydigi.ARCH_REQUEST,
                                            flydigi.INFO_REQUEST,
                                            flydigi.INFO_REQUEST_PADDED,
                                            flydigi.INFO_REQUEST_NUMBERED,
                                            flydigi.INFO_REQUEST_NUMBERED_PADDED])
        self.assertIn("no info reply", "\n".join(self.diag))

    def test_the_shape_that_answered_is_named_in_the_diagnostics(self):
        # the first shape gets no answer, the padded one does: the log says which
        self.pad = FakePad(frames=[info_frame()], answer_on=3)
        self.assertEqual(self.read().level, 60)
        joined = "\n".join(self.diag)
        self.assertIn("info reply (padded)", joined)
        self.assertEqual(self.pad.written[2], flydigi.INFO_REQUEST_PADDED)

    def test_a_padded_write_still_reads_the_reply(self):
        # the first #191 test build compared the write's return value to the
        # buffer's length; Windows hidapi answers with the padded length (33), so
        # the read was skipped and a pad that was answering looked silent
        self.pad.queue = [info_frame(state=0, step=3)]
        self.assertEqual(self.read().level, 60)
        self.assertIn("padded to 33", "\n".join(self.diag))

    def test_a_failed_write_is_logged_and_the_next_shape_tried(self):
        self.pad = FakePad(frames=[info_frame()], fail=True)
        self.assertIsNone(self.read())
        joined = "\n".join(self.diag)
        self.assertIn("failed", joined)
        self.assertEqual(len(self.pad.written), 5)  # the arch check + four info shapes

    # ---- collection picking ---------------------------------------------
    def test_collections_keeps_only_the_vendor_page_of_known_ids(self):
        self.infos = [
            {"product_id": 0x2401, "usage_page": 0x0001, "usage": 0x0005, "path": PATH},
            {"product_id": 0x2401, "usage_page": 0xFFA0, "usage": 0x0001, "path": PATH},
            {"product_id": 0x2501, "usage_page": 0xFFA0, "usage": 0x0001, "path": PATH},
        ]
        got = flydigi.collections()
        self.assertEqual([d["usage_page"] for d in got], [0xFFA0])
        self.assertEqual(got[0]["product_id"], 0x2401)

    def test_read_connected_without_a_pad_is_silent(self):
        self.infos = []
        self.assertIsNone(flydigi.read_connected(self.diag))
        self.assertEqual(self.diag, [])

    def test_read_connected_names_the_pad_from_its_pid(self):
        self.infos = [{"product_id": 0x2401, "usage_page": 0xFFA0, "usage": 0x0001, "path": PATH}]
        self.pad.queue = [info_frame(state=1, step=5)]
        r = flydigi.read_connected(self.diag)
        self.assertEqual((r.level, r.charging, r.name), (100, True, "FlyDigi Vader 5 Pro"))
        self.assertIn("FlyDigi Vader 5 Pro (pid=2401), 1 vendor collection(s)",
                      "\n".join(self.diag))


if __name__ == "__main__":
    unittest.main()
