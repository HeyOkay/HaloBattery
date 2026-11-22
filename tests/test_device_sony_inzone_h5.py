"""Tests for the Sony INZONE H5 (054c:0ebf) HID path of the INZONE provider; no hardware."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import sony_inzone as S  # noqa: E402

# Captured from a real INZONE H5 dongle answering make_hid_report(0x0102): 50 %, on battery.
CAPTURED = bytes.fromhex("02 0e 04 ff 0b 00 96 c3 14 04 10 02 01 00 32 b6") + bytes(48)

ENTRY = {"product_id": 0x0EBF, "usage_page": 0xFF04, "usage": 0x0001,
         "path": b"h5-control", "interface_number": 5}
OTHER_COLLECTIONS = [
    {"product_id": 0x0EBF, "usage_page": 0xFF13, "usage": 1, "path": b"h5-col1",
     "interface_number": 5},
    {"product_id": 0x0EBF, "usage_page": 0x000C, "usage": 1, "path": b"h5-col2",
     "interface_number": 5},
]


def reply(level=50, status=0, tid=S.HID_TID, kind=S.HID_TYPE_RET, checksum=None):
    frame = bytearray(S.SIGNATURE + bytes([kind, tid & 0xFF, tid >> 8, status, level, 0]))
    frame[13] = sum(frame[4:13]) & 0xFF if checksum is None else checksum
    report = bytes([S.HID_REPORT_ID, len(frame)]) + bytes(frame)
    return list(report + bytes(S.HID_REPORT_SIZE - len(report)))


class FakeHid:
    """hid.device() stand-in: queued reads, recorded writes, optional failures."""

    def __init__(self, reads=(), open_error=None, write_error=None, read_error=None):
        self.reads = list(reads)
        self.open_error = open_error
        self.write_error = write_error
        self.read_error = read_error
        self.paths = []
        self.writes = []
        self.closed = False

    def open_path(self, path):
        if self.open_error:
            raise self.open_error
        self.paths.append(path)

    def write(self, data):
        if self.write_error:
            raise self.write_error
        self.writes.append(list(data))
        return len(data)

    def read(self, size, timeout_ms=0):
        if self.read_error:
            raise self.read_error
        return self.reads.pop(0) if self.reads else []

    def close(self):
        self.closed = True


class CapsHid(FakeHid):
    """A device opened through the app's HID transport: it also reports HIDP_CAPS sizes."""

    def __init__(self, reads=(), lengths=None, lengths_error=None):
        super().__init__(reads)
        self.lengths = {"input": 64, "output": 64, "feature": 0} if lengths is None else lengths
        self.lengths_error = lengths_error

    def get_report_lengths(self):
        if self.lengths_error:
            raise self.lengths_error
        return self.lengths


class FrameTest(unittest.TestCase):
    def test_query_frame_is_the_documented_h9_query(self):
        # the serial H9 QUERY is the same frame with transaction id 1
        self.assertEqual(S.make_query(1), S.QUERY)

    def test_hid_report_layout(self):
        report = S.make_hid_report(0x0102)
        self.assertEqual(len(report), 64)
        self.assertEqual(bytes(report[:14]),
                         bytes.fromhex("02 0c 01 00 fc 08 96 c3 41 04 01 02 01 a2"))
        self.assertEqual(report[14:], [0] * 50)

    def test_captured_reply(self):
        self.assertEqual(S.parse_hid_reply(CAPTURED, 0x0102), (50, False))

    def test_level_edges(self):
        self.assertEqual(S.parse_hid_reply(reply(level=0), S.HID_TID), (0, False))
        self.assertEqual(S.parse_hid_reply(reply(level=100, status=1), S.HID_TID), (100, True))

    def test_out_of_range_level_is_refused(self):
        self.assertIsNone(S.parse_hid_reply(reply(level=101), S.HID_TID))
        self.assertIsNone(S.parse_hid_reply(reply(level=0xFE), S.HID_TID))

    def test_headset_off(self):
        self.assertEqual(S.parse_hid_reply(reply(level=0xFF), S.HID_TID), (None, None))

    def test_unknown_status_keeps_level(self):
        self.assertEqual(S.parse_hid_reply(reply(level=40, status=7), S.HID_TID), (40, None))

    def test_rejects_other_transactions_types_and_bad_checksums(self):
        self.assertIsNone(S.parse_hid_reply(reply(tid=0x0203), S.HID_TID))
        self.assertIsNone(S.parse_hid_reply(reply(kind=0x01), S.HID_TID))
        good = reply()
        self.assertIsNone(S.parse_hid_reply(reply(checksum=(good[15] + 1) & 0xFF), S.HID_TID))

    def test_rejects_truncated_short_and_garbage(self):
        for data in (None, [], [0x02], CAPTURED[:15], bytes(64), b"\xff" * 64,
                     bytes([0x01]) + CAPTURED[1:], bytes([0x02, 0x05]) + CAPTURED[2:]):
            self.assertIsNone(S.parse_hid_reply(data, 0x0102), data)


