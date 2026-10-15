"""Tests for providers/hyperx.py. No hardware: the dongles are fakes answering with the
frames the reference projects describe.

The Kingston revision (0951:1718) is pinned to its three references: HeadsetControl's
hyperx_cloud_2_wireless_kingston.hpp, HyperHeadset's cloud_ii_wireless.rs and
Agustin-Jerusalinsky/hyperx-cloud-II-battery - the request preamble, the report-0x0B
reply shape, the level at byte 7 and the charging state at byte 4. The HP-branded 52-byte
exchange keeps a regression pin of its own.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import hyperx as H  # noqa: E402


def kingston_reply(cmd, level=0, charging=0):
    """A 64-byte reply as the references describe: 0B 00 BB <cmd> ..."""
    r = [0x0B, 0x00, 0xBB, cmd, charging, 0x00, 0x00, level]
    return bytes(r + [0x00] * (H.KINGSTON_READ_LEN - len(r)))


KINGSTON_SHAPE = [  # iface, usage page, usage - the reporter's dump in #192
    (3, 0xFF13, 0x0001),
    (3, 0x000C, 0x0001),
    (3, 0x000C, 0x0001),
]


class FakeDongle:
    def __init__(self, replies=()):
        self.replies = list(replies)
        self.written = []
        self.prepares = 0
        self.prepare_fails = False

    def open_path(self, path):
        pass

    def get_input_report(self, report_id, size):
        self.prepares += 1
        if self.prepare_fails:
            raise OSError("prepare read refused")
        return [0x06] + [0] * (size - 1)

    def write(self, data):
        self.written.append(list(data))
        return len(data)

    def read(self, size, timeout=None):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        pass


def kingston_ifaces(pid=0x1718):
    return [{"product_id": pid, "serial_number": "s", "path": (b"kx-%d" % n),
             "usage_page": p, "usage": u, "interface_number": iface,
             "product_string": "HyperX Cloud II Wireless"}
            for n, (iface, p, u) in enumerate(KINGSTON_SHAPE)]


class KingstonTests(unittest.TestCase):
    def setUp(self):
        self._hid, self._hidlist, self._time = H.hid, H.hidlist, H.time
        self.dongle = FakeDongle()
        self.infos = {H.KINGSTON_VID: kingston_ifaces()}
        H.hid = types.SimpleNamespace(device=lambda: self.dongle)
        H.hidlist = types.SimpleNamespace(enumerate=lambda vid: list(self.infos.get(vid, [])))
        H.time = types.SimpleNamespace(sleep=lambda s: None)
        self.provider = H.HyperXProvider()

    def tearDown(self):
        H.hid, H.hidlist, H.time = self._hid, self._hidlist, self._time

    def fresh(self):
        """A new dongle and provider for the next sub-case (the patches stay in place)."""
        self.dongle = FakeDongle()
        H.hid = types.SimpleNamespace(device=lambda: self.dongle)
        self.provider = H.HyperXProvider()

    def run_with(self, replies):
        self.dongle.replies = list(replies)
        return self.provider.poll()

    def test_the_request_is_the_references_frame_byte_for_byte(self):
        self.run_with([kingston_reply(H.KINGSTON_CMD_LEVEL),
                       kingston_reply(H.KINGSTON_CMD_CHARGING)])
        sent = self.dongle.written
        self.assertEqual(2, len(sent))
        self.assertEqual(H.kingston_request(H.KINGSTON_CMD_LEVEL), sent[0])
        self.assertEqual(62, len(sent[0]))
        self.assertEqual([0x06, 0x00, 0x02, 0x00], sent[0][:4])
        self.assertEqual([0x9A, 0x00, 0x00, 0x68, 0x4A, 0x8E, 0x0A], sent[0][4:11])
        self.assertEqual(0xBB, sent[0][14])
        self.assertEqual(H.KINGSTON_CMD_LEVEL, sent[0][15])
        self.assertEqual(H.KINGSTON_CMD_CHARGING, sent[1][15])
        self.assertEqual([0x00] * 45, sent[0][17:])

    def test_prepare_read_before_every_write(self):
        self.run_with([kingston_reply(H.KINGSTON_CMD_LEVEL),
                       kingston_reply(H.KINGSTON_CMD_CHARGING)])
        self.assertEqual(2, self.dongle.prepares)

    def test_a_failed_prepare_read_is_ignored(self):
        self.dongle.prepare_fails = True
        found = self.run_with([kingston_reply(H.KINGSTON_CMD_LEVEL, level=44),
                               kingston_reply(H.KINGSTON_CMD_CHARGING)])
        self.assertEqual([s.level for s in found], [44])

    def test_the_level_and_charging_are_read(self):
        found = self.run_with([kingston_reply(H.KINGSTON_CMD_LEVEL, level=57),
                               kingston_reply(H.KINGSTON_CMD_CHARGING, charging=0)])
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in found],
                         [("hyperx:1718", "HyperX Cloud II Wireless", 57, False, True, "headset")])

    def test_charging_states(self):
        for value, expected in ((1, True), (2, True), (0, False)):
            with self.subTest(value=value):
                self.fresh()
                found = self.run_with([kingston_reply(H.KINGSTON_CMD_LEVEL, level=88),
                                       kingston_reply(H.KINGSTON_CMD_CHARGING, charging=value)])
                self.assertEqual([s.charging for s in found], [expected])

    def test_a_reply_that_is_not_the_answer_is_refused(self):
        wrong_id = bytes([0x07] + list(kingston_reply(H.KINGSTON_CMD_LEVEL, 9))[1:])
        wrong_marker = bytes([0x0B, 0x00, 0x00] + list(kingston_reply(H.KINGSTON_CMD_LEVEL, 9))[3:])
        wrong_echo = kingston_reply(H.KINGSTON_CMD_CHARGING, level=9)   # answered, asked for 2
        for bad in (wrong_id, wrong_marker, wrong_echo):
            with self.subTest(first=bad[:4]):
                self.fresh()
                self.assertEqual([], self.run_with([bad]))

    def test_a_level_above_100_is_refused(self):
        self.assertEqual([], self.run_with([kingston_reply(H.KINGSTON_CMD_LEVEL, level=101)]))

    def test_100_and_0_are_levels(self):
        for level in (0, 100):
            with self.subTest(level=level):
                self.fresh()
                found = self.run_with([kingston_reply(H.KINGSTON_CMD_LEVEL, level=level),
                                       kingston_reply(H.KINGSTON_CMD_CHARGING)])
                self.assertEqual([s.level for s in found], [level])

    def test_no_vendor_collection_gets_no_write(self):
        self.infos = {H.KINGSTON_VID: [dict(d, usage_page=0x000C, usage=0x0001)
                                       for d in kingston_ifaces()]}
        self.assertEqual([], self.provider.poll())
        self.assertEqual([], self.dongle.written)
        self.assertTrue(any("vendor collection" in line for line in self.provider.diagnostics()))

    def test_an_unknown_product_id_is_not_talked_to(self):
        self.infos = {H.KINGSTON_VID: kingston_ifaces(pid=0x1234)}
        self.assertEqual([], self.provider.poll())
        self.assertEqual([], self.dongle.written)


class OldRevisionTests(unittest.TestCase):
    """The HP-branded path keeps its own 52-byte exchange (regression pin)."""

    def setUp(self):
        self._hid, self._hidlist, self._time = H.hid, H.hidlist, H.time
        self.dongle = FakeDongle()
        self.infos = {H.HYPERX_VID: [{"product_id": 0x0696, "serial_number": "s",
                                      "path": b"hp-0", "usage_page": H.USAGE_PAGE,
                                      "usage": H.USAGE, "interface_number": 3,
                                      "product_string": "HyperX Cloud II Wireless"}]}
        H.hid = types.SimpleNamespace(device=lambda: self.dongle)
        H.hidlist = types.SimpleNamespace(enumerate=lambda vid: list(self.infos.get(vid, [])))
        H.time = types.SimpleNamespace(sleep=lambda s: None)

    def tearDown(self):
        H.hid, H.hidlist, H.time = self._hid, self._hidlist, self._time

    def test_the_52_byte_exchange_still_works(self):
        level_reply = [0x06, 0xFF, 0xBB, 0x02, 0x00, 0x11, 0x2E, 66] + [0] * 12
        charging_reply = [0x06, 0xFF, 0xBB, 0x03, 0x01] + [0] * 15
        self.dongle.replies = [level_reply, charging_reply]
        found = H.HyperXProvider().poll()
        self.assertEqual([(s.key, s.level, s.charging) for s in found],
                         [("hyperx:0696", 66, True)])
        self.assertEqual(H.make_request(H.CMD_LEVEL), self.dongle.written[0])
        self.assertEqual(52, len(self.dongle.written[0]))


if __name__ == "__main__":
    unittest.main()
