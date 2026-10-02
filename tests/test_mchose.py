"""Tests for providers/mchose.py, the V9 Turbo+ headset part (issue #193).
No hardware is needed.

The two exchanges tested here are the ones the headset's own software uses:

  * M HUB's audio path (its own web bundle, from issue #189's decode): an
    AA-framed request on output report 0x55, cmd 0x0B, answered on the same
    report; the payload is ``sleepState, bit7 charging | percent``.
  * The '65 01' frame on the same report that github.com/JoaoKSS/
    MCHOSE_v9_PRO_Controller reads a V9 headset with, level byte 2 and state
    byte 3 (2 discharging, 3 charging, 4 full, 26 asleep) - the same layout
    M HUB decodes for the non-audio 0x3837 devices.

No frames were captured from the reporter's headset - it has not run against
any of this yet.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import mchose as M  # noqa: E402


def audio_reply(sleep_state=1, percent=100, charging=False, flags=0, cmd=0x0B,
                cmd_type=(0x00, 0x01), corrupt_checksum=False):
    """A 64-byte read buffer: report id, then the AA frame the headset would answer."""
    pct = (0x80 if charging else 0x00) | percent
    frame = bytearray([0xAA, 0x01, flags, 0x03, cmd_type[0], cmd_type[1], cmd,
                       sleep_state, pct, 0x00])
    if flags == 1:
        acc = flags ^ 0x03 ^ cmd_type[0] ^ cmd_type[1] ^ cmd ^ sleep_state ^ pct
        frame[9] = acc
    if corrupt_checksum:
        frame[9] ^= 0xFF
    return [0x55] + list(frame) + [0] * (64 - len(frame) - 1)


def status_reply(level=100, state=2):
    return [0x55, 0x65, level, state] + [0] * 60


class FakeCollection:
    """replies: read() pops the next one; empty queue = silence. Writes are recorded."""

    def __init__(self, replies=(), refuse=False):
        self.replies = list(replies)
        self.refuse = refuse
        self.writes = []
        self.opened = 0

    def read(self, n=0, timeout=0):
        if self.replies:
            r = self.replies.pop(0)
            return r if r is not None else []
        return []


def fake_device_class(cols, order_log):
    class FakeDevice:
        def open_path(self, path):
            self.c = cols[path]
            self.c.opened += 1
            order_log.append(path)

        def read(self, n, timeout):
            return self.c.read(n, timeout)

        def write(self, buf):
            if self.c.refuse:
                raise OSError("the collection refuses this report")
            self.c.writes.append(bytes(buf))
            return len(buf)

        def close(self):
            pass
    return FakeDevice


def headset_entries(with_dongle=True):
    """The collections from issue #193's dump: the headset (C-Media) and its dongle."""
    headset = [(0, 0xFF90, 0x01), (0, 0xFF82, 0x01), (0, 0xFF21, 0x01),
               (0, 0xFF22, 0x01), (0, 0x000B, 0x01), (0, 0x000C, 0x01)]
    dongle = [(0, 0xFF00, 0x01)]
    out = []
    for n, (i, p, u) in enumerate(headset):
        out.append({"vendor_id": 0x3837, "product_id": M.HEADSET_BODY_PID,
                    "interface_number": i, "usage_page": p, "usage": u,
                    "path": b"head-%d-%04x" % (i, p),
                    "product_string": "MCHOSE V9 Turbo+"})
    if with_dongle:
        for n, (i, p, u) in enumerate(dongle):
            out.append({"vendor_id": 0x3837, "product_id": M.HEADSET_DONGLE_PID,
                        "interface_number": i, "usage_page": p, "usage": u,
                        "path": b"dongle-%d-%04x" % (i, p),
                        "product_string": "V9 Turbo"})
    return out


