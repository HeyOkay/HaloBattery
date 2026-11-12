"""Tests for providers/eweadn.py - no hardware needed.

The exchange is the one the EWEADN web HUB uses for the S9. hub.eweadn.cn/deviceJson/pq.json
maps 089D:062F (usage page 0xFF01, usage 0x0010) to the protocol named "zhanli-receiver", and
the S9 page (/pq/web/) implements it: a 33-byte output report, report id 0, ``[0, cmd, 0, 0,
0, ... checksum]`` with checksum = sum(bytes 5..31) & 0xFF; command 0x30 asks for the device
info block, answered with the command echoed in byte 0, status in byte 1 and the block at
bytes 4..14 - device id "M156", firmware, ic type, report max, charge flag (12), battery (13),
connect status (14).

While the mouse sleeps the receiver still answers, with ``30 02`` and no payload. Captured on
a real S9 receiver: asleep -> ``30 02 00 ...``; after moving the mouse ->
``30 00 01 0B 4D 31 35 36 01 22 95 AA 00 46 01 ...`` = M156, 0x46 = 70 %.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import eweadn as E  # noqa: E402


def info(level=0x46, charge=0, dev_id=b"M156"):
    """The device-info answer captured on the hardware, parameterised."""
    r = [0x30, 0x00, 0x01, 0x0B] + list(dev_id) + [0x01, 0x22, 0x95, 0xAA, charge, level, 0x01]
    return (r + [0x00] * 32)[:32]


def asleep():
    return [0x30, 0x02] + [0x00] * 30


class FakeReceiver:
    def __init__(self, reply=None):
        self.replies = list(reply or [])
        self.path = None
        self.sent = []

    # hid.device API
    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def set_nonblocking(self, value):
        pass

    def write(self, data):
        self.sent.append((self.path, bytes(data)))
        return len(data)

    def read(self, size, timeout_ms=0):
        return self.replies.pop(0) if self.replies else []


def entries(vid=0x089D, pid=0x062F):
    shape = [(0, 0x0001, 0x0002),
             (1, 0x0001, 0x0006),
             (1, 0x000C, 0x0001),
             (1, 0xFF00, 0x0000),
             (2, 0xFF01, 0x0010)]      # the vendor collection
    return [{"vendor_id": vid, "product_id": pid, "interface_number": i,
             "usage_page": p, "usage": u,
             "path": b"ew-%d-%04x-%02x" % (i, p, u),
             "product_string": "Wireless-Receiver"}
            for i, p, u in shape]


REPLY_PATH = entries()[4]["path"]


class ParseTest(unittest.TestCase):
    def test_the_request_carries_the_vendors_checksum(self):
        req = E.make_request()
        self.assertEqual(len(req), 33)
        self.assertEqual((req[0], req[1]), (0x00, 0x30))
        self.assertEqual(req[32], sum(req[5:32]) & 0xFF)

    def test_the_captured_answer_reads_level_and_device_id(self):
        self.assertEqual(E.parse_info(info(0x46, 0)), (70, False, "M156"))
        self.assertEqual(E.parse_info(info(0x45, 1)), (69, True, "M156"))

    def test_the_sleeping_answer_is_refused_rather_than_shown_as_zero(self):
        self.assertIsNone(E.parse_info(asleep()))

    def test_another_command_or_status_is_refused(self):
        wrong = info()
        wrong[0] = 0x31
        self.assertIsNone(E.parse_info(wrong))
        wrong = info()
        wrong[1] = 0x02
        self.assertIsNone(E.parse_info(wrong))

    def test_a_level_above_100_is_refused(self):
        self.assertIsNone(E.parse_info(info(0x65)))

    def test_a_short_answer_is_refused(self):
        self.assertIsNone(E.parse_info([0x30, 0x00, 0x01]))
        self.assertIsNone(E.parse_info(None))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (E.hid, E.hidlist)

    def tearDown(self):
        E.hid, E.hidlist = self._saved

    def poll(self, fake, ents):
        E.hid = types.SimpleNamespace(device=lambda: fake)
        E.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        p = E.EweadnProvider()
        return p.poll(), p.diagnostics()

    def test_the_mouse_is_read_on_the_vendor_collection(self):
        fake = FakeReceiver([info(0x46, 0)])
        out, diag = self.poll(fake, entries())
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("eweadn", "EWEADN S9", 70, False, True, "eweadn", "mouse")])
        self.assertTrue(out[0].wake_on_input)
        self.assertEqual({p for p, _ in fake.sent}, {REPLY_PATH})

    def test_a_sleeping_mouse_keeps_an_icon_with_no_level(self):
        fake = FakeReceiver([asleep()] * 3)
        out, diag = self.poll(fake, entries())
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0].level)
        self.assertFalse(out[0].online)
        self.assertTrue(any("asleep" in line for line in diag))

    def test_the_last_level_is_kept_when_the_mouse_falls_asleep(self):
        provider = E.EweadnProvider()
        E.hid = types.SimpleNamespace(device=lambda: FakeReceiver([info(0x46, 0)]))
        E.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in entries() if e["vendor_id"] == vid])
        awake = provider.poll()
        self.assertEqual(awake[0].level, 70)

        E.hid = types.SimpleNamespace(device=lambda: FakeReceiver([asleep()] * 3))
        asleep_out = provider.poll()
        self.assertEqual([(s.level, s.online) for s in asleep_out], [(70, False)])

    def test_no_receiver_gives_nothing(self):
        out, _ = self.poll(FakeReceiver(), [])
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
