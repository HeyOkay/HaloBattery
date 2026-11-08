"""Tests for providers/hyperx_alpha_wireless.py. No hardware is needed.

The fake dongle answers as HeadsetControl's hyperx_cloud_alpha_wireless.hpp and
HyperXBatteryMonitor's CloudAlphaWirelessDevice.cs describe: a 31-byte output report
`21 bb <selector>`, answered by `21 bb <selector> <value>` (0x03 connection, 0x0C
charging, 0x0B level). Not confirmed on hardware.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import hyperx_alpha_wireless as H  # noqa: E402

PATH_VENDOR = b"\\\\?\\hid#vid_03f0&pid_098d&mi_03&col01#8&1234abcd&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}"
PATH_CONSUMER = PATH_VENDOR.replace(b"col01", b"col02")

VENDOR = {"product_id": 0x098D, "path": PATH_VENDOR, "interface_number": 3,
          "usage_page": 0xFF43, "usage": 0x0202}
CONSUMER = {"product_id": 0x098D, "path": PATH_CONSUMER, "interface_number": 3,
            "usage_page": 0x000C, "usage": 0x0001}


def frame(selector, value):
    return [H.REPORT_ID, H.MARKER, selector, value] + [0] * 27


class FakeDongle:
    """connected / charging / level: the values it answers with. A selector in `silent`
    gets no reply; `write_result` is what write() returns (-1 = failure, as hidapi)."""

    def __init__(self, connected=True, charging=0, level=64, silent=(), write_result=None,
                 noise=()):
        self.connected = connected
        self.charging = charging
        self.level = level
        self.silent = set(silent)
        self.write_result = write_result
        self.noise = list(noise)
        self.writes = []
        self.features = []
        self.opened = []
        self.pending = []

    def open_path(self, path):
        self.opened.append(path)

    def close(self):
        pass

    def error(self):
        return "WriteFile: (0x00000057) The parameter is incorrect."

    def write(self, packet):
        self.writes.append(list(packet))
        if self.write_result is not None:
            return self.write_result
        sel = packet[2]
        if sel in self.silent:
            return len(packet)
        self.pending.extend(self.noise)
        if sel == H.SEL_CONNECTION:
            self.pending.append(frame(sel, 0 if self.connected else 1))
        elif sel == H.SEL_CHARGING:
            self.pending.append(frame(sel, self.charging))
        elif sel == H.SEL_LEVEL:
            self.pending.append(frame(sel, self.level))
        return len(packet)

    def send_feature_report(self, packet):      # never used: the sources write output reports
        self.features.append(list(packet))
        return len(packet)

    def read(self, n, timeout_ms=0):
        return self.pending.pop(0) if self.pending else []


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = (H.hid, H.hidlist, H.time)
        clock = [0.0]

        def monotonic():
            clock[0] += 0.05                    # each read that finds nothing costs time
            return clock[0]
        H.time = types.SimpleNamespace(monotonic=monotonic)

    def tearDown(self):
        H.hid, H.hidlist, H.time = self._saved

    def poll(self, dongle, entries=(VENDOR, CONSUMER)):
        H.hid = types.SimpleNamespace(device=lambda: dongle)
        H.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        p = H.HyperXAlphaWirelessProvider()
        out = p.poll()
        return out, p.diagnostics()


class TestFrames(unittest.TestCase):
    def test_request_is_31_bytes_report_21_bb_selector(self):
        for sel in (0x03, 0x0B, 0x0C):
            r = H.make_request(sel)
            self.assertEqual(len(r), 31)
            self.assertEqual(r[:3], [0x21, 0xBB, sel])
            self.assertEqual(r[3:], [0] * 28)

    def test_level_needs_the_level_echo(self):
        self.assertEqual(H.parse_level(frame(0x0B, 64)), 64)
        self.assertIsNone(H.parse_level(frame(0x0C, 64)))
        self.assertIsNone(H.parse_level([0x21, 0xAA, 0x0B, 64]))
        self.assertIsNone(H.parse_level([0x22, 0xBB, 0x0B, 64]))
        self.assertIsNone(H.parse_level([0x21, 0xBB, 0x0B]))
        self.assertIsNone(H.parse_level([]))

    def test_level_above_100_is_refused(self):
        self.assertEqual(H.parse_level(frame(0x0B, 100)), 100)
        self.assertIsNone(H.parse_level(frame(0x0B, 101)))

    def test_unsolicited_level_notification_is_not_a_reply(self):
        self.assertIsNone(H.parse_level(frame(0x25, 50)))

    def test_charging_values(self):
        self.assertIs(H.parse_charging(frame(0x0C, 1)), True)
        self.assertIs(H.parse_charging(frame(0x0C, 0)), False)
        self.assertIsNone(H.parse_charging(frame(0x0C, 2)))
        self.assertIsNone(H.parse_charging(frame(0x26, 1)))

    def test_connection_value_1_means_not_connected(self):
        self.assertIs(H.parse_connected(frame(0x03, 1)), False)
        self.assertIs(H.parse_connected(frame(0x03, 0)), True)
        self.assertIsNone(H.parse_connected(frame(0x0B, 0)))


class TestPoll(Base):
    def test_connected_headset_reads_level(self):
        d = FakeDongle(level=64)
        out, _ = self.poll(d)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s.key, s.name, s.level, s.charging, s.online, s.source, s.kind),
                         ("hyperx:098d", "HyperX Cloud Alpha Wireless", 64, False, True,
                          "hyperx_alpha_wireless", "headset"))

    def test_requests_go_out_as_31_byte_output_reports_in_order(self):
        d = FakeDongle()
        self.poll(d)
        self.assertEqual([w[:3] for w in d.writes],
                         [[0x21, 0xBB, 0x03], [0x21, 0xBB, 0x0C], [0x21, 0xBB, 0x0B]])
        self.assertTrue(all(len(w) == 31 for w in d.writes))
        self.assertEqual(d.features, [])

    def test_only_the_vendor_collection_is_opened(self):
        d = FakeDongle()
        self.poll(d)
        self.assertEqual(set(d.opened), {PATH_VENDOR})

    def test_without_the_vendor_collection_nothing_is_written(self):
        d = FakeDongle()
        out, diag = self.poll(d, entries=(CONSUMER,))
        self.assertEqual(out, [])
        self.assertEqual(d.writes, [])
        self.assertTrue(any("no usage ff43:0202" in line for line in diag))

    def test_other_pids_are_ignored(self):
        d = FakeDongle()
        other = dict(VENDOR, product_id=0x08BE)
        out, _ = self.poll(d, entries=(other,))
        self.assertEqual(out, [])
        self.assertEqual(d.writes, [])

    def test_charging_shows_level_and_flag(self):
        out, _ = self.poll(FakeDongle(charging=1, level=40))
        self.assertEqual((out[0].level, out[0].charging, out[0].online), (40, True, True))

    def test_charging_without_a_level_reply_keeps_the_icon_online(self):
        out, _ = self.poll(FakeDongle(charging=1, silent={H.SEL_LEVEL}))
        self.assertEqual((out[0].level, out[0].charging, out[0].online), (None, True, True))

    def test_headset_off_gives_a_grey_icon(self):
        d = FakeDongle(connected=False)
        out, _ = self.poll(d)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].level, out[0].charging, out[0].online), (None, False, False))
        self.assertEqual([w[2] for w in d.writes], [H.SEL_CONNECTION])   # nothing else asked

    def test_no_reply_to_the_connection_query_gives_a_grey_icon(self):
        out, diag = self.poll(FakeDongle(silent={H.SEL_CONNECTION}))
        self.assertEqual([(s.online, s.level) for s in out], [(False, None)])
        self.assertTrue(any("no reply" in line for line in diag))

    def test_connected_but_no_level_and_not_charging_is_grey(self):
        out, _ = self.poll(FakeDongle(silent={H.SEL_LEVEL}))
        self.assertEqual([(s.online, s.level) for s in out], [(False, None)])

    def test_level_above_100_is_not_shown(self):
        out, _ = self.poll(FakeDongle(level=200))
        self.assertEqual([(s.online, s.level) for s in out], [(False, None)])

    def test_unknown_charging_value_counts_as_not_charging(self):
        out, _ = self.poll(FakeDongle(charging=7, level=55))
        self.assertEqual((out[0].level, out[0].charging), (55, False))

    def test_write_minus_one_is_a_failed_send_not_an_offline_headset(self):
        d = FakeDongle(write_result=-1)
        out, diag = self.poll(d)
        self.assertEqual(out, [])
        self.assertEqual(len(d.writes), 1)
        self.assertTrue(any("write -> -1" in line and "0x00000057" in line for line in diag))

    def test_write_that_raises_is_a_failed_send(self):
        d = FakeDongle()

        def boom(packet):
            raise OSError("WriteFile: (0x00000057) The parameter is incorrect.")
        d.write = boom
        out, diag = self.poll(d)
        self.assertEqual(out, [])
        self.assertTrue(any("write:" in line for line in diag))

    def test_unsolicited_frames_before_the_reply_are_skipped(self):
        noise = [frame(0x25, 12), frame(0x26, 1), frame(0x23, 1)]
        out, diag = self.poll(FakeDongle(level=77, noise=noise))
        self.assertEqual((out[0].level, out[0].charging, out[0].online), (77, False, True))
        self.assertTrue(any("ignored" in line for line in diag))

    def test_open_failure_gives_no_icon(self):
        d = FakeDongle()

        def refuse(path):
            raise OSError("cannot open")
        d.open_path = refuse
        out, diag = self.poll(d)
        self.assertEqual(out, [])
        self.assertTrue(any("open:" in line for line in diag))


if __name__ == "__main__":
    unittest.main()
