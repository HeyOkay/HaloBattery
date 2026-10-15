"""Tests for providers/mchose.py - the A5 Pro Max addition and the V9 Turbo+ headset
part (issue #193). No hardware is needed.

The A5 exchange is the one z750sasr/mchose-a5-pro-max-web-driver documents for the
first-generation A5 Pro Max (docs/PROTOCOL.md and lib/a5-protocol.ts): 64-byte feature
reports on the vendor collection (usage page 0xFFFF), report id 0; the reference's
payload carries route 2 at its byte 2, its own length 2 at byte 3, page 0 at byte 4 and
command 0x83 at byte 5. The reply starts with the 0xA1 marker and echoes page and
command at its bytes 4 and 5, with the charging state in byte 6 and the level in byte 7
(clamped to 100, as the reference clamps it). The collection shape is the reporter's
diagnostics dump from issue #149 (2023:f013 'MCHOSE A5 2.4G': the mouse, ffa0:0001 and
ffff:0001 on interface 1, a keyboard, consumer and system collection, then ffff:0000 on
interface 2).

The V9 Turbo+ headset exchanges are the ones the headset's own software uses:

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


def answer(level=58, charging=False):
    """A reply as the reference expects it: 0xA1, ..., page, command, charge, level."""
    return ([0xA1, 0x00, 0x00, 0x00, M.A5_PAGE, M.A5_CMD_BATTERY,
             1 if charging else 0, level] + [0x00] * 56)


class FakeMchose:
    """One fake receiver for every open_path; only `answer_path` replies with 0xA1."""

    def __init__(self, level=58, charging=False, answer_path=None):
        self.level = level
        self.charging = charging
        self.answer_path = answer_path
        self.path = None
        self.sent = []                    # (path, bytes)
        self.reads = 0

    # hid.device API
    def open_path(self, path):
        self.path = path

    def close(self):
        pass

    def send_feature_report(self, data):
        self.sent.append((self.path, bytes(data)))
        return len(data)

    def get_feature_report(self, report_id, length):
        self.reads += 1
        if self.path == self.answer_path:
            return [M.A5_REPORT_ID] + answer(self.level, self.charging)
        return [0x00] * length               # silent: no 0xA1 marker in it


def entries(pid=0xF013, prefix=b"a5"):
    # the reporter's dump for 2023:f013, in its order
    shape = [(0, 0x0001, 0x0002),   # the mouse: never written to
             (1, 0xFFA0, 0x0001),
             (1, 0xFFFF, 0x0001),   # the vendor page the reference prefers
             (1, 0x0001, 0x0006),
             (1, 0x000C, 0x0001),
             (1, 0x0001, 0x0080),
             (2, 0xFFFF, 0x0000)]
    return [{"vendor_id": 0x2023, "product_id": pid, "interface_number": i,
             "usage_page": p, "usage": u,
             "path": prefix + b"-%d-%04x-%02x" % (i, p, u),
             "product_string": "MCHOSE A5 2.4G"}
            for i, p, u in shape]


FF_F1 = entries()[2]["path"]         # ffff:0001 on interface 1
FF_F2 = entries()[6]["path"]         # ffff:0000 on interface 2
FFA0 = entries()[1]["path"]


class ParseTest(unittest.TestCase):
    def test_the_request_frame_is_the_references_plus_the_report_id(self):
        self.assertEqual(M.make_a5_request(),
                         [0x00, 0x00, 0x00, 0x02, 0x02, 0x00, 0x83] + [0x00] * 58)
        self.assertEqual(len(M.make_a5_request()), 65)

    def test_the_answer_reads_level_and_charging(self):
        self.assertEqual(M.parse_a5(answer(58, True)), (58, True))
        self.assertEqual(M.parse_a5(answer(58, False)), (58, False))

    def test_the_report_id_may_lead_the_answer(self):
        self.assertEqual(M.parse_a5([0x00] + answer(58, True)), (58, True))

    def test_a_level_above_100_is_clamped_as_the_reference_clamps_it(self):
        self.assertEqual(M.parse_a5(answer(200, False)), (100, False))

    def test_another_page_or_command_is_refused(self):
        wrong = answer()
        wrong[5] = 0x84
        self.assertIsNone(M.parse_a5(wrong))
        wrong = answer()
        wrong[4] = 0x02
        self.assertIsNone(M.parse_a5(wrong))

    def test_a_silent_or_short_answer_is_refused(self):
        self.assertIsNone(M.parse_a5([0x00] * 65))
        self.assertIsNone(M.parse_a5([0xA1, 0x00, 0x00]))
        self.assertIsNone(M.parse_a5(None))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (M.hid, M.hidlist, M.time)
        self.now = [1000.0]
        M.time = types.SimpleNamespace(sleep=lambda s: None, time=lambda: self.now[0])

    def tearDown(self):
        M.hid, M.hidlist, M.time = self._saved

    def poll(self, fake, ents):
        M.hid = types.SimpleNamespace(device=lambda: fake)
        M.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [e for e in ents if e["vendor_id"] == vid])
        p = M.MchoseProvider()
        return p.poll(), p.diagnostics()

    def test_the_receiver_is_read_on_the_ffff_collection(self):
        ents = entries()
        fake = FakeMchose(level=58, charging=True, answer_path=FF_F1)
        out, diag = self.poll(fake, ents)
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("mchose:2023", "MCHOSE A5 2.4G", 58, True, True,
                           "mchose", "mouse")])
        self.assertEqual({p for p, _ in fake.sent}, {FF_F1})    # nothing else was written to
        self.assertEqual(fake.sent[0][1], bytes(M.make_a5_request()))

    def test_the_second_ffff_collection_is_tried_too(self):
        # the same reply arrives on either ffff collection depending on the model/revision
        ents = entries()
        fake = FakeMchose(level=77, charging=False, answer_path=FF_F2)
        out, _ = self.poll(fake, ents)
        self.assertEqual([s.level for s in out], [77])
        self.assertTrue(any(p == FF_F2 for p, _ in fake.sent))
        self.assertNotIn(FFA0, {p for p, _ in fake.sent})

    def test_another_2023_pid_is_left_alone(self):
        ents = entries(pid=0xF017)       # an A5 Pro receiver, not a Pro Max identity
        fake = FakeMchose(level=58, charging=False, answer_path=FF_F1)
        out, diag = self.poll(fake, ents)
        self.assertEqual(out, [])
        self.assertEqual(fake.sent, [])
        self.assertTrue(any("leaving it alone" in line for line in diag))

    def test_silence_gives_nothing_and_says_so(self):
        ents = entries()
        fake = FakeMchose(answer_path=None)
        out, diag = self.poll(fake, ents)
        self.assertEqual(out, [])
        # the 0xFFA0 sibling is not written to even when the mouse stays silent
        # (review by @ahmedkhursheed23)
        self.assertNotIn(FFA0, {p for p, _ in fake.sent})
        self.assertTrue(any("no A1 answer" in line for line in diag))


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
