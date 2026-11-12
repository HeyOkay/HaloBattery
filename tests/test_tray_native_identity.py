"""Regression for the stable identity actually serialized to Shell_NotifyIcon."""
import ctypes
import importlib.util
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox

_sandbox.install_guards()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
with mock.patch.dict(os.environ, {"APPDATA": _sandbox.mkdtemp(prefix="halo_native_id_")}):
    spec = importlib.util.spec_from_file_location(
        "halo_battery_native_identity", os.path.join(ROOT, "halo_battery.pyw"))
    hb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb)


class FakeWinIcon:
    """No window creation or native icon loading, just the wrapper's input."""

    def __init__(self, *args, **kwargs):
        self._hwnd = 12345
        self._message_handlers = {}

    def _message(self, *args, **kwargs):
        raise AssertionError("stable identity unexpectedly delegated to pystray")


@unittest.skipUnless(sys.platform == "win32", "requires pystray's Windows ctypes structure")
class NativeTrayIdentityTests(unittest.TestCase):
    def test_message_serializes_stable_device_uid_into_native_field(self):
        from pystray._util import win32

        key = "logitech:C15E09CD"
        expected_uid = hb.icon_uid(key)
        self.assertGreater(expected_uid, 0)
        with mock.patch.object(hb.pystray, "Icon", FakeWinIcon), \
                mock.patch.object(win32, "Shell_NotifyIcon") as notify:
            icon = hb.tray_icon(key)
            icon._message(win32.NIM_MODIFY, win32.NIF_ICON, hIcon=67890)

        notify.assert_called_once()
        code, data = notify.call_args.args
        self.assertEqual(code, win32.NIM_MODIFY)
        self.assertEqual(data.hWnd, icon._hwnd)
        # Reconstruct from the bytes Windows receives. ctypes silently accepts
        # unknown keyword attributes (such as hID) without serializing them.
        native = win32.NOTIFYICONDATAW.from_buffer_copy(bytes(data))
        self.assertEqual((data.uID, native.uID), (expected_uid, expected_uid),
                         "the stable device UID must occupy the native uID field")


if __name__ == "__main__":
    unittest.main()
