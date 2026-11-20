"""Literal HID++ truncation regressions, with no generated receive padding or hardware."""

# Independent wire fixtures: Solaar e7304c4 lib/logitech_receiver/hidpp20_constants.py L37-55,90; hidpp10_constants.py L130-138,188-190.
# Keep protocol selectors independent of the provider constants under test.
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_logitech import FakeHidpp, LogitechTestCase, g502, receiver  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from providers import logitech as L  # noqa: E402


class WireDevice:
    def __init__(self, frames=()):
        self.frames = [list(f) for f in frames]
        self.writes = []

    def write(self, frame):
        self.writes.append(list(frame))

    def read(self, size):
        return self.frames.pop(0) if self.frames else []


class WireTests(unittest.TestCase):
    def channel(self, frames):
        channel = L._Channel.__new__(L._Channel)
        channel.long = WireDevice(frames)
        channel.devs = [channel.long, WireDevice()]
        channel.alt_path = None
        channel.alt = None
        channel._swid = 0
        return channel

    def exchange(self, frames, method, *args):
        channel = self.channel(frames)
        # One iteration per literal frame, then a deterministic timeout.
        times = [0] * (len(frames) + 1) + [2]
        with mock.patch.object(L.time, "time", side_effect=times), \
                mock.patch.object(L.time, "sleep"):
            return getattr(channel, method)(*args, timeout=1)

    def test_truncated_feature_frames_are_not_padded(self):
        for frame in (bytes.fromhex("11 01 06 0b"),
                      bytes.fromhex("11 01 06 0b 0f"),
                      bytes.fromhex("11 01 06 0b 0f 7b")):
            with self.subTest(frame=frame):
                self.assertIsNone(self.exchange([frame], "request", 1, 6, 0))

    def test_valid_short_and_long_feature_payloads_remain_exact(self):
        short = bytes.fromhex("10 01 06 0b 0f 7b 00")
        long = bytes.fromhex("11 01 06 0b 0f 7b 00 00 00 00 00 00 00 00 00 00 00 00 00 00")
        for frame in (short, bytes.fromhex("11 01 06 0b 0f 7b 00"), long):
            with self.subTest(frame=frame):
                result = self.exchange([frame], "request", 1, 6, 0)
                self.assertEqual(result, list(frame[4:]))
                self.assertEqual(L.parse_battery(0x1001, result), (76, False, ""))

    def test_truncated_feature_is_skipped_before_valid_reply(self):
        frames = [bytes.fromhex("11 01 06 0b 0f 7b"),
                  bytes.fromhex("10 01 06 0b 0f 7b 00")]
        self.assertEqual(self.exchange(frames, "request", 1, 6, 0), [0x0F, 0x7B, 0])

    def test_truncated_register_frames_are_not_padded(self):
        for frame in (bytes.fromhex("10 01 81 0d"),
                      bytes.fromhex("10 01 81 0d 57"),
                      bytes.fromhex("10 01 81 0d 57 00")):
            with self.subTest(frame=frame):
                self.assertIsNone(self.exchange([frame], "read_register", 1, 0x0D))

    def test_valid_short_and_long_register_payloads_remain_exact(self):
        short = bytes.fromhex("10 01 81 0d 57 00 50")
        long = bytes.fromhex("11 01 81 0d 57 00 50 00 00 00 00 00 00 00 00 00 00 00 00 00")
        for frame in (short, long):
            with self.subTest(frame=frame):
                result = self.exchange([frame], "read_register", 1, 0x0D)
                self.assertEqual(result, list(frame[4:]))
                self.assertEqual(L.parse_register_battery(0x0D, result), (87, True, ""))

    def test_truncated_register_is_skipped_before_valid_reply(self):
        frames = [bytes.fromhex("10 01 81 0d 57"),
                  bytes.fromhex("10 01 81 0d 57 00 30")]
        self.assertEqual(self.exchange(frames, "read_register", 1, 0x0D), [87, 0, 0x30])

    def test_headset_register_skips_missing_connection_state(self):
        frames = [bytes.fromhex("10 01 05 0d 0f 7b"),
                  bytes.fromhex("10 01 05 0d 0f 7b 01")]
        result = self.exchange(frames, "read_headset_register", 1, 0x0D)
        self.assertEqual(result, list(frames[1]))
        self.assertEqual(L.parse_headset_voltage(result)[1], False)


