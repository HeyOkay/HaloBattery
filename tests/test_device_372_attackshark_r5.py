"""Attack Shark R5 Ultra feature-report battery read (373E:0047). No hardware."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import attackshark_r5 as A  # noqa: E402


def reply(level=87, charging=0):
    # The first layout the reference accepts: 0xA1 at [1], command at [4]/[6],
    # then (level, charging) at [7]/[8].
    return [0x00, 0xA1, 0x00, 0x00, 0x02, 0x00, 0x83, level, charging] + [0] * 56


class FakeDev:
    def __init__(self, frames=None, write_result=None):
        self.frames = list(frames or [])
        self.write_result = write_result
        self.writes = []
        self.reads = 0
        self.closed = False

    def open_path(self, path):
        self.path = path

    def send_feature_report(self, data):
        self.writes.append(list(data))
        return len(data) if self.write_result is None else self.write_result

    def get_feature_report(self, report_id, length):
        self.reads += 1
        return self.frames.pop(0) if self.frames else []

    def close(self):
        self.closed = True


INFO = {"product_id": A.PID, "usage_page": 0xFFA0, "usage": 0x0001,
        "interface_number": 1, "path": b"r5", "product_string": "R5 Ultra Mouse 2.4G"}


class R5Test(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist)

    def tearDown(self):
        A.hid, A.hidlist = self._saved

    def poll(self, dev, infos=None):
        A.hid = types.SimpleNamespace(device=lambda: dev)
        A.hidlist = types.SimpleNamespace(enumerate=lambda vid: list(infos or [INFO]))
        return A.AttackSharkR5Provider()

    def test_request_is_the_documented_feature_command(self):
        self.assertEqual(A.make_request()[:7], [0, 0, 0, 2, 2, 0, 0x83])
        self.assertEqual(len(A.make_request()), 65)

    def test_level_and_charging(self):
        dev = FakeDev([reply(73, 1)])
        [st] = self.poll(dev).poll()
        self.assertEqual((st.level, st.charging, st.kind, st.name),
                         (73, True, "mouse", "Attack Shark R5 Ultra"))
        self.assertEqual(dev.writes[0], A.make_request())

    def test_the_other_reply_layout_is_accepted(self):
        # 0xA1 at [0], command at [3]/[5]: level at [6], charging at [7].
        raw = [0xA1, 0, 0, 2, 0, 0x83, 66, 0] + [0] * 57
        [st] = self.poll(FakeDev([raw])).poll()
        self.assertEqual((st.level, st.charging), (66, False))

    def test_unknown_reply_is_not_a_level(self):
        self.assertIsNone(A.parse_reply([0, 1, 2, 3]))
        self.assertIsNone(A.parse_reply([0xA1, 0, 0, 2, 0, 0x83, 101, 0]))

    def test_full_frames_refuse_out_of_range_levels_and_invalid_charge_pairs(self):
        for first, second in ((0, 101), (0, 255), (1, 255), (101, 0), (255, 0), (255, 1),
                              (2, 73), (73, 2), (255, 255)):
            for prefix in ([0xA1, 0, 0, 2, 0, 0x83], [0, 0xA1, 0, 2, 2, 0, 0x83]):
                for length in (9, 65):
                    frame = prefix + [first, second]
                    frame += [0] * (length - len(frame))
                    with self.subTest(pair=(first, second), prefix=prefix, length=length):
                        self.assertEqual(len(frame), length)
                        self.assertIsNone(A.parse_reply(frame))

    def test_valid_boundary_levels_in_both_documented_orders_and_layouts(self):
        pairs = ((0, 0, (0, False)), (1, 0, (0, True)), (1, 1, (1, True)),
                 (0, 100, (100, False)), (1, 100, (100, True)),
                 (100, 0, (100, False)), (100, 1, (100, True)))
        for first, second, expected in pairs:
            for prefix in ([0xA1, 0, 0, 2, 0, 0x83], [0, 0xA1, 0, 2, 2, 0, 0x83]):
                with self.subTest(pair=(first, second), prefix=prefix):
                    frame = prefix + [first, second]
                    self.assertEqual(A.parse_reply(frame + [0] * (65 - len(frame))), expected)

    def test_invalid_full_report_cannot_publish_a_live_zero(self):
        raw = bytes.fromhex("00a1000202008300ff") + bytes(56)
        dev = FakeDev([raw, raw])
        self.assertEqual(self.poll(dev).poll(), [])
        self.assertTrue(dev.closed)

    def test_garbage_full_frames_and_non_byte_values_are_refused(self):
        for raw in (None, True, "not a HID report", {}, reply(-1, 0), reply(256, 0),
                    reply(None, 0), [0x99] + [0] * 64, bytes(65)):
            with self.subTest(raw=raw):
                self.assertIsNone(A.parse_reply(raw))

    def test_the_reporters_reply_reads_91_not_charging(self):
        # #372, test build 3 on the reporter's receiver (interface 2, ffff:0000):
        # 00 a1 00 02 02 00 83 00 5b -> 91 %, not charging. This firmware sends
        # the charge byte before the level, which _normalize accepts.
        raw = bytes.fromhex("00a10002020083005b000000") + bytes(53)
        self.assertEqual(A.parse_reply(list(raw)), (91, False))

    def test_no_reply_shows_nothing(self):
        provider = self.poll(FakeDev([]))
        self.assertEqual(provider.poll(), [])
        self.assertTrue(any("no battery reply" in line for line in provider.diagnostics()))

    def test_only_the_r5_pid_is_opened(self):
        infos = [dict(INFO, product_id=0x0046, usage_page=0x0001, usage=0x0002),
                 dict(INFO, product_id=0x0046, usage_page=0x000C, usage=0x0001)]
        dev = FakeDev([reply()])
        self.assertEqual(self.poll(dev, infos).poll(), [])
        self.assertEqual(dev.writes, [])

    def test_the_second_collection_is_tried(self):
        first = dict(INFO, path=b"first")
        second = dict(INFO, path=b"second", usage_page=0xFFFF)
        dev = FakeDev([[], [], reply(55, 0)])
        # FakeDev has one frame list: the first collection gets two empty reads,
        # the second asks again and gets the real reply.
        [st] = self.poll(dev, [first, second]).poll()
        self.assertEqual(st.level, 55)
        self.assertEqual(len(dev.writes), 3)

    def test_the_root_ffff0000_collection_is_included(self):
        root = dict(INFO, usage_page=0xFFFF, usage=0x0000,
                    interface_number=2, path=b"root")
        dev = FakeDev([reply(64, 1)])
        [st] = self.poll(dev, [root]).poll()
        self.assertEqual((st.level, st.charging), (64, True))
        self.assertEqual(len(dev.writes), 1)


if __name__ == "__main__":
    unittest.main()
