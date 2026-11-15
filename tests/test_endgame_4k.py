
"""Corrected A1 feature protocol and synthetic replies; no hardware."""
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch
from providers import endgame_4k as E
from providers import endgame4k_protocol as P


def reply(payload=b'', status=1, length=63):
    raw = bytearray(length)
    raw[:2] = bytes([0xA1, status])
    raw[16:16+len(payload)] = payload
    return bytes(raw)


def ident(pid=0x1968):
    return reply(bytes.fromhex('67 33') + pid.to_bytes(2, 'little') + b'\0\0\1\7')


def entry(pid=0x1970, path=b'receiver', page=0xFF01):
    return dict(vendor_id=0x3367, product_id=pid, path=path, usage_page=page, usage=2)


class Fake:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.writes = []
        self.closed = 0
    def open_path(self, path):
        self.path = path
    def send_feature_report(self, data):
        self.writes.append(bytes(data))
        return len(data)
    def get_feature_report(self, rid, size):
        item = next(self.replies)
        if isinstance(item, Exception):
            raise item
        return item
    def close(self):
        self.closed += 1


class Endgame4kTests(unittest.TestCase):
    def setUp(self):
        policy = patch.object(E, 'ENABLED_PIDS', frozenset({0x1970, 0x1968, 0x1982}))
        policy.start()
        self.addCleanup(policy.stop)
        native = patch.object(E.Endgame4kProvider, '_read', E.Endgame4kProvider._read_local)
        native.start()
        self.addCleanup(native.stop)
        self.sleep = patch.object(P.time, 'sleep', lambda _: None)
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    def run_fake(self, replies, entries=None, provider=None):
        dev = Fake(replies)
        provider = provider or E.Endgame4kProvider()
        with patch.object(E, 'hid', types.SimpleNamespace(device=lambda: dev)), patch.object(
                E.hidlist, 'enumerate', lambda _: [entry()] if entries is None else entries):
            result = provider.poll()
        return result, dev, provider

    def test_confirmed_wireless_identity_and_two_agreeing_values(self):
        result, dev, _ = self.run_fake([ident(), reply(b'\x23'), reply(b'\x23')])
        self.assertEqual((result[0].level, result[0].name), (35, 'Endgame Gear XM2w 4k'))
        self.assertEqual([d[1] for d in dev.writes], [14, 180, 180])
        self.assertFalse(result[0].charging_known)
        self.assertEqual(dev.closed, 1)

    def test_boundary_values_zero_and_hundred(self):
        for pct in (0, 100):
            result, _, _ = self.run_fake([ident(), reply(bytes([pct])), reply(bytes([pct]))])
            self.assertEqual(result[0].level, pct)

    def test_noisy_read_needs_consecutive_agreement(self):
        result, _, _ = self.run_fake([ident(), reply(b'\x23'), reply(b'\x28'), reply(b'\x28')])
        self.assertEqual(result[0].level, 40)

    def test_unstable_read_is_not_averaged_or_published(self):
        result, _, _ = self.run_fake([ident(), reply(b'\x23'), reply(b'\x28'), reply(b'\x2d')])
        self.assertEqual(result, [])

    def test_out_of_range_percentage_refused(self):
        self.assertEqual(self.run_fake([ident(), reply(b'\xff')])[0], [])

    def test_wired_preferred_no_percentage_and_one_icon(self):
        result, dev, _ = self.run_fake([ident(), reply(b'\x19')],
                                      [entry(), entry(0x1968, b'wired')])
        self.assertEqual(dev.path, b'wired')
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].level)
        self.assertTrue(result[0].charging)
        self.assertFalse(result[0].charging_known)
        self.assertEqual(result[0].ui_message, 'state.charging_level_unknown')

    def test_other_model_identity_does_not_query_battery(self):
        result, dev, _ = self.run_fake([ident(0x1972)])
        self.assertEqual(result, [])
        self.assertEqual(len(dev.writes), 1)

    def test_old_we_collection_is_not_opened(self):
        result, dev, _ = self.run_fake([], [entry(page=0xFF02)])
        self.assertEqual(result, [])
        self.assertEqual(dev.writes, [])

    def test_multiple_receivers_refuse_requests(self):
        result, dev, _ = self.run_fake([], [entry(), entry(path=b'other')])
        self.assertEqual(result, [])
        self.assertEqual(dev.writes, [])

    def test_sleep_stale_state_then_expires_without_wake(self):
        result, _, provider = self.run_fake([ident(), reply(b'\x23'), reply(b'\x23')])
        result, dev, _ = self.run_fake([reply(status=8)], provider=provider)
        self.assertFalse(result[0].online)
        self.assertTrue(result[0].wake_on_input)
        self.assertEqual([d[1] for d in dev.writes], [14])
        provider._seen -= 301
        self.assertEqual(self.run_fake([reply(status=8)], provider=provider)[0], [])

    def test_receiver_removed_clears_cache(self):
        _, _, provider = self.run_fake([ident(), reply(b'\x23'), reply(b'\x23')])
        self.assertEqual(self.run_fake([], [], provider)[0], [])
        self.assertIsNone(provider._last)

    def test_disappearing_device_closes_handle(self):
        result, dev, _ = self.run_fake([OSError('gone')])
        self.assertEqual(result, [])
        self.assertEqual(dev.closed, 1)

    def test_busy_retry_bounded(self):
        result, dev, _ = self.run_fake([reply(status=3)] * 4)
        self.assertEqual(result, [])
        self.assertEqual(len(dev.writes), 1)

    def test_production_allowlist_excludes_wake_and_configuration(self):
        for cmd in range(256):
            if cmd in (14, 180):
                continue
            with self.assertRaises(ValueError):
                P.frame(cmd)

    def test_v2_has_independent_identity_key(self):
        result, _, _ = self.run_fake([ident(0x1982), reply(b'\x23'), reply(b'\x23')])
        self.assertEqual(result[0].key, 'endgame_4k:xm2w4kv2')
        self.assertEqual(result[0].name, 'Endgame Gear XM2w 4k v2')

    def test_both_observed_lengths_and_malformed_reports(self):
        for length in (63, 64):
            raw = reply(b'\x00', length=length)
            self.assertEqual(P.response(raw), raw)
        for raw in (b'', b'\xa1', bytes(62), bytes(65), b'\0' + reply()):
            with self.assertRaises(P.ProtocolError):
                P.response(raw)
        for status in (3, 8, 17):
            with self.assertRaises(P.DeviceStatusError):
                P.response(reply(bytes([95]), status=status))

    def test_short_send_never_reads_and_closes(self):
        dev = Fake([])
        with patch.object(dev, 'send_feature_report', return_value=-1), patch.object(
                dev, 'get_feature_report') as read, patch.object(
                E, 'hid', types.SimpleNamespace(device=lambda: dev)):
            self.assertIsNone(E.Endgame4kProvider()._read_local(entry()))
            read.assert_not_called()
        self.assertEqual(dev.closed, 1)

    def test_wired_identity_mismatch_never_queries_battery(self):
        result, dev, _ = self.run_fake([ident(0x1982)], [entry(0x1968, b'wired')])
        self.assertEqual(result, [])
        self.assertEqual(len(dev.writes), 1)

    def test_wrong_vendor_identity_never_queries_battery(self):
        data = bytearray(ident())
        data[16:18] = b'\0\0'
        result, dev, _ = self.run_fake([data])
        self.assertEqual(result, [])
        self.assertEqual(len(dev.writes), 1)

    def test_hold_is_checked_before_enumeration_or_worker(self):
        with patch.object(E, 'ENABLED_PIDS', frozenset()), patch.object(
                E.hidlist, 'enumerate') as enumerate_, patch.object(
                E.Endgame4kProvider, '_read') as read:
            self.assertEqual(E.Endgame4kProvider().poll(), [])
            enumerate_.assert_not_called()
            read.assert_not_called()
