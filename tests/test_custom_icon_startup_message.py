"""An unverifiable saved icon must not silently become a default icon."""
import threading
from unittest import mock

from halo import i18n
from test_hide_rename import HideRenameTestCase, hb, make_app


class CustomIconStartupMessageTests(HideRenameTestCase):
    def test_failed_selection_is_reported_once_in_each_language(self):
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                app = make_app({"language": language})
                app._custom_icon_errors = ["a" * 64]
                with mock.patch.object(hb, "show_settings_error") as show:
                    app.report_custom_icon_failure()
                    app.report_custom_icon_failure()
                text = app.tr("settings.iconify_saved_unavailable")
                show.assert_called_once_with(text)
                self.assertNotEqual(text, "settings.iconify_saved_unavailable")
                self.assertIn("a" * 64, app._custom_icon_errors)

    def test_valid_selections_create_no_warning_thread(self):
        app = make_app()
        app._custom_icon_errors = []
        with mock.patch.object(hb.threading, "Thread") as thread:
            app.report_custom_icon_failure()
        thread.assert_not_called()

    def test_stopping_app_does_not_start_a_warning(self):
        app = make_app()
        app._custom_icon_errors = ["a" * 64]
        app.stop_evt = threading.Event()
        app.stop_evt.set()
        with mock.patch.object(hb.threading, "Thread") as thread:
            app.report_custom_icon_failure()
        thread.assert_not_called()

    def test_queued_warning_is_suppressed_after_shutdown_or_recovery(self):
        for recovered in (False, True):
            with self.subTest(recovered=recovered):
                app = make_app()
                app._custom_icon_errors = ["a" * 64]
                with mock.patch.object(hb.threading, "Thread") as thread:
                    app.report_custom_icon_failure()
                target = thread.call_args.kwargs["target"]
                args = thread.call_args.kwargs["args"]
                if recovered:
                    app._custom_icon_errors.clear()
                else:
                    app.stop_evt = threading.Event()
                    app.stop_evt.set()
                with mock.patch.object(hb, "show_settings_error") as show:
                    target(*args)
                show.assert_not_called()
