"""Tests for the ROG Raikiri II vendor query and its XInput pairing (#269)."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import raikiri as R, wgi  # noqa: E402
from providers import xinput as X  # noqa: E402


def reply(level=65, charging=0):
    frame = bytearray(64)
    frame[0:3] = bytes([0xB0, 0x12, 0x04])
    frame[R.LEVEL_INDEX] = level
    frame[R.CHARGING_INDEX] = charging
    return bytes(frame)


class FakeDevice:
    def __init__(self, frame=reply(), sent=64):
        self.frame = frame
        self.sent = sent
        self.writes = []
        self.opened = []
        self.closed = False

    def open_path(self, path):
        self.opened.append(path)

    def send_feature_report(self, data):
        self.writes.append(bytes(data))
        return self.sent

    def get_feature_report(self, rid, length):
        return self.frame

    def close(self):
        self.closed = True


INFO = [{"path": b"raikiri", "interface_number": 4,
         "usage_page": 0xFF03, "usage": 0x0001, "product_id": R.PID}]


class QueryTests(unittest.TestCase):
    def setUp(self):
        self._saved = (R.hid, R.hidlist)

    def tearDown(self):
        R.hid, R.hidlist = self._saved

    def read(self, frame=reply()):
        dev = FakeDevice(frame)
        R.hid = type("HID", (), {"device": staticmethod(lambda: dev)})
        R.hidlist = type("HL", (), {"enumerate": staticmethod(lambda vid=0: list(INFO))})
        return R.read_connected([]), dev

    def test_the_documented_query_and_offsets(self):
        reading, dev = self.read()
        self.assertEqual((reading.level, reading.charging, reading.name),
                         (65, False, "ROG Raikiri II"))
        self.assertEqual(64, len(dev.writes[0]))
        self.assertEqual(bytes([0xB0, 0x12, 0x04]), dev.writes[0][:3])
        self.assertTrue(all(b == 0 for b in dev.writes[0][3:]))

    def test_charging_is_byte_ten(self):
        reading, _ = self.read(reply(charging=1))
        self.assertTrue(reading.charging)

    def test_a_bad_or_short_reply_is_refused(self):
        for frame in (b"", reply()[:10], reply(101)):
            with self.subTest(frame=frame):
                reading, _ = self.read(frame)
                self.assertIsNone(reading)


class PairingTests(unittest.TestCase):
    def setUp(self):
        self._saved = (X.load_xinput, X.hidlist, X.wgi.query)
        self.reports = [wgi.WgiController({"vid": R.VID, "pid": R.PID,
                                          "status": "Discharging", "remain": 1000,
                                          "full": 1000, "name": "Xbox 360 Controller"})]

    def tearDown(self):
        X.load_xinput, X.hidlist, X.wgi.query = self._saved

    def test_one_raikiri_slot_uses_the_vendor_reading_and_no_second_icon(self):
        class Dll:
            def XInputGetState(self, slot, ref):
                return 0 if slot == 0 else 1167

            def XInputGetBatteryInformation(self, slot, devtype, ref):
                info = ref._obj
                info.BatteryType, info.BatteryLevel = X.TYPE_WIRED, 0
                return 0

        X.load_xinput = lambda: Dll()
        X.hidlist = types.SimpleNamespace(
            enumerate=lambda vid=0: [], interface_paths=lambda: frozenset())
        X.wgi.query = lambda diag: list(self.reports)
        with mock.patch.object(X.raikiri, "read_connected",
                               return_value=R.Reading(65, False, R.NAME)):
            out = X.XInputProvider().poll()
        self.assertEqual([(s.name, s.level, s.charging) for s in out],
                         [("ROG Raikiri II", 65, False)])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
