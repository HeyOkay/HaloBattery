import unittest
from unittest.mock import patch

from providers import aula_f108 as aula


def reply(level):
    frame = bytes((0, 0x20, 0x01, 0, level)) + bytes(27)
    return frame + bytes((sum(frame) & 0xFF,))


def receiver(**overrides):
    return {"vendor_id": 0x05AC, "product_id": 0x024F, "interface_number": 3,
            "usage_page": 0xFF60, "usage": 0x0061, "product_string": "F108Pro Dongle",
            "path": b"f108", **overrides}


class ParseReplyTest(unittest.TestCase):
    def test_request_matches_the_hardware_query(self):
        self.assertEqual(aula.make_request(), b"\x00\x20\x01" + bytes(29) + b"\x21")

    def test_battery_levels_with_and_without_report_id(self):
        for level in (0, 33, 100):
            with self.subTest(level=level):
                frame = reply(level)
                self.assertEqual(aula.parse_reply(list(frame)), level)
                self.assertEqual(aula.parse_reply(frame[1:]), level)

    def test_literal_capture_and_malformed_byte_values(self):
        # PR #402 Windows capture: screen 33%, report ID 0 included.
        captured = bytes.fromhex(
            "00 20 01 00 21 00 00 00 00 00 00 00 00 00 00 00"
            " 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 42")
        self.assertEqual(aula.parse_reply(captured), 33)
        for value in (None, "33", 33.0, True, -1, 256):
            frame = list(captured)
            frame[4] = value
            with self.subTest(value=value):
                self.assertIsNone(aula.parse_reply(frame))

    def test_invalid_reports(self):
        valid = reply(33)
        for frame in (None, [], valid[:5], valid[:-1], valid + b"\x00", bytes(33),
                      b"\x00\x20\x02" + valid[3:-1] + b"\x43", valid[:-1] + b"\x00",
                      reply(101)):
            with self.subTest(frame=frame):
                self.assertIsNone(aula.parse_reply(frame))