class HeadsetTest(unittest.TestCase):
    def setUp(self):
        self._saved = (M.hid, M.hidlist, M.time)
        self.now = [1000.0]
        M.time = types.SimpleNamespace(time=lambda: self.now[0], sleep=lambda s: None)
        self.provider = M.MchoseProvider()
        self.order = []

    def tearDown(self):
        M.hid, M.hidlist, M.time = self._saved

    def poll(self, entries, cols):
        R_hid = types.SimpleNamespace(device=fake_device_class(cols, self.order))
        R_hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [d for d in entries if d["vendor_id"] == vid])
        M.hid, M.hidlist = R_hid, R_hidlist
        return self.provider.poll()

    # ---------------------------------------------------------------- parsing

    def test_an_awake_audio_reply_reads_100(self):
        self.assertEqual(M.parse_audio(audio_reply()), (100, False))

    def test_a_charging_audio_reply_reads_charging(self):
        self.assertEqual(M.parse_audio(audio_reply(percent=80, charging=True)), (80, True))

    def test_a_sleeping_audio_reply_is_a_none_level(self):
        self.assertEqual(M.parse_audio(audio_reply(sleep_state=0)), (None, False))

    def test_an_audio_checksum_is_checked_when_on(self):
        self.assertEqual(M.parse_audio(audio_reply(flags=1)), (100, False))
        self.assertIsNone(M.parse_audio(audio_reply(flags=1, corrupt_checksum=True)))

    def test_other_audio_commands_are_refused(self):
        self.assertIsNone(M.parse_audio(audio_reply(cmd=0x0C)))
        self.assertIsNone(M.parse_audio(audio_reply(cmd_type=(0x00, 0x04))))   # a notify

    def test_an_audio_level_above_100_is_refused(self):
        self.assertIsNone(M.parse_audio(audio_reply(percent=101)))

    def test_not_an_audio_frame(self):
        self.assertIsNone(M.parse_audio([]))
        self.assertIsNone(M.parse_audio([0x55, 0x65, 0x64, 0x02]))
        self.assertIsNone(M.parse_audio([0x11, 0xAA, 0x01, 0, 3, 0, 1, 0x0B, 1, 100, 0]))

    def test_a_status_reply_reads_discharging_charging_and_full(self):
        self.assertEqual(M.parse_status_55(status_reply(level=64, state=2)), (64, False))
        self.assertEqual(M.parse_status_55(status_reply(level=64, state=3)), (64, True))
        self.assertEqual(M.parse_status_55(status_reply(level=100, state=4)), (100, False))

    def test_a_sleeping_status_reply_is_a_none_level(self):
        self.assertEqual(M.parse_status_55(status_reply(level=0, state=M.STATUS_ASLEEP)),
                         (None, False))

    def test_an_unknown_status_state_is_refused(self):
        self.assertIsNone(M.parse_status_55(status_reply(state=9)))

    def test_a_status_level_above_100_is_refused(self):
        self.assertIsNone(M.parse_status_55(status_reply(level=101)))

    # ---------------------------------------------------------------- transport

    def test_only_the_two_status_requests_are_ever_written(self):
        entries = headset_entries()
        head = next(e for e in entries if e["product_id"] == M.HEADSET_BODY_PID
                    and e["usage_page"] == 0xFF90)
        cols = {e["path"]: FakeCollection(refuse=True) for e in entries}
        cols[head["path"]] = FakeCollection([audio_reply(), audio_reply()])
        self.poll(entries, cols)
        allowed = {bytes([M.AUDIO_REPORT]) + M.AUDIO_REQUEST,
                   bytes([M.AUDIO_REPORT]) + M.STATUS_REQUEST}
        wrote = cols[head["path"]].writes
        self.assertTrue(wrote, "the headset must have been asked")
        for w in wrote:
            self.assertIn(w, allowed)
        self.assertEqual(wrote[0], bytes([M.AUDIO_REPORT]) + M.AUDIO_REQUEST)
        self.assertEqual(len(wrote[0]), 64)

    def test_the_headset_answers_one_icon_named_for_the_product(self):
        entries = headset_entries()
        head = next(e for e in entries if e["product_id"] == M.HEADSET_BODY_PID
                    and e["usage_page"] == 0xFF90)
        cols = {e["path"]: FakeCollection(refuse=True) for e in entries}
        cols[head["path"]] = FakeCollection([audio_reply()])
        out = self.poll(entries, cols)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual((s.key, s.name, s.level, s.charging, s.online, s.source, s.kind),
                         ("mchose:3837:6008", "MCHOSE V9 Turbo+", 100, False, True,
                          "mchose", "headset"))

    def test_a_refusing_collection_does_not_stop_the_next(self):
        entries = headset_entries()
        head = next(e for e in entries if e["product_id"] == M.HEADSET_BODY_PID
                    and e["usage_page"] == 0xFF82)
        cols = {e["path"]: FakeCollection(refuse=True) for e in entries}
        cols[head["path"]] = FakeCollection([audio_reply(percent=42)])
        out = self.poll(entries, cols)
        self.assertEqual(out[0].level, 42)

    def test_the_status_channel_answers_when_the_audio_frame_is_silent(self):
        entries = headset_entries(with_dongle=False)
        head = next(e for e in entries if e["usage_page"] == 0xFF90)
        cols = {e["path"]: FakeCollection() for e in entries}
        # both AA attempts read silence, then the '65 01' frame is answered
        cols[head["path"]] = FakeCollection([None, None, status_reply(level=77, state=3)])
        out = self.poll(entries, cols)
        self.assertEqual((out[0].level, out[0].charging), (77, True))

    def test_the_dongle_alone_works_on_its_own_page(self):
        entries = headset_entries(with_dongle=True)
        dongle = next(e for e in entries if e["product_id"] == M.HEADSET_DONGLE_PID)
        cols = {e["path"]: FakeCollection(refuse=True) for e in entries}
        cols[dongle["path"]] = FakeCollection([audio_reply(percent=55)])
        out = self.poll(entries, cols)
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].key, out[0].level), ("mchose:3837:6008", 55))

    def test_an_asleep_headset_shows_nothing_until_it_has_been_heard(self):
        entries = headset_entries()
        head = next(e for e in entries if e["product_id"] == M.HEADSET_BODY_PID
                    and e["usage_page"] == 0xFF90)
        cols = {e["path"]: FakeCollection(refuse=True) for e in entries}
        cols[head["path"]] = FakeCollection([audio_reply(sleep_state=0)])
        out = self.poll(entries, cols)
        self.assertEqual(out, [])

    def test_silence_keeps_the_last_level_greyed_out(self):
        entries = headset_entries()
        head = next(e for e in entries if e["product_id"] == M.HEADSET_BODY_PID
                    and e["usage_page"] == 0xFF90)
        cols = {e["path"]: FakeCollection(refuse=True) for e in entries}
        cols[head["path"]] = FakeCollection([audio_reply(percent=90)])
        self.poll(entries, cols)
        self.now[0] += 60
        out = self.poll(entries, {e["path"]: FakeCollection() for e in entries})
        self.assertEqual((out[0].level, out[0].online), (90, False))

    # ---------------------------------------------------------------- keys

    def test_the_headset_pair_shares_one_key(self):
        self.assertEqual(M.device_key(0x3837, M.HEADSET_BODY_PID), "mchose:3837:6008")
        self.assertEqual(M.device_key(0x3837, M.HEADSET_DONGLE_PID), "mchose:3837:6008")

    def test_other_mchose_devices_get_a_key_of_their_own(self):
        self.assertEqual(M.device_key(0x5253, 0x1020), "mchose")
        self.assertEqual(M.device_key(0x3837, 0x3033), "mchose:3837:3033")


if __name__ == "__main__":
    unittest.main()
