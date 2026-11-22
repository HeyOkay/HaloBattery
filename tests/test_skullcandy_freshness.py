"""Fresh IRQ events and changed snapshots are distinct from cached GET_REPORT."""
import unittest
from unittest import mock

from providers import skullcandy as S
from test_device_pr207_skullcandy import CAPTURE_ASK, CAPTURE_REPORT, STALE_REPORT


class Device:
    def __init__(self, *, before=STALE_REPORT, snapshot=STALE_REPORT, pre=(), post=(),
                 write_result=62, stream=False):
        self.before, self.snapshot = before, snapshot
        self.pre, self.post = list(pre), list(post)
        self.write_result, self.stream = write_result, stream
        self.writes, self.pre_reads, self.post_reads, self.closed = [], 0, 0, False
    def open_path(self, path):
        pass
    def get_input_report(self, report_id, size):
        value = self.snapshot if self.writes else self.before
        if isinstance(value, Exception):
            raise value
        return value
    def read(self, size, timeout_ms):
        self.pre_reads += not bool(self.writes)
        self.post_reads += bool(self.writes)
        if self.stream and not self.writes:
            return STALE_REPORT
        queue = self.post if self.writes else self.pre
        value = queue.pop(0) if queue else []
        if isinstance(value, Exception):
            raise value
        return value
    def write(self, data):
        self.writes.append(bytes(data))
        if isinstance(self.write_result, Exception):
            raise self.write_result
        return self.write_result
    def close(self):
        self.closed = True


class FreshnessTests(unittest.TestCase):
    def read(self, device):
        provider = S.SkullcandyProvider()
        with mock.patch.object(S.hid, "device", return_value=device), \
                mock.patch.object(S.time, "sleep", return_value=None):
            result = provider._read(b"known-ff13-collection")
        self.assertTrue(device.closed)
        return result, provider.diagnostics()

    def test_unchanged_snapshot_with_no_new_interrupt_is_not_a_fresh_reading(self):
        dev = Device()
        level, lines = self.read(dev)
        self.assertIsNone(level)
        self.assertEqual(dev.writes, [CAPTURE_ASK])
        self.assertTrue(any("refused as stale" in line for line in lines))

    def test_identical_battery_bytes_from_a_new_interrupt_remain_valid(self):
        dev = Device(post=[STALE_REPORT])
        level, lines = self.read(dev)
        self.assertEqual(level, 65)
        self.assertEqual(dev.post_reads, 1)
        self.assertTrue(any("fresh interrupt report" in line for line in lines))

    def test_changed_known_snapshot_preserves_get_report_compatibility(self):
        dev = Device(snapshot=CAPTURE_REPORT)
        level, lines = self.read(dev)
        self.assertEqual(level, 70)
        self.assertTrue(any("changed input snapshot" in line for line in lines))

    def test_failed_prequery_snapshot_does_not_authorize_unproven_get_report(self):
        dev = Device(before=OSError("no baseline"), snapshot=CAPTURE_REPORT)
        self.assertIsNone(self.read(dev)[0])

    def test_prequery_interrupt_backlog_cannot_be_reused_as_the_response(self):
        dev = Device(pre=[STALE_REPORT])
        self.assertIsNone(self.read(dev)[0])
        self.assertEqual(dev.pre_reads, 2)
        self.assertEqual(dev.writes, [CAPTURE_ASK])

    def test_continuous_prequery_stream_is_bounded_and_defers_query(self):
        dev = Device(stream=True)
        level, lines = self.read(dev)
        self.assertIsNone(level)
        self.assertLessEqual(dev.pre_reads, 16)
        self.assertEqual(dev.writes, [])
        self.assertTrue(any("query deferred" in line for line in lines))

    def test_bad_write_never_takes_a_stale_or_even_available_input(self):
        for result in (-1, 0, 61, None, OSError("device gone")):
            with self.subTest(result=result):
                dev = Device(write_result=result, snapshot=CAPTURE_REPORT, post=[CAPTURE_REPORT])
                self.assertIsNone(self.read(dev)[0])
                self.assertEqual(dev.post_reads, 0)
                self.assertEqual(dev.writes, [CAPTURE_ASK])

    def test_read_disappearance_does_not_escape_or_keep_device_open(self):
        dev = Device(post=[OSError("IRQ gone")], snapshot=OSError("GET gone"))
        self.assertIsNone(self.read(dev)[0])

    def test_zero_and_hundred_are_accepted_only_from_fresh_valid_reports(self):
        for level in (0, 100):
            raw = bytearray(CAPTURE_REPORT)
            raw[18] = level
            dev = Device(post=[raw])
            with self.subTest(level=level):
                self.assertEqual(self.read(dev)[0], level)

    def test_wrong_recipient_role_status_and_garbage_are_not_levels(self):
        reports = [None, [None] * 30, b"", b"random bytes"]
        for index, value in ((2, 0), (17, 1), (16, 1), (18, 255), (1, 255)):
            raw = bytearray(CAPTURE_REPORT)
            raw[index] = value
            reports.append(raw)
        for report in reports:
            with self.subTest(report=report):
                self.assertIsNone(S.battery_percent(report))
                self.assertIsNone(self.read(Device(post=[report]))[0])
