"""Startup guards and bounded worker, with no native registry or hardware."""
import sys
import threading
import types
import unittest
from unittest import mock
from test_tray_native_identity import hb

class TrayCleanupStartupTests(unittest.TestCase):
    def app(self):
        app = hb.App.__new__(hb.App)
        app.stop_evt = mock.Mock()
        app.stop_evt.wait.return_value = False
        return app

    def test_source_and_unsupported_backend_never_touch_registry(self):
        for frozen, known, platform in ((False, True, "win32"), (True, False, "win32"), (True, True, "linux")):
            with self.subTest(frozen=frozen, known=known, platform=platform), \
                    mock.patch.object(hb.sys, "frozen", frozen, create=True), \
                    mock.patch.object(hb.sys, "platform", platform), \
                    mock.patch.object(hb, "_pystray_known", return_value=known), \
                    mock.patch.object(hb.tray_history, "prepare") as prepare, \
                    mock.patch.object(hb.threading, "Thread") as thread:
                hb.App.start_tray_history_cleanup(self.app())
                prepare.assert_not_called()
                thread.assert_not_called()

    def test_frozen_start_prepares_before_launching_background_cleanup(self):
        with mock.patch.object(hb.sys, "frozen", True, create=True), \
                mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.object(hb, "_pystray_known", return_value=True), \
                mock.patch.object(hb.tray_history, "prepare") as prepare, \
                mock.patch.object(hb.threading, "Thread") as thread:
            app = self.app()
            def before_start():
                prepare.assert_called_once_with(sys.executable)
            thread.return_value.start.side_effect = before_start
            hb.App.start_tray_history_cleanup(app)
            prepare.assert_called_once_with(sys.executable)
            thread.return_value.start.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["args"], (prepare.return_value,))
        self.assertEqual(thread.call_args.kwargs["target"], app.cleanup_tray_history)
        self.assertTrue(thread.call_args.kwargs["daemon"])

    def test_prepare_failure_cannot_break_startup(self):
        with mock.patch.object(hb.tray_history, "prepare", side_effect=OSError("denied")), \
                mock.patch.object(hb.tray_history, "reconcile") as reconcile, \
                self.assertLogs(hb.log, level="WARNING"):
            hb.App.cleanup_tray_history(self.app())
            reconcile.assert_not_called()

    def test_worker_retries_pending_and_finishes_once_ready(self):
        prepared = object()
        with mock.patch.object(hb.tray_history, "reconcile", side_effect=[
                hb.tray_history.ReconcileResult("pending"), hb.tray_history.ReconcileResult("done", removed=2)]) as reconcile:
            app = self.app()
            hb.App.cleanup_tray_history(app, prepared)
        self.assertEqual(reconcile.call_count, 2)
        self.assertEqual(app.stop_evt.wait.call_args_list, [mock.call(1), mock.call(1)])
        self.assertEqual(reconcile.call_args.args, (sys.executable, hb.DATA_DIR, prepared))

    def test_worker_stops_promptly_and_has_bounded_retries(self):
        with mock.patch.object(hb.tray_history, "reconcile", return_value=hb.tray_history.ReconcileResult("pending")) as reconcile:
            app = self.app()
            app.stop_evt.wait.return_value = True
            hb.App.cleanup_tray_history(app, object())
            reconcile.assert_not_called()
            app.stop_evt.wait.return_value = False
            hb.App.cleanup_tray_history(app, object())
            self.assertEqual(reconcile.call_count, 30)

    def test_worker_leaves_a_failed_backup_for_a_later_start(self):
        with mock.patch.object(hb.tray_history, "reconcile", return_value=hb.tray_history.ReconcileResult("error", error="backup denied")) as reconcile, \
                self.assertLogs(hb.log, level="WARNING"):
            hb.App.cleanup_tray_history(self.app(), object())
        reconcile.assert_called_once()

if __name__ == "__main__":
    unittest.main()
