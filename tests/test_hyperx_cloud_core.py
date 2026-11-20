"""Tests for providers/hyperx_cloud_core.py. No hardware is needed.

The protocol is LennardKittner/HyperHeadset's `cloud-core` branch, from the
reporter's reverse-engineering in its issue #59: a 64-byte feature report
0xFF, answered by a short-lived `FF 12` report with the voltage at bytes 11-12.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import hyperx_cloud_core as H  # noqa: E402

DONGLE_PATH = b"\\\\?\\hid#vid_0951&pid_173f&mi_03&col03#8&1234abcd&0&0002#{4d1e55b2-f16f-11cf-88cb-001111000030}"
CONSUMER_PATH = DONGLE_PATH.replace(b"col03", b"col01")
TELEPHONY_PATH = DONGLE_PATH.replace(b"col03", b"col02")

# The reporter's valid answer from HyperHeadset issue #59: voltage 0x0D71 =
# 3441 mV, byte 9 = 0x03 (on battery).
CAPTURED = [0xFF, 0x12, 0x05, 0xFE, 0x0C, 0x04, 0x1F, 0x0B,
            0x01, 0x03, 0x05, 0x71, 0x0D, 0x00, 0x00, 0x03,
            0x68, 0x10] + [0] * 46


class FakeDevice:
    def __init__(self, reply=None):
        self.reply = list(reply) if reply is not None else []
        self.opened = []
        self.writes = []
        self.feature_reads = 0

    def open_path(self, path):
        self.opened.append(path)

    def close(self):
        pass

    def send_feature_report(self, packet):
        self.writes.append(list(packet))
        return len(packet)

    def get_feature_report(self, report_id, length):
        self.feature_reads += 1
        if self.feature_reads > 1:
            return []
        return list(self.reply)


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = (H.hid, H.hidlist)

    def tearDown(self):
        H.hid, H.hidlist = self._saved

    def entries(self, pid=0x173F, vid=0x0951):
        return {
            "vendor": {"product_id": pid, "path": DONGLE_PATH, "interface_number": 3,
                       "usage_page": H.USAGE_PAGE, "usage": H.USAGE},
            "consumer": {"product_id": pid, "path": CONSUMER_PATH, "interface_number": 3,
                         "usage_page": 0x000C, "usage": 0x0001},
            "telephony": {"product_id": pid, "path": TELEPHONY_PATH, "interface_number": 3,
                          "usage_page": 0x000B, "usage": 0x0001},
        }

    def poll(self, device, entries=None, vids=(H.KINGSTON_VID,)):
        data = entries if entries is not None else list(self.entries().values())
        H.hid = types.SimpleNamespace(device=lambda: device)
        H.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: list(data) if vid in vids else [])
        p = H.HyperXCloudCoreProvider()
        return p.poll(), p.diagnostics()


class Protocol(Base):
    def test_the_request_is_the_reference_feature_packet(self):
        self.assertEqual(len(H.BATTERY_REQUEST), 64)
        self.assertEqual(list(H.BATTERY_REQUEST[:12]),
                         [0xFF, 0x07, 0x00, 0xFD, 0x04, 0x0C, 0xF1, 0x02,
                          0x01, 0x04, 0xF0, 0x0C])
        self.assertEqual(set(H.BATTERY_REQUEST[12:]), {0})

    def test_the_captured_answer_reads_voltage_and_battery_state(self):
        self.assertEqual(H.parse_response(CAPTURED), (3441, False))
        self.assertEqual(H.parse_response([0xFF, 0x12] + [0] * 63), None)

    def test_charging_byte_five_is_charging_and_other_values_are_unknown(self):
        charging = list(CAPTURED)
        charging[9] = 0x05
        self.assertEqual(H.parse_response(charging), (3441, True))
        unknown = list(CAPTURED)
        unknown[9] = 0x04
        self.assertEqual(H.parse_response(unknown), (3441, None))

    def test_the_voltage_lookup_clamps_at_both_ends(self):
        self.assertEqual(H.voltage_to_percent(3000), 5)
        self.assertEqual(H.voltage_to_percent(4000), 100)
        self.assertEqual(H.voltage_to_percent(3441), 5)
        # The test build's first reading: 3854 mV must not be shown as full.
        self.assertEqual(H.voltage_to_percent(3854), 70)


class Poll(Base):
    def test_the_vendor_collection_is_the_only_one_opened(self):
        d = FakeDevice(CAPTURED)
        out, diag = self.poll(d)
        self.assertEqual(d.opened, [DONGLE_PATH])
        self.assertEqual(len(out), 1)
        st = out[0]
        self.assertEqual((st.key, st.name, st.level, st.charging, st.kind),
                         ("hyperx:173f", "HyperX Cloud Core Wireless", 5, False,
                          "headset"))
        self.assertEqual(st.source, "hyperx_cloud_core")
        self.assertEqual(st.approx, "about 5%")
        self.assertIn("3441 mV", "\n".join(diag))

    def test_the_usb_c_id_shares_the_dongle_icon(self):
        d = FakeDevice(CAPTURED)
        entry = dict(self.entries(pid=0x1740)["vendor"])
        entry["path"] = DONGLE_PATH.replace(b"173f", b"1740")
        out, diag = self.poll(d, entries=[entry])
        self.assertEqual([(s.key, s.level) for s in out], [("hyperx:173f", 5)])

    def test_no_vendor_collection_writes_nothing(self):
        d = FakeDevice(CAPTURED)
        entries = [e for e in self.entries().values()
                   if e["usage_page"] != H.USAGE_PAGE]
        out, diag = self.poll(d, entries=entries)
        self.assertEqual(out, [])
        self.assertEqual(d.writes, [])
        self.assertIn("no usage ff00:0001", "\n".join(diag))

    def test_an_empty_answer_is_not_a_reading(self):
        d = FakeDevice([])
        out, diag = self.poll(d)
        self.assertEqual(out, [])
        self.assertIn("no battery answer", "\n".join(diag))


if __name__ == "__main__":
    unittest.main()
