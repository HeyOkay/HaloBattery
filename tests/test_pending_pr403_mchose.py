"""PR #403's captured K7 V2 Ultra+ exchange, isolated from other 3837 products."""
import types
import unittest
from unittest.mock import Mock, patch

from providers import mchose as M


# K7 V2 Ultra+ / MagDock 85%, captured in c4eb9eeba7ea9663, not generated
# with the production request/checksum/parser.
CAPTURE = bytes.fromhex(
    "4d 01 01 10 00 09 00 00 37 38 26 40 04 00 00 00 00 10 02 00 55 00 08 e4 de")


def reply(level=85, charge=0, vendor=0x3837, busy=False):
    frame = bytearray(CAPTURE)
    frame[8:10] = vendor.to_bytes(2, "little")
    frame[19], frame[20] = charge, level
    if busy:
        frame[8] = 255
    checksum = 0
    for byte in frame[2:24]:
        checksum ^= byte
    frame[24] = checksum
    return bytes(frame)


def entry(**changes):
    return {"vendor_id": 0x3837, "product_id": 0x1018, "interface_number": 2,
            "usage_page": 0xFF01, "usage": 1, "path": b"k7",
            "product_string": "MCHOSE K7 V2 Ultra+", **changes}


class ParseTests(unittest.TestCase):
    def test_literal_capture_and_zero_padded_windows_buffer(self):
        self.assertEqual(M.parse_mc_info(CAPTURE), (85, False))
        self.assertEqual(M.parse_mc_info(CAPTURE + bytes(39)), (85, False))
        self.assertEqual(M.make_mc_request(),
                         bytes.fromhex("4d 01 01 00 00 09 00 00 08") + bytes(55))

    def test_charging_bounds_and_unknown_state(self):
        self.assertEqual(M.parse_mc_info(reply(100, 1)), (100, True))
        for frame in (reply(0), reply(101), reply(255), reply(charge=7),
                      reply(vendor=0x5253), reply(busy=True)):
            with self.subTest(frame=frame):
                self.assertIsNone(M.parse_mc_info(frame))

    def test_every_truncation_and_corruption_is_rejected(self):
        for length in range(len(CAPTURE)):
            self.assertIsNone(M.parse_mc_info(CAPTURE[:length]))
        for at in (0, 1, 2, 3, 4, 5, 8, 20, 24):
            frame = bytearray(CAPTURE)
            frame[at] ^= 1
            with self.subTest(at=at):
                self.assertIsNone(M.parse_mc_info(frame))
        self.assertIsNone(M.parse_mc_info(CAPTURE + bytes(40)))
        self.assertIsNone(M.parse_mc_info(CAPTURE + b"\x01"))

    def test_unsupported_flags_never_bypass_checksum(self):
        for flags in (0, 2, 255):
            frame = bytearray(CAPTURE)
            frame[2] = flags
            frame[24] = 0
            self.assertIsNone(M.parse_mc_info(frame))

    def test_malformed_bytes_refused(self):
        for value in (True, -1, 256, "85", None, 85.0):
            frame = list(CAPTURE)
            frame[20] = value
            self.assertIsNone(M.parse_mc_info(frame))


class PollTests(unittest.TestCase):
    def setUp(self):
        self.provider = M.MchoseProvider()
        self.dev = Mock()
        self.dev.write.return_value = 64
        self.dev.read.return_value = []
        self.entries = [entry()]

    def poll(self):
        with patch.object(M, "hid", types.SimpleNamespace(device=lambda: self.dev)), \
                patch.object(M.hidlist, "enumerate", side_effect=lambda vid: [
                    row for row in self.entries if row["vendor_id"] == vid]):
            return self.provider.poll()

    def test_captured_response_only_on_exact_collection(self):
        self.entries = [entry(interface_number=1, path=b"other-interface"),
                        entry(usage_page=0xFF0B, path=b"sibling"),
                        entry(usage=2, path=b"other-usage"), entry()]
        self.dev.read.return_value = CAPTURE
        status = self.poll()[0]
        self.assertEqual((status.key, status.level, status.charging, status.online),
                         ("mchose:3837", 85, False, True))
        self.dev.open_path.assert_called_once_with(b"k7")
        self.dev.send_feature_report.assert_not_called()
        self.dev.close.assert_called_once_with()

    def test_silent_confirmed_receiver_never_falls_back(self):
        self.assertEqual(self.poll(), [])
        self.dev.write.assert_called_once_with(M.make_mc_request())
        self.assertEqual(self.dev.read.call_count, 5)
        self.dev.send_feature_report.assert_not_called()

    def test_other_new_receiver_products_remain_unopened(self):
        for pid in (0x1014, 0x1016, 0x101C, 0x101D, 0x1012, 0x3033, 0x9999):
            with self.subTest(pid=pid):
                self.entries = [entry(product_id=pid)]
                self.assertEqual(self.poll(), [])
        self.dev.open_path.assert_not_called()
        self.dev.write.assert_not_called()
        self.dev.send_feature_report.assert_not_called()

    def test_legacy_allowlisted_a7_keeps_its_own_path(self):
        self.entries = [entry(product_id=0x100B)]
        with patch.object(self.provider, "_read_collection", return_value=(61, 0, 0, 0)) as old:
            self.assertEqual(self.poll()[0].level, 61)
        old.assert_called_once_with(b"k7")
        self.dev.write.assert_not_called()

    def test_short_write_does_not_read(self):
        self.dev.write.return_value = 63
        self.assertEqual(self.poll(), [])
        self.dev.read.assert_not_called()
        self.dev.close.assert_called_once_with()

    def test_busy_and_zero_retry_then_fresh_reply(self):
        self.dev.read.side_effect = [reply(busy=True), reply(0), CAPTURE]
        self.assertEqual(self.poll()[0].level, 85)
        self.assertEqual(self.dev.write.call_count, 3)

    def test_unknown_charge_does_not_invent_discharge_or_retry(self):
        self.dev.read.return_value = reply(charge=7)
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.dev.write.call_count, 1)

    def test_disappearance_or_open_failure_closes_handle(self):
        for operation in ("open_path", "read"):
            self.dev.reset_mock()
            getattr(self.dev, operation).side_effect = OSError("disconnected")
            self.assertEqual(self.poll(), [])
            self.dev.close.assert_called_once_with()
            getattr(self.dev, operation).side_effect = None

    def test_zero_level_after_fresh_answer_keeps_only_grey_cached_level(self):
        self.dev.read.return_value = CAPTURE
        self.assertTrue(self.poll()[0].online)
        self.dev.read.return_value = reply(0)
        status = self.poll()[0]
        self.assertEqual((status.level, status.online), (85, False))


if __name__ == "__main__":
    unittest.main()
