"""Endgame Gear 4K dongle (3367:1970): the family's 17-byte status frame. No hardware."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import endgame_4k as E  # noqa: E402

FEATURE = b"\\\\?\\hid#vid_3367&pid_1970#8&1a2b3c4d&0&0000#{...}"      # ff02:0002
INPUT = b"\\\\?\\hid#vid_3367&pid_1970#8&1a2b3c4d&0&0000&col02#{...}"  # ff01:0002


def battery_reply(level, charging):
    body = [0x09, 0x04, 0x00, 0x00, 0x00, 2, level, charging] + [0x00] * 8
    return body + [(0x55 - sum(body)) & 0xFF]


class FakeDongle:
    def __init__(self, replies=None):
        self.replies = [list(r) for r in (replies or [])]
        self.writes = []

    def send_feature_report(self, data):
        self.writes.append(list(data))
        return len(data)

    def read(self, size, timeout_ms):
        return self.replies.pop(0) if self.replies else []


class FakeBus:
    def __init__(self, feature, input_, dongle):
        self.feature, self.input, self.dongle = feature, input_, dongle
        self.opened = []

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in (bus.feature, bus.input):
                    raise OSError("cannot open")
                self.path = path
                bus.opened.append(path)

            def send_feature_report(self, data):
                if self.path != bus.feature:
                    raise AssertionError("a write went to the input collection")
                return bus.dongle.send_feature_report(data)

            def read(self, size, timeout_ms):
                if self.path != bus.input:
                    raise AssertionError("a read went to the feature collection")
                return bus.dongle.read(size, timeout_ms)

            def close(self):
                pass

        return FakeDevice


def entry(pid, path, iface, page, usage):
    return {"product_id": pid, "interface_number": iface, "usage_page": page,
            "usage": usage, "path": path, "product_string": "Endgame Gear HS Dongle"}


class Endgame4kTest(unittest.TestCase):
    def setUp(self):
        self._saved = (E.hid, E.hidlist)

    def tearDown(self):
        E.hid, E.hidlist = self._saved

    def poll(self, dongles, entries=None):
        if entries is None:
            entries = [entry(0x1970, b"if0", 0, 0x0001, 0x0002),
                       entry(0x1970, INPUT, 1, 0xFF01, 0x0002),
                       entry(0x1970, b"ff02-1", 1, 0xFF02, 0x0001),
                       entry(0x1970, FEATURE, 1, 0xFF02, 0x0002),
                       entry(0x1970, b"consumer", 1, 0x000C, 0x0001)]
        P = types.SimpleNamespace(device=FakeBus(FEATURE, INPUT, dongles).device_class())
        E.hid = P
        E.hidlist = types.SimpleNamespace(enumerate=lambda vid: list(entries))
        return E.Endgame4kProvider()

    def test_the_level_and_the_charging_flag(self):
        provider = self.poll(FakeDongle([battery_reply(75, 0)]))
        [st] = provider.poll()
        self.assertEqual((st.name, st.level, st.charging, st.kind),
                         ("Endgame Gear 4K mouse", 75, False, "mouse"))

    def test_charging(self):
        [st] = self.poll(FakeDongle([battery_reply(40, 1)])).poll()
        self.assertEqual((st.level, st.charging), (40, True))

    def test_the_request_is_the_documented_status_frame(self):
        dongle = FakeDongle([battery_reply(50, 0)])
        self.poll(dongle).poll()
        self.assertEqual(dongle.writes[0], E.query_frame(0x04))
        self.assertEqual(dongle.writes[0][0], 0x08)
        self.assertEqual(sum(dongle.writes[0]) & 0xFF, 0x55)

    def test_a_bad_checksum_is_skipped(self):
        bad = battery_reply(10, 0)
        bad[-1] ^= 0xFF
        [st] = self.poll(FakeDongle([bad, battery_reply(66, 0)])).poll()
        self.assertEqual(st.level, 66)

    def test_no_reply_shows_nothing(self):
        provider = self.poll(FakeDongle([]))
        self.assertEqual(provider.poll(), [])
        self.assertTrue(any("no reply" in line for line in provider.diagnostics()))

    def test_parse_bounds(self):
        self.assertEqual(E.parse_battery([75, 0]), (75, False))
        self.assertEqual(E.parse_battery([0]), (0, False))
        self.assertIsNone(E.parse_battery([101, 0]))
        self.assertIsNone(E.parse_battery([]))

    def test_only_documented_queries_can_be_built(self):
        self.assertEqual(E.query_frame(0x03)[1], 0x03)
        for opcode in (0x07, 0x08, 0x12, 0x00):
            with self.assertRaises(ValueError):
                E.query_frame(opcode)

    def test_two_dongles_pair_their_own_collections(self):
        other_in = INPUT.replace(b"&1a2b3c4d", b"&9f9f9f9f")
        other_feat = FEATURE.replace(b"&1a2b3c4d", b"&9f9f9f9f")
        entries = [entry(0x1970, INPUT, 1, 0xFF01, 0x0002),
                   entry(0x1970, FEATURE, 1, 0xFF02, 0x0002),
                   entry(0x1970, other_in, 1, 0xFF01, 0x0002),
                   entry(0x1970, other_feat, 1, 0xFF02, 0x0002)]
        [st] = self.poll(FakeDongle([battery_reply(80, 0)]), entries).poll()
        self.assertEqual(st.level, 80)

    def test_receiver_path_ignores_the_collection_part(self):
        a = b"\\\\?\\hid#vid_3367&pid_1970#8&1a2b3c4d&0&0000#{x}"
        b = b"\\\\?\\hid#vid_3367&pid_1970#8&1a2b3c4d&0&0000&col02#{x}"
        self.assertEqual(E.receiver_path(a), E.receiver_path(b))


if __name__ == "__main__":
    unittest.main()
