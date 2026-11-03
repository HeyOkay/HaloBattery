"""Tests for the settings file, "Start with Windows" from a temporary folder, and the
tray icons' stable ids and cached icon handles in halo_battery.pyw. No tray, no
hardware: pystray's icon is replaced by a small fake one.

Run from the repository root:

    python -m unittest discover -s tests
"""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers
import _sandbox  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# The app writes its log and settings to %APPDATA%\HaloBattery when it loads. Point it
# at a temporary folder, so the tests never touch the real log or settings.
_real_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = _sandbox.mkdtemp(prefix="halo_battery_test_")
try:
    spec = importlib.util.spec_from_file_location("halo_battery_robust", os.path.join(ROOT, "halo_battery.pyw"))
    hb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb)
finally:
    if _real_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _real_appdata


class FakeWinIcon:
    """The parts of pystray's Windows icon that load and release icon handles."""
    loads = 0

    def __init__(self, *args, **kw):
        self._icon_handle = None
        self.icon = None
        self.released = []
        self._message_handlers = {}            # pystray's window message -> handler table

    def _assert_icon_handle(self):
        if self._icon_handle:
            return
        FakeWinIcon.loads += 1                 # pystray writes a temporary .ico here
        self._icon_handle = 1000 + FakeWinIcon.loads

    def _release_icon(self):
        if self._icon_handle:
            self.released.append(self._icon_handle)
            self._icon_handle = None

    def _update_icon(self):                    # what pystray does for every new image
        self._release_icon()
        self._assert_icon_handle()


class TrayIconTests(unittest.TestCase):
    def setUp(self):
        FakeWinIcon.loads = 0
        p = mock.patch.object(hb.pystray, "Icon", FakeWinIcon)
        p.start()
        self.addCleanup(p.stop)
        d = mock.patch.object(hb, "_destroy_handles", lambda cache: cache.clear())
        d.start()
        self.addCleanup(d.stop)

    def test_animation_frames_are_loaded_once(self):
        ic = hb.tray_icon("logitech:C15E09CD")
        frames = [object() for _ in range(30)]
        for _cycle in range(3):
            for f in frames:
                ic.icon = f
                ic._update_icon()
        self.assertEqual(FakeWinIcon.loads, 30)
        self.assertEqual(ic.released, [])      # handles are kept, not destroyed each frame

    def test_cache_is_bounded(self):
        ic = hb.tray_icon("logitech:C15E09CD")
        for _ in range(hb.ICON_HANDLE_CACHE * 3):
            ic.icon = object()
            ic._update_icon()
        self.assertLessEqual(len(ic._hb_handles), hb.ICON_HANDLE_CACHE)
        self.assertIsNotNone(ic._icon_handle)

    def test_forget_handles_empties_the_cache(self):
        ic = hb.tray_icon("logitech:C15E09CD")
        ic.icon = object()
        ic._update_icon()
        ic.forget_handles()
        self.assertEqual(ic._hb_handles, {})
        self.assertIsNone(ic._icon_handle)

    def test_icon_does_not_veto_the_end_of_the_session(self):
        # pystray answers an unhandled message with 0, which for WM_QUERYENDSESSION is "no" (#242)
        ic = hb.tray_icon("x")
        self.assertEqual(ic._message_handlers[hb.WM_QUERYENDSESSION](0, 0), 1)

    def test_the_class_follows_pystray_icon(self):
        self.assertIsInstance(hb.tray_icon("x"), FakeWinIcon)

    def test_icon_uid_is_stable_and_differs_per_device(self):
        self.assertEqual(hb.icon_uid("logitech:C15E09CD"), hb.icon_uid("logitech:C15E09CD"))
        self.assertNotEqual(hb.icon_uid("logitech:C15E09CD"), hb.icon_uid("xinput:0"))
        self.assertNotEqual(hb.icon_uid(hb.IDLE_KEY), hb.icon_uid("xinput:0"))
        self.assertTrue(0 <= hb.icon_uid("xinput:0") <= 0x7FFFFFFF)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = _sandbox.mkdtemp(prefix="halo_cfg_")
        self.path = os.path.join(self.dir, "config.json")
        p = mock.patch.object(hb, "CONFIG_PATH", self.path)
        p.start()
        self.addCleanup(p.stop)

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_no_file_gives_the_defaults(self):
        self.assertEqual(hb.load_config(), hb.DEFAULTS)

    def test_damaged_file_is_kept_aside(self):
        self.write('{"interval": 30, "low":')
        self.assertEqual(hb.load_config(), hb.DEFAULTS)
        self.assertTrue(os.path.exists(self.path + ".bad"))

    def test_not_an_object(self):
        self.write("[1, 2, 3]")
        self.assertEqual(hb.load_config(), hb.DEFAULTS)

    def test_wrong_values_fall_back_one_by_one(self):
        self.write(json.dumps({"interval": "60", "low": 15, "bluetooth": 1,
                               "badges": False, "icon_theme": 3,
                               "names": {"k": "My mouse"}}))
        cfg = hb.load_config()
        self.assertEqual(cfg["interval"], hb.DEFAULTS["interval"])   # a string
        self.assertEqual(cfg["low"], 15)
        self.assertEqual(cfg["bluetooth"], True)                     # 1 is not a bool
        self.assertEqual(cfg["badges"], False)
        self.assertEqual(cfg["icon_theme"], "auto")
        self.assertEqual(cfg["names"], {"k": "My mouse"})            # other keys are kept

    def test_interval_out_of_range(self):
        self.write(json.dumps({"interval": 0}))
        self.assertEqual(hb.load_config()["interval"], hb.DEFAULTS["interval"])

    def test_save_is_atomic_and_round_trips(self):
        cfg = dict(hb.DEFAULTS, interval=30, names={"k": "Мышь"})
        hb.save_config(cfg)
        self.assertEqual(hb.load_config()["names"], {"k": "Мышь"})
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_failed_save_keeps_the_old_file(self):
        hb.save_config(dict(hb.DEFAULTS, interval=30))
        hb.save_config(dict(hb.DEFAULTS, interval=object()))        # cannot be written
        self.assertEqual(hb.load_config()["interval"], 30)
        self.assertFalse(os.path.exists(self.path + ".tmp"))


class TempFolderTests(unittest.TestCase):
    def test_copy_in_temp_is_detected(self):
        tmp = tempfile.gettempdir()
        exe = os.path.join(tmp, "Rar$EXa1234.5678", "HaloBattery", "HaloBattery.exe")
        with mock.patch.object(hb.sys, "frozen", True, create=True), \
                mock.patch.object(hb.sys, "executable", exe):
            self.assertTrue(hb.running_from_temp())

    def test_copy_elsewhere_is_not(self):
        exe = os.path.join(os.path.dirname(tempfile.gettempdir().rstrip("\\/")) or "/",
                           "Tools", "HaloBattery", "HaloBattery.exe")
        with mock.patch.object(hb.sys, "frozen", True, create=True), \
                mock.patch.object(hb.sys, "executable", exe), \
                mock.patch.dict(os.environ, {"TEMP": "", "TMP": ""}):
            self.assertFalse(hb.running_from_temp())

    def test_autostart_is_not_set_from_temp(self):
        with mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.object(hb, "running_from_temp", lambda: True):
            self.assertFalse(hb.set_autostart(True))        # returns before touching winreg


if __name__ == "__main__":
    unittest.main()
