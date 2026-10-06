"""Tests for portable mode (portable.txt next to the app). No tray, no hardware.

Run from the repository root:

    python -m unittest discover -s tests
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# The app writes its log and settings to %APPDATA%\HaloBattery when it loads. Point it
# at a temporary folder, so the tests never touch the real log or settings.
_real_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="halo_battery_test_")
try:
    spec = importlib.util.spec_from_file_location("halo_battery", os.path.join(ROOT, "halo_battery.pyw"))
    hb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb)
finally:
    if _real_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _real_appdata

APPDATA = os.path.join("C:\\", "Users", "test", "AppData", "Roaming")


class PortableModeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app_dir = tmp.name

    def make_marker(self):
        with open(os.path.join(self.app_dir, hb.PORTABLE_MARKER), "w") as f:
            f.write("")

    def test_without_marker_data_goes_to_appdata(self):
        self.assertEqual(hb._calculate_data_dir(self.app_dir, APPDATA),
                         (False, os.path.join(APPDATA, "HaloBattery")))

    def test_marker_keeps_data_in_app_folder(self):
        self.make_marker()
        self.assertEqual(hb._calculate_data_dir(self.app_dir, APPDATA), (True, self.app_dir))

    def test_read_only_app_folder_falls_back_to_appdata(self):
        self.make_marker()
        with mock.patch.object(hb, "_writable", return_value=False):
            self.assertEqual(hb._calculate_data_dir(self.app_dir, APPDATA),
                             (False, os.path.join(APPDATA, "HaloBattery")))

    def test_writable_check(self):
        self.assertTrue(hb._writable(self.app_dir))
        self.assertFalse(hb._writable(os.path.join(self.app_dir, "missing")))
        self.assertEqual(os.listdir(self.app_dir), [])   # the probe file is gone

    def test_default_appdata_is_the_module_one(self):
        with mock.patch.object(hb, "APPDATA_DIR", APPDATA):
            self.assertEqual(hb._calculate_data_dir(self.app_dir),
                             (False, os.path.join(APPDATA, "HaloBattery")))


if __name__ == "__main__":
    unittest.main()
