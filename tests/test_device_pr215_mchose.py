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

The X9 (3837:6045) is M HUB's BLK audio profile, whose battery read is the
'65 01' frame alone; its collections and the reply ``55 65 28 02`` (40 %,
discharging) were measured on a real X9 on its dongle.

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


# the X9's collections as its own report descriptor lists them, all on interface 0
X9_PAGES = (0xFF21, 0xFF22, 0x000C, 0x000B, 0xFF90, 0xFF82)
X9_REPLY = list(bytes.fromhex("55 65 28 02")) + [0] * 60     # measured: 40 %, discharging


def x9_entries():
    return [{"vendor_id": 0x3837, "product_id": M.X9_PID, "interface_number": 0,
             "usage_page": p, "usage": 0x01, "path": b"x9-%04x" % p,
             "product_string": "MCHOSE X9"} for p in X9_PAGES]


def x9_cable_entries():
    """The X9 on its cable (3837:6044), as its own report descriptor lists it."""
    return [{"vendor_id": 0x3837, "product_id": M.X9_CABLE_PID, "interface_number": 0,
             "usage_page": p, "usage": 0x01, "path": b"x9c-%04x" % p,
             "product_string": "MCHOSE X9 HEADSET"}
            for p in (0xFF22, 0x000C, 0x000B, 0xFF90, 0xFF82)]


class GoneCollection(FakeCollection):
    """A collection whose device is unplugged between the write and the read."""

    def read(self, n=0, timeout=0):
        raise OSError("The device is not connected.")


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
        self.assertEqual(M.device_key(0x3837, 0x3033), "mchose:3837")
        # Keys that existed before this PR must not move: custom names, hidden icons and
        # battery history live on them (#215 review, @ahmedkhursheed23).
        self.assertEqual(M.device_key(0xA8A5, 0x2255), "mchose:a8a5")    # G7
        self.assertEqual(M.device_key(0x3837, 0x100B), "mchose:3837")    # A7 V2 Ultra

    # ---------------------------------------------------------------- X9

    def x9_poll(self, ff90, others=FakeCollection):
        entries = x9_entries()
        cols = {e["path"]: others() for e in entries}
        cols[b"x9-ff90"] = ff90
        return self.poll(entries, cols), cols

    def test_the_x9_reads_the_measured_reply(self):
        out, _ = self.x9_poll(FakeCollection([X9_REPLY]))
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.source, s.kind)
                          for s in out],
                         [("mchose:3837:6045", "MCHOSE X9", 40, False, True,
                           "mchose", "headset")])

    def test_the_x9_is_only_sent_the_blk_battery_read_on_ff90(self):
        _, cols = self.x9_poll(FakeCollection())          # silent everywhere
        self.assertEqual(self.order, [b"x9-ff90"])        # no other collection is opened
        wrote = [w for c in cols.values() for w in c.writes]
        self.assertEqual(len(wrote), M.HEADSET_ATTEMPTS)
        self.assertEqual(set(wrote), {bytes([M.AUDIO_REPORT]) + M.STATUS_REQUEST})
        self.assertEqual(len(wrote[0]), 64)
        self.assertEqual(wrote[0][:3], b"\x55\x65\x01")

    def test_a_second_ff90_collection_is_never_written_to(self):
        # listed first, so a page-only match would pick it
        other = dict(x9_entries()[0], usage_page=0xFF90, usage=0x0002, path=b"x9-ff90-2")
        entries = [other] + x9_entries()
        out = self.poll(entries, {e["path"]: FakeCollection([X9_REPLY]) for e in entries})
        self.assertEqual(self.order, [b"x9-ff90"])
        self.assertEqual([s.level for s in out], [40])

    def test_the_x9_charging_full_and_level_bounds(self):
        for level, state, charging in ((0, 2, False), (100, 4, False), (64, 3, True)):
            self.provider = M.MchoseProvider()
            out, _ = self.x9_poll(FakeCollection([status_reply(level=level, state=state)]))
            self.assertEqual([(s.level, s.charging) for s in out], [(level, charging)])

    def test_an_asleep_x9_shows_nothing(self):
        out, _ = self.x9_poll(FakeCollection([status_reply(level=0, state=M.STATUS_ASLEEP)]))
        self.assertEqual(out, [])

    def test_short_garbage_and_out_of_range_x9_replies_are_refused(self):
        for bad in ([0x55, 0x65, 0x28], [0x55, 0x66, 0x28, 0x02], [0x41, 0x65, 0x28, 0x02],
                    [0xFF] * 64, status_reply(level=101), status_reply(state=0)):
            self.provider = M.MchoseProvider()
            out, _ = self.x9_poll(FakeCollection([bad, bad]))
            self.assertEqual(out, [], bad[:4])

    def test_an_x9_unplugged_mid_poll_does_not_stop_the_poll(self):
        entries = x9_entries() + headset_entries(with_dongle=True)
        cols = {e["path"]: FakeCollection(refuse=True) for e in entries}
        cols[b"x9-ff90"] = GoneCollection()
        dongle = next(e for e in entries if e["product_id"] == M.HEADSET_DONGLE_PID)
        cols[dongle["path"]] = FakeCollection([audio_reply(percent=55)])
        out = self.poll(entries, cols)
        self.assertEqual([(s.key, s.level) for s in out], [("mchose:3837:6008", 55)])

    def test_an_x9_that_cannot_be_opened_shows_nothing(self):
        class Gone(dict):
            def __missing__(self, path):
                raise OSError("open failed")
        out = self.poll(x9_entries(), Gone())
        self.assertEqual(out, [])

    def test_a_charging_x9_keeps_its_level_greyed_and_its_cable_is_never_opened(self):
        self.x9_poll(FakeCollection([X9_REPLY]))
        self.now[0] += 60
        # measured: on its cable the dongle goes silent and 3837:6044 appears
        entries = x9_entries() + x9_cable_entries()
        out = self.poll(entries, {e["path"]: FakeCollection([status_reply(state=3)] * 2)
                                  if e["product_id"] == M.X9_CABLE_PID else FakeCollection()
                                  for e in entries})
        self.assertEqual([(s.key, s.level, s.charging, s.online) for s in out],
                         [("mchose:3837:6045", 40, False, False)])
        self.assertEqual([p for p in self.order if p.startswith(b"x9c-")], [])

    def test_the_x9_cable_alone_is_left_alone(self):
        entries = x9_cable_entries()
        out = self.poll(entries, {e["path"]: FakeCollection([status_reply()]) for e in entries})
        self.assertEqual((out, self.order), ([], []))
        # nor does it lend its name to the 0x3837 mice' shared key
        self.assertNotIn("mchose:3837", self.provider._names)

    def test_the_x9_keeps_a_key_apart_from_the_3837_mice(self):
        self.assertEqual(M.device_key(0x3837, M.X9_PID), "mchose:3837:6045")
        self.assertNotEqual(M.device_key(0x3837, M.X9_PID), M.device_key(0x3837, 0x100B))


if __name__ == "__main__":
    unittest.main()
