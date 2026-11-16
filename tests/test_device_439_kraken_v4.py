"""Tests for providers/kraken_v4.py - the Razer Kraken V4 (#439).

No hardware is needed. The frame is the one OpenRGB documents for this model:
report 2, 64 bytes, sequence in [2], domain in [9], command in [10], XOR
checksum in [62]; a reply repeats the sequence, carries address 0x80, the
domain, an ack, a count and the value at [13].

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import kraken_v4 as K  # noqa: E402


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
    def test_the_frame_carries_sequence_domain_and_command(self):
        f = K.make_frame(3, 0x80, K.CMD_BATTERY)
        self.assertEqual(K.REPORT_SIZE, len(f))
        self.assertEqual([0x02, 0x00, 0x63, 0x00, 0x00, 0x00, 0x04], f[:7])
        self.assertEqual([0x80, 0x21], f[9:11])
        checksum = 0
        for b in f[:62]:
            checksum ^= b
        self.assertEqual(checksum, f[62])
        self.assertEqual(0, f[63])

    def test_the_reply_reads_its_first_payload_byte(self):
        self.assertEqual(44, K.parse_reply(good_reply(3, 0x80, K.CMD_BATTERY, 44), 3,
                                          0x80, K.CMD_BATTERY))

    def test_a_reply_without_the_report_id_is_read_too(self):
        reply = good_reply(3, 0x80, K.CMD_BATTERY, 44)[1:]
        self.assertEqual(44, K.parse_reply(reply, 3, 0x80, K.CMD_BATTERY))

    def test_the_cable_accepts_the_headset_address_for_domain_zero(self):
        reply = good_reply(4, 0x80, K.CMD_BATTERY, 60)
        self.assertEqual(60, K.parse_reply(reply, 4, 0x00, K.CMD_BATTERY))

    def test_a_reply_must_match_sequence_domain_command_and_ack(self):
        self.assertIsNone(K.parse_reply(good_reply(3, 0x80, K.CMD_BATTERY, 44), 4,
                                        0x80, K.CMD_BATTERY))
        self.assertIsNone(K.parse_reply(good_reply(3, 0x80, K.CMD_BATTERY, 44), 3,
                                        0x40, K.CMD_BATTERY))
        self.assertIsNone(K.parse_reply(good_reply(3, 0x80, 0x2A, 1), 3,
                                        0x80, K.CMD_BATTERY))
        self.assertIsNone(K.parse_reply(good_reply(3, 0x80, K.CMD_BATTERY, 44, ack=0),
                                        3, 0x80, K.CMD_BATTERY))
        self.assertIsNone(K.parse_reply(good_reply(3, 0x80, K.CMD_BATTERY, 44, count=0),
                                        3, 0x80, K.CMD_BATTERY))
        self.assertIsNone(K.parse_reply(good_reply(3, 0x80, K.CMD_BATTERY, 44,
                                                   address=0x60), 3, 0x80, K.CMD_BATTERY))

    def test_a_bad_checksum_is_refused(self):
        reply = good_reply(3, 0x80, K.CMD_BATTERY, 44, checksum_xor=0xFF)
        self.assertIsNone(K.parse_reply(reply, 3, 0x80, K.CMD_BATTERY))

    def test_a_short_frame_is_ignored(self):
        self.assertIsNone(K.parse_reply([0x02, 0x00, 0x63], 3, 0x80, K.CMD_BATTERY))


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
    return [{"product_id": pid, "interface_number": 5, "usage_page": 0xFF14, "usage": 0x0001,
             "path": b"%04x-ff14" % pid, "product_string": "Razer Kraken V4 2.4"} for pid in pids]


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (K.hid, K.hidlist, K.time)
        K.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)
        self.provider = K.KrakenV4Provider()

    def tearDown(self):
        K.hid, K.hidlist, K.time = self._saved

    def poll(self, infos, cols):
        K.hid = types.SimpleNamespace(device=lambda: cols[next(iter(cols))])
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_the_receiver_reports_the_battery_level(self):
        infos = entries(0x056C)
        cols = {b"056c-ff14": FakeDevice({K.CMD_BATTERY: 44})}
        res = self.poll(infos, cols)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.online, r.kind) for r in res],
                         [("kraken_v4", "Razer Kraken V4", 44, False, True, "headset")])
        self.assertFalse(res[0].charging_known)
        dev = cols[b"056c-ff14"]
        self.assertEqual([0x80, 0x21], [dev.frames[0][9], dev.frames[0][10]])
        self.assertEqual(1, len(dev.frames))

    def test_only_the_battery_command_is_sent(self):
        infos = entries(0x056C)
        dev = FakeDevice({K.CMD_BATTERY: 44})
        self.poll(infos, {b"056c-ff14": dev})
        self.assertEqual([0x21], [f[10] for f in dev.frames])

    def test_the_cable_is_read_before_the_dongle(self):
        infos = entries(0x056C, 0x056B)
        cable = FakeDevice({K.CMD_BATTERY: 60})
        dongle = FakeDevice({K.CMD_BATTERY: 44})

        class PerPath:
            def __init__(self):
                self.current = None

            def __call__(self):
                outer = self

                class D:
                    def open_path(self, path):
                        outer.current = {b"056b-ff14": cable, b"056c-ff14": dongle}[path]

                    def write(self, data):
                        return outer.current.write(data)

                    def read(self, size, timeout_ms=0):
                        return outer.current.read(size, timeout_ms)

                    def close(self):
                        pass
                return D()

        K.hid = types.SimpleNamespace(device=PerPath())
        K.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        res = self.provider.poll()
        self.assertEqual([r.level for r in res], [60])
        self.assertTrue(cable.frames and not dongle.frames)
        self.assertEqual(0x00, cable.frames[0][9])

    def test_an_impossible_percent_is_refused(self):
        infos = entries(0x056C)
        cols = {b"056c-ff14": FakeDevice({K.CMD_BATTERY: 250})}
        self.assertEqual(self.poll(infos, cols), [])

    def test_no_device_reports_nothing(self):
        self.assertEqual(self.poll([], {}), [])

    def test_a_silent_headset_keeps_the_last_value_greyed(self):
        infos = entries(0x056C)
        dev = FakeDevice({K.CMD_BATTERY: 44})
        cols = {b"056c-ff14": dev}
        self.poll(infos, cols)
        dev.values = {}
        res = self.poll(infos, cols)
        self.assertEqual([(r.level, r.online) for r in res], [(44, False)])
        self.provider._last = (44, time.time() - K.ASLEEP_KEEP)
        self.assertEqual(self.poll(infos, cols), [])


if __name__ == "__main__":
    unittest.main()
