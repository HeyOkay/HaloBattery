"""Replace the wrong XM2we 17-byte fixtures with source-backed XM2w A1 tests.

Vole 4ba29dea sections1-3 and stuffz ENDGAME.md 25871cce establish feature
A1/0E/B4 on FF01:0002. A1 has no checksum. Former checksum/charging/pairing
categories now check status/frame rejection, unknown/inferred charging, and
ambiguous endpoint refusal, without claiming recovery or fresh correlation.
"""
import types
import unittest
from unittest.mock import Mock, patch

from providers import endgame_4k as E
from providers import endgame4k_protocol as P


# Literal public XM2w original/v2 identity fields (VID/PID/FW); 63-byte
# explicit-A1 framing is the observed layout, not production-generated data.
ORIGINAL_ID = bytes.fromhex("a1 01") + bytes(14) + bytes.fromhex("67 33 68 19 00 00 01 10") + bytes(39)
V2_ID = bytes.fromhex("a1 01") + bytes(14) + bytes.fromhex("67 33 82 19 00 00 01 07") + bytes(39)
LEVEL75 = bytes.fromhex("a1 01") + bytes(14) + bytes.fromhex("4b") + bytes(46)


def reply(level=75, status=1):
    data = list(LEVEL75)
    data[1], data[16] = status, level
    return data


def entry(**changes):
    return {"vendor_id": 0x3367, "product_id": 0x1970,
            "usage_page": 0xFF01, "usage": 2, "path": b"receiver", **changes}


