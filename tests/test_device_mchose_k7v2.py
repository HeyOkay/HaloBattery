"""Tests for providers/mchose.py - the K7 V2 Ultra+ (3837:1018) and its 0x4D frame.

No hardware is needed. The frame is M HUB's own (www.mchose.com.cn, assets/main-*.js):
getCommandBuffer builds 4D 01 <flags 1> <len 0> 00 09 00 00 <xor of bytes 2..7> as a
64-byte output report, waitCommand takes the input report echoing 0x0900, and
deviceInfoToObj reads chargeStatus at payload byte 11 and batteryLevel at byte 12. The
reply below is the one captured from a real K7 V2 Ultra+ in its MagDock (85 %, not
charging); the collection shape is that device's enumeration.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import mchose as M  # noqa: E402

CAPTURED = bytes.fromhex(
    "4d 01 01 10 00 09 00 00 37 38 26 40 04 00 00 00 00 10 02 00 55 00 08 e4 de")


def reply(level=0x55, charge=0, vid=0x3837, first=None, checksum=True, cmd=0x0900):
    """A 0x0900 reply in the captured layout, with a correct (or broken) checksum."""
    pay = bytearray(CAPTURED[8:24])
    pay[0:2] = vid.to_bytes(2, "little")
    if first is not None:
        pay[0] = first
    pay[M.MC_CHARGE_AT] = charge
    pay[M.MC_LEVEL_AT] = level
    frame = bytearray([0x4D, 0x01, 0x01, len(pay), cmd & 0xFF, cmd >> 8, 0x00, 0x00]) + pay
    chk = 0
    for b in frame[2:]:
        chk ^= b
    frame.append(chk if checksum else chk ^ 0x01)
    return list(frame.ljust(64, b"\x00"))


def entries(pid=0x1018):
    # the real enumeration of 3837:1018 'MCHOSE K7 V2 Ultra+', plus the MagDock
    shape = [(0x1018, 0, 0x0001, 0x0002, "MCHOSE K7 V2 Ultra+"),
             (0x1018, 1, 0x000C, 0x0001, "MCHOSE K7 V2 Ultra+"),
             (0x1018, 1, 0x0001, 0x0006, "MCHOSE K7 V2 Ultra+"),
             (0x1018, 2, 0xFF0B, 0x00C4, "MCHOSE K7 V2 Ultra+"),
             (0x1018, 2, 0xFF01, 0x0001, "MCHOSE K7 V2 Ultra+")]
    return [{"vendor_id": 0x3837, "product_id": pid if p == 0x1018 else p,
             "interface_number": i, "usage_page": up, "usage": u,
             "path": b"k7-%d-%04x-%02x" % (i, up, u), "product_string": name}
            for p, i, up, u, name in shape]


MAGDOCK = {"vendor_id": 0x3837, "product_id": 0x1012, "interface_number": 0,
           "usage_page": 0xFF00, "usage": 0x0001, "path": b"dock",
           "product_string": "MCHOSE MagDock"}
CONFIG = b"k7-2-ff01-01"
FF0B = b"k7-2-ff0b-c4"


class FakeDevice:
    """Answers each write with the queued input reports, in order."""

    def __init__(self, answers=(), raise_on_read=False):
        self.answers = [list(a) for a in answers]
        self.raise_on_read = raise_on_read
        self.writes = []
        self.paths = []
        self.feature_sends = []

    def open_path(self, path):
        self.paths.append(path)

    def close(self):
        pass

    def write(self, data):
        self.writes.append((self.paths[-1], bytes(data)))
        return len(data)

    def read(self, n, timeout_ms=0):
        if self.raise_on_read:
            raise OSError("read error")
        return self.answers.pop(0) if self.answers else []

    def send_feature_report(self, data):
        self.feature_sends.append((self.paths[-1], bytes(data)))
        return len(data)

    def get_feature_report(self, report_id, length):
        raise OSError("read error")


class RequestTest(unittest.TestCase):
    def test_request_is_m_hubs_frame(self):
        req = M.make_mc_request()
        self.assertEqual(len(req), 64)
        self.assertEqual(req[:9], bytes([0x4D, 0x01, 0x01, 0x00, 0x00, 0x09, 0x00, 0x00, 0x08]))
        self.assertEqual(req[9:], bytes(55))


class ParseTest(unittest.TestCase):
    def test_captured_reply(self):
        self.assertEqual(M.parse_mc_info(CAPTURED), (85, False))

    def test_charging(self):
        self.assertEqual(M.parse_mc_info(reply(level=40, charge=1)), (40, True))

    def test_unknown_charge_state_is_not_charging(self):
        self.assertEqual(M.parse_mc_info(reply(level=40, charge=7)), (40, False))

    def test_level_100(self):
        self.assertEqual(M.parse_mc_info(reply(level=100)), (100, False))

    def test_level_0_is_not_a_reading(self):
        self.assertIsNone(M.parse_mc_info(reply(level=0)))

    def test_out_of_range_level_is_refused(self):
        for level in (101, 0xFF):
            self.assertIsNone(M.parse_mc_info(reply(level=level)))

    def test_short_truncated_and_garbage(self):
        for resp in (None, [], [0x4D], CAPTURED[:8], CAPTURED[:20], CAPTURED[:24],
                     bytes(64), bytes([0xFF] * 64), bytes(range(64))):
            self.assertIsNone(M.parse_mc_info(resp), resp)

    def test_bad_checksum_busy_other_vid_and_other_command(self):
        self.assertIsNone(M.parse_mc_info(reply(checksum=False)))
        self.assertIsNone(M.parse_mc_info(reply(first=0xFF)))
        self.assertIsNone(M.parse_mc_info(reply(vid=0x1234)))
        self.assertIsNone(M.parse_mc_info(reply(cmd=0x0600)))


class PollTest(unittest.TestCase):
    def poll(self, dev, devices=None):
        provider = M.MchoseProvider()
        devices = entries() if devices is None else devices
        with mock.patch.object(M.hidlist, "enumerate",
                               side_effect=lambda vid: [d for d in devices if d["vendor_id"] == vid]), \
                mock.patch.object(M.hid, "device", return_value=dev):
            return provider, provider.poll()

    def test_reads_the_level_and_writes_only_the_config_collection(self):
        dev = FakeDevice([CAPTURED])
        provider, out = self.poll(dev)
        self.assertEqual(len(out), 1)
        st = out[0]
        self.assertEqual((st.key, st.name, st.level, st.charging, st.online, st.kind),
                         ("mchose:3837", "MCHOSE K7 V2 Ultra+", 85, False, True, "mouse"))
        self.assertEqual({p for p, _ in dev.writes}, {CONFIG})
        self.assertEqual(dev.writes[0][1], M.make_mc_request())
        self.assertNotIn(FF0B, dev.paths)
        self.assertEqual(dev.feature_sends, [])      # the old 0x06 frame is not sent

    def test_pushed_reports_are_skipped(self):
        push = [0x4D, 0x01, 0x00, 0x05, 0x00, 0x06, 0x00, 0x00, 0x02, 0x00, 0x30] + [0] * 53
        dev = FakeDevice([push, CAPTURED])
        _, out = self.poll(dev)
        self.assertEqual(out[0].level, 85)

    def test_mouse_names_the_icon_over_the_magdock(self):
        dev = FakeDevice([CAPTURED])
        _, out = self.poll(dev, [MAGDOCK] + entries())
        self.assertEqual([s.name for s in out], ["MCHOSE K7 V2 Ultra+"])

    def test_asleep_mouse_keeps_its_last_level_greyed(self):
        provider = M.MchoseProvider()
        devices = entries()
        patches = lambda dev: (
            mock.patch.object(M.hidlist, "enumerate",
                              side_effect=lambda vid: devices if vid == 0x3837 else []),
            mock.patch.object(M.hid, "device", return_value=dev))
        a, b = patches(FakeDevice([CAPTURED]))
        with a, b:
            provider.poll()
        a, b = patches(FakeDevice([reply(level=0)] * M.MC_ATTEMPTS))
        with a, b:
            out = provider.poll()
        self.assertEqual([(s.level, s.online) for s in out], [(85, False)])
        self.assertIn("    no 0x0900 answer (mouse asleep?)", provider.diagnostics())

    def test_silent_receiver_shows_nothing(self):
        _, out = self.poll(FakeDevice([]))
        self.assertEqual(out, [])

    def test_device_disappears_mid_poll(self):
        provider, out = self.poll(FakeDevice(raise_on_read=True))
        self.assertEqual(out, [])
        self.assertTrue(any("0x0900: read error" in line for line in provider.diagnostics()))

    def test_open_failure(self):
        dev = FakeDevice()
        dev.open_path = mock.Mock(side_effect=OSError("gone"))
        provider, out = self.poll(dev)
        self.assertEqual(out, [])
        self.assertTrue(any("open: gone" in line for line in provider.diagnostics()))

    def test_other_3837_pids_keep_the_old_path(self):
        dev = FakeDevice()
        _, out = self.poll(dev, entries(pid=0x100B))     # the A7 V2 Ultra in issue #4
        self.assertEqual(out, [])
        self.assertEqual(dev.writes, [])
        self.assertTrue(dev.feature_sends)


if __name__ == "__main__":
    unittest.main()
