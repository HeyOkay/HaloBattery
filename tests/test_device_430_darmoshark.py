"""Darmoshark N5 / MOJI 1K NRF receiver battery read (#430)."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import darmoshark as D  # noqa: E402


def response(level=67, v2=False):
    frame = bytearray(64)
    frame[0] = D.TYPE_POWER | D.WIRELESS_FLAG
    frame[3] = D.SUB_POWER
    if v2:
        frame[6], frame[7] = 0x15, 0x19
        frame[11] = level
    else:
        frame[6] = level
    return bytes(frame)


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.writes = []
        self.closed = False

    def open_path(self, path):
        self.path = path

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, length, timeout_ms):
        return self.replies.pop(0) if self.replies else []

    def close(self):
        self.closed = True


INFO = [{"path": b"nrf", "interface_number": 3, "usage_page": D.USAGE_PAGE,
         "usage": D.USAGE, "product_id": D.RECEIVER_PID,
         "product_string": "1K NRF Receiver"}]


class QueryTests(unittest.TestCase):
    def test_the_query_is_the_openlogi_power_frame(self):
        q = D.make_query(True)
        self.assertEqual((0x41, 0x00, 129, 1), tuple(q[:4]))
        self.assertEqual(222, q[63])
        self.assertEqual(0xA1, sum(q) & 0xFF)

    def test_both_response_layouts(self):
        self.assertEqual(67, D.parse_reply(response(67)))
        self.assertEqual(42, D.parse_reply(response(42, v2=True)))

    def test_bad_lengths_and_levels_are_refused(self):
        for frame in (b"", response(101), response(101, v2=True)):
            with self.subTest(frame=frame):
                self.assertIsNone(D.parse_reply(frame))
        bad = bytearray(response(67))
        bad[3] = 2
        self.assertIsNone(D.parse_reply(bad))


class PollTests(unittest.TestCase):
    def setUp(self):
        self._saved = (D.hid, D.hidlist)

    def tearDown(self):
        D.hid, D.hidlist = self._saved

    def poll(self, replies, infos=None):
        dev = FakeDevice(replies)
        D.hid = types.SimpleNamespace(device=lambda: dev)
        D.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(INFO if infos is None else infos))
        return D.DarmosharkProvider().poll(), dev

    def test_the_receiver_reads_one_icon_without_charging(self):
        out, dev = self.poll([response(67)])
        self.assertEqual([(s.key, s.name, s.level, s.charging_known, s.kind)
                          for s in out],
                         [("darmoshark:n5", "Darmoshark N5", 67, False, "mouse")])
        self.assertEqual([0x41, 0, 129, 1], list(dev.writes[0][:4]))
        self.assertTrue(dev.closed)

    def test_v2_receiver_reads_byte_eleven(self):
        out, _ = self.poll([response(42, v2=True)])
        self.assertEqual(out[0].level, 42)

    def test_no_reply_does_not_create_an_icon(self):
        out, _ = self.poll([])
        self.assertEqual(out, [])

    def test_wrong_collection_is_not_opened(self):
        out, dev = self.poll([], [dict(INFO[0], usage_page=0x0001, usage=0x0002)])
        self.assertEqual(out, [])
        self.assertEqual(dev.writes, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
