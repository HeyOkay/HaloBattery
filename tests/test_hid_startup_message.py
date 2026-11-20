"""Blocked HID support is visible once while other app components can run."""
import threading
from unittest import mock
from test_hide_rename import HideRenameTestCase, hb, make_app

class HidStartupMessageTests(HideRenameTestCase):
    def test_message_shows_once_with_localized_text(self):
        app=make_app({"language":"ru"})
        with mock.patch.object(hb,"HID_BACKEND_ERROR","ImportError: blocked"), \
                mock.patch.object(hb,"show_settings_error") as show:
            app.report_hid_backend_failure()
            app.report_hid_backend_failure()
        show.assert_called_once_with(app.tr("startup.hid_unavailable"))
        self.assertIn("Bluetooth",show.call_args.args[0])

    def test_normal_start_has_no_extra_dialog_or_thread(self):
        app=make_app()
        with mock.patch.object(hb,"HID_BACKEND_ERROR",""), \
                mock.patch.object(hb.threading,"Thread") as thread:
            app.report_hid_backend_failure()
        thread.assert_not_called()

    def test_shutdown_does_not_open_a_late_error_window(self):
        app=make_app()
        app.stop_evt=threading.Event()
        app.stop_evt.set()
        with mock.patch.object(hb,"HID_BACKEND_ERROR","OSError: blocked"), \
                mock.patch.object(hb,"show_settings_error") as show:
            app.report_hid_backend_failure()
        show.assert_not_called()
