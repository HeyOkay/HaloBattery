"""Corsair Void Elite Wireless (1b1c:0a55): the older Void exchange. No hardware.

The frames are the ones HeadsetControl's Void device describes: an output report
C9 with the byte 64, answered by 64 00 <level|mic flag> B1 <status>.
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import corsair as C  # noqa: E402


class FakeDongle:
    def __init__(self, replies=None, silent=False):
        self.replies = [list(r) for r in (replies or [])]
        self.silent = silent
        self.writes = []

    def read(self, size, timeout_ms):
        if self.silent or not self.replies:
            return []
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)


class FakeBus:
    def __init__(self, dongles):
        self.dongles = dongles

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.dongles:
                    raise OSError("cannot open")
                self.dongle = bus.dongles[path]

            def write(self, data):
                return self.dongle.write(data)

            def read(self, size, timeout_ms):
                return self.dongle.read(size, timeout_ms)

            def close(self):
                pass

        return FakeDevice


def entry(pid, path, iface, page, usage=1):
    return {"product_id": pid, "interface_number": iface, "usage_page": page,
            "usage": usage, "path": path, "product_string": "Hid Interface"}


def reply(level, status):
    return [0x64, 0x00, level, 0xB1, status]


class VoidEliteTest(unittest.TestCase):
    def setUp(self):
        self._saved = (C.hid, C.hidlist)

    def tearDown(self):
        C.hid, C.hidlist = self._saved

    def poll(self, dongles, entries=None):
        if entries is None:
            # the reporter's layout: 000c:0001, ffc5:0001 and ff00:0001, all interface 3
            entries = [entry(0x0A55, b"consumer", 3, 0x000C),
                       entry(0x0A55, b"vendor", 3, 0xFFC5),
                       entry(0x0A55, b"ff00", 3, 0xFF00)]
        C.hid = types.SimpleNamespace(device=FakeBus(dongles).device_class())
        C.hidlist = types.SimpleNamespace(enumerate=lambda vid: list(entries))
        return C.CorsairProvider().poll()

    def test_the_reported_level_is_shown(self):
        dev = FakeDongle([reply(0x4B, 0x01)])        # 75 %, normal
        [st] = self.poll({b"vendor": dev})
        self.assertEqual((st.name, st.level, st.charging, st.kind),
                         ("Corsair Void Elite Wireless", 75, False, "headset"))
        self.assertEqual(dev.writes[0][:2], bytes([0xC9, 0x64]))

    def test_a_charging_status_sets_the_flag(self):
        dev = FakeDongle([reply(0x32, 0x04)])
        [st] = self.poll({b"vendor": dev})
        self.assertEqual((st.level, st.charging), (50, True))

    def test_an_empty_receiver_shows_nothing(self):
        self.assertEqual(self.poll({b"vendor": FakeDongle([reply(0x32, 0x00)])}), [])

    def test_a_silent_receiver_shows_nothing(self):
        self.assertEqual(self.poll({b"vendor": FakeDongle(silent=True)}), [])

    def test_a_foreign_frame_is_skipped(self):
        dev = FakeDongle([[0x11, 0x00, 0x32, 0xB1, 0x01], reply(0x32, 0x01)])
        [st] = self.poll({b"vendor": dev})
        self.assertEqual(st.level, 50)

    def test_the_microphone_flag_is_masked_off(self):
        dev = FakeDongle([reply(0x80 | 0x32, 0x01)])
        [st] = self.poll({b"vendor": dev})
        self.assertEqual(st.level, 50)

    def test_only_the_vendor_page_is_asked(self):
        consumer, vendor = FakeDongle([reply(0x01, 0x01)]), FakeDongle([reply(0x32, 0x01)])
        self.poll({b"consumer": consumer, b"vendor": vendor})
        self.assertEqual(consumer.writes, [])
        self.assertTrue(vendor.writes)

    def test_parse_void(self):
        self.assertEqual(C.parse_void(reply(0x4B, 0x01)), (75, False))
        self.assertEqual(C.parse_void(reply(0x4B, 0x05)), (75, True))
        self.assertIsNone(C.parse_void(reply(0x4B, 0x00)))
        self.assertIsNone(C.parse_void([0x64, 0x00, 0x4B, 0xB1]))
        self.assertIsNone(C.parse_void([0x11, 0x00, 0x4B, 0xB1, 0x01]))


if __name__ == "__main__":
    unittest.main()
