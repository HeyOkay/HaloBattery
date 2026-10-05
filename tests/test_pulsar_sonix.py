"""Tests for the 64-byte Pulsar X2 V3 Mini protocol."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import pulsar as P  # noqa: E402


class FakeDevice:
    response = []
    opened = None
    sent = []

    def open_path(self, path):
        type(self).opened = path

    def send_feature_report(self, packet):
        type(self).sent.append(list(packet))
        return len(packet)

    def get_feature_report(self, report_id, length):
        return list(type(self).response)

    def close(self):
        pass


class PulsarSonixTest(unittest.TestCase):
    def setUp(self):
        self._hid, self._hidlist = P.hid, P.hidlist
        FakeDevice.response = [0] * P.SONIX_REPORT_LEN
        FakeDevice.response[2:5] = P.SONIX_BATTERY_COMMAND
        FakeDevice.response[P.SONIX_LEVEL_INDEX] = 73
        FakeDevice.opened = None
        FakeDevice.sent = []
        self.infos = [
            {"vendor_id": P.SONIX_VID, "product_id": 0x5403, "path": b"if2",
             "interface_number": 2, "usage_page": 0xFFFF, "usage": 1},
            {"vendor_id": P.SONIX_VID, "product_id": 0x5403, "path": b"if3",
             "interface_number": 3, "usage_page": 0xFFFF, "usage": 1},
        ]
        P.hid = types.SimpleNamespace(device=FakeDevice)
        P.hidlist = types.SimpleNamespace(
            enumerate=lambda vid: self.infos if vid == P.SONIX_VID else [])

    def tearDown(self):
        P.hid, P.hidlist = self._hid, self._hidlist

    def test_8k_receiver_uses_interface_3_and_reads_battery(self):
        found = P.PulsarProvider().poll()
        self.assertEqual(1, len(found))
        self.assertEqual("Pulsar X2 V3 Mini (8K wireless)", found[0].name)
        self.assertEqual("pulsar:37105403", found[0].key)
        self.assertEqual(73, found[0].level)
        self.assertFalse(found[0].charging)
        self.assertEqual(b"if3", FakeDevice.opened)

    def test_feature_request_has_command_and_little_endian_checksum(self):
        P.PulsarProvider().poll()
        packet, = FakeDevice.sent
        self.assertEqual(P.SONIX_REPORT_LEN, len(packet))
        self.assertEqual([0, 0, 0x08, 0x81, 0x01], packet[:5])
        self.assertEqual(sum(packet[1:63]) & 0xFFFF, packet[63] | packet[64] << 8)

    def test_wired_id_is_charging(self):
        for d in self.infos:
            d["product_id"] = 0x3402
        found = P.PulsarProvider().poll()
        self.assertTrue(found[0].charging)
        self.assertEqual("Pulsar X2 V3 Mini (wired)", found[0].name)

    def test_invalid_level_is_rejected(self):
        FakeDevice.response[P.SONIX_LEVEL_INDEX] = 255
        self.assertEqual([], P.PulsarProvider().poll())

    def test_no_interface_3_is_not_queried(self):
        self.infos = self.infos[:1]
        provider = P.PulsarProvider()
        self.assertEqual([], provider.poll())
        self.assertEqual([], FakeDevice.sent)
        self.assertIn("no interface 3", "\n".join(provider.diagnostics()))


if __name__ == "__main__":
    unittest.main()
