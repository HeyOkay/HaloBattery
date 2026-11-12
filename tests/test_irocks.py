"""Tests for providers/irocks.py. No hardware is needed.

The frames are @jimchen5209's measured ones from #309 (their branch ``m31r``):

  * power replies on ff01:0000, header 0x09: ``09 04 00 00 00 02 37 00 ... 0f`` = 55 %,
    not charging; ``09 04 00 00 00 02 4b 01 ... fa`` = 75 %, charging. Checksums are
    reproduced by the helper below and asserted against those last bytes.
  * online status on the receiver: ``09 03 00 00 00 01 01 ... 47`` = online;
    ``09 03 00 00 00 01 00 ... 48`` = offline.

The transport is the point: the request is a *feature* report on ff02:0002 and the reply
an *input* report on ff01:0000, so the fake ties replies to requests.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import irocks as I  # noqa: E402


def frame(cmd, level=0, flag=0, status=0, online=False, header=0x09):
    f = [header, cmd] + [0x00] * (I.FRAME_LEN - 3)
    f[2] = status
    if cmd == I.CMD_POWER:
        f[5] = 0x02
        f[I.LEVEL_INDEX] = level
        f[I.FLAG_INDEX] = flag
    else:
        f[5] = 0x01
        f[I.ONLINE_INDEX] = 0x01 if online else 0x00
    f.append((0x55 - sum(f[:I.FRAME_LEN - 1])) % 256)
    return f


CAPTURED_55 = frame(I.CMD_POWER, level=0x37, flag=0x00)
CAPTURED_75_CHARGING = frame(I.CMD_POWER, level=0x4B, flag=0x01)
CAPTURED_ONLINE = frame(I.CMD_CONNECT, online=True)
CAPTURED_OFFLINE = frame(I.CMD_CONNECT, online=False)


class FakeLink:
    """One receiver: replies are keyed by how many requests the writer has sent."""

    def __init__(self, replies):
        self.replies = {k: list(v) for k, v in replies.items()}
        self.requests = 0
        self.sent = []

    def device(self):
        return FakeDevice(self)


class FakeDevice:
    def __init__(self, link):
        self.link = link
        self.path = None

    def open_path(self, path):
        self.path = path

    def send_feature_report(self, data):
        self.link.requests += 1
        self.link.sent.append((self.path, bytes(data)))
        return len(data)

    def read(self, size, timeout):
        queue = self.link.replies.get(self.link.requests, [])
        return queue.pop(0) if queue else []

    def close(self):
        pass


class ParseTest(unittest.TestCase):
    def test_the_captured_frames_carry_the_measured_checksums(self):
        self.assertEqual(0x0F, CAPTURED_55[-1])
        self.assertEqual(0xFA, CAPTURED_75_CHARGING[-1])
        self.assertEqual(0x47, CAPTURED_ONLINE[-1])
        self.assertEqual(0x48, CAPTURED_OFFLINE[-1])

    def test_the_captured_power_replies(self):
        self.assertEqual((55, False), I.parse_power(CAPTURED_55))
        self.assertEqual((75, True), I.parse_power(CAPTURED_75_CHARGING))

    def test_the_header_0x08_is_accepted_too(self):
        self.assertEqual((42, False), I.parse_power(frame(I.CMD_POWER, level=42, header=0x08)))

    def test_a_bad_checksum_is_refused(self):
        bad = list(CAPTURED_55)
        bad[-1] ^= 0xFF
        self.assertIsNone(I.parse_power(bad))

    def test_another_command_or_level_is_refused(self):
        self.assertIsNone(I.parse_power(CAPTURED_ONLINE))          # cmd 0x03
        self.assertEqual((0, False), I.parse_power(frame(I.CMD_POWER, level=0)))
        self.assertIsNone(I.parse_power(frame(I.CMD_POWER, level=101)))
        self.assertIsNone(I.parse_power([]))

    def test_the_online_answers(self):
        self.assertIs(True, I.parse_online(CAPTURED_ONLINE))
        self.assertIs(False, I.parse_online(CAPTURED_OFFLINE))
        bad = list(CAPTURED_ONLINE)
        bad[-1] ^= 0xFF
        self.assertIsNone(I.parse_online(bad))
        self.assertIsNone(I.parse_online(CAPTURED_55))             # cmd 0x04


class PollTest(unittest.TestCase):
    REPLY_PATH = b"replies"
    COMMAND_PATH = b"command"
    INFO = [{"path": b"replies", "interface_number": 1,
             "usage_page": I.REPLY_USAGE[0], "usage": I.REPLY_USAGE[1],
             "product_id": I.RECEIVER_PID, "product_string": "Compx Wireless Mouse"},
            {"path": b"command", "interface_number": 1,
             "usage_page": I.COMMAND_USAGE[0], "usage": I.COMMAND_USAGE[1],
             "product_id": I.RECEIVER_PID, "product_string": "Compx Wireless Mouse"}]

    def setUp(self):
        self._saved = (I.hid, I.hidlist, I.time)
        self.now = [1000.0]
        I.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)

    def tearDown(self):
        I.hid, I.hidlist, I.time = self._saved

    def poll(self, replies, infos=None):
        link = FakeLink(replies)
        I.hid = types.SimpleNamespace(device=link.device)
        infos = self.INFO if infos is None else infos
        I.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return I.IRocksProvider().poll()

    def test_the_receiver_is_asked_online_then_read(self):
        # request 1 = 0x03 online, request 2 = 0x04 power
        out = self.poll({1: [CAPTURED_ONLINE], 2: [CAPTURED_55]})
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in out],
                         [("irocks:c009", "iRocks M31R (2.4 GHz)", 55, False, True, "mouse")])

    def test_the_online_request_comes_first(self):
        link = FakeLink({1: [CAPTURED_ONLINE], 2: [CAPTURED_55]})
        I.hid = types.SimpleNamespace(device=link.device)
        I.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.INFO))
        I.IRocksProvider().poll()
        self.assertEqual(2, len(link.sent))
        self.assertEqual(I.make_request(I.CMD_CONNECT), list(link.sent[0][1]))
        self.assertEqual(I.make_request(I.CMD_POWER), list(link.sent[1][1]))

    def test_an_offline_receiver_reads_no_cached_level(self):
        out = self.poll({1: [CAPTURED_OFFLINE], 2: [CAPTURED_75_CHARGING]})
        self.assertEqual([], out)

    def test_an_offline_receiver_keeps_the_last_reading_greyed(self):
        provider = I.IRocksProvider()
        link = FakeLink({1: [CAPTURED_ONLINE], 2: [CAPTURED_55]})
        I.hid = types.SimpleNamespace(device=link.device)
        I.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(self.INFO))
        self.assertEqual(55, provider.poll()[0].level)
        # the mouse goes to sleep: the receiver answers 0x03 with offline
        link.replies = {1: [CAPTURED_OFFLINE]}
        self.now[0] += 30
        out = provider.poll()
        self.assertEqual([(55, False)], [(s.level, s.online) for s in out])
        self.now[0] += I.ASLEEP_KEEP + 1
        self.assertEqual([], provider.poll())

    def test_the_cable_is_read_without_an_online_check(self):
        infos = [dict(self.INFO[0], product_id=I.CABLE_PID),
                 dict(self.INFO[1], product_id=I.CABLE_PID)]
        out = self.poll({1: [CAPTURED_75_CHARGING]}, infos=infos)
        self.assertEqual([(s.key, s.level, s.charging) for s in out],
                         [("irocks:c010", 75, True)])
        # the cable has no offline state: a silent poll reports nothing and keeps nothing
        self.assertEqual([], self.poll({}, infos=infos))

    def test_missing_collections_are_reported_and_nothing_is_sent(self):
        provider = I.IRocksProvider()
        link = FakeLink({})
        I.hid = types.SimpleNamespace(device=link.device)
        I.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: [self.INFO[0]])
        self.assertEqual([], provider.poll())
        self.assertEqual([], link.sent)
        self.assertIn("ff02:0002", "\n".join(provider.diagnostics()))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
