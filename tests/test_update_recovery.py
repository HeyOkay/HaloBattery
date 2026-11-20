"""Persisted update attempts report rollback once; no processes or network."""
import os
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import hb, make_app


class UpdateRecoveryTests(unittest.TestCase):
    def app(self, **cfg):
        app = make_app(cfg)
        app.show_whats_new = mock.Mock()
        app.notify_update = mock.Mock()
        return app

    def after(self, app):
        with mock.patch.object(hb, 'save_config') as saved:
            app.after_update()
        return saved

    def make_target_available(self, app):
        app.placeholder = SimpleNamespace(visible=True)
        app.deliver_update_failure()

    def test_rollback_is_reported_once_when_notification_target_is_available(self):
        app = self.app(last_version=hb.VERSION, update_from=hb.VERSION,
                       update_target='99.0.0', whats_new_notes={'version': '99.0.0', 'notes': 'new'})
        self.after(app).assert_called_once_with(app.cfg)
        app.notify_update.assert_not_called()
        self.make_target_available(app)
        app.notify_update.assert_called_once_with('notification.update_not_completed', target='99.0.0', version=hb.VERSION)
        app.show_whats_new.assert_not_called()
        for key in ('update_from', 'update_target', 'whats_new_notes'):
            self.assertNotIn(key, app.cfg)
        self.after(app)
        app.deliver_update_failure()
        self.assertEqual(app.notify_update.call_count, 1)

    def test_existing_tray_target_receives_the_error_immediately(self):
        app = self.app(update_target='99.0.0')
        app.icons['mouse'] = SimpleNamespace(icon=SimpleNamespace(visible=True))
        self.after(app)
        app.notify_update.assert_called_once()
        self.assertIsNone(app._pending_update_failure)

    def test_legacy_attempt_uses_cached_target_without_new_marker(self):
        app = self.app(last_version=hb.VERSION, update_from=hb.VERSION,
                       whats_new_notes={'version': '99.0.0'})
        self.after(app)
        self.make_target_available(app)
        app.notify_update.assert_called_once_with('notification.update_not_completed', target='99.0.0', version=hb.VERSION)

    def test_cached_future_notes_without_an_attempt_do_not_imply_failure(self):
        app = self.app(last_version=hb.VERSION, whats_new_notes={'version': '99.0.0'})
        self.after(app)
        self.make_target_available(app)
        app.notify_update.assert_not_called()

    def test_completed_update_retains_normal_whats_new_behavior(self):
        for target in (hb.VERSION, '1.0.0'):
            with self.subTest(target=target):
                app = self.app(last_version='1.0.0', update_from='1.0.0', update_target=target)
                self.after(app)
                app.show_whats_new.assert_called_once_with('1.0.0')
                app.notify_update.assert_not_called()
                self.assertNotIn('update_target', app.cfg)

    def test_bad_target_types_are_cleared_without_false_failure(self):
        for target in (True, [], {}, 9, None, '', 'broken', '9' * 5000):
            with self.subTest(target=type(target).__name__):
                app = self.app(last_version=hb.VERSION, update_from=hb.VERSION, update_target=target)
                self.after(app)
                self.make_target_available(app)
                app.notify_update.assert_not_called()
                app.show_whats_new.assert_not_called()
                self.assertNotIn('update_target', app.cfg)

    def test_failure_message_is_complete_in_all_languages(self):
        for language in hb.i18n.LANGUAGES:
            with self.subTest(language=language):
                text = hb.i18n.translate('notification.update_not_completed', language=language,
                                         target='99.0.0', version=hb.VERSION)
                self.assertIn('99.0.0', text)
                self.assertIn(hb.VERSION, text)
                self.assertNotIn('{', text)
                self.assertNotEqual(text, 'notification.update_not_completed')

    def test_runner_launch_failure_clears_attempt_and_keeps_app_running(self):
        app = self.app(last_version=hb.VERSION)
        app.update = hb.updates.Release('99.0.0', 'https://example.invalid/release', zip_url='https://example.invalid/a.zip')
        app.update_lock = threading.Lock()
        app.refresh_menus = mock.Mock()
        app.open_update = mock.Mock()
        app.quit = mock.Mock()
        with mock.patch.object(hb.updates, 'staging_dir', return_value='fake-stage'), \
                mock.patch.object(hb.updates, 'download', return_value='fake-new'), \
                mock.patch.object(hb.updates, 'prepare_runner', return_value='fake-runner'), \
                mock.patch.object(hb.updates, 'current_process_creation_time', return_value=0x123456789ABCDEF0), \
                mock.patch.object(hb.updates, 'start_detached', side_effect=OSError('blocked')) as launch, \
                mock.patch.object(hb, 'save_config'):
            app.install_update()
        launch.assert_called_once()
        app.quit.assert_not_called()
        app.open_update.assert_called_once_with()
        self.assertIsNone(app.update_progress)
        for key in ('update_target', 'update_from', 'whats_new_notes'):
            self.assertNotIn(key, app.cfg)
        self.assertEqual(app.notify_update.call_args.args[0], 'notification.update_failed')
        app.notify_update.reset_mock()
        self.after(app)
        self.make_target_available(app)
        app.notify_update.assert_not_called()

    def test_target_is_persisted_before_starting_the_runner(self):
        app = self.app()
        app.update = hb.updates.Release('99.0.0', 'https://example.invalid/release', zip_url='https://example.invalid/a.zip')
        app.update_lock = threading.Lock()
        app.quit = mock.Mock()
        observed = []
        with mock.patch.object(hb.updates, 'staging_dir', return_value='fake-stage'), \
                mock.patch.object(hb.updates, 'download', return_value='fake-new'), \
                mock.patch.object(hb.updates, 'prepare_runner', return_value='fake-runner'), \
                mock.patch.object(hb.updates, 'current_process_creation_time', return_value=0x123456789ABCDEF0), \
                mock.patch.object(hb, 'save_config', side_effect=lambda cfg: observed.append(dict(cfg))), \
                mock.patch.object(hb.updates, 'start_detached', side_effect=lambda *a, **k:
                                  self.assertEqual(observed[-1]['update_target'], '99.0.0')):
            app.install_update()
        app.quit.assert_called_once_with()
