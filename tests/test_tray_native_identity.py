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
    def test_message_serializes_shared_app_uid_into_native_field(self):
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
                         "the shared app UID must occupy the native uID field")


    def test_shared_uid_keeps_images_tooltips_and_deletion_independent_by_hwnd(self):
        from pystray._util import win32
        live = {}
        def shell(code, data):
            identity = (data.hWnd, data.uID)
            if code == win32.NIM_ADD:
                live[identity] = {"image": data.hIcon, "tip": data.szTip}
            elif code == win32.NIM_DELETE:
                del live[identity]
            elif code == win32.NIM_MODIFY:
                if data.uFlags & win32.NIF_ICON:
                    live[identity]["image"] = data.hIcon
                if data.uFlags & win32.NIF_TIP:
                    live[identity]["tip"] = data.szTip
            return True
        with mock.patch.object(hb.pystray, "Icon", FakeWinIcon), \
                mock.patch.object(win32, "Shell_NotifyIcon", side_effect=shell) as notify:
            first, second = hb.tray_icon("mouse:1"), hb.tray_icon("headset:2")
            first._hwnd, second._hwnd = 101, 202
            flags = win32.NIF_ICON | win32.NIF_TIP
            first._message(win32.NIM_ADD, flags, hIcon=11, szTip="Mouse: 55%")
            second._message(win32.NIM_ADD, flags, hIcon=22, szTip="Headset: 100%")
            uid = hb.tray_history.MASTER_UID
            self.assertEqual(live, {(101, uid): {"image": 11, "tip": "Mouse: 55%"},
                                    (202, uid): {"image": 22, "tip": "Headset: 100%"}})
            adds = [c.args[1] for c in notify.call_args_list if c.args[0] == win32.NIM_ADD]
            self.assertTrue(all(data.szTip == "HaloBattery" for data in adds))
            first._message(win32.NIM_MODIFY, win32.NIF_ICON, hIcon=33)
            self.assertEqual(live[(202, uid)]["image"], 22)
            self.assertEqual(live[(101, uid)]["image"], 33)
            first._message(win32.NIM_DELETE, 0)
            self.assertEqual(live, {(202, uid): {"image": 22, "tip": "Headset: 100%"}})

    def test_failed_add_does_not_restore_a_tooltip_on_an_absent_icon(self):
        from pystray._util import win32
        with mock.patch.object(hb.pystray, "Icon", FakeWinIcon), \
                mock.patch.object(win32, "Shell_NotifyIcon", return_value=False) as notify:
            icon = hb.tray_icon("mouse:1")
            with self.assertRaises(OSError):
                icon._message(win32.NIM_ADD, win32.NIF_TIP, szTip="Mouse: 55%")
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
