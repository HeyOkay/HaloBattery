"""Tests for providers/keychron.py. No hardware is needed.

The Ultra-Link 8K (3434:d028) and M5 (3434:d048) put their vendor collection on
interface 4; the Nape Pro's "Keychron Link-KM" receiver (3434:d026, #270) puts it on
interface 1 as `ffc1:0001`, with the VIA channel on interface 3. The battery exchange is
the same documented one: a 64-byte feature report `b3 06`, answered `b4 06` with the
level at byte 20.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import keychron as K  # noqa: E402


def reply(level, charging=False):
    raw = level | (0x80 if charging else 0)
    return [K.REPORT_ID_RESP, K.CMD_STATUS] + [0x00] * 18 + [raw] + [0x00] * (K.REPORT_SIZE - 21)


class FakeDevice:
    def __init__(self, replies, feature_result=None, feature_error=None):
        self.replies = list(replies)
        self.opened = []
        self.sent = []
        self.written = []
        self.feature_result = feature_result
        self.feature_error = feature_error

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        if self.feature_error is not None:
            raise self.feature_error
        return len(data) if self.feature_result is None else self.feature_result

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size, timeout):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


class PollTest(unittest.TestCase):
    NAPE = [
        {"path": b"nape-mouse", "interface_number": 0,
         "usage_page": 0x0001, "usage": 0x0002, "product_id": 0xD026},
        {"path": b"nape-keyboard4", "interface_number": 4,
         "usage_page": 0x0001, "usage": 0x0006, "product_id": 0xD026},
        {"path": b"nape-vendor", "interface_number": 1,
         "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD026},
        {"path": b"nape-via", "interface_number": 3,
         "usage_page": 0xFF60, "usage": 0x0061, "product_id": 0xD026},
    ]

    def setUp(self):
        self._saved = (K.hid, K.hidlist, K.time)

    def tearDown(self):
        K.hid, K.hidlist, K.time = self._saved

    # every collection of the Link-KM receiver in the #270 diagnostics except its two
    # vendor pages: mouse, joystick, consumer, system control and keyboard collections
    LINKKM_OS = [
        {"path": b"km-if0", "interface_number": 0,
         "usage_page": 0x0001, "usage": 0x0002, "product_id": 0xD026},
        {"path": b"km-if1-a", "interface_number": 1,
         "usage_page": 0x0001, "usage": 0x0004, "product_id": 0xD026},
        {"path": b"km-if1-b", "interface_number": 1,
         "usage_page": 0x008C, "usage": 0x0001, "product_id": 0xD026},
        {"path": b"km-if2-a", "interface_number": 2,
         "usage_page": 0x000C, "usage": 0x0001, "product_id": 0xD026},
        {"path": b"km-if2-b", "interface_number": 2,
         "usage_page": 0x0001, "usage": 0x0080, "product_id": 0xD026},
        {"path": b"km-if2-c", "interface_number": 2,
         "usage_page": 0x0001, "usage": 0x0006, "product_id": 0xD026},
        {"path": b"km-if4-a", "interface_number": 4,
         "usage_page": 0x0001, "usage": 0x0006, "product_id": 0xD026},
        {"path": b"km-if4-b", "interface_number": 4,
         "usage_page": 0x0001, "usage": 0x0006, "product_id": 0xD026},
    ]

    def poll(self, replies, infos, feature_result=None, feature_error=None):
        dev = FakeDevice(replies, feature_result=feature_result, feature_error=feature_error)
        K.hid = types.SimpleNamespace(device=lambda: dev)
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        K.time = types.SimpleNamespace(sleep=lambda s: None)
        return K.KeychronProvider().poll(), dev

    def test_the_nape_pro_uses_its_vendor_collection_on_interface_1(self):
        out, dev = self.poll([reply(75)], self.NAPE)
        self.assertEqual([(s.key, s.name, s.level) for s in out],
                         [("keychron:d026", "Keychron Nape Pro", 75)])
        self.assertEqual([b"nape-vendor"], dev.opened)
        self.assertEqual([bytes(K.make_request())], dev.sent)

    def test_the_charging_bit_reaches_the_icon(self):
        out, _ = self.poll([reply(99, True)], self.NAPE)
        self.assertEqual((out[0].level, out[0].charging), (99, True))

    def test_ffc1_wins_over_the_via_page_when_enumerated_last(self):
        # #270: the Link-KM receiver lists ff60 (VIA) and ffc1 (battery); the
        # VIA page accepts writes but never answers, so ffc1 must be chosen
        infos = [self.NAPE[3], self.NAPE[2]]
        out, dev = self.poll([reply(100)], infos)
        self.assertEqual(out[0].level, 100)
        self.assertEqual([b"nape-vendor"], dev.opened)

    def test_a_plain_interface_4_keyboard_page_does_not_win(self):
        # #270: the Link-KM also exposes 0001:0006 on interface 4; the old
        # number-only rule sent every b3 06 write to that silent keyboard page.
        infos = [self.NAPE[1], self.NAPE[2], self.NAPE[3]]
        out, dev = self.poll([reply(100)], infos)
        self.assertEqual(out[0].level, 100)
        self.assertEqual([b"nape-vendor"], dev.opened)

    def test_interface_4_still_wins_when_it_exists(self):
        infos = [
            {"path": b"iface1", "interface_number": 1,
             "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD028},
            {"path": b"iface4", "interface_number": 4,
             "usage_page": 0xFF00, "usage": 0x0001, "product_id": 0xD028},
        ]
        out, dev = self.poll([reply(42)], infos)
        self.assertEqual(42, out[0].level)
        self.assertEqual([b"iface4"], dev.opened)

    def test_a_bad_reply_is_not_a_level(self):
        self.assertIsNone(K.parse_level([0xB3, 0x06] + [0] * 62))
        self.assertIsNone(K.parse_level([0xB4, 0x06] + [0] * 10))     # too short
        self.assertIsNone(K.parse_level(reply(101)))
        self.assertIsNone(K.parse_level([]))

    def test_the_charging_bit_is_masked_and_reported(self):
        self.assertEqual(K.parse_level(reply(99, True)), (99, True))
        self.assertEqual(K.parse_level(reply(99, False)), (99, False))

    def test_a_refused_feature_report_falls_back_to_the_output_endpoint(self):
        out, dev = self.poll([reply(100)], self.NAPE, feature_result=-1)
        self.assertEqual(out[0].level, 100)
        self.assertEqual(dev.written, [bytes(K.make_request())])

    def test_a_keyboard_only_receiver_is_never_opened(self):
        # #421 review: interface 4 0001:0006 was chosen, and a refused feature report
        # then wrote b3 06 to that keyboard collection. Nothing may be opened now.
        for infos in (self.LINKKM_OS, [self.NAPE[1]]):
            out, dev = self.poll([reply(100)], infos, feature_error=OSError("refused"))
            self.assertEqual([], out)
            self.assertEqual(([], [], []), (dev.opened, dev.sent, dev.written))
        p = K.KeychronProvider()
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.LINKKM_OS))
        p.poll()
        self.assertTrue(any("no battery collection" in line for line in p.diagnostics()))

    def test_an_unrelated_vendor_only_receiver_is_never_opened(self):
        # the VIA channel - on interface 3, or on interface 4 - and another vendor
        # page are not battery endpoints either, whatever the receiver id
        infos = [
            {"path": b"via-if3", "interface_number": 3,
             "usage_page": 0xFF60, "usage": 0x0061, "product_id": 0xD026},
            {"path": b"via-if4", "interface_number": 4,
             "usage_page": 0xFF60, "usage": 0x0061, "product_id": 0xD028},
            {"path": b"vendor-if2", "interface_number": 2,
             "usage_page": 0xFF00, "usage": 0x0001, "product_id": 0xD048},
        ]
        out, dev = self.poll([reply(100)], infos, feature_result=-1)
        self.assertEqual([], out)
        self.assertEqual(([], [], []), (dev.opened, dev.sent, dev.written))

    def test_the_output_retry_is_only_made_on_ffc1(self):
        # the M5's interface-4 vendor page is queried the way the Linux module does it,
        # with a feature report; refused or unanswered, it is not retried with write()
        m5 = [{"path": b"m5-vendor", "interface_number": 4,
               "usage_page": 0xFF00, "usage": 0x0001, "product_id": 0xD048}]
        for result in (-1, None):           # refused; accepted but never answered
            out, dev = self.poll([], m5, feature_result=result)
            self.assertEqual([], out)
            self.assertEqual([b"m5-vendor"], dev.opened)
            self.assertEqual([], dev.written, result)

    def test_the_ultra_link_8k_battery_collection_keeps_the_output_retry(self):
        # #373 diagnostics: the 8K's collection is ffc1:0001 on interface 4, and its
        # SET_FEATURE is refused while the same frame as an output report is answered
        infos = [
            {"path": b"8k-keyboard", "interface_number": 0,
             "usage_page": 0x0001, "usage": 0x0006, "product_id": 0xD028},
            {"path": b"8k-battery", "interface_number": 4,
             "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD028},
        ]
        out, dev = self.poll([reply(99)], infos, feature_result=-1)
        self.assertEqual([("keychron:d028", 99)], [(s.key, s.level) for s in out])
        self.assertEqual([b"8k-battery"], dev.opened)
        self.assertEqual([bytes(K.make_request())], dev.written)

    def test_no_reply_reports_nothing(self):
        out, _dev = self.poll([], self.NAPE)
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
