"""Tests for the Turtle Beach Stealth Pro II provider. No hardware: the transmitter is a
fake that answers with the frames of the Swarm II capture in issue #173, byte for byte.

The conversation the provider has is the capture's: the "SInf" session opener first
(packet 150; the device answers with its whole state dump within about 30 ms), then the
GSI ask (packet 616) whose answer (packets 696-702) carries key 240 = 86. What a
record ask without the opener gets is what both test builds on the reporter's unit saw:
idle frames only (#173).

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from providers import turtlebeach  # noqa: E402

PATH = b"\\\\?\\hid#vid_10f5&pid_229b&mi_03&col02#x"

# The transmitter's answer to the GSI request, as captured: four 62-byte input reports,
# each framed 07 <len> 00 <len bytes>. The record's envelope (05 5b .. prefix, the
# 61 19 62 32 6a 21 01 ff marker) wraps the JSON.
GSI_REPLY = [
    bytes.fromhex("073b00055bd000029968454414040000"
                  "0000000000611962326a2101ff7b224f"
                  "52223a22475349222c224b5650223a7b"
                  "22323030223a2231222c22323130"),
    bytes.fromhex("073b00223a2230222c22323230223a22"
                  "4d792048656164736574222c22323330"
                  "223a2232222c22323430223a22383622"
                  "2c22323530223a2230222c223236"),
    bytes.fromhex("073b0030223a22313030222c22323730"
                  "223a2230222c22323830223a2231222c"
                  "22323930223a2232222c22326130223a"
                  "223430222c22326230223a223630"),
    bytes.fromhex("072300222c22326330223a223530222c"
                  "22326430223a223738222c2232653022"
                  "3a2231227d7d00000000000000000000"
                  "0000000000000000000000000000"),
]

# A key update the transmitter pushed by itself in the capture:
# {"UP":"GSI","KVP":{"290":"2"}} - no 240/220 in it.
PUSH_290 = bytes.fromhex("073b00000000000061186232ef2101ff"
                         "7b225550223a22475349222c224b5650"
                         "223a7b22323930223a2232227d7d055b"
                         "34000299584500b0030000000000")

EMPTY = bytes([0x07] + [0] * 61)


def frame(payload: bytes) -> bytes:
    """A reply as the transmitter frames one: 07 <len> 00 <payload>, padded to 62."""
    return bytes([0x07, len(payload), 0x00]) + payload + bytes(62 - 3 - len(payload))


# The session opener and the GSI ask, byte for byte from the capture (packets 150 and
# 616): output report 6, 62 bytes. Byte 1 counts the 24 content bytes from byte 3 up to
# and including the record name at bytes 21-26; the tokens are the capture's.
CAPTURE_SINF = bytes.fromhex(
    "061800055a140002994801c04a010000"
    "0000000000610053496e660000000000"
    "00000000000000000000000000000000"
    "0000000000000000000000000000")
CAPTURE_GSI = bytes.fromhex(
    "061800055a1400029948014414040000"
    "00000000006100534753490000000000"
    "00000000000000000000000000000000"
    "0000000000000000000000000000")

# The first frames of the state dump the SInf ask answers with (packets 151 and 154):
# key updates, none of them carrying 240.
SINF_DUMP = [frame(b"\x00\x00\x00\x00\x00\x61\x18\x62\x32\xef\x21\x01\xff"
                   b'{"UP":"GSI","KVP":{"290":"2"}}'),
             frame(b"\x00\x00\x00\x61\x15\x62\x32\xef\x21\x01\xff"
                   b'{"UP":"Inf","KVP":{"140":"1"}}')]


class FakeTransmitter:
    """Answers per (open, write) conversation.

    queues[(opens, write_n)] is what that conversation's reads return, in order; after
    it runs out (or when there is no entry) the reads are idle frames. The queues are
    indexed, never popped, so a test can point several conversations at the same list
    without draining it. written keeps every request in order, across opens.
    """

    def __init__(self, queues=None):
        self.queues = dict(queues or {})
        self.pos = {}
        self.written = []
        self.opens = 0
        self.write_n = 0

    def open_path(self, path):
        self.opens += 1
        self.write_n = 0

    def write(self, data):
        self.write_n += 1
        self.written.append(bytes(data))
        return len(data)

    def get_input_report(self, report_id, size):
        key = (self.opens, self.write_n)
        q = self.queues.get(key) or []
        i = self.pos.get(key, 0)
        self.pos[key] = i + 1
        return q[i] if i < len(q) else EMPTY

    def close(self):
        pass


def ifaces(pid=0x229B, serial="SN123", usage_page=0xFF13, usage=0x0001):
    return [{"product_id": pid, "serial_number": serial, "path": PATH,
             "usage_page": usage_page, "usage": usage, "interface_number": 3,
             "product_string": "Stealth Pro II Xbox"}]


class TurtleBeachTests(unittest.TestCase):
    def setUp(self):
        self.transmitter = FakeTransmitter()
        self.infos = ifaces()
        for p in (mock.patch.object(turtlebeach.hid, "device", lambda: self.transmitter),
                  mock.patch.object(turtlebeach.hidlist, "enumerate", lambda vid: self.infos),
                  mock.patch.object(turtlebeach.time, "sleep", lambda s: None)):
            p.start()
            self.addCleanup(p.stop)
        self.p = turtlebeach.TurtleBeachProvider()

    def test_the_capture_answers_86_percent(self):
        self.transmitter.queues = {(1, 1): SINF_DUMP, (1, 2): GSI_REPLY}
        res = self.p.poll()
        self.assertEqual([(s.key, s.name, s.level, s.charging, s.online, s.kind) for s in res],
                         [("turtlebeach:stealthpro2", "Turtle Beach Stealth Pro II",
                           86, False, True, "headset")])

    def test_the_asks_are_the_captures_requests_byte_for_byte(self):
        self.assertEqual(turtlebeach.ASK_SINF, CAPTURE_SINF)
        self.assertEqual(turtlebeach.ASK_GSI, CAPTURE_GSI)
        self.assertEqual(CAPTURE_SINF[21:27], b"a\x00SInf")      # the record names
        self.assertEqual(CAPTURE_GSI[21:27], b"a\x00SGSI")
        self.assertEqual((CAPTURE_SINF[1], CAPTURE_GSI[1]), (24, 24))   # bytes 3..26
        self.assertEqual(CAPTURE_GSI[12:14], bytes([0x14, 0x04]))       # the capture token
        # the fresh-token variant differs in the token bytes alone
        self.assertEqual(turtlebeach.ASK_GSI_FRESH[:12], CAPTURE_GSI[:12])
        self.assertEqual(turtlebeach.ASK_GSI_FRESH[12:14], bytes([0x11, 0x01]))
        self.assertEqual(turtlebeach.ASK_GSI_FRESH[14:], CAPTURE_GSI[14:])

    def test_the_session_opener_is_sent_first_then_the_gsi_ask(self):
        self.p.poll()
        self.assertEqual(self.transmitter.written[:2], [CAPTURE_SINF, CAPTURE_GSI])

    def test_the_fresh_token_is_tried_when_the_captured_one_gets_nothing(self):
        # only the third write is answered: the captured token got idle frames
        self.transmitter.queues = {(1, 3): GSI_REPLY}
        res = self.p.poll()
        self.assertEqual([s.level for s in res], [86])
        self.assertEqual(self.transmitter.written[2], turtlebeach.ASK_GSI_FRESH)
        self.assertTrue(any("GSI fresh token" in line for line in self.p.diagnostics()))

    def test_the_dump_alone_can_carry_the_level(self):
        self.transmitter.queues = {(1, 1): [frame(b'{"UP":"GSI","KVP":{"240":"72"}}')]}
        res = self.p.poll()
        self.assertEqual([s.level for s in res], [72])
        self.assertEqual(len(self.transmitter.written), 1)       # no record ask needed

    def test_the_frames_of_the_capture_strip_and_parse(self):
        blob = turtlebeach.strip_frames(GSI_REPLY)
        self.assertTrue(blob.endswith(
            b'{"OR":"GSI","KVP":{"200":"1","210":"0","220":"My Headset","230":"2",'
            b'"240":"86","250":"0","260":"100","270":"0","280":"1","290":"2",'
            b'"2a0":"40","2b0":"60","2c0":"50","2d0":"78","2e0":"1"}}'))
        self.assertEqual(turtlebeach.status_from_documents(turtlebeach.json_documents(blob)),
                         (86, "My Headset"))

    def test_empty_replies_between_the_record_are_tolerated(self):
        self.transmitter.queues = {(1, 2): [EMPTY, GSI_REPLY[0], EMPTY, GSI_REPLY[1],
                                            EMPTY, GSI_REPLY[2], GSI_REPLY[3]]}
        self.assertEqual([s.level for s in self.p.poll()], [86])

    def test_a_pushed_update_carries_the_level_too(self):
        self.transmitter.queues = {(1, 2): [frame(b'{"UP":"GSI","KVP":{"240":"84"}}')]}
        self.assertEqual([s.level for s in self.p.poll()], [84])

    def test_a_push_without_the_level_is_not_a_reading(self):
        self.transmitter.queues = {(1, 1): [PUSH_290] * 2, (1, 2): [PUSH_290] * 2,
                                   (1, 3): [PUSH_290] * 2}
        res = self.p.poll()
        self.assertEqual([(s.level, s.name) for s in res],
                         [(None, "Turtle Beach Stealth Pro II")])
        self.assertTrue(any("no GSI record" in line for line in self.p.diagnostics()))

    def test_a_muted_transmitter_shows_the_headset_without_a_level(self):
        res = self.p.poll()
        self.assertEqual([(s.level, s.online) for s in res], [(None, True)])

    def test_an_unknown_product_id_is_not_talked_to(self):
        self.infos = ifaces(pid=0x229C)
        self.assertEqual(self.p.poll(), [])
        self.assertEqual(self.transmitter.written, [])
        self.assertTrue(any("not a known model" in line for line in self.p.diagnostics()))

    def test_a_device_without_a_vendor_collection_gets_no_write(self):
        self.infos = ifaces(usage_page=0x000C)
        res = self.p.poll()
        self.assertEqual([(s.level, s.online) for s in res], [(None, True)])
        self.assertEqual(self.transmitter.written, [])
        self.assertTrue(any("no vendor collection" in line for line in self.p.diagnostics()))

    def test_the_cable_and_the_transmitter_share_one_icon(self):
        # both attached: the transmitter answers first (pid order) and there is one icon
        self.transmitter.queues = {(1, 2): GSI_REPLY}
        self.infos = ifaces() + ifaces(pid=0x229E, serial="SN456")
        res = self.p.poll()
        self.assertEqual([(s.key, s.name, s.level) for s in res],
                         [("turtlebeach:stealthpro2", "Turtle Beach Stealth Pro II", 86)])

    def test_the_cable_alone_is_enough(self):
        self.transmitter.queues = {(1, 2): GSI_REPLY}
        self.infos = ifaces(pid=0x229E, serial="SN456")
        res = self.p.poll()
        self.assertEqual([(s.key, s.level, s.kind) for s in res],
                         [("turtlebeach:stealthpro2", 86, "headset")])

    def test_a_silent_pair_shows_the_headset_without_a_level(self):
        self.infos = ifaces() + ifaces(pid=0x229E, serial="SN456")
        res = self.p.poll()
        self.assertEqual([(s.key, s.level, s.online) for s in res],
                         [("turtlebeach:stealthpro2", None, True)])

    def test_a_silent_transmitter_still_shows_the_cables_reading(self):
        # the transmitter is muted (the headset is off its link) but the cable answers
        self.transmitter.queues = {(2, 2): GSI_REPLY}
        self.infos = ifaces() + ifaces(pid=0x229E, serial="SN456")
        res = self.p.poll()
        self.assertEqual([(s.key, s.level) for s in res],
                         [("turtlebeach:stealthpro2", 86)])


class ParserTests(unittest.TestCase):
    def test_strip_frames_skips_short_and_unframed_replies(self):
        self.assertEqual(turtlebeach.strip_frames([b"", b"\x07\x3b"]),
                         b"")
        # a length byte longer than the frame is clamped, not trusted
        self.assertEqual(turtlebeach.strip_frames([bytes([0x07, 0xFF, 0x00, 0x41])]), b"A")

    def test_json_documents_finds_nested_and_multiple_documents(self):
        blob = (b"\x05\x5b\xd0\x00 junk \x61\x19"
                b'{"OR":"GSI","KVP":{"240":"55"}}'
                b"\x00\x62\x32 garbage \xff"
                b'{"UP":"BT","KVP":{"320":"-40"}}')
        self.assertEqual(turtlebeach.json_documents(blob),
                         [{"OR": "GSI", "KVP": {"240": "55"}},
                          {"UP": "BT", "KVP": {"320": "-40"}}])

    def test_a_control_byte_abandons_a_candidate(self):
        # the envelope byte inside what looks like a document -> not a document
        blob = b'{"OR":"GSI",\x00"KVP":{}} {"OR":"GSI","KVP":{"240":"77"}}'
        docs = turtlebeach.json_documents(blob)
        self.assertEqual(docs, [{"OR": "GSI", "KVP": {"240": "77"}}])

    def test_out_of_range_and_non_numeric_levels_are_refused(self):
        self.assertEqual(turtlebeach.status_from_documents([{"OR": "GSI", "KVP": {"240": "101"}}]),
                         (None, None))
        self.assertEqual(turtlebeach.status_from_documents([{"OR": "GSI", "KVP": {"240": "x"}}]),
                         (None, None))
        self.assertEqual(turtlebeach.status_from_documents([{"OR": "GSI", "KVP": {"240": "0"}}]),
                         (0, None))

    def test_named_and_other_documents_are_ignored(self):
        docs = [{"OR": "Inf", "KVP": {"100": "10F5"}},
                {"OR": "TX1", "KVP": {"400": {"info": ["2", "17"]}}},
                {"OR": "GSI", "KVP": {"220": "Desk", "240": "42"}}]
        self.assertEqual(turtlebeach.status_from_documents(docs), (42, "Desk"))


if __name__ == "__main__":
    unittest.main()
