"""Tests for the ASUS TUF Gaming H3 Wireless headset in providers/asus.py.

The frames are byte for byte the replies in two USB captures of Armoury Crate 6.0.0
(Armoury Crate at 100 % and at 75 %). No hardware is needed.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import asus as A  # noqa: E402


def frame(lsb=0x4C, msb=0x0F, state=0x03):
    """The captured battery reply; 0x0F4C = 3916 mV (Armoury Crate 75 %)."""
    r = [0xFF, 0x12, 0x05, 0xFE, 0x0C, 0x04, 0x1F, 0x0B, 0x01, state, 0x05, lsb, msb,
         0x0F, 0x00, 0x00, 0x03, 0x68, 0x10]
    return r + [0] * (64 - len(r))


# Armoury Crate's other request gets this reply on the same report; it is not a level
OTHER_REPLY = [0xFF, 0x0C, 0x05, 0xFE, 0x00, 0x04, 0x0F, 0x05, 0x81, 0xAA, 0xC6, 0x01,
               0x0F, 0x00, 0x00, 0x03, 0x68, 0x10] + [0] * 46

ENTRIES = [
    {"product_id": A.TUF_H3_PID, "interface_number": 3, "usage_page": 0x000C, "usage": 1,
     "path": b"mi03-000c", "product_string": "Hid Interface"},
    {"product_id": A.TUF_H3_PID, "interface_number": 3, "usage_page": 0xFFC0, "usage": 1,
     "path": b"mi03-ffc0", "product_string": "Hid Interface"},
    {"product_id": A.TUF_H3_PID, "interface_number": 3, "usage_page": 0xFF00, "usage": 1,
     "path": b"mi03-ff00", "product_string": "Hid Interface"},
]
LENGTHS = {b"mi03-000c": 4, b"mi03-ffc0": 64, b"mi03-ff00": 64}


class TufH3Test(unittest.TestCase):
    def setUp(self):
        self._saved = (A.hid, A.hidlist, A.feature_length, A.HEADSET_SLEEP,
                       A.TUF_H3_RECHECK_S)
        A.HEADSET_SLEEP = A.TUF_H3_RECHECK_S = 0

    def tearDown(self):
        (A.hid, A.hidlist, A.feature_length, A.HEADSET_SLEEP,
         A.TUF_H3_RECHECK_S) = self._saved

    def poll(self, replies, open_error=False, read_error=False):
        """replies: the feature reports the headset answers, one per read; the last
        one is repeated, as a headset keeps answering."""
        self.sent, self.opened = [], []
        replies = list(replies)
        test = self

        class FakeDevice:
            def open_path(self, path):
                if open_error:
                    raise OSError("device gone")
                test.opened.append(path)

            def send_feature_report(self, data):
                test.sent.append(list(data))
                return len(data)

            def get_feature_report(self, rid, n):
                if read_error:
                    raise OSError("device gone")
                if len(replies) > 1:
                    return replies.pop(0)
                return list(replies[0]) if replies else []

            def close(self):
                pass

        A.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(ENTRIES))
        A.feature_length = lambda path: LENGTHS.get(path)
        A.hid = types.SimpleNamespace(device=lambda: FakeDevice())
        self.provider = A.AsusProvider()
        return self.provider.poll()

    def test_sends_only_the_captured_read_request(self):
        self.poll([frame()])
        self.assertEqual([0xFF, 0x07, 0x00, 0xFD, 0x04, 0x0C, 0xF1, 0x02, 0x01]
                         + [0x00] * 55, self.sent[0])
        # on battery: the same read, once more for the charging check; never
        # Armoury Crate's write
        self.assertEqual([self.sent[0]] * 2, self.sent)
        self.assertEqual({b"mi03-ffc0"}, set(self.opened))  # first 64-byte feature collection

    def test_captured_frames_follow_the_li_ion_curve(self):
        for lsb, msb, level in ((0x82, 0x0F, 77),    # 3970 mV, Armoury Crate 100 %
                                (0x4C, 0x0F, 69)):   # 3916 mV, Armoury Crate 75 %
            with self.subTest(mv=(msb << 8) | lsb):
                (d,) = self.poll([frame(lsb, msb)])
                self.assertEqual(level, d.level)
                self.assertFalse(d.charging)
                self.assertTrue(d.charging_known)
        self.assertEqual("asus:tuf-gaming-h3-wireless", d.key)
        self.assertEqual("TUF Gaming H3 Wireless", d.name)
        self.assertEqual("headset", d.kind)
        self.assertIn("-> 69%", "\n".join(self.provider.diagnostics()))

    def test_level_bounds(self):
        (d,) = self.poll([frame(0x68, 0x10)])        # 4200 mV: full
        self.assertEqual(100, d.level)
        (d,) = self.poll([frame(0xB8, 0x0B)])        # 3000 mV: the lowest accepted
        self.assertEqual(0, d.level)

    def test_out_of_range_voltage_is_refused(self):
        for lsb, msb in ((0xB7, 0x0B), (0x95, 0x11), (0x00, 0x00), (0xFF, 0xFF)):
            with self.subTest(mv=(msb << 8) | lsb):
                self.assertEqual([], self.poll([frame(lsb, msb)]))

    def test_charging_state(self):
        (d,) = self.poll([frame(state=0x05)])        # the cable: charging at once
        self.assertTrue(d.charging)
        self.assertTrue(d.charging_known)
        self.assertEqual(1, len(self.sent))          # no re-read once charging is seen
        for state in (0x0A, 0x07):                   # say nothing about the charger
            with self.subTest(state=state):
                (d,) = self.poll([frame(state=state)])
                self.assertEqual(69, d.level)
                self.assertFalse(d.charging)
                self.assertFalse(d.charging_known)
                self.assertEqual(1 + A.TUF_H3_RECHECKS, len(self.sent))

    def test_battery_needs_two_frames_apart(self):
        # alone on battery every frame is 03: two of them, then stop
        (d,) = self.poll([frame(state=0x03)])
        self.assertFalse(d.charging)
        self.assertTrue(d.charging_known)
        self.assertEqual(2, len(self.sent))
        # with ASUS software on the dongle: a 0A blip between the 03 frames
        (d,) = self.poll([frame(state=0x03), frame(state=0x0A), frame(state=0x03)])
        self.assertFalse(d.charging)
        self.assertTrue(d.charging_known)
        self.assertEqual(3, len(self.sent))
        (d,) = self.poll([frame(state=0x0A), frame(state=0x03), frame(state=0x0A)])
        self.assertFalse(d.charging)                 # one 03, nothing against it
        self.assertTrue(d.charging_known)

    def test_cable_rotation_with_asus_software(self):
        # the cable while ASUS software polls the dongle: 0A -> 03 -> 05 frames
        (d,) = self.poll([frame(state=0x03), frame(state=0x05)])
        self.assertTrue(d.charging)
        self.assertEqual(2, len(self.sent))
        (d,) = self.poll([frame(state=0x0A), frame(state=0x03), frame(state=0x05)])
        self.assertTrue(d.charging)
        self.assertEqual(3, len(self.sent))

    def test_level_comes_from_the_last_frame(self):
        (d,) = self.poll([frame(0x4C, 0x0F, 0x03), frame(0x82, 0x0F, 0x05)])
        self.assertEqual(77, d.level)                # 3970 mV, the newer frame

    def test_recheck_waits_between_frames(self):
        waits = []
        saved = A.time
        A.TUF_H3_RECHECK_S = 3.0
        A.time = types.SimpleNamespace(sleep=waits.append)
        try:
            self.poll([frame(state=0x03)])
            self.assertEqual(1, waits.count(3.0))    # 03, wait, 03: battery
            waits.clear()
            self.poll([frame(state=0x05)])           # charging seen: no wait
            self.assertNotIn(3.0, waits)
            waits.clear()
            self.assertEqual([], self.poll([]))      # off or asleep: no wait either
            self.assertNotIn(3.0, waits)
        finally:
            A.time = saved

    def test_re_read_failing_keeps_the_first_reading(self):
        # the headset goes silent after the first frame: the first reading stands
        (d,) = self.poll([frame(state=0x03), [], [], []])
        self.assertEqual(69, d.level)
        self.assertFalse(d.charging)
        self.assertTrue(d.charging_known)

    def test_other_frames_are_not_a_level(self):
        strix = frame()
        strix[1] = A.HEADSET_HEADER
        for name, reply in (("armoury other reply", OTHER_REPLY),
                            ("strix go header", strix),
                            ("error", [0xFF, 0xFF, 0xAA] + [0] * 61),
                            ("zeros", [0xFF] + [0] * 63),
                            ("truncated", frame()[:12]),
                            ("short", [0xFF, 0x12]),
                            ("garbage", [0x5A] * 64)):
            with self.subTest(name):
                self.assertEqual([], self.poll([reply] * A.HEADSET_ATTEMPTS))

    def test_report_id_left_out(self):
        (d,) = self.poll([frame()[1:]])
        self.assertEqual(69, d.level)

    def test_empty_read_is_retried(self):
        (d,) = self.poll([[], frame(state=0x05)])
        self.assertEqual(69, d.level)
        self.assertEqual(2, len(self.sent))

    def test_device_gone_during_poll(self):
        self.assertEqual([], self.poll([], open_error=True))
        self.assertEqual([], self.poll([], read_error=True))
        self.assertEqual([], self.poll([]))           # silent: off or asleep

    def test_no_64_byte_collection(self):
        global LENGTHS
        saved = LENGTHS
        LENGTHS = {b"mi03-000c": 4, b"mi03-ffc0": 8, b"mi03-ff00": 8}
        try:
            self.assertEqual([], self.poll([frame()]))
            self.assertEqual([], self.sent)
        finally:
            LENGTHS = saved


if __name__ == "__main__":
    unittest.main()
