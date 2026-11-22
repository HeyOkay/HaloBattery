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
    def __init__(self, replies, feature_result=None, fail_read=(), feature_error=None):
        self.replies = list(replies)
        self.opened = []
        self.sent = []
        self.written = []
        self.feature_result = feature_result
        self.feature_error = feature_error
        self.fail_read = set(fail_read)     # paths whose read() raises, as if unplugged
        self.current = None

    def open_path(self, path):
        self.opened.append(path)
        self.current = path

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        if self.feature_error is not None:
            raise self.feature_error
        return len(data) if self.feature_result is None else self.feature_result

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def read(self, size, timeout):
        if self.current in self.fail_read:
            raise OSError("device went away")
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


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

    def poll(self, replies, infos, feature_result=None, fail_read=(), provider=None,
             feature_error=None):
        dev = FakeDevice(replies, feature_result=feature_result, fail_read=fail_read,
                         feature_error=feature_error)
        K.hid = types.SimpleNamespace(device=lambda: dev)
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        K.time = types.SimpleNamespace(sleep=lambda s: None)
        return (provider or K.KeychronProvider()).poll(), dev

    def test_the_nape_pro_uses_its_vendor_collection_on_interface_1(self):
        out, dev = self.poll([reply(75)], self.NAPE)
        self.assertEqual([(s.key, s.name, s.level) for s in out],
                         [("keychron:d026:" + K._identity(self.NAPE[1]), "Keychron Nape Pro", 75)])
        self.assertEqual([b"nape-vendor"], dev.opened)
        self.assertEqual([bytes(K.make_request())], dev.sent)

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

    def test_a_rejected_feature_report_falls_back_to_output(self):
        out, dev = self.poll([reply(99)], self.NAPE, feature_result=-1)
        self.assertEqual(out[0].level, 99)
        self.assertEqual(dev.written, [bytes(K.make_request())])

    def test_an_accepted_silent_feature_request_keeps_the_link_km_output_retry(self):
        out, dev = self.poll([[], reply(66, True)], self.NAPE)
        self.assertEqual([(66, True)], [(s.level, s.charging) for s in out])
        self.assertEqual([b"nape-vendor"], dev.opened)
        self.assertEqual([bytes(K.make_request())], dev.sent)
        self.assertEqual([bytes(K.make_request())], dev.written)

    def test_ffc1_wins_over_via_when_the_battery_page_is_last(self):
        out, dev = self.poll([reply(100)], [self.NAPE[2], self.NAPE[1]])
        self.assertEqual([100], [s.level for s in out])
        self.assertEqual([b"nape-vendor"], dev.opened)

    def test_a_silent_feature_fallback_keeps_both_receivers_independent(self):
        a = {"path": b"receiver-a", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028}
        b = dict(a, path=b"receiver-b")
        out, dev = self.poll([[], reply(80, True), reply(70)], [a, b])
        self.assertEqual([(80, True), (70, False)], [(s.level, s.charging) for s in out])
        self.assertEqual(["keychron:d028:" + K._identity(d) for d in (a, b)],
                         [s.key for s in out])
        self.assertEqual([b"receiver-a", b"receiver-b"], dev.opened)
        self.assertEqual([bytes(K.make_request())] * 2, dev.sent)
        self.assertEqual([bytes(K.make_request())], dev.written)

    def test_a_keyboard_only_receiver_is_never_opened(self):
        # #421 review: interface 4 0001:0006 was the last fallback, and a refused
        # feature report then wrote b3 06 to that keyboard collection
        for infos in (self.LINKKM_OS, [self.LINKKM_OS[-1]]):
            provider = K.KeychronProvider()
            out, dev = self.poll([reply(100)], infos, feature_error=OSError("refused"),
                                 provider=provider)
            self.assertEqual([], out)
            self.assertEqual(([], [], []), (dev.opened, dev.sent, dev.written))
            self.assertTrue(any("no battery collection" in line
                                for line in provider.diagnostics()))
            self.assertEqual({}, provider.legacy_key_aliases())

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
        # Outside the captured FFC1 output endpoint, keep the existing feature-only
        # path: neither a refusal nor silence licenses a new output write.
        m5 = [{"path": b"m5-vendor", "interface_number": 4,
               "usage_page": 0xFF00, "usage": 0x0001, "product_id": 0xD048}]
        for kwargs in ({"feature_result": -1}, {"feature_error": OSError("refused")}, {}):
            out, dev = self.poll([], m5, **kwargs)
            self.assertEqual([], out)
            self.assertEqual([b"m5-vendor"], dev.opened)
            self.assertEqual([], dev.written, kwargs)

    def test_the_ultra_link_8k_battery_collection_keeps_the_output_retry(self):
        # #373 diagnostics: the 8K's collection is ffc1:0001 on interface 4, and its
        # SET_FEATURE is refused while the same frame as an output report is answered
        battery = {"path": b"8k-battery", "interface_number": 4,
                   "usage_page": 0xFFC1, "usage": 0x0001, "product_id": 0xD028}
        infos = [{"path": b"8k-keyboard", "interface_number": 0,
                  "usage_page": 0x0001, "usage": 0x0006, "product_id": 0xD028}, battery]
        out, dev = self.poll([reply(99)], infos, feature_result=-1)
        self.assertEqual([("keychron:d028:" + K._identity(battery), 99)],
                         [(s.key, s.level) for s in out])
        self.assertEqual([b"8k-battery"], dev.opened)
        self.assertEqual([bytes(K.make_request())], dev.written)

    def test_both_receivers_are_queried_and_get_separate_icons(self):
        infos = [
            {"path": b"r1", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028},
            {"path": b"r2", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028},
        ]
        out, dev = self.poll([reply(80), reply(70)], infos)
        self.assertEqual([(s.level, s.charging) for s in out], [(80, False), (70, False)])
        self.assertEqual(2, len({s.key for s in out}))
        self.assertEqual([b"r1", b"r2"], dev.opened)

    def test_the_link_km_keyboard_page_on_interface_4_is_never_queried(self):
        # the real Link-KM layout from #270: the plain keyboard collection also
        # sits on interface 4, so interface 4 alone is not the battery page
        infos = self.NAPE + [
            {"path": b"nape-keyboard4", "interface_number": 4,
             "usage_page": 0x0001, "usage": 0x0006, "product_id": 0xD026},
        ]
        out, dev = self.poll([reply(66)], infos)
        self.assertEqual([b"nape-vendor"], dev.opened)
        self.assertEqual(66, out[0].level)

    def test_a_receivers_key_does_not_depend_on_who_answers(self):
        infos = [
            {"path": b"r1", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028},
            {"path": b"r2", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028},
        ]
        both, _ = self.poll([reply(80), reply(70)], infos)
        first_only, _ = self.poll([reply(79)], infos)          # r2 asleep, still plugged in
        both_again, _ = self.poll([reply(78), reply(69)], infos)
        keys = [s.key for s in both]
        self.assertEqual(2, len(set(keys)))
        self.assertEqual([keys[0]], [s.key for s in first_only])
        self.assertEqual(keys, [s.key for s in both_again])
        self.assertEqual(79, first_only[0].level)

    def test_unplugging_one_receiver_does_not_rename_the_other(self):
        # #373 review: two connected -> one physically connected -> two connected.
        a = {"path": b"receiver-a", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028}
        b = {"path": b"receiver-b", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028}
        provider = K.KeychronProvider()
        both, _ = self.poll([reply(80), reply(70)], [a, b], provider=provider)
        alone, _ = self.poll([reply(79)], [a], provider=provider)         # B unplugged
        again, _ = self.poll([reply(78), reply(69)], [a, b], provider=provider)
        key_a, key_b = (s.key for s in both)
        self.assertEqual([key_a], [s.key for s in alone])
        self.assertEqual([key_a, key_b], [s.key for s in again])
        self.assertNotIn("keychron:d028", (key_a, key_b))
        # B alone keeps B's key as well, not the one A had while it was alone
        only_b, _ = self.poll([reply(68)], [b], provider=provider)
        self.assertEqual([key_b], [s.key for s in only_b])

    def test_the_old_shared_key_moves_only_to_an_unambiguous_receiver(self):
        a = {"path": b"receiver-a", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028}
        b = {"path": b"receiver-b", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028}
        provider = K.KeychronProvider()
        out, _ = self.poll([reply(80)], [a], provider=provider)
        self.assertEqual({"keychron:d028": out[0].key}, provider.legacy_key_aliases())
        self.poll([reply(80), reply(70)], [a, b], provider=provider)     # two: unknowable
        self.assertEqual({}, provider.legacy_key_aliases())
        self.poll([reply(80)], [a, b], provider=provider)                # B silent, still plugged in
        self.assertEqual({}, provider.legacy_key_aliases())
        self.poll([], [a], provider=provider)                            # A silent
        self.assertEqual({}, provider.legacy_key_aliases())

    def test_the_alias_passes_the_polling_runtime_checks(self):
        provider = K.KeychronProvider()
        out, _ = self.poll([reply(75)], self.NAPE, provider=provider)
        from halo.polling import PollingRuntime
        aliases, error = PollingRuntime._alias_metadata(provider)
        self.assertIsNone(error)
        self.assertEqual({"keychron:d026": out[0].key}, aliases)

    def test_one_receiver_going_away_does_not_hide_the_other(self):
        infos = [
            {"path": b"r1", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028},
            {"path": b"r2", "interface_number": 4, "usage_page": 0xFFC1,
             "usage": 0x0001, "product_id": 0xD028},
        ]
        out, dev = self.poll([reply(64)], infos, fail_read={b"r1"})
        self.assertEqual([64], [s.level for s in out])
        self.assertEqual([], dev.written)

    def test_no_reply_reports_nothing(self):
        out, _dev = self.poll([], self.NAPE)
        self.assertEqual([], out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
