"""sync_installed_version(): after an in-app update, Settings > Apps shows the new
version - but only for the copy the installer put there. Uses a fake winreg."""
import importlib.util
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers
import _sandbox  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_real_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = _sandbox.mkdtemp(prefix="halo_battery_test_")
try:
    spec = importlib.util.spec_from_file_location("halo_battery_installed", os.path.join(ROOT, "halo_battery.pyw"))
    hb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb)
finally:
    if _real_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _real_appdata


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, values=None):
        self.values = values            # None: the key does not exist

    def OpenKey(self, root, sub, reserved=0, access=0):
        if self.values is None:
            raise FileNotFoundError(sub)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, k, name, reserved, kind, value):
        self.values[name] = value


APP = r"C:\Users\x\AppData\Local\Programs\HaloBattery"


@unittest.skipUnless(sys.platform == "win32", "Windows registry paths")
class SyncInstalledVersionTests(unittest.TestCase):
    def run_sync(self, reg, app_dir=APP, version="1.17.0", frozen=True):
        with mock.patch.dict(sys.modules, {"winreg": reg}), \
             mock.patch.object(hb.sys, "frozen", frozen, create=True), \
             mock.patch.object(hb, "PRETEND_VERSION", ""):
            return hb.sync_installed_version(app_dir, version)

    def test_updates_version_of_installed_copy(self):
        reg = FakeWinreg({"InstallLocation": APP + "\\", "DisplayVersion": "1.16.0"})
        self.assertTrue(self.run_sync(reg))
        self.assertEqual(reg.values["DisplayVersion"], "1.17.0")

    def test_same_version_writes_nothing(self):
        reg = FakeWinreg({"InstallLocation": APP, "DisplayVersion": "1.17.0"})
        self.assertFalse(self.run_sync(reg))

    def test_install_location_compare_ignores_case(self):
        reg = FakeWinreg({"InstallLocation": APP.upper(), "DisplayVersion": "1.16.0"})
        self.assertTrue(self.run_sync(reg))

    def test_missing_display_version_is_written(self):
        reg = FakeWinreg({"InstallLocation": APP})
        self.assertTrue(self.run_sync(reg))
        self.assertEqual(reg.values["DisplayVersion"], "1.17.0")

    def test_portable_copy_elsewhere_leaves_entry_alone(self):
        reg = FakeWinreg({"InstallLocation": APP, "DisplayVersion": "1.16.0"})
        self.assertFalse(self.run_sync(reg, app_dir=r"E:\Tools\HaloBattery"))
        self.assertEqual(reg.values["DisplayVersion"], "1.16.0")

    def test_not_installed(self):
        self.assertFalse(self.run_sync(FakeWinreg(None)))

    def test_entry_without_install_location(self):
        self.assertFalse(self.run_sync(FakeWinreg({"DisplayVersion": "1.16.0"})))

    def test_running_from_source_does_nothing(self):
        reg = FakeWinreg({"InstallLocation": APP, "DisplayVersion": "1.16.0"})
        self.assertFalse(self.run_sync(reg, frozen=False))
        self.assertEqual(reg.values["DisplayVersion"], "1.16.0")

    def test_pretend_version_does_nothing(self):
        reg = FakeWinreg({"InstallLocation": APP, "DisplayVersion": "1.16.0"})
        with mock.patch.dict(sys.modules, {"winreg": reg}), \
             mock.patch.object(hb.sys, "frozen", True, create=True), \
             mock.patch.object(hb, "PRETEND_VERSION", "1.0.0"):
            self.assertFalse(hb.sync_installed_version(APP, "1.0.0"))


if __name__ == "__main__":
    unittest.main()