class PollTest(unittest.TestCase):
    def setUp(self):
        hid_patch = patch.object(aula, "hid")
        self.hid = hid_patch.start()
        self.addCleanup(hid_patch.stop)
        self.dev = self.hid.device.return_value
        self.dev.get_report_lengths.return_value = {"input": 33, "output": 33, "feature": 0}
        self.dev.write.return_value = 33
        self.provider = aula.AulaF108Provider()

    def poll(self, entries):
        with patch.object(aula.hidlist, "enumerate", return_value=entries) as enumerate_hid:
            statuses = self.provider.poll()
        enumerate_hid.assert_called_once_with(0x05AC)
        return statuses

    def test_selects_only_the_f108_battery_collection(self):
        self.dev.read.side_effect = [bytes(33), list(reply(33))]
        statuses = self.poll([
            receiver(product_string="Apple Keyboard", path=b"apple"),
            receiver(product_string="2.4G Wireless Receiver", usage_page=0xFF02,
                     usage=0x0002, path=b"lofree"),
            receiver(product_string="", path=b"blank"),
            receiver(product_string=123, path=b"bad-product"),
            receiver(vendor_id=0x1234, path=b"wrong-vendor"),
            receiver(interface_number=4, path=b"wrong-interface"),
            receiver(usage_page=0xFF59, path=b"wrong-usage"),
            receiver(product_id=0x800A, path=b"wrong-pid"),
            receiver(),
        ])
        self.dev.open_path.assert_called_once_with(b"f108")
        self.dev.write.assert_called_once_with(aula.make_request())
        self.dev.close.assert_called_once_with()
        self.assertEqual(len(statuses), 1)
        status = statuses[0]
        self.assertTrue(status.key.startswith("aula_f108:024f:"))
        self.assertEqual((status.name, status.level, status.kind, status.source),
                         ("AULA F108 Pro", 33, "keyboard", "aula_f108"))
        self.assertTrue(status.online)
        self.assertFalse(status.charging_known)

    def test_unplugged_during_read_produces_no_reading_and_closes_the_handle(self):
        self.dev.read.side_effect = OSError("receiver disconnected")
        self.assertEqual(self.poll([receiver()]), [])
        self.dev.close.assert_called_once_with()
        self.assertIn("receiver disconnected", "\n".join(self.provider.diagnostics()))

    def test_short_write_skips_reading(self):
        self.dev.write.return_value = 32
        self.assertEqual(self.poll([receiver()]), [])
        self.dev.read.assert_not_called()
        self.dev.close.assert_called_once_with()

    def test_silent_receiver_produces_no_reading(self):
        self.dev.read.return_value = []
        self.assertEqual(self.poll([receiver()]), [])
        self.dev.close.assert_called_once_with()
        self.assertIn("no valid battery reply", "\n".join(self.provider.diagnostics()))

    def test_singleton_legacy_alias_is_cached_without_io_and_cleared(self):
        self.dev.read.return_value = reply(33)
        [status] = self.poll([receiver()])
        self.dev.reset_mock()
        self.assertEqual(self.provider.legacy_key_aliases(), {"aula_f108:024f": status.key})
        self.dev.open_path.assert_not_called()
        self.dev.read.assert_not_called()
        self.assertEqual(self.poll([]), [])
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_legacy_hidapi_keeps_the_exact33_byte_write(self):
        del self.dev.get_report_lengths
        self.dev.read.return_value = reply(33)
        self.assertEqual(self.poll([receiver()])[0].level, 33)
        self.dev.write.assert_called_once_with(b"\x00\x20\x01" + bytes(29) + b"\x21")

    def test_malformed_or_undersized_native_caps_never_write_or_read(self):
        for caps in ({}, {"input": 32, "output": 33}, {"input": 33, "output": 32},
                     {"input": 33, "output": 0}, {"input": 33.0, "output": 33}):
            with self.subTest(caps=caps):
                self.dev.reset_mock()
                self.dev.get_report_lengths.return_value = caps
                self.assertEqual(self.poll([receiver()]), [])
                self.dev.write.assert_not_called()
                self.dev.read.assert_not_called()

    def multi_poll(self, entries, levels):
        devices = []
        def factory():
            from unittest.mock import Mock
            device = Mock()
            device.get_report_lengths.return_value = {"input": 33, "output": 33, "feature": 0}
            device.write.return_value = 33
            def open_path(path):
                level = levels.get(path)
                device.read.return_value = reply(level) if level is not None else []
            device.open_path.side_effect = open_path
            devices.append(device)
            return device
        self.hid.device.side_effect = factory
        return self.poll(entries), devices

    def test_two_receivers_have_independent_keys_even_if_enumeration_reverses(self):
        entries = [receiver(path=b"one", serial_number=""), receiver(path=b"two", serial_number="")]
        first, _ = self.multi_poll(entries, {b"one": 10, b"two": 90})
        second, _ = self.multi_poll(list(reversed(entries)), {b"one": 10, b"two": 90})
        self.assertEqual(len(first), 2)
        self.assertEqual({s.level: s.key for s in first}, {s.level: s.key for s in second})
        self.assertEqual(len({s.key for s in first}), 2)
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_silent_sibling_prevents_ambiguous_legacy_alias(self):
        result, _ = self.multi_poll([receiver(path=b"one"), receiver(path=b"two")], {b"one": 10})
        self.assertEqual([s.level for s in result], [10])
        self.assertEqual(self.provider.legacy_key_aliases(), {})

    def test_repeated_case_variants_of_one_path_are_deduplicated(self):
        result, devices = self.multi_poll([receiver(path=b"ONE"), receiver(path="one")], {b"ONE": 10})
        self.assertEqual(len(result), 1)
        self.assertEqual(len(devices), 1)


class NativePaddingTest(unittest.TestCase):
    def test_full_padded_native_write_and_short_completion(self):
        import types
        from tests import test_experimental_hid as F
        from halo.hid_transport import HidDevice, ReportCaps, TransportRuntime
        for count in (64, 63, 33):
            with self.subTest(transferred=count):
                class Api(F.Api):
                    def create_write(self, owner, data):
                        self.writes.append(data)
                        request = F.Request(b"", transferred=count)
                        self.requests.append(request)
                        return request
                api = Api()
                api.next_read = F.Request(reply(33))
                runtime = TransportRuntime(api=api, timeout=.5)
                metadata = F.Metadata(ReportCaps(33, 64, 0))
                try:
                    fake = types.SimpleNamespace(device=lambda: HidDevice(runtime, metadata=metadata))
                    with patch.object(aula, "hid", fake):
                        value = aula.AulaF108Provider()._read(b"fake-f108")
                    self.assertEqual(api.writes, [b"\x00\x20\x01" + bytes(29) + b"\x21" + bytes(31)])
                    self.assertEqual(value, 33 if count == 64 else None)
                    self.assertEqual(api.next_read is None, count == 64)
                finally:
                    self.assertTrue(runtime.close(.5))


if __name__ == "__main__":
    unittest.main()
