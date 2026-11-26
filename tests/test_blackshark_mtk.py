"""Tests for providers/blackshark_mtk.py - the BlackShark V2 HyperSpeed (#85, #86).

No hardware is needed. Frames are the ones justik13/razer-blackshark-v2-
hyperspeed-webhid, arkady-emelyanov/blackshark-battery and openrazer PR #2974
agree on: report 2, 64 bytes, sequence in [2], domain in [9], command in [10],
XOR checksum in [62]; a reply repeats the sequence, carries the headset
address 0x80, the domain, an ack, a count and the value at [13].

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import blackshark_mtk as B  # noqa: E402


def good_reply(seq, domain, cmd, value, count=1, ack=0x01, address=0x80,
               checksum_xor=0x00):
    p = [0x00] * 64
    p[0] = 0x02
    p[2] = 0x60 | (seq & 0x1F)
    p[8] = address
    p[9] = domain
    p[10] = cmd
    p[11] = ack
    p[12] = count
    p[13] = value
    checksum = 0
    for b in p[1:62]:
        checksum ^= b
    p[62] = checksum ^ checksum_xor
    return p


class FrameTest(unittest.TestCase):
    def test_the_frame_carries_the_sequence_domain_and_command(self):
        f = B.make_frame(3, 0x80, B.CMD_BATTERY)
        self.assertEqual(B.REPORT_SIZE, len(f))
        self.assertEqual([0x02, 0x00, 0x63, 0x00, 0x00, 0x00, 0x04], f[:7])
        self.assertEqual([0x80, 0x21], f[9:11])
        checksum = 0
        for b in f[:62]:
            checksum ^= b
        self.assertEqual(checksum, f[62])
        self.assertEqual(0, f[63])

    def test_the_reply_reads_its_first_payload_byte(self):
        self.assertEqual(47, B.parse_reply(good_reply(3, 0x80, B.CMD_BATTERY, 47), 3,
                                          0x80, B.CMD_BATTERY))

    def test_a_reply_without_the_report_id_is_read_too(self):
        reply = good_reply(3, 0x80, B.CMD_BATTERY, 47)[1:]
        self.assertEqual(47, B.parse_reply(reply, 3, 0x80, B.CMD_BATTERY))

    def test_the_cable_accepts_the_headset_address_for_domain_zero(self):
        reply = good_reply(4, 0x80, B.CMD_BATTERY, 60)   # wired request, domain 0
        self.assertEqual(60, B.parse_reply(reply, 4, 0x00, B.CMD_BATTERY))

    def test_a_reply_must_match_sequence_domain_command_and_ack(self):
        self.assertIsNone(B.parse_reply(good_reply(3, 0x80, B.CMD_BATTERY, 47), 4,
                                        0x80, B.CMD_BATTERY))
        self.assertIsNone(B.parse_reply(good_reply(3, 0x80, B.CMD_BATTERY, 47), 3,
                                        0x40, B.CMD_BATTERY))
        self.assertIsNone(B.parse_reply(good_reply(3, 0x80, B.CMD_CHARGING, 1), 3,
                                        0x80, B.CMD_BATTERY))
        self.assertIsNone(B.parse_reply(good_reply(3, 0x80, B.CMD_BATTERY, 47, ack=0),
                                        3, 0x80, B.CMD_BATTERY))
        self.assertIsNone(B.parse_reply(good_reply(3, 0x80, B.CMD_BATTERY, 47, count=0),
                                        3, 0x80, B.CMD_BATTERY))
        self.assertIsNone(B.parse_reply(good_reply(3, 0x80, B.CMD_BATTERY, 47,
                                                   address=0x60), 3, 0x80, B.CMD_BATTERY))

    def test_a_bad_checksum_is_refused(self):
        reply = good_reply(3, 0x80, B.CMD_BATTERY, 47, checksum_xor=0xFF)
        self.assertIsNone(B.parse_reply(reply, 3, 0x80, B.CMD_BATTERY))

    def test_a_short_frame_is_ignored(self):
        self.assertIsNone(B.parse_reply([0x02, 0x00, 0x63], 3, 0x80, B.CMD_BATTERY))


class FakeDevice:
    def __init__(self, values):
        self.values = dict(values)
        self.frames = []
        self.opened = 0

    def open_path(self, path):
        self.opened += 1

    def write(self, data):
        self.frames.append(list(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        if not self.frames:
            return []
        frame = self.frames[-1]
        seq, domain, cmd = frame[2], frame[9], frame[10]
        value = self.values.get(cmd)
        if value is None:
            return []
        return good_reply(seq, domain, cmd, value)

    def close(self):
        pass


def entries(*pids):
    return [{"product_id": pid, "interface_number": 3, "usage_page": 0xFF14, "usage": 0x0001,
             "path": b"%04x-ff14" % pid, "product_string": "Razer BlackShark V2 HS 2.4"}
            for pid in pids]


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (B.hid, B.hidlist, B.time)
        B.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)
        self.provider = B.BlackSharkMtkProvider()

    def tearDown(self):
        B.hid, B.hidlist, B.time = self._saved

    def poll(self, infos, cols):
        B.hid = types.SimpleNamespace(device=lambda: cols[next(iter(cols))])
        B.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_the_receiver_reports_the_level_and_charging(self):
        infos = entries(0x0565)
        cols = {b"0565-ff14": FakeDevice({B.CMD_BATTERY: 47, B.CMD_CHARGING: 1})}
        res = self.poll(infos, cols)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.online, r.kind) for r in res],
                         [("blackshark_hs", "Razer BlackShark V2 HyperSpeed", 47, True,
                           True, "headset")])
        self.assertTrue(res[0].charging_known)
        dev = cols[b"0565-ff14"]
        self.assertEqual([0x80, 0x21], [dev.frames[0][9], dev.frames[0][10]])
        self.assertEqual([0x80, 0x2A], [dev.frames[1][9], dev.frames[1][10]])

    def test_the_cable_is_read_before_the_dongle(self):
        infos = entries(0x0565, 0x056E)
        cable = FakeDevice({B.CMD_BATTERY: 60, B.CMD_CHARGING: 1})
        dongle = FakeDevice({B.CMD_BATTERY: 47, B.CMD_CHARGING: 0})

        class PerPath:
            def __init__(self):
                self.current = None

            def __call__(self):
                outer = self

                class D:
                    def open_path(self, path):
                        outer.current = {b"056e-ff14": cable, b"0565-ff14": dongle}[path]

                    def write(self, data):
                        return outer.current.write(data)

                    def read(self, size, timeout_ms=0):
                        return outer.current.read(size, timeout_ms)

                    def close(self):
                        pass
                return D()

        B.hid = types.SimpleNamespace(device=PerPath())
        B.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        res = self.provider.poll()
        self.assertEqual([r.level for r in res], [60])
        self.assertTrue(cable.frames and not dongle.frames)
        self.assertEqual(0x00, cable.frames[0][9])

    def test_charging_is_unknown_when_the_second_query_is_silent(self):
        infos = entries(0x0565)
        cols = {b"0565-ff14": FakeDevice({B.CMD_BATTERY: 71})}
        res = self.poll(infos, cols)
        self.assertEqual([(r.level, r.charging, r.charging_known) for r in res],
                         [(71, False, False)])

    def test_an_impossible_percent_is_refused(self):
        infos = entries(0x0565)
        cols = {b"0565-ff14": FakeDevice({B.CMD_BATTERY: 250})}
        self.assertEqual(self.poll(infos, cols), [])

    def test_no_device_reports_nothing(self):
        self.assertEqual(self.poll([], {}), [])

    def test_a_silent_headset_keeps_the_last_value_greyed(self):
        infos = entries(0x0565)
        dev = FakeDevice({B.CMD_BATTERY: 47, B.CMD_CHARGING: 0})
        cols = {b"0565-ff14": dev}
        self.poll(infos, cols)
        dev.values = {}
        res = self.poll(infos, cols)
        self.assertEqual([(r.level, r.online) for r in res], [(47, False)])
        self.provider._last = (47, False, time.time() - B.ASLEEP_KEEP)
        self.assertEqual(self.poll(infos, cols), [])


if __name__ == "__main__":
    unittest.main()
