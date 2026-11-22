"""Tests for providers/akko_s9.py: the Akko Verge S9 Ultra dongle (36eb:1087).

No hardware. The frames are the ones captured with USBPcap in #505: a link
query `02 10 01 00`, a battery query `02 12 01 00`, and replies whose byte 4 is
the level `0x64` (100 %) or `0x63` (99 %) - the number the vendor app showed.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import akko_s9 as A  # noqa: E402


def frame(command, **values):
    body = [A.REPORT, command, A.ACK, 0x00] + [0x00] * (A.LENGTH - 6)
    for name, value in values.items():
        body[int(name[1:])] = value
    checksum = A.frame_checksum(body)
    return body + [checksum >> 8, checksum & 0xFF]


def link(on=True):
    return frame(A.LINK_CMD, b4=0x01 if on else 0x00)


def battery(level, flag=0x00):
    return frame(A.BATTERY_CMD, b3=flag, b4=level)


class FakeDevice:
    def __init__(self, replies, write_error=None, open_error=None):
        self.replies = list(replies)
        self.sent = []
        self.opened = []
        self.write_error = write_error
        self.open_error = open_error
        self.closed = 0

    def open_path(self, path):
        self.opened.append(path)
        if self.open_error is not None:
            raise self.open_error

    def write(self, data):
        if self.write_error is not None:
            raise self.write_error
        self.sent.append(bytes(data))
        return len(data)

    def read(self, length, timeout):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        self.closed += 1


def entry(page=0x008C, usage=0x0001, pid=A.PID, path=None):
    return {"vendor_id": A.VID, "product_id": pid, "usage_page": page, "usage": usage,
            "interface_number": 3, "path": path or f"akko-{page:04x}".encode(),
            "product_string": "Akko S9 Ultra_Dongle"}


class ParseTests(unittest.TestCase):
    def test_the_captured_battery_replies_read(self):
        self.assertEqual(A.parse_battery(battery(0x64)), 100)
        self.assertEqual(A.parse_battery(battery(0x63)), 99)

    def test_a_reply_without_a_level_is_not_a_reading(self):
        self.assertIsNone(A.parse_battery(battery(0x00, flag=0x01)))   # the captured "no value"
        self.assertIsNone(A.parse_battery(battery(101)))
        self.assertIsNone(A.parse_battery(battery(0)))
        self.assertIsNone(A.parse_battery([]))
        self.assertIsNone(A.parse_battery([A.REPORT, A.BATTERY_CMD, A.ACK, 0]))   # short

    def test_undocumented_echoes_are_refused(self):
        self.assertIsNone(A.parse_battery([0x03] + list(battery(80))[1:]))
        self.assertIsNone(A.parse_battery([A.REPORT, A.LINK_CMD, A.ACK, 0, 80]))
        wrong_ack = list(battery(80))
        wrong_ack[2] = 0x00
        self.assertIsNone(A.parse_battery(wrong_ack))

    def test_the_checksum_is_the_captured_one(self):
        # the values the dongle put on the wire in #505
        self.assertEqual([0x4B, 0x43], A.make_request(A.LINK_CMD)[-2:])
        self.assertEqual([0x71, 0x12], A.make_request(A.BATTERY_CMD)[-2:])
        self.assertEqual(0x71E1, A.frame_checksum(bytes.fromhex("0210010001") + bytes(57)))
        self.assertEqual(0xC267, A.frame_checksum(bytes.fromhex("0212010063") + bytes(57)))

    def test_a_bad_checksum_is_refused(self):
        bad = battery(80)
        bad[-1] ^= 0xFF
        self.assertIsNone(A.parse_battery(bad))
        self.assertIsNone(A.parse_link(bad))

    def test_link_replies(self):
        self.assertIs(A.parse_link(link(True)), True)
        self.assertIs(A.parse_link(link(False)), False)
        self.assertIsNone(A.parse_link(battery(80)))
        self.assertIsNone(A.parse_link([]))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist, A.time)
        self.now = [1000.0]
        A.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        self.dev = FakeDevice([])
        A.hid = types.SimpleNamespace(device=lambda: self.dev)
        self.provider = A.AkkoS9Provider()

    def tearDown(self):
        A.hid, A.hidlist, A.time = self._saved

    def poll(self, infos, dev=None):
        if dev is not None:
            self.dev = dev
            A.hid = types.SimpleNamespace(device=lambda: dev)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_the_captured_exchange_gives_the_level(self):
        dev = FakeDevice([link(True), battery(0x63)])
        out = self.poll([entry()], dev)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.charging_known,
                           s.kind) for s in out],
                         [("akko:1087", "Akko S9 Ultra", 99, False, True, False, "headset")])
        self.assertEqual([bytes(A.make_request(A.LINK_CMD)),
                          bytes(A.make_request(A.BATTERY_CMD))], dev.sent)

    def test_the_request_is_the_captured_frame(self):
        dev = FakeDevice([link(True), battery(100)])
        self.poll([entry()], dev)
        self.assertEqual(64, len(dev.sent[1]))
        self.assertEqual([0x02, 0x12, 0x01, 0x00], list(dev.sent[1][:4]))
        self.assertEqual(0, any(dev.sent[1][4:62]))       # zero payload
        self.assertEqual([0x71, 0x12], list(dev.sent[1][62:]))

    def test_link_down_keeps_the_last_level_greyed(self):
        self.poll([entry()], FakeDevice([link(True), battery(77)]))
        self.now[0] += 60
        out = self.poll([entry()], FakeDevice([link(False)]))
        self.assertEqual([(s.level, s.online, s.charging, s.charging_known) for s in out],
                         [(77, False, False, False)])

    def test_link_down_after_the_keep_window_has_no_level(self):
        self.poll([entry()], FakeDevice([link(True), battery(77)]))
        self.now[0] += A.ASLEEP_KEEP + 1
        out = self.poll([entry()], FakeDevice([link(False)]))
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])

    def test_a_reply_with_no_level_stays_online_without_a_number(self):
        out = self.poll([entry()], FakeDevice([link(True), battery(0x00, flag=0x01)]))
        self.assertEqual([(s.level, s.online) for s in out], [(None, True)])

    def test_the_headset_collection_is_tried_first(self):
        headset = entry(0x008C, 0x0001, path=b"headset")
        consumer = entry(0x000C, 0x0001, path=b"consumer")
        dev = FakeDevice([link(True), battery(88)])
        self.poll([consumer, headset], dev)
        self.assertEqual([b"headset"], dev.opened)

    def test_the_consumer_collection_answers_when_the_headset_page_is_silent(self):
        headset = entry(0x008C, 0x0001, path=b"headset")
        consumer = entry(0x000C, 0x0001, path=b"consumer")
        # the headset page is silent for both attempts, then the consumer answers
        dev = FakeDevice([[], [], link(True), battery(88)])
        out = self.poll([headset, consumer], dev)
        self.assertEqual([b"headset", b"consumer"], dev.opened)
        self.assertEqual(88, out[0].level)

    def test_silence_keeps_the_icon_offline(self):
        out = self.poll([entry()], FakeDevice([]))
        self.assertEqual([(s.level, s.online, s.charging_known) for s in out],
                         [(None, False, False)])
        self.assertIn("no reply on any collection", "\n".join(self.provider.diagnostics()))

    def test_an_open_error_does_not_raise(self):
        dev = FakeDevice([], open_error=OSError("busy"))
        out = self.poll([entry()], dev)
        self.assertEqual([(s.level, s.online) for s in out], [(None, False)])
        self.assertIn("open: busy", "\n".join(self.provider.diagnostics()))

    def test_a_write_error_does_not_raise(self):
        dev = FakeDevice([], write_error=OSError("busy"))
        out = self.poll([entry()], dev)
        self.assertEqual(1, len(out))
        self.assertIn("busy", "\n".join(self.provider.diagnostics()))

    def test_another_product_is_ignored(self):
        self.assertEqual([], self.poll([entry(pid=0x1088)], FakeDevice([])))

    def test_a_retry_reads_after_a_junk_reply(self):
        junk = [0x00] * 64
        dev = FakeDevice([junk, link(True), battery(55)])
        out = self.poll([entry()], dev)
        self.assertEqual(55, out[0].level)
        self.assertEqual(3, len(dev.sent))             # link, link again, battery


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