class DecoderTests(unittest.TestCase):
    def test_missing_battery_parameters_yield_no_reading(self):
        for feature in L.BATTERY_FEATURES:
            for params in (None, [], [0x0F], [0x0F, 0x7B]):
                with self.subTest(feature=feature, params=params):
                    self.assertEqual(L.parse_battery(feature, params), (None, False, ""))
                    self.assertFalse(L.charge_complete(feature, params))

    def test_missing_register_parameters_yield_no_reading(self):
        for reg, payloads in (
                (0x0D, (None, [], [87], [87, 0])),
                (0x07, (None, [], [7]))):
            for payload in payloads:
                with self.subTest(reg=reg, payload=payload):
                    self.assertEqual(L.parse_register_battery(reg, payload), (None, False, ""))
        self.assertEqual(L.parse_register_battery(0x07, [7, 0x21]),
                         (90, True, "about 90% (full)"))

    def test_connection_flags_and_valid_approximations_are_preserved(self):
        for payload, expected in (
                ("0f 7b 00", (None, False, "")),  # ADC connection bit absent
                ("0f 7b 01", (76, False, "")),
                ("0f 7b 03", (76, True, ""))):
            self.assertEqual(L.parse_battery(0x1F20, bytes.fromhex(payload)), expected)
        self.assertEqual(L.parse_battery(0x1004, bytes.fromhex("00 08 03")),
                         (90, True, "about 90% (full)"))
        self.assertTrue(L.charge_complete(0x1001, bytes.fromhex("10 6a 81")))
        self.assertEqual(L.parse_headset_voltage(None), (None, False))
        self.assertEqual(L.parse_headset_voltage(bytes.fromhex("10 01 05 0d 0f 7b")), (None, False))


class ConsumerTests(unittest.TestCase):
    def identity(self, length, chunks, kind, info):
        channel = L._Channel.__new__(L._Channel)
        channel.feature_index = lambda idx, feature: 3 if feature == 0x0005 else 2

        def request(idx, feat, func, params=()):
            if feat == 2:
                return info
            if func == 0:
                return length
            if func == 2:
                return kind
            return chunks.get(params[0])
        channel.request = mock.Mock(side_effect=request)
        return channel.identity(1), channel.request

    def test_empty_or_truncated_feature_discovery_is_no_feature(self):
        channel = L._Channel.__new__(L._Channel)
        for params in (None, [], [6], [6, 0]):
            channel.request = mock.Mock(return_value=params)
            with self.subTest(params=params):
                self.assertEqual(channel.feature_index(1, 0x1001), 0)
        channel.request = mock.Mock(return_value=[6, 0, 0])
        self.assertEqual(channel.feature_index(1, 0x1001), 6)

    def test_incomplete_ping_never_marks_slot_awake(self):
        provider = L.LogitechProvider()
        slot = (0xC539, "", 1)
        channel = mock.Mock(error=False, error_code=0)
        for params in (None, [], [4], [4, 2]):
            channel.request.return_value = params
            with self.subTest(params=params):
                self.assertFalse(provider._ping(channel, slot))
                self.assertIn(slot, provider._asleep)
        channel.request.return_value = [4, 2, 0]
        self.assertTrue(provider._ping(channel, slot))
        self.assertNotIn(slot, provider._asleep)

    def test_partial_name_is_discarded_and_partial_unit_id_is_not_exposed(self):
        result, request = self.identity([18, 0, 0], {0: b"ABCDEFGHIJKLMNOP", 16: b"Q"},
                                        [3, 0, 0], [1, 0xC1, 0x5E])
        self.assertEqual(result, ("", "mouse", ""))
        self.assertEqual([c.args[3][0] for c in request.call_args_list if len(c.args) == 4], [0, 16])

    def test_missing_next_name_fragment_discards_partial_name(self):
        result, request = self.identity([15, 0, 0], {0: b"G50"}, [3, 0, 0], None)
        self.assertEqual(result, ("", "mouse", ""))
        offsets = [c.args[3][0] for c in request.call_args_list if len(c.args) == 4]
        self.assertEqual(offsets, [0, 3])

    def test_short_name_fragments_advance_by_received_length(self):
        result, request = self.identity([8, 0, 0], {0: b"ABC", 3: b"DEF", 6: b"GH\x00"},
                                        [3, 0, 0], [1, 0xC1, 0x5E, 0x09, 0xCD])
        self.assertEqual(result, ("ABCDEFGH", "mouse", "C15E09CD"))
        offsets = [c.args[3][0] for c in request.call_args_list if len(c.args) == 4]
        self.assertEqual(offsets, [0, 3, 6])

    def test_short_length_and_type_replies_are_not_used(self):
        for payload in (None, [], [5], [5, 0]):
            with self.subTest(payload=payload):
                result, _ = self.identity(payload, {}, [3], None)
                self.assertEqual(result, ("", "", ""))

    def test_complete_name_and_unit_id_and_short_final_fragment_are_preserved(self):
        result, _ = self.identity([18, 0, 0], {0: b"ABCDEFGHIJKLMNOP", 16: b"QR\x00"},
                                  [3, 0, 0], [1, 0xC1, 0x5E, 0x09, 0xCD])
        self.assertEqual(result, ("ABCDEFGHIJKLMNOPQR", "mouse", "C15E09CD"))


