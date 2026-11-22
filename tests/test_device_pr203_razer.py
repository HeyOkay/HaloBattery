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


# ------------------------------------------------------------------ tests
class TableTest(unittest.TestCase):


    def test_the_pro_type_ultra_receiver_is_known(self):
        # #196: the reporter's careful retest shows the receiver answering 0x9F
        # (status 02) while 0x1F comes back 04 even with the keyboard awake, so 0x9F
        # is the preferred id - 0x1F stays reachable through TRANSACTION_IDS.
        self.assertEqual(R.KNOWN[0x027B], ("Razer Pro Type Ultra", 0x9F))

    def test_the_pro_type_ultra_wired_id_stays_out(self):
        # 0x0277's only source is an open OpenRazer pull request (#2739); until a
        # unit can be tested it is not listed, so it is left alone like any other
        # unknown wired device.
        self.assertNotIn(0x0277, R.KNOWN)
        self.assertFalse(R.maybe_wireless(0x0277, "Razer Pro Type Ultra"))


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

    def poll_with(self, provider, entries, mice):
        # the same as poll(), but with the caller's provider - its caches must
        # survive across polls for the recovery tests below
        bus = FakeBus(mice)
        R.hid = types.SimpleNamespace(device=bus.device_class())
        R.hidlist = types.SimpleNamespace(enumerate=lambda vid=0: list(entries))
        return provider.poll()

    def test_the_blackwidow_answers_through_its_own_collection(self):
        # The keyboard answers on a control collection that is not a vendor page, so
        # the probe order (which ranks 0001/ff00 first) must still reach it. The fake's
        # page 0059 is a stand-in: the unit test only pins that ranking does not filter.
        e = entry(0x025C, b"kbd", "Razer BlackWidow V3 Pro", iface=3, page=0x0059)
        mouse = FakeMouse(tid=0x9F, raw_level=0xB5, charging=1)      # 0xB5 -> 71%
        out = self.poll([e], {b"kbd": mouse})
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].level, out[0].charging), (round(0xB5 / 255 * 100), True))
        self.assertEqual(mouse.tids[0], 0x9F)


class PollRazerTest(PollTest):


    def test_pro_type_ultra_answers_on_its_wireless_tid(self):
        # #196: the reporter's receiver reads with 0x9F and answers 0x1F with 04;
        # the fake refuses every other transaction id, so the preferred one must go
        # out first. (The collection is not pinned here: the fake answers on any
        # path.)
        e = entry(0x027B, b"kbd", "Razer Pro Type Ultra", iface=3, page=0x0059)
        kbd = FakeMouse(tid=0x9F, raw_level=0xB5, charging=1)      # 0xB5 -> 71%
        out = self.poll([e], {b"kbd": kbd})
        self.assertEqual([(s.name, s.level, s.charging) for s in out],
                         [("Razer Pro Type Ultra", round(0xB5 / 255 * 100), True)])
        self.assertEqual(kbd.tids[0], 0x9F)

    def test_a_timeout_on_the_preferred_id_still_tries_the_next_one(self):
        # #196, the second half: a 04 from a collection that has never answered OK
        # must not end that collection's transaction-id list. The reporter's
        # receiver answered the preferred id with 04 and read on another; before
        # the fix the walk left the collection and the fallback ids were never sent.
        e = entry(0x00A8, b"if0", "Razer Naga V2 Pro")
        mouse = TimeoutOnThePreferredId(0x1F, 0x9F, raw_level=0x80)   # 0x80 -> 50%
        out = self.poll([e], {b"if0": mouse})
        self.assertEqual([(s.name, s.level) for s in out], [("Razer Naga V2 Pro", 50)])
        self.assertEqual(mouse.tids[0], 0x1F)      # the preferred id goes first
        self.assertIn(0x9F, mouse.tids)            # then the walk goes on


# ------------------------------------------------------------------ receiver + cable
class AsleepMouse(FakeMouse):
    """The receiver of a mouse that is not on the radio: status 04 to every request."""

    def reply(self):
        out = [0x00] + [0] * 90
        out[1] = R.STATUS_TIMEOUT
        return out


class TimeoutOnThePreferredId(FakeMouse):
    """A receiver that answers 04 to one transaction id and normally to `tid` - the
    Pro Type Ultra's shape on the reporter's unit (#196): the preferred id came back
    04 while the next one read."""

    def __init__(self, preferred, tid, raw_level=0xB5, charging=0):
        super().__init__(tid, raw_level, charging)
        self.preferred = preferred

    def reply(self):
        if self.request[1] == self.preferred:
            out = [0x00] + [0] * 90
            out[1] = R.STATUS_TIMEOUT
            return out
        return super().reply()


class SleepRecoveryTest(PollTest):
    """#196 follow-up: the keyboard comes back after a sleep without a restart.

    The Pro Type Ultra's receiver showed three faces in the reporter's run: the
    keyboard answers on one collection, another collection of the same receiver
    answers 04 ("not responding") no matter what, and the keyboard being asleep
    (or its host restarting) changes which collection answers. A collection that
    never answered OK must never be pinned by a timeout, and the collection that
    did answer must survive a failed poll - otherwise a wrong collection's 04
    shadows the walk forever (a cached timeout is returned before the walk can
    run again) and only a restart cures it.
    """

    KBD = entry(0x027B, b"kbd", "Razer Pro Type Ultra", iface=3, page=0x0059)
    SIBLING = entry(0x027B, b"sib", "Razer Pro Type Ultra", iface=5, page=0xFF00)
    KEY = (0x027B, "000000000000")

    def test_a_wrong_collection_is_never_pinned_and_answers_come_back(self):
        p = R.RazerProvider()
        # awake: the keyboard answers on its own collection
        out = self.poll_with(p, [self.KBD, self.SIBLING],
                             {b"kbd": FakeMouse(0x1F, raw_level=0x80),
                              b"sib": AsleepMouse(0x1F)})
        self.assertEqual([(s.online, s.level) for s in out], [(True, 50)])
        # asleep: the working collection refuses to open for one poll (a sleeping
        # device can do that) while the sibling answers 04 for the same command
        out = self.poll_with(p, [self.KBD, self.SIBLING], {b"sib": AsleepMouse(0x1F)})
        self.assertEqual([(s.online, s.level) for s in out], [(False, 50)])   # greyed
        self.assertIsNone(p._cache.get(self.KEY))    # nothing pinned by the timeout
        self.assertNotIn(b"kbd", p._dead)            # the proven collection is kept
        # awake again - without restarting anything:
        out = self.poll_with(p, [self.KBD, self.SIBLING],
                             {b"kbd": FakeMouse(0x1F, raw_level=0x80),
                              b"sib": AsleepMouse(0x1F)})
        self.assertEqual([(s.online, s.level) for s in out], [(True, 50)])

    def test_the_proven_collection_timing_out_keeps_its_cached_tid(self):
        p = R.RazerProvider()
        out = self.poll_with(p, [self.KBD], {b"kbd": FakeMouse(0x1F, raw_level=0x80)})
        self.assertEqual([s.online for s in out], [True])
        out = self.poll_with(p, [self.KBD], {b"kbd": AsleepMouse(0x1F)})
        self.assertEqual([(s.online, s.level) for s in out], [(False, 50)])
        cand = p._cache[self.KEY]
        self.assertEqual((cand.path, cand.tid), (b"kbd", 0x1F))


if __name__ == "__main__":
    unittest.main()
