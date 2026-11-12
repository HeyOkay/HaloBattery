"""Tests for the Razer model table in providers/razer.py. No hardware is needed.

The table must agree with OpenRazer, the Linux driver that reads the battery of
these mice. OPENRAZER_CHARGE_LEVEL below is a copy of the PID -> transaction id
list in OpenRazer's razer_attr_read_charge_level() (driver/razermouse_driver.c,
commit 6820f9da16). The poll tests replace the HID layer with a fake mouse that
answers the 90-byte feature report the way the provider expects.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import razer as R  # noqa: E402

OPENRAZER_CHARGE_LEVEL = {
    0x001F: 0xFF,   # NAGA_EPIC
    0x0024: 0xFF,   # MAMBA_2012_WIRED
    0x0025: 0xFF,   # MAMBA_2012_WIRELESS
    0x0032: 0xFF,   # OUROBOROS
    0x003E: 0xFF,   # NAGA_EPIC_CHROMA
    0x003F: 0xFF,   # NAGA_EPIC_CHROMA_DOCK
    0x0044: 0xFF,   # MAMBA_WIRED
    0x0045: 0xFF,   # MAMBA_WIRELESS
    0x0059: 0x3F,   # LANCEHEAD_WIRED
    0x005A: 0x3F,   # LANCEHEAD_WIRELESS
    0x0062: 0x1F,   # ATHERIS_RECEIVER
    0x006F: 0x1F,   # LANCEHEAD_WIRELESS_RECEIVER
    0x0070: 0x1F,   # LANCEHEAD_WIRELESS_WIRED
    0x0072: 0x3F,   # MAMBA_WIRELESS_RECEIVER
    0x0073: 0x3F,   # MAMBA_WIRELESS_WIRED
    0x0077: 0x1F,   # PRO_CLICK_RECEIVER
    0x007A: 0xFF,   # VIPER_ULTIMATE_WIRED
    0x007B: 0xFF,   # VIPER_ULTIMATE_WIRELESS
    0x007C: 0x3F,   # DEATHADDER_V2_PRO_WIRED
    0x007D: 0x3F,   # DEATHADDER_V2_PRO_WIRELESS
    0x0080: 0x1F,   # PRO_CLICK_WIRED
    0x0083: 0xFF,   # BASILISK_X_HYPERSPEED
    0x0086: 0x1F,   # BASILISK_ULTIMATE_WIRED
    0x0088: 0x1F,   # BASILISK_ULTIMATE_RECEIVER
    0x008F: 0x1F,   # NAGA_PRO_WIRED
    0x0090: 0x1F,   # NAGA_PRO_WIRELESS
    0x0094: 0x1F,   # OROCHI_V2_RECEIVER
    0x0095: 0x1F,   # OROCHI_V2_BLUETOOTH
    0x009A: 0x1F,   # PRO_CLICK_MINI_RECEIVER
    0x009C: 0x1F,   # DEATHADDER_V2_X_HYPERSPEED
    0x009E: 0x1F,   # VIPER_MINI_SE_WIRED
    0x009F: 0x1F,   # VIPER_MINI_SE_WIRELESS
    0x00A5: 0x1F,   # VIPER_V2_PRO_WIRED
    0x00A6: 0x1F,   # VIPER_V2_PRO_WIRELESS
    0x00A7: 0x1F,   # NAGA_V2_PRO_WIRED
    0x00A8: 0x1F,   # NAGA_V2_PRO_WIRELESS
    0x00AA: 0x1F,   # BASILISK_V3_PRO_WIRED
    0x00AB: 0x1F,   # BASILISK_V3_PRO_WIRELESS
    0x00AF: 0x1F,   # COBRA_PRO_WIRED
    0x00B0: 0x1F,   # COBRA_PRO_WIRELESS
    0x00B3: 0x1F,   # HYPERPOLLING_WIRELESS_DONGLE
    0x00B4: 0x1F,   # NAGA_V2_HYPERSPEED_RECEIVER
    0x00B6: 0x1F,   # DEATHADDER_V3_PRO_WIRED
    0x00B7: 0x1F,   # DEATHADDER_V3_PRO_WIRELESS
    0x00B8: 0x1F,   # VIPER_V3_HYPERSPEED
    0x00B9: 0x1F,   # BASILISK_V3_X_HYPERSPEED
    0x00BE: 0x1F,   # DEATHADDER_V4_PRO_WIRED
    0x00BF: 0x1F,   # DEATHADDER_V4_PRO_WIRELESS
    0x00C0: 0x1F,   # VIPER_V3_PRO_WIRED
    0x00C1: 0x1F,   # VIPER_V3_PRO_WIRELESS
    0x00C2: 0x1F,   # DEATHADDER_V3_PRO_WIRED_ALT
    0x00C3: 0x1F,   # DEATHADDER_V3_PRO_WIRELESS_ALT
    0x00C4: 0x1F,   # DEATHADDER_V3_HYPERSPEED_WIRED
    0x00C5: 0x1F,   # DEATHADDER_V3_HYPERSPEED_WIRELESS
    0x00C7: 0x1F,   # PRO_CLICK_V2_VERTICAL_EDITION_WIRED
    0x00C8: 0x1F,   # PRO_CLICK_V2_VERTICAL_EDITION_WIRELESS
    0x00CB: 0x1F,   # BASILISK_V3_35K
    0x00CC: 0x1F,   # BASILISK_V3_PRO_35K_WIRED
    0x00CD: 0x1F,   # BASILISK_V3_PRO_35K_WIRELESS
    0x00D0: 0x1F,   # PRO_CLICK_V2_WIRED
    0x00D1: 0x1F,   # PRO_CLICK_V2_WIRELESS
    0x00D3: 0x1F,   # BASILISK_MOBILE_WIRED
    0x00D4: 0x1F,   # BASILISK_MOBILE_RECEIVER
    0x00D6: 0x1F,   # BASILISK_V3_PRO_35K_PHANTOM_GREEN_EDITION_WIRED
    0x00D7: 0x1F,   # BASILISK_V3_PRO_35K_PHANTOM_GREEN_EDITION_WIRELESS
    # added from OpenRazer commit 477f4d6d3ab7 (Viper V3 Pro SE); the rest of the list
    # is still the older copy
    0x00DE: 0x1F,   # VIPER_V3_PRO_SE_WIRED
    0x00DF: 0x1F,   # VIPER_V3_PRO_SE_WIRELESS
}

# In OpenRazer's list, but not in the app's table on purpose (see providers/razer.py).
LEFT_OUT = {
    0x0095,   # Orochi V2 over Bluetooth: Windows' Bluetooth battery value covers it
    0x00CB,   # Basilisk V3 35K: a wired-only mouse
}


# ------------------------------------------------------------------ fake mouse
class FakeMouse:
    """One Razer HID interface. Answers the battery (07:80) and charging (07:84)
    requests with status 02 when the transaction id is `tid`, as the mice do."""

    def __init__(self, tid, raw_level=0xB5, charging=0):
        self.tid = tid
        self.values = {0x80: raw_level, 0x84: charging}
        self.tids = []          # transaction id of every request, in order
        self.request = None

    def reply(self):
        req = self.request
        out = bytearray(90)
        out[1:8] = req[1:8]
        if req[1] == self.tid:
            out[0] = R.STATUS_OK
            out[9] = self.values.get(req[7], 0)
        else:
            out[0] = R.STATUS_NOT_SUPPORTED
        return [0x00] + list(out)        # hidapi on Windows puts the report id first


class FakeBus:
    def __init__(self, mice):
        self.mice = mice                 # {path: FakeMouse}

    def device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.mice:
                    raise OSError("cannot open")
                self.mouse = bus.mice[path]

            def send_feature_report(self, data):
                req = bytes(data[1:])
                self.mouse.request = req
                self.mouse.tids.append(req[1])
                return len(data)

            def get_feature_report(self, report_id, size):
                return self.mouse.reply()

            def close(self):
                pass

        return FakeDevice


def entry(pid, path, name, iface=0, page=0x0001):
    return {"product_id": pid, "interface_number": iface, "usage_page": page, "usage": 2,
            "path": path, "product_string": name, "serial_number": "000000000000"}


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (R.hid, R.hidlist, R.time)
        # no real waiting: the provider sleeps between a request and its reply
        R.time = types.SimpleNamespace(time=time.time, sleep=lambda s: None)

    def tearDown(self):
        R.hid, R.hidlist, R.time = self._saved

    def poll(self, entries, mice):
        bus = FakeBus(mice)
        R.hid = types.SimpleNamespace(device=bus.device_class())
        R.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return R.RazerProvider().poll()

    def test_the_viper_v3_pro_se_receiver_shows_its_level(self):
        # the diagnostics in issue #255: pid 00df, product string without a wireless word
        e = entry(0x00DF, b"vse", "Razer Viper V3 Pro SE")
        mouse = FakeMouse(tid=0x1F, raw_level=0xB5, charging=0)
        out = self.poll([e], {b"vse": mouse})
        self.assertEqual([(d.name, d.level) for d in out],
                         [("Razer Viper V3 Pro SE", round(0xB5 / 255 * 100))])
        self.assertEqual(mouse.tids[0], 0x1F)


# ------------------------------------------------------------------ tests
class TableTest(unittest.TestCase):


    def test_viper_v3_pro_se_wired_and_wireless_share_a_name_and_tid(self):
        # issue #255: the receiver 1532:00DF was skipped as "not a known wireless device"
        # (its product string has no wireless word). OpenRazer reads both ids with 0x1f.
        self.assertEqual(R.KNOWN[0x00DE], ("Razer Viper V3 Pro SE", 0x1F))
        self.assertEqual(R.KNOWN[0x00DF], R.KNOWN[0x00DE])
        self.assertNotEqual(R.KNOWN[0x00DF], R.KNOWN[0x00C1])    # not the Viper V3 Pro


# ------------------------------------------------------------------ receiver + cable
class AsleepMouse(FakeMouse):
    """The receiver of a mouse that is not on the radio: status 04 to every request."""

    def reply(self):
        out = [0x00] + [0] * 90
        out[1] = R.STATUS_TIMEOUT
        return out


if __name__ == "__main__":
    unittest.main()
