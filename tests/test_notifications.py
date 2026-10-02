"""Notifications: the "HaloBattery" app id (not "Python") and the shared notification
texts. No Windows is needed.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from test_hide_rename import dev, hb, make_app  # noqa: E402


class TextTests(unittest.TestCase):
    def test_texts_are_the_ones_users_see(self):
        self.assertEqual(hb.low_battery_text("G502", 15, False), "G502: 15% left. Time to charge.")
        self.assertEqual(hb.low_battery_text("Pad", None, True), "Pad: battery is low. Time to charge.")
        self.assertEqual(hb.fully_charged_text("G502"), "G502 is fully charged.")
        self.assertIn("Download v1.2.3…", hb.update_text("1.2.3"))

    def test_real_low_battery_alert_uses_the_shared_text(self):
        app = make_app({"low": 20})
        with mock.patch.object(hb, "DeviceIcon", __import__("test_hide_rename").FakeIcon), \
                mock.patch.object(hb, "fullscreen_app_running", return_value=False):
            app.apply([dev(level=10)])
        self.assertEqual(app.notes, [hb.low_battery_text("G502 LIGHTSPEED", 10, False)])


class AppIdTests(unittest.TestCase):
    def test_nothing_happens_off_windows(self):
        with mock.patch.object(hb.sys, "platform", "linux"):
            hb.set_app_id()           # must not raise or touch anything

    def test_registers_the_name_and_sets_the_id(self):
        values = {}
        calls = []

        class Key:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER="HKCU", REG_SZ=1,
            CreateKey=lambda root, path: (calls.append((root, path)), Key())[1],
            SetValueEx=lambda k, name, _r, _t, value: values.__setitem__(name, value))
        shell32 = types.SimpleNamespace(
            SetCurrentProcessExplicitAppUserModelID=lambda i: calls.append(("id", i)))
        ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(shell32=shell32))
        with mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.dict(sys.modules, {"winreg": winreg, "ctypes": ctypes}), \
                mock.patch.object(hb, "app_icon_png", lambda path: True):
            hb.set_app_id()
        self.assertIn(("HKCU", "Software\\Classes\\AppUserModelId\\HaloBattery"), calls)
        self.assertIn(("id", "HaloBattery"), calls)
        self.assertEqual(values["DisplayName"], "HaloBattery")
        self.assertEqual(values["IconUri"], hb.APP_ICON_PATH)

    def test_icon_png_is_drawn(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "icon.png")
            self.assertTrue(hb.app_icon_png(path))
            self.assertTrue(os.path.getsize(path) > 0)


if __name__ == "__main__":
    unittest.main()
