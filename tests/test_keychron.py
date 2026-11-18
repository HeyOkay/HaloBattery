"""Tests for providers/keychron.py. No hardware is needed.

The Ultra-Link 8K (3434:d028) and M5 (3434:d048) put their vendor collection on
interface 4; the Nape Pro's "Keychron Link-KM" receiver (3434:d026, #270) puts it on
interface 1 as `ffc1:0001`, with the VIA channel on interface 3. The battery exchange is
the same documented one: a 64-byte feature report `b3 06`, answered `b4 06` with the
level at byte 20.

Run from the repository root:

    python -m unittest discover -s tests
"""

# Independent wire fixtures: csutcliff/keychron-battery-dkms keychron_battery.c lines185-228,267-289; #270/#373.
# Keep reply bytes/offsets independent of the provider constants under test.
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import keychron as K  # noqa: E402


def reply(level, charging=False):
    level |= 0x80 if charging else 0
    return [0xB4, 0x06] + [0x00] * 18 + [level] + [0x00] * (64 - 21)


class FakeDevice:
    def __init__(self, replies, feature_result=64, output_result=64):
        self.replies = list(replies)
        self.opened = []
        self.sent = []
        self.written = []
        self.feature_result, self.output_result = feature_result, output_result
        self.closed = False

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        if isinstance(self.feature_result, Exception):
            raise self.feature_result
        return self.feature_result

    def write(self, data):
        self.written.append(bytes(data))
        if isinstance(self.output_result, Exception):
            raise self.output_result
        return self.output_result

    def read(self, size, timeout):
        value = self.replies.pop(0) if self.replies else []
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.closed = True


