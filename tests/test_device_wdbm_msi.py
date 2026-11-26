"""Tests for providers/msi.py. No hardware is needed."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import msi as M  # noqa: E402


_DEFAULT_WRITE_RESULT = object()


def query_reply(level=86, charging=0):
    r = [0x0F, 0x20] + [0] * 30
    r[14] = (0x80 if charging else 0) | level
    return r


def passive_reply(raw=0x4D):
    return [0xA0, raw, 0x07, 0x00] + [0] * 20


def pro_reply(level=86, charging=0x02):
    r = [0] * 65
    r[7] = 0x05
    r[9] = level
    r[10] = charging
    return r


class FakeDevice:
    def __init__(
        self, replies, write_result=_DEFAULT_WRITE_RESULT, write_error=None
    ):
        self.replies = list(replies)
        self.write_result = write_result
        self.write_error = write_error
        self.writes = []
        self.reads = 0
        self.closed = 0

    def open_path(self, path):
        self.path = path

    def write(self, data):
        self.writes.append(list(data))
        if self.write_error is not None:
            raise self.write_error
        if self.write_result is not _DEFAULT_WRITE_RESULT:
            return self.write_result
        return len(data)

    def read(self, length, timeout):
        self.reads += 1
        if not self.replies:
            return []
        return self.replies.pop(0)

    def close(self):
        self.closed += 1


def entry(pid, mode="query"):
    page, usage = (0xFF10, 0x0006) if mode == "passive" else (0xFF00, 0x0001)
    return {"vendor_id": M.VID, "product_id": pid, "interface_number": 1,
            "usage_page": page, "usage": usage, "output_report_byte_length": 65,
            "input_report_byte_length": 65, "path": b"msi-%04x" % pid,
            "product_string": "MSI"}


def fake_hid(dev):
    return types.SimpleNamespace(device=lambda: dev)


class ParseTests(unittest.TestCase):
    def test_query(self):
        self.assertEqual(M.parse_query(query_reply()), (86, False))
        self.assertEqual(M.parse_query(query_reply(50, 1)), (50, True))
        self.assertEqual(M.parse_query([0x00] + query_reply())[0], 86)

    def test_bad_query(self):
        self.assertIsNone(M.parse_query([]))
        self.assertIsNone(M.parse_query(query_reply(101)))

    def test_passive(self):
        self.assertEqual(M.parse_passive(passive_reply()), (57, False))
        self.assertIsNone(M.parse_passive([0xA0, 1, 0]))

    def test_pro_reply(self):
        self.assertEqual(M.parse_pro_reply(pro_reply(86, 0x02)), (86, False))
        self.assertEqual(M.parse_pro_reply(pro_reply(42, 0x01)), (42, True))
        self.assertIsNone(M.parse_pro_reply(pro_reply(0, 0x01)))
        self.assertIsNone(M.parse_pro_reply(pro_reply(0, 0x02)))
        self.assertEqual(M.parse_pro_reply(pro_reply(100, 0x01)), (100, True))

    def test_bad_pro_reply(self):
        self.assertIsNone(M.parse_pro_reply(None))
        self.assertIsNone(M.parse_pro_reply([]))
        self.assertIsNone(M.parse_pro_reply(pro_reply()[:64]))
        self.assertIsNone(M.parse_pro_reply(pro_reply(101)))
        self.assertIsNone(M.parse_pro_reply(pro_reply(50, 0x00)))
        self.assertIsNone(M.parse_pro_reply(pro_reply(50, 0x03)))

        bad_type = pro_reply()
        bad_type[7] = 0x04
        self.assertIsNone(M.parse_pro_reply(bad_type))

    def test_pro_reply_without_leading_report_id(self):
        raw = pro_reply(28, 0x02)[1:]
        self.assertEqual(len(raw), 64)
        self.assertEqual(M.parse_pro_reply(raw), (28, False))


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (M.hid, M.hidlist)
        self.provider = M.MsiProvider()

    def tearDown(self):
        M.hid, M.hidlist = self._saved

    def poll(self, infos, dev):
        M.hid = fake_hid(dev)
        M.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(infos))
        return self.provider.poll()

    def test_versa_query(self):
        dev = FakeDevice([query_reply(86)])
        e = entry(0x9E22); e["usage_page"], e["usage"] = 0xFF0D, 0x00B0
        res = self.poll([e], dev)
        self.assertEqual([(r.key, r.level, r.via) for r in res],
                         [("msi:9e22", 86, "2.4 GHz receiver")])
        self.assertEqual(dev.writes[0][:3], [0x00, 0x0F, 0x20])
        self.assertEqual(len(dev.writes[0]), 65)

    def test_gm41_passive(self):
        dev = FakeDevice([passive_reply()])
        res = self.poll([entry(0x0D4C, "passive")], dev)
        self.assertEqual([(r.key, r.level, r.charging) for r in res],
                         [("msi:0d41", 57, False)])


    def pro_entry(self, pid, path=None):
        e = entry(pid)
        e["usage_page"], e["usage"] = M.PRO_PAGE, M.PRO_USAGE
        e["path"] = path or (b"msi-pro-%04x" % pid)
        return e

    def test_versa_pro_query(self):
        dev = FakeDevice([pro_reply(86, 0x01)])
        res = self.poll([self.pro_entry(0x1FC6)], dev)

        self.assertEqual(
            [(r.key, r.name, r.level, r.charging, r.kind, r.online)
             for r in res],
            [("msi:1fc6", "MSI VERSA PRO WIRELESS", 86, True, "mouse", True)],
        )
        self.assertEqual(len(dev.writes), 1)
        self.assertEqual(dev.writes[0], list(M.PRO_REQUEST))
        self.assertEqual(len(dev.writes[0]), 65)
        self.assertEqual(dev.closed, 1)

    def test_pro_query_skips_empty_and_invalid_reports(self):
        invalid = pro_reply(86)
        invalid[7] = 0x04

        dev = FakeDevice([[], invalid, pro_reply(73, 0x01)])
        result = self.poll([self.pro_entry(0x1FC6)], dev)

        self.assertEqual([(r.level, r.charging) for r in result],
                         [(73, True)])
        self.assertEqual(len(dev.writes), 1)
        self.assertEqual(dev.closed, 1)

    def test_pro_query_timeout_closes_device_without_caching(self):
        e = self.pro_entry(0x1FC6)
        dev = FakeDevice([])

        original_monotonic = M.time.monotonic
        clock = [1000.0]

        def fake_monotonic():
            clock[0] += 0.3
            return clock[0]

        M.time.monotonic = fake_monotonic
        try:
            result = self.poll([e], dev)
            self.assertEqual(result, [])
            self.assertEqual(dev.closed, 1)
            self.assertNotIn("msi:1fc6", self.provider._pro_cache)
        finally:
            M.time.monotonic = original_monotonic

    def test_pro_query_without_report_length_metadata(self):
        e = self.pro_entry(0x1FC6)
        e.pop("input_report_byte_length", None)
        e.pop("output_report_byte_length", None)

        dev = FakeDevice([pro_reply(86, 0x01)])
        res = self.poll([e], dev)

        self.assertEqual([(r.key, r.level, r.charging) for r in res],
                         [("msi:1fc6", 86, True)])
        self.assertEqual(dev.writes, [list(M.PRO_REQUEST)])
        self.assertEqual(dev.closed, 1)

    def test_pro_query_accepts_padded_write(self):
        dev = FakeDevice(
            [pro_reply(86, 0x01)],
            write_result=len(M.PRO_REQUEST) + 1,
        )
        result = self.poll([self.pro_entry(0x1FC6)], dev)

        self.assertEqual(result[0].level, 86)
        self.assertEqual(len(dev.writes), 1)
        self.assertGreater(dev.reads, 0)
        self.assertEqual(dev.closed, 1)
        self.assertTrue(any(
            "HID write returned" in line and "continuing" in line
            for line in self.provider.diagnostics()
        ))

    def test_pro_query_rejects_none_write_result(self):
        dev = FakeDevice(
            [pro_reply(86, 0x01)],
            write_result=None,
        )
        result = self.poll([self.pro_entry(0x1FC6)], dev)

        self.assertEqual(result, [])
        self.assertEqual(dev.reads, 0)
        self.assertEqual(dev.closed, 1)
        self.assertTrue(any(
            "HID write failed (None)" in line
            for line in self.provider.diagnostics()
        ))

    def test_pro_query_rejects_negative_write_result(self):
        dev = FakeDevice(
            [pro_reply(86, 0x01)],
            write_result=-1,
        )
        result = self.poll([self.pro_entry(0x1FC6)], dev)

        self.assertEqual(result, [])
        self.assertEqual(dev.reads, 0)
        self.assertEqual(dev.closed, 1)
        self.assertTrue(any(
            "HID write failed (-1)" in line
            for line in self.provider.diagnostics()
        ))

    def test_pro_query_handles_write_exception(self):
        dev = FakeDevice(
            [pro_reply(86, 0x01)],
            write_error=OSError("write failed"),
        )
        result = self.poll([self.pro_entry(0x1FC6)], dev)

        self.assertEqual(result, [])
        self.assertEqual(len(dev.writes), 1)
        self.assertEqual(dev.reads, 0)
        self.assertEqual(dev.closed, 1)
        self.assertTrue(any(
            "HID error" in line
            for line in self.provider.diagnostics()
        ))

    def test_strike_pro_keyboard(self):
        dev = FakeDevice([pro_reply(73, 0x02)])
        res = self.poll([self.pro_entry(0x1620)], dev)

        self.assertEqual(
            [(r.key, r.level, r.charging, r.kind)
             for r in res],
            [("msi:1620", 73, False, "keyboard")],
        )

    def test_pro_cache_retains_stale_status_until_expiry(self):
        e = self.pro_entry(0x1FC6)
        key = "msi:1fc6"
        original_timeout = M.QUERY_LISTEN_MS
        M.QUERY_LISTEN_MS = 1

        try:
            # First poll succeeds and stores a valid status.
            first = FakeDevice([pro_reply(86)])
            result = self.poll([e], first)
            self.assertEqual(result[0].level, 86)
            self.assertTrue(result[0].online)

            # Temporary query failure retains the last status as stale.
            failed = FakeDevice([])
            result = self.poll([e], failed)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].level, 86)
            self.assertFalse(result[0].online)
            self.assertTrue(result[0].polling_stale)

            # Expired cache must no longer produce a status.
            path, _, status = self.provider._pro_cache[key]
            self.provider._pro_cache[key] = (
                path, M.time.monotonic() - M.PRO_KEEP - 1, status
            )

            expired = FakeDevice([])
            result = self.poll([e], expired)
            self.assertEqual(result, [])
            self.assertNotIn(key, self.provider._pro_cache)
        finally:
            M.QUERY_LISTEN_MS = original_timeout

    def test_pro_cache_retains_status_when_interface_is_missing(self):
        e = self.pro_entry(0x1FC6)
        key = "msi:1fc6"

        first = FakeDevice([pro_reply(86)])
        result = self.poll([e], first)
        self.assertEqual(result[0].level, 86)
        self.assertTrue(result[0].online)

        # Receiver remains enumerated, but its HID interface no longer matches.
        unmatched = dict(e)
        unmatched["usage_page"] = 0x0001

        result = self.poll([unmatched], FakeDevice([]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].level, 86)
        self.assertFalse(result[0].online)
        self.assertTrue(result[0].polling_stale)

        # The cached status expires after PRO_KEEP.
        path, _, status = self.provider._pro_cache[key]
        self.provider._pro_cache[key] = (
            path, M.time.monotonic() - M.PRO_KEEP - 1, status
        )

        result = self.poll([unmatched], FakeDevice([]))
        self.assertEqual(result, [])
        self.assertNotIn(key, self.provider._pro_cache)

    def test_pro_cache_refreshes_on_every_poll(self):
        e = self.pro_entry(0x1FC6)
        first = FakeDevice([pro_reply(86)])
        second = FakeDevice([pro_reply(80)])

        first_result = self.poll([e], first)
        self.assertEqual(first_result[0].level, 86)
        self.assertEqual(len(first.writes), 1)

        refreshed_result = self.poll([e], second)
        self.assertEqual(refreshed_result[0].level, 80)
        self.assertEqual(len(second.writes), 1)

    def test_pro_cache_cleared_when_receiver_disappears(self):
        e = self.pro_entry(0x1FC6)
        first = FakeDevice([pro_reply(86)])
        self.assertEqual(self.poll([e], first)[0].level, 86)

        self.assertEqual(self.poll([], FakeDevice([])), [])

        next_device = FakeDevice([pro_reply(70)])
        result = self.poll([e], next_device)
        self.assertEqual(result[0].level, 70)
        self.assertEqual(len(next_device.writes), 1)

    def test_pro_cache_cleared_when_path_changes(self):
        first_entry = self.pro_entry(0x1FC6, b"path-1")
        first = FakeDevice([pro_reply(86)])
        self.assertEqual(self.poll([first_entry], first)[0].level, 86)

        second_entry = self.pro_entry(0x1FC6, b"path-2")
        second = FakeDevice([pro_reply(70)])
        result = self.poll([second_entry], second)
        self.assertEqual(result[0].level, 70)
        self.assertEqual(len(second.writes), 1)


if __name__ == "__main__":
    unittest.main()