class ProtocolTests(unittest.TestCase):
    def test_only_source_requests_can_be_built(self):
        self.assertEqual(P.request(14), bytes.fromhex("a1 0e 00 00") + bytes(60))
        self.assertEqual(P.request(180), bytes.fromhex("a1 b4 00 00") + bytes(60))
        for command in range(256):
            if command not in (14, 180):
                with self.assertRaises(ValueError):
                    P.request(command)
        for command, target in ((14, 1), (180, 15), (14.0, 0), (True, 0), (14, False)):
            with self.assertRaises(ValueError):
                P.request(command, target)

    def test_zero_hundred_and_invalid_levels_without_clamping(self):
        for level in (0, 75, 100):
            self.assertEqual(P.battery(reply(level)), level)
        for level in (101, 255, -1, True, None, 75.0):
            with self.assertRaises(P.ProtocolError):
                P.battery(reply(level))

    def test_no_checksum_instead_reject_non_success_invalid_identity_frame_and_bytes(self):
        for status in (0, 3, 7, 8, 17, 255):
            with self.subTest(status=status), self.assertRaises(P.StatusError):
                P.battery(reply(75, status))
        data = bytearray(ORIGINAL_ID)
        data[16:18] = b"\x00\x00"
        with self.assertRaises(P.ProtocolError):
            P.identity(data)
        for length in range(63):
            with self.assertRaises(P.ProtocolError):
                P.response(LEVEL75[:length])
        for data in (LEVEL75 + bytes(2), b"\x00" + LEVEL75, "x" * 63, 1, None):
            with self.assertRaises(P.ProtocolError):
                P.response(data)
        data = list(LEVEL75)
        data[1] = True
        with self.assertRaises(P.ProtocolError):
            P.response(data)
        self.assertEqual(P.battery(LEVEL75 + b"\x00"), 75)

    def test_cross_command_success_and_repeated_old_success_do_not_prove_freshness(self):
        device = Mock()
        device.send_feature_report.return_value = 64
        device.get_feature_report.return_value = ORIGINAL_ID
        reader = P.Reader(device, sleep=lambda _: None)
        # No documented command/target echo exists. Framing accepts identity
        # while asking B4; its first payload byte103 then fails level bounds.
        self.assertEqual(reader.exchange(180), ORIGINAL_ID)
        with self.assertRaises(P.ProtocolError):
            P.battery(ORIGINAL_ID)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.provider = E.Endgame4kProvider()
        self.device = Mock()
        self.device.send_feature_report.return_value = 64
        self.device.get_feature_report.side_effect = [ORIGINAL_ID, LEVEL75, LEVEL75]
        self.entries = [entry()]
        for context in (
            patch.object(E, "hid", types.SimpleNamespace(device=lambda: self.device)),
            patch.object(E.hidlist, "enumerate", side_effect=lambda vid: self.entries),
            patch.object(P.time, "sleep", return_value=None),
        ):
            context.start()
            self.addCleanup(context.stop)

    def probe(self, transport="receiver"):
        return self.provider.probe_experimental(transport, acknowledged=True)

    def test_level_queries_identity_then_battery_and_unknown_charging(self):
        result = self.probe()
        self.assertEqual((result["model"], result["percentage"], result["charging"],
                          result["charging_known"], result["freshness"]),
                         ("XM2w 4k", 75, None, False, "unverified"))
        self.assertEqual([bytes(call.args[0]) for call in self.device.send_feature_report.call_args_list],
                         [bytes.fromhex("a1 0e 00 00") + bytes(60),
                          bytes.fromhex("a1 b4 00 00") + bytes(60),
                          bytes.fromhex("a1 b4 00 00") + bytes(60)])
        self.device.get_feature_report.assert_called_with(0xA1, 64)
        self.device.close.assert_called_once_with()
        self.device.read.assert_not_called()
        self.device.write.assert_not_called()

    def test_wired_charging_is_inferred_without_a_percentage(self):
        self.entries = [entry(product_id=0x1968, path=b"cable"), entry()]
        self.device.get_feature_report.side_effect = [ORIGINAL_ID, LEVEL75]
        result = self.probe("wired")
        self.assertEqual((result["percentage"], result["charging"], result["charging_known"],
                          result["charging_basis"]), (None, True, False, "USB connection inference"))
        self.device.open_path.assert_called_once_with(b"cable")
        self.assertEqual(self.device.send_feature_report.call_count, 2)

    def test_v2_identity_has_separate_model_and_firmware(self):
        self.device.get_feature_report.side_effect = [V2_ID, LEVEL75, LEVEL75]
        result = self.probe()
        self.assertEqual((result["model"], result["mouse_pid"], result["firmware_bytes"]),
                         ("XM2w 4k v2", "1982", "01 07"))

    def test_zero_and_hundred_survive_the_entire_experimental_read(self):
        for level in (0, 100):
            self.device.get_feature_report.side_effect = [ORIGINAL_ID, reply(level), reply(level)]
            self.assertEqual(self.probe()["percentage"], level)

    def test_silence_unknown_identity_or_wired_mismatch_never_queries_battery(self):
        bad = bytearray(ORIGINAL_ID)
        bad[18:20] = bytes.fromhex("72 19")
        for identity, transport in (([], "receiver"), (bad, "receiver"), (V2_ID, "wired")):
            self.device.reset_mock()
            self.entries = [entry(product_id=0x1968)] if transport == "wired" else [entry()]
            self.device.get_feature_report.side_effect = [identity]
            self.assertIsNone(self.probe(transport))
            self.assertEqual(self.device.send_feature_report.call_count, 1)
            self.device.close.assert_called_once_with()

    def test_ambiguous_endpoints_and_wrong_collections_refuse_before_open(self):
        for rows in ([entry(), entry(path=b"second")],
                     [entry(product_id=0x1972)], [entry(vendor_id=0x1234)],
                     [entry(usage_page=0xFF02)], [entry(usage=1)], [entry(product_id=0x1961)]):
            self.entries = rows
            self.assertIsNone(self.probe())
        self.device.open_path.assert_not_called()
        self.device.send_feature_report.assert_not_called()

    def test_case_duplicate_same_endpoint_is_deduplicated_without_pairing_units(self):
        self.entries = [entry(path=b"Receiver"), entry(path=b"receiver")]
        self.assertEqual(self.probe()["percentage"], 75)
        self.device.open_path.assert_called_once_with(b"receiver")

    def test_default_and_normal_probe_variants_never_enumerate_or_open(self):
        self.assertFalse(E.ENABLED_PIDS)
        with patch.object(E.hidlist, "enumerate") as inventory, patch.object(E, "hid") as hid:
            for args in ({}, {"HALO_PROBE": "1"}):
                with patch.dict("os.environ", args):
                    self.assertEqual(self.provider.poll(), [])
            with self.assertRaises(ValueError):
                self.provider.probe_experimental("receiver")
            inventory.assert_not_called()
            hid.device.assert_not_called()

    def test_short_send_never_reads_and_closes(self):
        self.device.send_feature_report.return_value = 63
        self.assertIsNone(self.probe())
        self.device.get_feature_report.assert_not_called()
        self.device.close.assert_called_once_with()

    def test_disappearance_open_send_read_and_close_failures(self):
        self.device.get_feature_report.side_effect = OSError("PRIVATE-PATH")
        self.assertIsNone(self.probe())
        self.device.close.assert_called_once_with()
        self.assertNotIn("PRIVATE", " ".join(self.provider.diagnostics()))
        for operation in ("open_path", "send_feature_report"):
            self.device.reset_mock()
            getattr(self.device, operation).side_effect = OSError("PRIVATE-PATH")
            self.assertIsNone(self.probe())
            self.device.close.assert_called_once_with()
            getattr(self.device, operation).side_effect = None
        self.device.close.side_effect = OSError("PRIVATE-CLOSE")
        with self.assertRaises(OSError):
            self.probe()
        self.assertNotIn("PRIVATE", " ".join(self.provider.diagnostics()))

    def test_busy_is_reread_without_resending_and_is_bounded(self):
        self.device.get_feature_report.side_effect = [reply(status=3)] * 4
        self.assertIsNone(self.probe())
        self.assertEqual(self.device.send_feature_report.call_count, 1)
        self.assertEqual(self.device.get_feature_report.call_count, 4)

    def test_busy_then_success_continues_without_resending_identity(self):
        self.device.get_feature_report.side_effect = [reply(status=3), ORIGINAL_ID, LEVEL75, LEVEL75]
        self.assertEqual(self.probe()["percentage"], 75)
        self.assertEqual(self.device.get_feature_report.call_count, 4)
        self.assertEqual([call.args[0][1] for call in self.device.send_feature_report.call_args_list],
                         [14, 180, 180])

    def test_failed_status_never_uses_stale_valid_looking_payload(self):
        self.device.get_feature_report.side_effect = [reply(95, 8)]
        self.assertIsNone(self.probe())
        self.assertEqual(self.device.send_feature_report.call_count, 1)
        self.assertIn("payload not used", " ".join(self.provider.diagnostics()))

    def test_no_cached_value_survives_a_new_physical_unit(self):
        self.assertEqual(self.probe()["percentage"], 75)
        self.entries = [entry(path=b"second")]
        self.device.get_feature_report.side_effect = [reply(95, 8)]
        self.assertIsNone(self.probe())

    def test_two_identical_old_battery_successes_remain_explicitly_unverified(self):
        # Reproduces #428's known limit, rather than blessing agreement as fresh.
        result = self.probe()
        self.assertEqual(result["samples"], [75, 75])
        self.assertEqual(result["freshness"], "unverified")
        self.assertEqual(self.provider.poll(), [])

    def test_unstable_three_samples_rejected_and_budget_prevents_followup(self):
        self.device.get_feature_report.side_effect = [ORIGINAL_ID, reply(30), reply(35), reply(30)]
        self.assertIsNone(self.probe())
        self.assertEqual(self.device.send_feature_report.call_count, 4)
        self.device.reset_mock()
        def expired_send(data):
            self.clock = 10
            return 64
        self.clock = 0
        self.device.send_feature_report.side_effect = expired_send
        with patch.object(P.time, "monotonic", side_effect=lambda: self.clock):
            self.assertIsNone(self.probe())
        self.device.get_feature_report.assert_not_called()
        self.device.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
