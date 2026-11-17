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
        self.assertEqual((status.key, status.name, status.level, status.kind, status.source),
                         ("aula_f108:024f", "AULA F108 Pro", 33, "keyboard", "aula_f108"))
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


if __name__ == "__main__":
    unittest.main()
