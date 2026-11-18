"""Shutdown publication fences and session hooks. No native windows/devices."""
import json
import os
import tempfile
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import HideRenameTestCase, _RealThread, hb, make_app, dev


class ShutdownTests(HideRenameTestCase):
    def app(self):
        app = make_app({'status_file': True})
        app.stop_evt = threading.Event()
        app.providers = []
        # Startup/teardown is tested with fake components; transport has its own
        # integration tests and must never install real HID hooks in this suite.
        app.start_native_transport = mock.Mock()
        for name in ('bt_wake', 'bt_fresh', 'update_wake', 'anim_wake', 'theme_evt', 'diag_requested'):
            setattr(app, name, threading.Event())
        app.bt_cache = []
        app.history.save = mock.Mock()
        return app

    def test_final_status_is_written_once_and_late_results_cannot_publish(self):
        app = self.app()
        with mock.patch.object(hb, 'write_json') as write:
            app.quit()
            app.quit()
            app.write_status([dev()])
            app.apply([dev()])
            app._bt_update([dev('bt:mouse')])
        write.assert_called_once()
        self.assertIs(write.call_args.args[1]['running'], False)
        app.history.save.assert_called_once_with(force=True)
        self.assertEqual(app.icons, {})
        self.assertEqual(app.bt_cache, [])
        self.assertFalse(app.bt_fresh.is_set())
        self.assertTrue(app.wake.is_set())

    def test_live_status_write_finishes_before_the_final_stopped_status(self):
        app = self.app()
        entered, release, finished = (threading.Event() for _ in range(3))
        writes, errors = [], []
        def write(path, data):
            writes.append(data['running'])
            if data['running']:
                entered.set()
                if not release.wait(3):
                    raise AssertionError('test did not release write')
        def run(target):
            try:
                target()
            except BaseException as error:
                errors.append(error)
        with mock.patch.object(hb, 'write_json', side_effect=write):
            live = _RealThread(target=lambda: run(lambda: app.write_status([dev()])), daemon=True)
            live.start()
            self.assertTrue(entered.wait(2))
            closing = _RealThread(target=lambda: (run(app.quit), finished.set()), daemon=True)
            closing.start()
            release.set()
            self.assertTrue(finished.wait(3))
            live.join(2)
            closing.join(2)
            app.write_status([dev()])
        self.assertEqual(errors, [])
        self.assertEqual(writes, [True, False])

    def _disable_status_during_write(self, *, quitting):
        app = self.app()
        entered, release, decision, removed, quit_waiting = (threading.Event() for _ in range(5))
        errors = []

        class ObservedLock:
            def __init__(self):
                self.lock = threading.RLock()

            def __enter__(self):
                name = threading.current_thread().name
                if name == 'status-disable':
                    decision.set()  # Fixed handler reached the in-flight writer's lock.
                elif name == 'status-quit':
                    quit_waiting.set()
                self.lock.acquire()
                return self

            def __exit__(self, *args):
                self.lock.release()

        app.lock = ObservedLock()
        real_write, real_remove = hb.write_json, hb.remove_status_file
        with tempfile.TemporaryDirectory(prefix='halo-status-disable-') as folder:
            path = os.path.join(folder, 'status.json')
            real_write(path, {'running': True, 'old': True})

            def write(destination, data):
                if data['running']:
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('test did not release live status write')
                real_write(destination, data)

            def remove():
                real_remove()
                removed.set()
                decision.set()  # Original unlocked handler completed removal instead.

            def call(callback):
                try:
                    callback()
                except BaseException as error:
                    errors.append(error)

            with mock.patch.object(hb, 'STATUS_PATH', path), \
                    mock.patch.object(hb, 'write_json', side_effect=write), \
                    mock.patch.object(hb, 'remove_status_file', side_effect=remove):
                menu = app.build_menu(None)
                preferences = next(item for item in menu.items if item.text == 'Preferences').submenu
                toggle = next(item for item in preferences.items if item.text == 'Status file for other apps')
                writer = _RealThread(target=lambda: call(lambda: app.write_status([])), daemon=True)
                disabling = _RealThread(target=lambda: call(lambda: toggle(None)),
                                        name='status-disable', daemon=True)
                threads = [writer, disabling]
                writer.start()
                try:
                    self.assertTrue(entered.wait(2))
                    disabling.start()
                    self.assertTrue(decision.wait(2))
                    # The event comes either from an actual completed removal
                    # (the buggy handler) or a real lock attempt (the fixed one).
                    # No timing/sleep guess determines which interleaving runs.
                    if quitting:
                        closer = _RealThread(target=lambda: call(app.quit), name='status-quit', daemon=True)
                        threads.append(closer)
                        closer.start()
                        self.assertTrue(quit_waiting.wait(2))
                finally:
                    release.set()
                    for thread in threads:
                        if thread.ident is not None:
                            thread.join(3)
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                self.assertEqual(errors, [])
                self.assertFalse(app.cfg['status_file'])
                leaked = None
                if os.path.exists(path):
                    with open(path, encoding='utf-8') as stream:
                        leaked = json.load(stream)
                self.assertFalse(os.path.exists(path), f'disabled exporter recreated status: {leaked}')
                app.write_status([])
                self.assertFalse(os.path.exists(path))
                self.assertTrue(removed.is_set())
                if quitting:
                    self.assertTrue(app.stop_evt.is_set())
                    self.assertTrue(app._shutdown_done.is_set())

    def test_fallback_disable_cannot_recreate_status_after_inflight_live_write(self):
        self._disable_status_during_write(quitting=False)

    def test_fallback_disable_and_quit_leave_no_status_after_inflight_live_write(self):
        self._disable_status_during_write(quitting=True)

    def test_poll_finishing_after_quit_does_not_apply_history_or_status(self):
        app = self.app()
        entered, release, finished = (threading.Event() for _ in range(3))
        errors = []
        app.change_signature = lambda: None
        app.apply = mock.Mock()
        def poll():
            entered.set()
            if not release.wait(3):
                raise AssertionError('test did not release poll')
            return [dev()]
        app.poll_once = poll
        def run():
            try:
                app.loop()
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()
        with mock.patch.object(hb, 'write_json') as write:
            worker = _RealThread(target=run, daemon=True)
            worker.start()
            self.assertTrue(entered.wait(2))
            app.quit()
            release.set()
            self.assertTrue(finished.wait(3))
            worker.join(2)
        self.assertEqual(errors, [])
        app.apply.assert_not_called()
        write.assert_called_once()
        self.assertFalse(write.call_args.args[1]['running'])

    def test_cleanup_failure_does_not_block_other_cleanup_or_final_persistence(self):
        app = self.app()
        app._settings_host = mock.Mock()
        app._settings_host.close.side_effect = OSError('blocked')
        icon = mock.Mock()
        app.icons['mouse'] = icon
        app.placeholder = mock.Mock()
        app.ui_host = mock.Mock()
        with mock.patch.object(hb, 'write_json') as write, self.assertLogs('halo_battery', level='ERROR'):
            app.quit()
        icon.stop.assert_called_once_with()
        app.placeholder.stop.assert_called_once_with()
        app.ui_host.stop.assert_called_once_with()
        write.assert_called_once()
        app.history.save.assert_called_once_with(force=True)

    def test_settings_cleanup_runs_without_the_application_lock(self):
        app = self.app()
        acquired = threading.Event()
        def close():
            def probe():
                with app.lock:
                    acquired.set()
            worker = _RealThread(target=probe, daemon=True)
            worker.start()
            worker.join(2)
            self.assertTrue(acquired.is_set())
        app._settings_host = SimpleNamespace(close=close)
        with mock.patch.object(hb, 'write_json'):
            app.quit()

    def test_history_error_still_writes_stopped_status(self):
        app = self.app()
        app.history.save.side_effect = RuntimeError('history failure')
        with mock.patch.object(hb, 'write_json') as write, self.assertLogs('halo_battery', level='ERROR'):
            app.begin_shutdown()
        self.assertTrue(app.stop_evt.is_set())
        write.assert_called_once()
        self.assertFalse(write.call_args.args[1]['running'])

    def test_session_query_and_cancellation_do_not_stop_or_persist(self):
        app = self.app()
        icon = SimpleNamespace(_message_handlers={})
        with mock.patch.object(hb.sys, 'platform', 'win32'):
            hb.connect_session_end(icon, app.begin_shutdown)
        with mock.patch.object(hb, 'write_json') as write:
            self.assertEqual(icon._message_handlers[hb.WM_QUERYENDSESSION](0, 0), 1)
            self.assertEqual(icon._message_handlers[hb.WM_ENDSESSION](0, 0), 0)
        write.assert_not_called()
        app.history.save.assert_not_called()
        self.assertFalse(app.stop_evt.is_set())

    def test_multiple_session_broadcasts_persist_synchronously_without_native_teardown(self):
        app = self.app()
        app._settings_host = mock.Mock()
        owners = [SimpleNamespace(_message_handlers={}) for _ in range(2)]
        with mock.patch.object(hb.sys, 'platform', 'win32'):
            for owner in owners:
                hb.connect_session_end(owner, app.begin_shutdown)
        with mock.patch.object(hb, 'write_json') as write:
            for owner in owners:
                self.assertEqual(owner._message_handlers[hb.WM_ENDSESSION](1, 0x80000000), 0)
                self.assertTrue(app.stop_evt.is_set())
                self.assertEqual(write.call_count, 1)
        app.history.save.assert_called_once_with(force=True)
        app._settings_host.close.assert_not_called()

    def test_empty_hidden_owner_can_exist_before_the_first_poll(self):
        app = self.app()
        app.show_placeholder(False, ensure_owner=True)
        self.assertIsNotNone(app.placeholder)
        self.assertTrue(app.placeholder_ready.is_set())
        self.assertFalse(app.placeholder.visible)


    def test_run_waits_for_cleanup_owned_by_a_settings_worker(self):
        app = self.app()
        closing, release, waiting, finished = (threading.Event() for _ in range(4))
        complete = threading.Event()
        class Completion:
            def set(self):
                complete.set()
            def wait(self, timeout):
                waiting.set()
                return complete.wait(timeout)
        app._shutdown_done = Completion()
        app.start_tray_history_cleanup = mock.Mock()
        app.show_placeholder = mock.Mock()
        app.ui_host = mock.Mock()
        def close():
            closing.set()
            if not release.wait(3):
                raise AssertionError('test did not release Settings cleanup')
        app._settings_host = SimpleNamespace(close=close)
        with mock.patch.object(hb, 'write_json'), \
                mock.patch.object(hb.threading, 'Thread', return_value=SimpleNamespace(start=lambda: None)):
            runner = _RealThread(target=lambda: (app.run(), finished.set()), daemon=True)
            runner.start()
            closer = _RealThread(target=app.quit, daemon=True)
            closer.start()
            try:
                self.assertTrue(closing.wait(2))
                self.assertTrue(waiting.wait(2))
                self.assertFalse(finished.is_set())
            finally:
                release.set()
            self.assertTrue(finished.wait(3))
            closer.join(2)
            runner.join(2)
        self.assertTrue(complete.is_set())

    def test_stopped_poll_does_not_query_the_next_provider(self):
        app = self.app()
        first = SimpleNamespace(name='first', poll=lambda: (app.stop_evt.set(), [dev()])[1])
        second = SimpleNamespace(name='second', poll=mock.Mock(return_value=[]))
        app.providers = [first, second]
        self.assertEqual(app.poll_once(), [])
        second.poll.assert_not_called()

    def test_startup_failure_still_persists_and_cleans_up(self):
        app = self.app()
        app.start_tray_history_cleanup = mock.Mock()
        app.show_placeholder = mock.Mock()
        app.ui_host = mock.Mock()
        app.ui_host.start.side_effect = RuntimeError('UI host unavailable')
        with mock.patch.object(hb, 'write_json') as write, \
                mock.patch.object(hb.threading, 'Thread', return_value=SimpleNamespace(start=lambda: None)):
            with self.assertRaisesRegex(RuntimeError, 'UI host unavailable'):
                app.run()
        self.assertTrue(app.stop_evt.is_set())
        app.history.save.assert_called_once_with(force=True)
        write.assert_called_once()
        self.assertFalse(write.call_args.args[1]['running'])
        app.ui_host.stop.assert_called_once_with()
