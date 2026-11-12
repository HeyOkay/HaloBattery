"""Tests for providers/lamzu.py. No hardware is needed.

The fake dongle has the collections of a real LAMZU Maya X 8K Dongle (373E:001E)
from a diagnostics report, and answers the feature report exchange:
00 00 02 02 00 83 out, a1 00 02 02 00 83 <charging> <battery %> back.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import lamzu as L  # noqa: E402


class FakeCollection:
    """answer: None = never answers (asleep); else (charging, battery %)."""

    def __init__(self, answer=(0, 81)):
        self.answer = answer
        self.sent = []
        self.opened = 0

    def reply(self):
        if not self.sent or self.answer is None:
            return [0x00] * 65
        chg, batt = self.answer
        return [0x00, 0xA1, 0x00, 0x02, 0x02, 0x00, 0x83, chg, batt] + [0x00] * 56


def fake_device_class(cols):
    class FakeDevice:
        def open_path(self, path):
            self.c = cols[path]
            self.c.opened += 1

        def send_feature_report(self, data):
            self.c.sent.append(list(data))
            return len(data)

        def get_feature_report(self, report_id, n):
            return self.c.reply()

        def close(self):
            pass
    return FakeDevice


def maya_x_entries(pid=L.DONGLE, prefix=b"dongle"):
    # the collections of the LAMZU Maya X 8K Dongle in a real diagnostics report
    shape = [(0, 0x0001, 0x02), (1, 0x000C, 0x01), (1, 0x0001, 0x06), (1, 0x0001, 0x80),
             (1, 0xFFA0, 0x01), (1, 0xFFFF, 0x01), (2, 0xFFFF, 0x00)]
    return [{"product_id": pid, "interface_number": i, "usage_page": p, "usage": u,
             "path": prefix + b"-%d-%04x-%d" % (i, p, n), "product_string": "LAMZU Maya X 8K Dongle"}
            for n, (i, p, u) in enumerate(shape)]


class AtlantisProviderTest(unittest.TestCase):
    def setUp(self):
        self._saved = (L.hid, L.hidlist, L.compx_output_length)
        self.sent = []
        self.replies = []
        self.entries = []
        L.compx_output_length = lambda _path: None

        owner = self
        class FakeDevice:
            def open_path(self, path):
                self.path = path
                self.sent_request = False
            def read(self, length, timeout=0):
                if self.sent_request and owner.replies:
                    return owner.replies.pop(0)
                return b""
            def write(self, frame):
                owner.sent.append((self.path, list(frame)))
                self.sent_request = True
                return len(frame)
            def close(self):
                pass
        self.device = FakeDevice
        L.hid = types.SimpleNamespace(device=lambda: self.device())
        L.hidlist = types.SimpleNamespace(enumerate=lambda vid: list(self.entries)
                                          if vid == L.COMPX_VID else [])

    def tearDown(self):
        L.hid, L.hidlist, L.compx_output_length = self._saved

    def add_mouse(self, pid, prefix):
        shapes = [(0, 0x0001, 0x0006), (1, 0xFF05, 0), (1, 0xFF03, 0),
                  (1, 0x000C, 1), (1, 0x0001, 0x80), (1, 0xFF02, 2),
                  (1, 0xFF04, 2), (2, 0x0001, 2)]
        for n, (iface, page, usage) in enumerate(shapes):
            self.entries.append({"vendor_id": L.COMPX_VID, "product_id": pid,
                                 "interface_number": iface, "usage_page": page,
                                 "usage": usage, "path": f"{prefix}-{n}".encode(),
                                 "product_string": "LAMZU THORN"})

    @staticmethod
    def power_reply(level, charging=0, millivolts=0):
        frame = [0x08, 0x04, 0, 0, 0, 2, level, charging]
        frame += list(millivolts.to_bytes(2, "big")) + [0] * 6
        frame.append((0x55 - sum(frame)) & 0xFF)
        return bytes(frame)

    def test_thorn_4k_receiver_is_reported_as_lamzu(self):
        self.add_mouse(0xF510, "receiver")
        self.replies = [self.power_reply(95, 0, 4092)]
        provider = L.LamzuAtlantisProvider()
        found = provider.poll()
        self.assertEqual([("lamzu:3554f510", "LAMZU Thorn (4K receiver)", 95, False, "lamzu")],
                         [(s.key, s.name, s.level, s.charging, s.source) for s in found])
        self.assertEqual(1, len(self.sent))
        self.assertEqual((0x08, 0x04), tuple(self.sent[0][1][:2]))

    def test_thorn_cable_shares_identity_and_reports_charging(self):
        self.add_mouse(0xF50F, "cable")
        self.replies = [self.power_reply(95, 1, 4205)]
        found = L.LamzuAtlantisProvider().poll()
        self.assertEqual([("lamzu:3554f510", "LAMZU Thorn (wired)", 95, True, "lamzu")],
                         [(s.key, s.name, s.level, s.charging, s.source) for s in found])

    def test_cable_wins_when_receiver_and_cable_are_both_present(self):
        self.add_mouse(0xF510, "receiver")
        self.add_mouse(0xF50F, "cable")
        self.replies = [self.power_reply(95, 1, 4205), self.power_reply(95, 0, 4092)]
        found = L.LamzuAtlantisProvider().poll()
        self.assertEqual(1, len(found))
        self.assertTrue(found[0].charging)
        self.assertEqual("lamzu:3554f510", found[0].key)
        self.assertTrue(all(path.startswith(b"cable-") for path, _frame in self.sent))

    def test_unlisted_compx_pid_is_not_opened(self):
        self.add_mouse(0xF58F, "other")
        self.assertEqual([], L.LamzuAtlantisProvider().poll())
        self.assertEqual([], self.sent)


if __name__ == "__main__":
    unittest.main()