class PollTest(unittest.TestCase):
    NAPE = [
        {"path": b"nape-mouse", "interface_number": 0,
         "usage_page": 0x0001, "usage": 0x0002, "product_id": 0xD026},
        {"path": b"nape-vendor", "interface_number": 1,
         "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD026},
        {"path": b"nape-via", "interface_number": 3,
         "usage_page": 0xFF60, "usage": 0x0061, "product_id": 0xD026},
    ]

    def setUp(self):
        self._saved = (K.hid, K.hidlist, K.time)

    def tearDown(self):
        K.hid, K.hidlist, K.time = self._saved

    def poll(self, replies, infos, **device_args):
        dev = FakeDevice(replies, **device_args)
        K.hid = types.SimpleNamespace(device=lambda: dev)
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        K.time = types.SimpleNamespace(sleep=lambda s: None)
        return K.KeychronProvider().poll(), dev

    def test_the_nape_pro_uses_its_vendor_collection_on_interface_1(self):
        out, dev = self.poll([reply(75)], self.NAPE)
        self.assertEqual([(s.key, s.name, s.level) for s in out],
                         [("keychron:d026:e764dbe1fc0fc5b913585b9e11b96e051ed5c52eac2efd33914b1cea723fce85", "Keychron Nape Pro", 75)])
        self.assertEqual([b"nape-vendor"], dev.opened)
        self.assertEqual([bytes(K.make_request())], dev.sent)

    def test_interface_4_still_wins_when_it_exists(self):
        infos = [
            {"path": b"iface1", "interface_number": 1,
             "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD028},
            {"path": b"iface4", "interface_number": 4,
             "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD028},
        ]
        out, dev = self.poll([reply(42)], infos)
        self.assertEqual(42, out[0].level)
        self.assertEqual([b"iface4"], dev.opened)

    def test_a_bad_reply_is_not_a_level(self):
        self.assertIsNone(K.parse_level([0xB3, 0x06] + [0] * 62))
        self.assertIsNone(K.parse_level([0xB4, 0x06] + [0] * 10))     # too short
        self.assertIsNone(K.parse_level(reply(101)))
        self.assertIsNone(K.parse_level([]))

    def test_no_reply_reports_nothing(self):
        out, _dev = self.poll([], self.NAPE)
        self.assertEqual([], out)


    def test_charging_bit_and_boundary_levels_reach_status(self):
        for level in (0, 99, 100):
            for charging in (False, True):
                with self.subTest(level=level, charging=charging):
                    out, dev = self.poll([reply(level, charging)], self.NAPE)
                    self.assertEqual((out[0].level, out[0].charging), (level, charging))
                    self.assertEqual(K.parse_level(reply(level, charging)), (level, charging))
                    self.assertTrue(dev.closed)

    def test_raw_capture_is_parsed_independently_of_provider_constants(self):
        # #373 M6 8K capture: byte 20 is 63 on battery / e3 on charger.
        raw = bytes.fromhex("b4 06 00 44 44 04 90 01 20 03 40 06 80 0c 88 13 15 05 04 0a 63 00")
        self.assertEqual(K.parse_level(raw), (99, False))
        charged = raw[:20] + b"\xe3" + raw[21:]
        self.assertEqual(K.parse_level(charged), (99, True))

    def test_malformed_levels_cannot_become_charging_or_percent(self):
        for raw in (101, 127, 229, 255, -1, 256, None, True, "100"):
            with self.subTest(raw=raw):
                candidate = [0xb4, 6] + [0] * 18 + [raw]
                self.assertIsNone(K.parse_level(candidate))
        for malformed in (None, "b4 06", 42, {}, [0xb4], [0xb4, 6]):
            with self.subTest(malformed=malformed):
                self.assertIsNone(K.parse_level(malformed))

    def test_refused_feature_uses_exact_captured_output_frame(self):
        for refusal in (-1, 0, 63, None, OSError("feature unsupported")):
            with self.subTest(refusal=refusal):
                out, dev = self.poll([reply(99, True)], self.NAPE, feature_result=refusal)
                self.assertEqual((out[0].level, out[0].charging), (99, True))
                self.assertEqual(dev.sent, [bytes([0xb3, 6] + [0] * 62)])
                self.assertEqual(dev.written, dev.sent)

    def test_silent_or_unreadable_feature_retries_output(self):
        for response in ([], OSError("feature read error"), [0xb3, 6] + [0] * 62):
            with self.subTest(response=response):
                out, dev = self.poll([response, reply(61)], self.NAPE)
                self.assertEqual(out[0].level, 61)
                self.assertEqual(dev.written, [bytes([0xb3, 6] + [0] * 62)])

    def test_refused_or_disconnected_output_is_bounded_and_closed(self):
        for output in (-1, 0, 63, None, OSError("output removed")):
            with self.subTest(output=output):
                out, dev = self.poll([], self.NAPE, feature_result=-1, output_result=output)
                self.assertEqual(out, [])
                self.assertEqual(len(dev.sent), 3)
                self.assertEqual(len(dev.written), 3)
                self.assertTrue(dev.closed)
        out, dev = self.poll([OSError("read removed")] * 3, self.NAPE, feature_result=-1)
        self.assertEqual(out, [])
        self.assertTrue(dev.closed)

    def test_plain_keyboard_via_and_unrelated_vendor_collections_are_never_opened(self):
        wrong = [
            dict(path=b"keyboard4", product_id=0xd026, interface_number=4, usage_page=1, usage=6),
            dict(path=b"via", product_id=0xd026, interface_number=3, usage_page=0xff60, usage=0x61),
            dict(path=b"other-vendor", product_id=0xd028, interface_number=4, usage_page=0xff00, usage=1),
            dict(path=b"wrong-usage", product_id=0xd028, interface_number=4, usage_page=0xffc1, usage=2),
            dict(path=b"wrong-interface", product_id=0xd026, interface_number=3, usage_page=0xffc1, usage=1),
            dict(path=b"wrong-pid", product_id=0xd099, interface_number=1, usage_page=0xffc1, usage=1),
            dict(path=b"wrong-vendor", vendor_id=0xffff, product_id=0xd026, interface_number=1, usage_page=0xffc1, usage=1)]
        out, dev = self.poll([reply(100)], wrong)
        self.assertEqual(out, [])
        self.assertEqual(dev.opened, [])
        self.assertEqual(dev.sent + dev.written, [])

    def test_captured_battery_collection_wins_over_keyboard_and_via_order(self):
        keyboard4 = dict(path=b"keyboard4", product_id=0xd026, interface_number=4, usage_page=1, usage=6)
        out, dev = self.poll([reply(100)], [keyboard4, self.NAPE[2], self.NAPE[1]])
        self.assertEqual(out[0].level, 100)
        self.assertEqual(dev.opened, [b"nape-vendor"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
