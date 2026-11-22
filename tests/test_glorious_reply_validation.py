"""Glorious response validation, literal protocol fixtures; no hardware captures here."""
import unittest
from unittest.mock import patch
from providers import glorious as G

# Independent literal headers from the documented Sinowealth response offsets.
EMPTY = bytes.fromhex("00 a1 00 00 00 00 83 00 00") + bytes(56)
FULL = bytes.fromhex("00 a1 00 00 00 00 83 01 64") + bytes(56)
ECHO = bytes.fromhex("00 00 00 02 02 00 83 00 00") + bytes(56)

class ResponseValidationTest(unittest.TestCase):
    def test_real_zero_and_full_levels_are_not_retry_heuristics(self):
        self.assertEqual((0, False), G.parse_reply(EMPTY))
        self.assertEqual((100, True), G.parse_reply(FULL))

    def test_request_echo_is_never_a_live_zero(self):
        for length in (64, 65):
            with self.subTest(length=length):
                self.assertIsNone(G.parse_reply(ECHO[:length]))
                self.assertIsNone(G.parse_reply(bytes(G.make_request(length))))
        # Altering the charge byte does not turn an unacknowledged command into data.
        charge_echo = bytearray(ECHO)
        charge_echo[7] = 1
        self.assertIsNone(G.parse_reply(charge_echo))

    def test_only_confirmed_awake_status_can_publish_a_level(self):
        for status in (0, 0xa0, 0xa2, 0xa3, 0xa4, 0xff):
            for level in (0, 75, 100):
                frame = bytearray(EMPTY)
                frame[1], frame[8] = status, level
                with self.subTest(status=status, level=level):
                    self.assertIsNone(G.parse_reply(frame))

    def test_wrong_report_function_range_and_truncation(self):
        for index, value in ((0, 1), (6, 0x81), (8, 101), (8, 255)):
            frame = bytearray(EMPTY)
            frame[index] = value
            self.assertIsNone(G.parse_reply(frame))
        for length in range(9):
            self.assertIsNone(G.parse_reply(EMPTY[:length]))

class FakeDevice:
    def __init__(self, replies, sent=None):
        self.replies, self.sent, self.writes = list(replies), sent, []
        self.closed = False
    def open_path(self, path): pass
    def send_feature_report(self, data):
        self.writes.append(data)
        return len(data) if self.sent is None else self.sent
    def get_feature_report(self, rid, length):
        result = self.replies.pop(0) if self.replies else []
        if isinstance(result, Exception): raise result
        return result
    def close(self): self.closed = True

class ExchangeValidationTest(unittest.TestCase):
    def ask(self, replies, sent=None):
        dev = FakeDevice(replies, sent)
        provider = G.GloriousProvider()
        with patch.object(G, 'hid') as hid, patch.object(G.time, 'sleep'):
            hid.device.return_value = dev
            result = provider._ask(b'fake', 65)
        self.assertTrue(dev.closed)
        return result, dev
    def test_echo_retries_until_a_real_answer(self):
        result, dev = self.ask([ECHO, FULL])
        self.assertEqual((100, True), result)
        self.assertEqual(2, len(dev.writes))
    def test_zero_is_accepted_on_first_confirmed_response(self):
        result, dev = self.ask([EMPTY, FULL])
        self.assertEqual((0, False), result)
        self.assertEqual(1, len(dev.writes))
    def test_waking_reply_is_retried(self):
        waking = bytearray(EMPTY)
        waking[1] = 0xa2
        result, dev = self.ask([waking, FULL])
        self.assertEqual((100, True), result)
        self.assertEqual(2, len(dev.writes))
    def test_device_disappears_mid_read(self):
        result, _ = self.ask([OSError('removed')])
        self.assertIsNone(result)
    def test_short_or_refused_write_never_reads_cached_answer(self):
        for sent in (-1, 0, 12, 64):
            with self.subTest(sent=sent):
                result, dev = self.ask([FULL], sent)
                self.assertIsNone(result)
                self.assertEqual([FULL], dev.replies)
