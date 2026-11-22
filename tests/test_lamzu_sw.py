"""Tachi Aurora fixtures replace the layout-only Glorious collection hypothesis.

The request bytes coincide, but the verified target is iface2 FFFF:0000 and
the Aurora status/tag/command echoes are checked. Normal polling stays held.
No hardware or network: manufacturer script SHA5895a882 and Dorsal55f702fc.
"""
import types
import unittest
from unittest.mock import patch

from providers import lamzu_sw as L


def reply(level, charging=0, status=0xA1):
    return [0, status, 0, 2, 2, 0, 0x83, charging, level] + [0] * 56


def entry(**changes):
    return {"vendor_id": 0x37B0, "product_id": 0x000C, "interface_number": 2,
            "usage_page": 0xFFFF, "usage": 0, "path": b"tachi", **changes}


class FakeDevice:
    def __init__(self, replies):
        self.replies = list(replies)
        self.opened, self.sent, self.reads = [], [], []
        self.closed = False

    def open_path(self, path):
        self.opened.append(path)

    def get_report_lengths(self):
        return {"input": 0, "output": 0, "feature": 65}

    def send_feature_report(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def get_feature_report(self, rid, length):
        self.reads.append((rid, length))
        return self.replies.pop(0) if self.replies else []

    def close(self):
        self.closed = True


class ProbeTests(unittest.TestCase):
    def probe(self, replies, infos=None):
        device = FakeDevice(replies)
        provider = L.LamzuSinowealthProvider(sleep=lambda _: None)
        with patch.object(L, "hid", types.SimpleNamespace(device=lambda: device)), \
                patch.object(L.hidlist, "enumerate", return_value=[entry()] if infos is None else infos):
            result = provider.probe_experimental(acknowledged=True)
        self.assertEqual(device.closed, bool(device.opened))
        return result, device

    def test_tachi_query_uses_the_official_aurora_endpoint_and_exact_report(self):
        result, device = self.probe([reply(75)])
        self.assertEqual((result["model"], result["percentage"], result["charging"],
                          result["freshness"]), ("Lamzu Tachi", 75, False, "unverified"))
        self.assertEqual(device.opened, [b"tachi"])
        self.assertEqual(device.sent, [bytes.fromhex("00 00 00 02 02 00 83") + bytes(58)])
        self.assertEqual(device.reads, [(0, 65)])

    def test_charging_is_explicit_without_changing_hundred_to_ninety_nine(self):
        result, _ = self.probe([reply(100, charging=1)])
        self.assertTrue(result["charging"])
        self.assertTrue(result["charging_known"])
        self.assertEqual(result["percentage"], 100)

    def test_asleep_reply_is_not_a_diagnostic_reading(self):
        result, _ = self.probe([reply(50, status=0xA4)])
        self.assertIsNone(result)

    def test_no_receiver_reports_nothing_and_opens_nothing(self):
        result, device = self.probe([], infos=[])
        self.assertIsNone(result)
        self.assertEqual(device.sent, [])
        self.assertEqual(device.opened, [])


if __name__ == "__main__":
    unittest.main()