class CacheTests(LogitechTestCase):
    def raw_replies(self, bus, replacements):
        original = bus._reply

        def reply(path, idx, feat, fn, params):
            payload = replacements.get((feat, fn >> 4))
            if payload is None:
                return original(path, idx, feat, fn, params)
            # No FakeBus padding: the literal bytes are all that arrived.
            bus.queues[path].append([0x11, idx, feat, fn] + list(payload))
            if len(payload) < 3:
                bus._error(path, idx, feat, fn, 0x02)
        bus._reply = reply
        return original

    def test_missing_voltage_flags_never_enter_level_or_discharge_cache(self):
        bus = self.use(receiver(), {b"long": ({1: g502()}, b"short")})
        original = self.raw_replies(bus, {(6, 0): bytes.fromhex("0f 7b")})
        provider = L.LogitechProvider()
        self.assertEqual(provider.poll(), [])
        self.assertEqual(provider._last, {})
        self.assertEqual(provider._discharge, {})
        bus._reply = original
        self.assertEqual([s.level for s in provider.poll()], [76])
        before, before_discharge = dict(provider._last), dict(provider._discharge)
        self.raw_replies(bus, {(6, 0): bytes.fromhex("0f 7b")})
        result = provider.poll()
        self.assertEqual([(s.level, s.online) for s in result], [(76, False)])
        self.assertEqual(provider._last, before)
        self.assertEqual(provider._discharge, before_discharge)

    def test_missing_register_status_never_enters_level_cache(self):
        dev = FakeHidpp(error=0x01,
                        registers={0x0D: [87, 0, 0x50]})
        bus = self.use(receiver(0xC52B), {b"long": ({3: dev}, b"short")})
        original = bus._register_reply

        def truncated(path, idx, reg, params):
            # The literal 0x57 charge byte arrived without its status field.
            bus.queues[b"short"].append([0x10, idx, 0x81, reg, *bytes.fromhex("57")])
            bus._register_error(path, idx, reg, 0x02)

        bus._register_reply = truncated
        provider = L.LogitechProvider()
        self.assertEqual(provider.poll(), [])
        self.assertEqual(provider._last, {})
        bus._register_reply = original
        self.assertEqual([s.level for s in provider.poll()], [87])
        before = dict(provider._last)
        bus._register_reply = truncated
        result = provider.poll()
        self.assertEqual([(s.level, s.online) for s in result], [(87, False)])
        self.assertEqual(provider._last, before)

    def test_partial_identity_is_not_cached_and_next_poll_recovers(self):
        bus = self.use(receiver(), {b"long": ({1: g502()}, b"short")})
        original = self.raw_replies(bus, {
            (3, 1): bytes.fromhex("47 35 30"),  # first fragment: "G50"
            (2, 0): bytes.fromhex("03 c1 5e"),  # only two of the four unit-ID bytes
        })
        first_fragment = bus._reply

        def missing_tail(path, idx, feat, fn, params):
            if feat == 3 and fn >> 4 == 1 and params[0] != 0:
                # Next fragment never arrived: an incomplete reply, then an error.
                bus.queues[path].append([0x11, idx, feat, fn])
                return bus._error(path, idx, feat, fn, 0x02)
            return first_fragment(path, idx, feat, fn, params)
        bus._reply = missing_tail
        provider = L.LogitechProvider()
        self.assertEqual([s.name for s in provider.poll()], ["Logitech device"])
        self.assertEqual(provider._ids, {})
        bus._reply = original
        result = provider.poll()
        self.assertEqual([(s.name, s.key) for s in result],
                         [("G502 LIGHTSPEED", "logitech:C15E09CD")])


if __name__ == "__main__":
    unittest.main()
