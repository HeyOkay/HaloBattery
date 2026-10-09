"""Tests for providers/mchose_v9.py. No hardware is needed.

The fake headset answers the way JoaoKSS/MCHOSE_v9_PRO_Controller's
HIDService.query_status describes (mchose_qt.py lines 146-179): a 64-byte output
report starting ``55 65 01`` is answered by a 64-byte input report whose first two
bytes echo ``55 65``, byte 2 is the percent and byte 3 a status code. The tests
also pin the safety rules: only the V9 Pro pair (291D:385D) is opened and only its
vendor collection is written to, and the Windows "Incorrect function" fallback
goes out as a feature report.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import mchose_v9 as M  # noqa: E402

# hidapi 0.14's Windows text for ERROR_INVALID_FUNCTION.
INCORRECT_FUNCTION = "WriteFile: (0x00000001) Incorrect function."


def reply(level=46, status=0, header=(0x55, 0x65)):
    return list(header) + [level, status] + [0] * 60


def entry(vid, pid, path, iface=0, page=0xFF90, usage=0x0001, name=""):
    return {"vendor_id": vid, "product_id": pid, "path": path,
            "interface_number": iface, "usage_page": page, "usage": usage,
            "product_string": name}


class FakeHeadset:
    """One collection of the headset's HID interface.

    write / feature: "ok" accepted (returns the length, as hidapi does), "-1"
    returns -1, "raise" raises OSError with `error_text`.
    """

    def __init__(self, data=None, silent=False, write="ok", feature="ok",
                 error_text=INCORRECT_FUNCTION):
        self.data = data if data is not None else reply()
        self.silent = silent
        self.write_mode = write
        self.feature_mode = feature
        self.error_text = error_text
        self.writes = []
        self.features = []
        self.opened = 0

    def open_path(self, path):
        self.path = path
        self.opened += 1

    def close(self):
        pass

    def error(self):
        return self.error_text

    def _do(self, mode, buf):
        if mode == "raise":
            raise OSError(self.error_text)
        if mode == "-1":
            return -1
        return len(buf)

    def write(self, data):
        self.writes.append(bytes(data))
        return self._do(self.write_mode, data)

    def send_feature_report(self, data):
        self.features.append(bytes(data))
        return self._do(self.feature_mode, data)

    def read(self, size, timeout_ms=0):
        return [] if self.silent else list(self.data)


class FakeBus:
    """The hid module the provider sees: enumerate() plus one hid.device() per path."""

    def __init__(self, entries, devices):
        self.entries = entries           # the hid.enumerate() dictionaries
        self.devices = devices           # {path: FakeHeadset}
        self.hid = types.SimpleNamespace(device=self._device_class)
        self.hidlist = types.SimpleNamespace(enumerate=self._enumerate)

    def _enumerate(self, vid=0):
        return [e for e in self.entries if not vid or e["vendor_id"] == vid]

    def _device_class(self):
        bus = self

        class FakeDevice:
            def open_path(self, path):
                if path not in bus.devices:
                    raise OSError("cannot open")
                self._d = bus.devices[path]
                self._d.open_path(path)

            def close(self):
                pass

            def error(self):
                return self._d.error()

            def write(self, data):
                return self._d.write(data)

            def send_feature_report(self, data):
                return self._d.send_feature_report(data)

            def read(self, size, timeout_ms=0):
                return self._d.read(size, timeout_ms)

        return FakeDevice()

    def poll(self):
        M.hid = self.hid
        M.hidlist = self.hidlist
        p = M.MchoseV9Provider()
        return p.poll(), p.diagnostics()


class ParseStatusTest(unittest.TestCase):
    def test_a_valid_reply_is_the_level_and_the_status(self):
        self.assertEqual(M.parse_status(reply(46, 0)), (46, 0))
        self.assertEqual(M.parse_status(reply(46, 1)), (46, 1))
        self.assertEqual(M.parse_status(reply(0, 2)), (0, 2))

    def test_a_bad_header_is_refused(self):
        self.assertIsNone(M.parse_status(reply(46, 0, header=(0xAA, 0x65))))
        self.assertIsNone(M.parse_status(reply(46, 0, header=(0x55, 0x11))))
        self.assertIsNone(M.parse_status(reply(46, 0, header=(0x65, 0x55))))

    def test_a_level_above_100_is_refused(self):
        self.assertIsNone(M.parse_status(reply(101, 0)))
        self.assertIsNone(M.parse_status(reply(255, 0)))

    def test_a_short_or_empty_reply_is_refused(self):
        self.assertIsNone(M.parse_status([]))
        self.assertIsNone(M.parse_status(None))
        self.assertIsNone(M.parse_status([0x55, 0x65, 46]))

    def test_the_request_is_the_source_frame(self):
        self.assertEqual(len(M.REQUEST), 64)
        self.assertEqual(M.REQUEST[:3], bytes([0x55, 0x65, 0x01]))
        self.assertEqual(M.make_request(), M.REQUEST)
        self.assertEqual(M.make_feature_request(), M.REQUEST)


class PollTest(unittest.TestCase):
    def setUp(self):
        self._saved = (M.hid, M.hidlist, M.time)
        M.time = types.SimpleNamespace(sleep=lambda s: None)

    def tearDown(self):
        M.hid, M.hidlist, M.time = self._saved

    def test_291d_385d_is_shown_as_a_headset_at_46(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001, name="MCHOSE V9 PRO")
        bus = FakeBus([e], {b"v9": FakeHeadset(reply(46, 0))})
        out, diag = bus.poll()
        self.assertEqual(len(out), 1)
        st = out[0]
        # status 0x00 = on the cable (measured), so charging
        self.assertEqual((st.key, st.level, st.charging, st.online, st.kind, st.source),
                         ("mchose_v9", 46, True, True, "headset", "mchose_v9"))
        self.assertEqual(st.name, "MCHOSE V9 PRO")
        self.assertEqual(bus.devices[b"v9"].writes[0], M.REQUEST)

    def test_the_product_string_falls_back_to_the_default_name(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001, name="")
        bus = FakeBus([e], {b"v9": FakeHeadset(reply(46, 0))})
        out, _ = bus.poll()
        self.assertEqual(out[0].name, "MCHOSE V9 Pro")

    def test_3837_6008_and_600a_are_not_claimed_by_this_provider(self):
        # The V9 Turbo / Turbo+ (3837:6008/600A) use the same 65 01 frame but are read
        # another way, and are covered by the open PR #215; this provider must neither
        # enumerate nor open them.
        self.assertEqual(M.V9_VIDS, (0x291D,))
        self.assertEqual(M.ALLOWED, frozenset({(0x291D, 0x385D)}))
        for pid in (0x6008, 0x600A, 0x100B):
            self.assertNotIn((0x3837, pid), M.ALLOWED)

    def test_status_one_does_not_report_charging(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001)
        bus = FakeBus([e], {b"v9": FakeHeadset(reply(46, 1))})
        out, _ = bus.poll()
        self.assertFalse(out[0].charging)

    def test_status_two_does_not_report_charging(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001)
        bus = FakeBus([e], {b"v9": FakeHeadset(reply(46, 2))})
        out, _ = bus.poll()
        # reporter: 0x02 while NOT on the cable, so never shown as charging
        self.assertFalse(out[0].charging)

    def test_status_zero_is_charging(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001)
        bus = FakeBus([e], {b"v9": FakeHeadset(reply(46, 0))})
        out, _ = bus.poll()
        # reporter: 0x00 while on the cable at 60%, 0x02 off it
        self.assertTrue(out[0].charging)

    def test_a_non_vendor_collection_is_never_written(self):
        e = entry(0x291D, 0x385D, b"audio", page=0x000B, usage=0x0001)
        dev = FakeHeadset(reply(46, 0))
        bus = FakeBus([e], {b"audio": dev})
        out, diag = bus.poll()
        self.assertEqual(out, [])
        self.assertEqual(dev.writes, [])
        self.assertEqual(dev.features, [])
        self.assertTrue(any("no vendor collection" in line for line in diag))

    def test_only_the_vendor_collection_is_opened(self):
        e = [entry(0x291D, 0x385D, b"audio", page=0x000B, usage=0x0001),
             entry(0x291D, 0x385D, b"vendor", page=0xFF00, usage=0x0001)]
        audio = FakeHeadset(reply(46, 0))
        vendor = FakeHeadset(reply(46, 0))
        bus = FakeBus(e, {b"audio": audio, b"vendor": vendor})
        out, _ = bus.poll()
        self.assertEqual(out[0].level, 46)
        self.assertEqual(audio.writes, [])
        self.assertEqual(len(vendor.writes), 1)

    def test_a_product_outside_the_allowlist_is_left_alone(self):
        # 291D:9999 is not the V9 Pro, so nothing is written to it (same guard that
        # keeps the 3837 mice and the Turbo pair alone in production).
        e = entry(0x291D, 0x9999, b"other", page=0xFF00, usage=0x0001)
        dev = FakeHeadset(reply(46, 0))
        bus = FakeBus([e], {b"other": dev})
        out, diag = bus.poll()
        self.assertEqual(out, [])
        self.assertEqual(dev.writes, [])
        self.assertTrue(any("not the V9 Pro" in line for line in diag))

    def test_no_reply_is_no_icon(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001)
        bus = FakeBus([e], {b"v9": FakeHeadset(silent=True)})
        out, diag = bus.poll()
        self.assertEqual(out, [])
        self.assertTrue(any("no 55 65 reply" in line for line in diag))

    def test_incorrect_function_retries_as_a_feature_report(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001)
        dev = FakeHeadset(reply(46, 0), write="raise")
        bus = FakeBus([e], {b"v9": dev})
        out, diag = bus.poll()
        self.assertEqual(out[0].level, 46)
        self.assertEqual(dev.features[0], M.make_feature_request())
        self.assertTrue(any("feature report accepted" in line for line in diag))

    def test_an_unrelated_write_error_is_not_retried_as_a_feature_report(self):
        e = entry(0x291D, 0x385D, b"v9", page=0xFF00, usage=0x0001)
        dev = FakeHeadset(reply(46, 0), write="raise",
                          error_text="device disconnected")
        bus = FakeBus([e], {b"v9": dev})
        out, diag = bus.poll()
        self.assertEqual(out, [])
        self.assertEqual(dev.features, [])
        self.assertTrue(any("device disconnected" in line for line in diag))


class MouseProviderExclusionTest(unittest.TestCase):
    """The mouse provider (mchose.py) must not open or write the V9 headset ids."""

    def test_the_mouse_provider_still_declares_the_headset_pids(self):
        from providers import mchose as MC
        # mchose.py still skips the 3837 pair so its inverted 0x06 frame never reaches a
        # headset. This provider does not claim those ids - the open PR #215 owns them.
        self.assertEqual(tuple(sorted(MC.HEADSET_PIDS)), (0x6008, 0x600A))
        for pid in MC.HEADSET_PIDS:
            self.assertNotIn((0x3837, pid), M.ALLOWED)

    def test_the_mouse_provider_leaves_a_v9_alone(self):
        from providers import mchose as MC
        saved = (MC.hid, MC.hidlist)
        opened = []

        class Dev:
            def open_path(self, path):
                opened.append(path)

            def close(self):
                pass

        try:
            MC.hid = types.SimpleNamespace(device=lambda: Dev())
            MC.hidlist = types.SimpleNamespace(
                enumerate=lambda vid=0: ([{"vendor_id": 0x3837, "product_id": 0x600A,
                                           "path": b"hs", "interface_number": 0,
                                           "usage_page": 0xFF90, "usage": 0x0001,
                                           "product_string": "MCHOSE V9 Turbo+"}]
                                         if vid == 0x3837 else []))
            out = MC.MchoseProvider().poll()
        finally:
            MC.hid, MC.hidlist = saved
        self.assertEqual(out, [])
        self.assertEqual(opened, [])


if __name__ == "__main__":
    unittest.main()
