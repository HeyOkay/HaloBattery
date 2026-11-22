"""Sourced Maxwell targets preserve the existing one-headset cable preference."""
import unittest
from unittest.mock import patch

from providers import audeze


def info(pid=0x4B19, path=b"dongle", **changes):
    d = dict(product_id=pid, serial_number="0000000000000000", path=path,
             usage_page=0xFF13, usage=1, interface_number=0,
             product_string="Audeze Maxwell HID")
    d.update(changes)
    return d


class MaxwellTargetsTest(unittest.TestCase):
    def poll(self, infos):
        reader = audeze.AudezeProvider()
        with patch.object(audeze.hidlist, "enumerate", return_value=infos), \
                patch.object(reader, "_read_battery", return_value=(85, False)) as read:
            out = reader.poll()
        return out, read

    def test_missing_exact_control_usage_never_sends_the_sequence(self):
        for changes in ({"usage_page": 0xFF00}, {"usage": 2}, {"usage_page": 0x000C}):
            with self.subTest(changes=changes):
                out, read = self.poll([info(**changes)])
                read.assert_not_called()
                self.assertEqual([s.level for s in out], [None])

    def test_inferred_xbox_cable_is_not_a_request_target(self):
        out, read = self.poll([info(pid=0x4B1E)])
        self.assertEqual(out, [])
        read.assert_not_called()

    def test_zero_serial_cable_and_dongle_keep_one_key_and_one_query(self):
        for reverse in (False, True):
            infos = [info(), info(0x4B1A, b"cable")]
            out, read = self.poll(list(reversed(infos)) if reverse else infos)
            self.assertEqual([(s.key, s.level, s.charging) for s in out],
                             [("audeze:0000000000000000", 85, True)])
            read.assert_called_once_with(b"cable", (0x4B1A, "0000000000000000"))

    def test_maxwell_two_interface_five_remains_allowed(self):
        out, read = self.poll([info(0x4B29, interface_number=5)])
        self.assertEqual([s.level for s in out], [85])
        read.assert_called_once()
