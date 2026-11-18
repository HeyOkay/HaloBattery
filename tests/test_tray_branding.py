"""Brand the persisted app row while retaining live per-device native icons."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_tray_native_identity import hb, FakeWinIcon


@unittest.skipUnless(sys.platform == "win32", "Windows native tray backend")
class TrayBrandingTests(unittest.TestCase):
    def setUp(self):
        from pystray._util import win32
        self.win32 = win32
        for target, name, kwargs in (
            (hb.pystray, "Icon", {"new": FakeWinIcon}),
            (win32, "Shell_NotifyIcon", {"return_value": True}),
            (hb.tray_dpi, "load_icon_handle", {"return_value": 900}),
            (hb.tray_dpi, "destroy_icon_handle", {}),
        ):
            patcher = mock.patch.object(target, name, **kwargs)
            value = patcher.start()
            self.addCleanup(patcher.stop)
            if name == "Shell_NotifyIcon": self.notify = value
            if name == "load_icon_handle": self.load = value
            if name == "destroy_icon_handle": self.destroy = value
        self.icon = hb.tray_icon("mouse:test")
        self.icon._hb_branding = True

    def add(self):
        self.icon._message(self.win32.NIM_ADD, self.win32.NIF_ICON | self.win32.NIF_TIP,
                           hIcon=11, szTip="Mouse: 55%")

    def test_logo_add_restores_device_image_and_tooltip_immediately(self):
        self.add()
        first, second = self.notify.call_args_list
        self.assertEqual((first.args[0], first.args[1].hIcon, first.args[1].szTip),
                         (self.win32.NIM_ADD, 900, hb.TRAY_APP_TIP))
        self.assertEqual((second.args[0], second.args[1].hIcon, second.args[1].szTip),
                         (self.win32.NIM_MODIFY, 11, "Mouse: 55%"))
        self.assertEqual(second.args[1].uFlags, self.win32.NIF_ICON | self.win32.NIF_TIP)
        self.assertEqual(first.args[1].uID, hb.tray_history.MASTER_UID)
        self.assertEqual(second.args[1].hWnd, self.icon._hwnd)

    def test_later_device_frames_do_not_use_or_reload_logo(self):
        self.add()
        self.notify.reset_mock()
        for handle in (12, 13, 14):
            self.icon._message(self.win32.NIM_MODIFY, self.win32.NIF_ICON, hIcon=handle)
        self.assertEqual([call.args[1].hIcon for call in self.notify.call_args_list], [12, 13, 14])
        self.load.assert_called_once()

    def test_failed_add_does_not_modify_and_retry_reuses_logo_handle(self):
        self.notify.side_effect = [False, True, True]
        with self.assertRaises(OSError): self.add()
        self.notify.assert_called_once()
        self.add()
        self.assertEqual([call.args[0] for call in self.notify.call_args_list],
                         [self.win32.NIM_ADD, self.win32.NIM_ADD, self.win32.NIM_MODIFY])
        self.load.assert_called_once()

    def test_failed_device_restore_deletes_accepted_add_before_retry(self):
        self.notify.side_effect = [True, False, True, True, True]
        with self.assertRaises(OSError): self.add()
        self.assertEqual([call.args[0] for call in self.notify.call_args_list],
                         [self.win32.NIM_ADD, self.win32.NIM_MODIFY, self.win32.NIM_DELETE])
        self.add()
        self.assertEqual(self.notify.call_args.args[1].hIcon, 11)
        self.load.assert_called_once()

    def test_failed_restore_and_failed_rollback_repair_live_registration(self):
        self.notify.side_effect = [True, False, False, True]
        with self.assertRaises(OSError): self.add()
        self.assertTrue(self.icon._hb_pending_brand_restore)
        self.add()
        self.assertEqual([call.args[0] for call in self.notify.call_args_list],
                         [self.win32.NIM_ADD, self.win32.NIM_MODIFY,
                          self.win32.NIM_DELETE, self.win32.NIM_MODIFY])
        self.assertEqual(self.notify.call_args.args[1].hIcon, 11)
        self.assertFalse(self.icon._hb_pending_brand_restore)

    def test_pending_registration_recovers_when_explorer_lost_the_icon(self):
        self.notify.side_effect = [True, False, False, False, False, True, True]
        with self.assertRaises(OSError): self.add()
        self.add()
        self.assertEqual([call.args[0] for call in self.notify.call_args_list],
                         [self.win32.NIM_ADD, self.win32.NIM_MODIFY, self.win32.NIM_DELETE,
                          self.win32.NIM_MODIFY, self.win32.NIM_DELETE,
                          self.win32.NIM_ADD, self.win32.NIM_MODIFY])
        self.assertFalse(self.icon._hb_pending_brand_restore)
        self.load.assert_called_once()

    def test_logo_handle_survives_dpi_pruning_and_is_freed_once(self):
        self.add()
        self.icon._hb_handles = {1: (object(), 11), 2: (object(), 22)}
        self.icon._icon_handle = 11
        self.icon.discard_cached_handles()
        self.assertEqual([call.args[0] for call in self.destroy.call_args_list], [22])
        self.assertEqual(self.icon._hb_brand_icon_handle, 900)
        self.icon.forget_handles()
        self.icon.forget_handles()
        self.assertEqual([call.args[0] for call in self.destroy.call_args_list], [22, 11, 900])

    def test_logo_load_failure_keeps_device_functional(self):
        self.load.side_effect = OSError("cannot load logo")
        with self.assertLogs(hb.log, level="WARNING"):
            self.add()
        self.assertEqual(self.notify.call_args_list[0].args[1].hIcon, 11)
        self.assertEqual(self.notify.call_args_list[1].args[1].uFlags, self.win32.NIF_TIP)
        self.assertFalse(self.icon._hb_branding)

    def test_developer_interpreter_is_not_branded_as_application(self):
        with mock.patch.object(hb.sys, "frozen", False, create=True):
            icon = hb.tray_icon("mouse:source")
        self.assertFalse(icon._hb_branding)
        with mock.patch.object(hb.sys, "frozen", True, create=True):
            icon = hb.tray_icon("mouse:frozen")
        self.assertTrue(icon._hb_branding)


if __name__ == "__main__":
    unittest.main()
