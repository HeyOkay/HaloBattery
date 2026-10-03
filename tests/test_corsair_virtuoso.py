"""Tests for the Corsair Virtuoso SE / XT provider. No hardware is needed.

The frames are the ones in Sapd/HeadsetControl, lib/devices/corsair_virtuoso_xt.hpp
at commit 8292ac41: write 02 00 to the collection ff42:0001; the reply is 64 bytes,
[0] 0x01, [1] status (0xF0 normal, 0x00 headset offline), [2] battery 0..100; unasked
volume reports start with 0x0E. Product id 0A40 (#204) is not in that file, so no
test here can say that it answers.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import corsair_virtuoso as V  # noqa: E402


def entry(pid, path, page=0xFF42, usage=0x0001, iface=3):
    return {"product_id": pid, "interface_number": iface, "usage_page": page, "usage": usage,
            "path": path, "product_string": "Slipstream Multi-Device Receiver",
            "serial_number": ""}


def reply(level, status=0xF0, report=0x01):
    return [report, status, level] + [0] * 61


def volume(kind=0x01):
    return [0x0E, kind] + [0] * 62


class FakeHeadset:
    def __init__(self, reads, write_result=2):
        self.reads = list(reads)
        self.write_result = write_result
        self.writes = []


class FakeBus:
    def __init__(self, headsets):
        self.headsets = headsets          # {path: FakeHeadset}
        self.opened = []

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.headsets:
                    raise OSError("cannot open")
                self.h = bus.headsets[path]
                bus.opened.append(path)

            def write(self, data):
                self.h.writes.append(bytes(data))
                return self.h.write_result

            def read(self, size, timeout_ms):
                return self.h.reads.pop(0) if self.h.reads else []

            def close(self):
                pass

        return FakeDevice


class ParseTest(unittest.TestCase):
    def test_the_level_is_byte_2(self):
        self.assertEqual(V.parse_reply(reply(73)), 73)

    def test_the_edges_are_levels(self):
        self.assertEqual(V.parse_reply(reply(0)), 0)
        self.assertEqual(V.parse_reply(reply(100)), 100)

    def test_a_level_past_100_is_refused(self):
        self.assertIsNone(V.parse_reply(reply(101)))
        self.assertIsNone(V.parse_reply(reply(255)))

    def test_offline_status_is_refused(self):
        self.assertIsNone(V.parse_reply(reply(80, status=0x00)))

    def test_a_volume_report_is_not_a_battery_reply(self):
        self.assertIsNone(V.parse_reply(volume()))

    def test_short_and_empty_replies_are_refused(self):
        self.assertIsNone(V.parse_reply([0x01, 0xF0]))
        self.assertIsNone(V.parse_reply([]))
        self.assertIsNone(V.parse_reply(None))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (V.hid, V.hidlist)

    def tearDown(self):
        V.hid, V.hidlist = self._saved

    def poll(self, entries, headsets):
        bus = FakeBus(headsets)
        V.hid = types.SimpleNamespace(device=bus.device_class())
        V.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        p = V.CorsairVirtuosoProvider()
        return p.poll(), bus, p

    def test_a_normal_reply_is_a_reading(self):
        out, bus, _ = self.poll([entry(0x0A3E, b"se")], {b"se": FakeHeadset([reply(64)])})
        self.assertEqual(len(out), 1)
        st = out[0]
        self.assertEqual((st.key, st.name, st.level, st.kind, st.source),
                         ("corsair_virtuoso:se", "Corsair Virtuoso SE", 64, "headset",
                          "corsair_virtuoso"))
        self.assertFalse(st.charging)
        self.assertTrue(st.online)

    def test_the_request_is_02_00(self):
        _, bus, _ = self.poll([entry(0x0A3E, b"se")], {b"se": FakeHeadset([reply(50)])})
        self.assertEqual(bus.headsets[b"se"].writes, [bytes([0x02, 0x00])])

    def test_offline_gives_no_icon(self):
        out, _, p = self.poll([entry(0x0A3E, b"se")],
                              {b"se": FakeHeadset([reply(80, status=0x00)])})
        self.assertEqual(out, [])
        self.assertTrue(any("not connected" in line for line in p.diagnostics()))

    def test_volume_reports_before_the_reply_are_skipped(self):
        reads = [volume(0x01), volume(0x02), volume(0x00), reply(41)]
        out, _, _ = self.poll([entry(0x0A64, b"xt")], {b"xt": FakeHeadset(reads)})
        self.assertEqual((out[0].key, out[0].level), ("corsair_virtuoso:xt", 41))

    def test_only_volume_reports_give_no_reading(self):
        reads = [volume()] * 20
        out, bus, _ = self.poll([entry(0x0A64, b"xt")], {b"xt": FakeHeadset(reads)})
        self.assertEqual(out, [])
        self.assertEqual(len(bus.headsets[b"xt"].reads), 20 - V.MAX_READS)

    def test_a_bad_level_gives_no_reading(self):
        out, _, p = self.poll([entry(0x0A3E, b"se")], {b"se": FakeHeadset([reply(200)])})
        self.assertEqual(out, [])
        self.assertTrue(any("out of range" in line for line in p.diagnostics()))

    def test_a_refused_write_gives_no_reading_and_no_read(self):
        h = FakeHeadset([reply(50)], write_result=-1)
        out, _, p = self.poll([entry(0x0A3E, b"se")], {b"se": h})
        self.assertEqual(out, [])
        self.assertEqual(len(h.reads), 1)           # the reply was never read
        self.assertTrue(any("write refused" in line for line in p.diagnostics()))

    def test_no_reply_gives_no_reading(self):
        out, _, _ = self.poll([entry(0x0A3E, b"se")], {b"se": FakeHeadset([])})
        self.assertEqual(out, [])

    def test_a_wrong_collection_is_not_opened(self):
        e = [entry(0x0A3E, b"audio", page=0x000C, usage=0x0001, iface=0),
             entry(0x0A3E, b"usage2", page=0xFF42, usage=0x0002, iface=3),
             entry(0x0A3E, b"mine", page=0xFF42, usage=0x0001, iface=3)]
        h = {b"audio": FakeHeadset([reply(10)]), b"usage2": FakeHeadset([reply(20)]),
             b"mine": FakeHeadset([reply(30)])}
        out, bus, _ = self.poll(e, h)
        self.assertEqual(bus.opened, [b"mine"])
        self.assertEqual(out[0].level, 30)

    def test_no_matching_collection_opens_nothing(self):
        out, bus, _ = self.poll([entry(0x0A3E, b"audio", page=0x000C, iface=0)],
                                {b"audio": FakeHeadset([reply(10)])})
        self.assertEqual((out, bus.opened), ([], []))

    def test_the_diagnostics_hexdump_the_reply(self):
        _, _, p = self.poll([entry(0x0A40, b"r")], {b"r": FakeHeadset([reply(55)])})
        self.assertTrue(any("reply: 01 f0 37 00" in line for line in p.diagnostics()))

    def test_the_unverified_0a40_receiver_is_read_like_0a3e(self):
        out, bus, _ = self.poll([entry(0x0A40, b"r")], {b"r": FakeHeadset([reply(55)])})
        self.assertEqual((out[0].key, out[0].level), ("corsair_virtuoso:se", 55))

    def test_every_sourced_pid_is_listed(self):
        for pid in (0x0A3E, 0x0A3D, 0x0A64, 0x0A62):
            self.assertIn(pid, V.PIDS)

    def test_receiver_and_cable_make_one_icon(self):
        e = [entry(0x0A3E, b"rx"), entry(0x0A3D, b"cable")]
        out, _, _ = self.poll(e, {b"rx": FakeHeadset([reply(60)]),
                                  b"cable": FakeHeadset([reply(60)])})
        self.assertEqual(len(out), 1)

    def test_se_and_xt_are_two_icons(self):
        e = [entry(0x0A3E, b"se"), entry(0x0A64, b"xt")]
        out, _, _ = self.poll(e, {b"se": FakeHeadset([reply(60)]),
                                  b"xt": FakeHeadset([reply(70)])})
        self.assertEqual({s.key for s in out}, {"corsair_virtuoso:se", "corsair_virtuoso:xt"})

    def test_an_open_failure_gives_no_reading(self):
        out, _, p = self.poll([entry(0x0A3E, b"se")], {})
        self.assertEqual(out, [])
        self.assertTrue(any("open:" in line for line in p.diagnostics()))


if __name__ == "__main__":
    unittest.main()
