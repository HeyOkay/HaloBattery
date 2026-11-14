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

    def test_no_reply_shows_nothing(self):
        provider = self.poll(FakeDev([]))
        self.assertEqual(provider.poll(), [])
        self.assertTrue(any("no battery reply" in line for line in provider.diagnostics()))

    def test_only_the_r5_vendor_collections_are_opened(self):
        infos = [dict(INFO, usage_page=0x0001, usage=0x0002),
                 dict(INFO, usage_page=0x000C, usage=0x0001)]
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


if __name__ == "__main__":
    unittest.main()