class PollTest(unittest.TestCase):
    def setUp(self):
        # keep the serial path out of these tests
        patcher = mock.patch.object(S, "list_ports", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def poll(self, fake, entries=None):
        entries = [ENTRY] + OTHER_COLLECTIONS if entries is None else entries
        with mock.patch.object(S.hidlist, "enumerate", return_value=entries), \
             mock.patch.object(S.hid, "device", return_value=fake):
            provider = S.SonyInzoneProvider()
            return provider.poll(), provider.diagnostics()

    def test_reads_the_control_collection(self):
        fake = FakeHid([CAPTURED])
        result, _ = self.poll(fake)
        self.assertEqual([(r.key, r.name, r.level, r.charging, r.online, r.kind,
                           r.charging_known) for r in result],
                         [("sony_inzone_h5", "Sony INZONE H5", 50, False, True,
                           "headset", True)])
        self.assertEqual(fake.paths, [b"h5-control"])
        self.assertEqual(fake.writes, [S.make_hid_report(S.HID_TID)])
        self.assertTrue(fake.closed)

    def test_skips_other_frames_before_the_reply(self):
        fake = FakeHid([[], reply(tid=0x0005, level=10), reply(level=77, status=1)])
        result, _ = self.poll(fake)
        self.assertEqual([(r.level, r.charging) for r in result], [(77, True)])

    def test_headset_off_is_shown_offline(self):
        result, _ = self.poll(FakeHid([reply(level=0xFF)]))
        self.assertEqual([(r.level, r.online, r.charging_known) for r in result],
                         [(None, False, False)])

    def test_unknown_charging_state(self):
        result, _ = self.poll(FakeHid([reply(level=60, status=9)]))
        self.assertEqual([(r.level, r.charging, r.charging_known) for r in result],
                         [(60, False, False)])

    def test_no_reply_gives_no_status(self):
        fake = FakeHid([])
        result, diag = self.poll(fake)
        self.assertEqual(result, [])
        self.assertEqual(len(fake.writes), 1)
        self.assertTrue(any("no battery reply" in line for line in diag))

    def test_out_of_range_reply_is_not_shown(self):
        result, _ = self.poll(FakeHid([reply(level=150)]))
        self.assertEqual(result, [])

    def test_device_disappears_mid_poll(self):
        for fake in (FakeHid(open_error=OSError("open failed")),
                     FakeHid(write_error=OSError("device gone")),
                     FakeHid(read_error=OSError("read error"))):
            result, diag = self.poll(fake)
            self.assertEqual(result, [])
            self.assertTrue(diag)

    def test_other_collections_and_pids_are_not_opened(self):
        fake = FakeHid([CAPTURED])
        entries = OTHER_COLLECTIONS + [dict(ENTRY, product_id=0x0E53)]
        result, _ = self.poll(fake, entries)
        self.assertEqual(result, [])
        self.assertEqual(fake.paths, [])

    def test_only_the_exact_control_usage_is_opened(self):
        # a second FF04 collection listed first must not be opened in its place
        fake = FakeHid([CAPTURED])
        entries = [dict(ENTRY, usage=0x0002, path=b"h5-other-ff04"), ENTRY]
        result, _ = self.poll(fake, entries)
        self.assertEqual([r.level for r in result], [50])
        self.assertEqual(fake.paths, [b"h5-control"])

    def test_ff04_without_the_control_usage_is_not_opened(self):
        fake = FakeHid([CAPTURED])
        result, _ = self.poll(fake, [dict(ENTRY, usage=0x0002)])
        self.assertEqual(result, [])
        self.assertEqual(fake.paths, [])


class ReportSizeTest(unittest.TestCase):
    """The query is written only when the collection's HIDP_CAPS can carry it (#515 review)."""

    def setUp(self):
        patcher = mock.patch.object(S, "list_ports", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def poll(self, fake):
        with mock.patch.object(S.hidlist, "enumerate", return_value=[ENTRY]), \
             mock.patch.object(S.hid, "device", return_value=fake):
            provider = S.SonyInzoneProvider()
            return provider.poll(), provider.diagnostics()

    def test_fitting_sizes_are_read(self):
        for lengths in ({"input": 64, "output": 64, "feature": 0},
                        {"input": 16, "output": 65, "feature": 0}):
            fake = CapsHid([CAPTURED], lengths)
            result, _ = self.poll(fake)
            self.assertEqual([r.level for r in result], [50], lengths)
            self.assertEqual(len(fake.writes), 1)

    def test_too_small_or_invalid_sizes_are_not_written(self):
        for lengths in ({"input": 64, "output": 63, "feature": 0},
                        {"input": 15, "output": 64, "feature": 0},
                        {"input": 64, "feature": 0},
                        {"input": "64", "output": 64},
                        {"input": 64, "output": 64.0},
                        {"input": True, "output": 64},
                        [64, 64],
                        None):
            fake = CapsHid([CAPTURED])
            fake.lengths = lengths
            result, diag = self.poll(fake)
            self.assertEqual(result, [], lengths)
            self.assertEqual(fake.writes, [], lengths)
            self.assertTrue(fake.closed)
            self.assertTrue(any("report sizes" in line for line in diag), lengths)

    def test_non_callable_metadata_is_refused(self):
        fake = FakeHid([CAPTURED])
        fake.get_report_lengths = {"input": 64, "output": 64}
        result, _ = self.poll(fake)
        self.assertEqual(result, [])
        self.assertEqual(fake.writes, [])

    def test_metadata_error_is_contained(self):
        fake = CapsHid([CAPTURED], lengths_error=OSError("collection closed"))
        result, diag = self.poll(fake)
        self.assertEqual(result, [])
        self.assertEqual(fake.writes, [])
        self.assertTrue(fake.closed)
        self.assertTrue(any("collection closed" in line for line in diag))

    def test_plain_hidapi_device_without_metadata_is_still_read(self):
        fake = FakeHid([CAPTURED])
        result, _ = self.poll(fake)
        self.assertEqual([r.level for r in result], [50])


if __name__ == "__main__":
    unittest.main()
