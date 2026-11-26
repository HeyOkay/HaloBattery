"""Poll exception boundaries, independent of hardware and retry wall time."""
import os
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import hb, make_app, dev


class PollReliabilityTests(unittest.TestCase):
    def app(self):
        app = make_app({'bluetooth': False})
        app.stop_evt = threading.Event()
        app.providers = []
        app.change_signature = mock.Mock(return_value=None)
        app.poll_once = mock.Mock(return_value=[])
        app.apply = mock.Mock()
        app.quiet = mock.Mock(return_value=False)
        app.was_quiet = False
        app.write_status = mock.Mock()
        app.diag_requested = threading.Event()
        app.wait_next = mock.Mock(side_effect=lambda sig: app.stop_evt.set())
        return app

    def test_bad_provider_reply_does_not_discard_a_healthy_providers_result(self):
        for malformed in (None, 7, [object()], [SimpleNamespace(level=50, key=[])]):
            with self.subTest(reply=malformed):
                app = make_app({'bluetooth': False})
                healthy = dev()
                app.providers = [SimpleNamespace(name='bad', poll=lambda: malformed),
                                 SimpleNamespace(name='good', poll=lambda: [healthy])]
                with self.assertLogs('halo_battery', level='ERROR'):
                    results = app.poll_once()
                self.assertEqual(results, [healthy])
                self.assertEqual(app.key_provider, {healthy.key: 'good'})
                self.assertEqual(app.hid_results, [healthy])

    def test_coordinator_error_recovers_on_the_next_cycle_with_backoff(self):
        for method in ('change_signature', 'poll_once', 'quiet', 'wait_next'):
            with self.subTest(method=method):
                app = self.app()
                target = getattr(app, method)
                if method == 'wait_next':
                    count = []
                    def waiting(*args):
                        count.append(None)
                        if len(count) == 1:
                            raise RuntimeError('temporary coordinator failure')
                        app.stop_evt.set()
                    target.side_effect = waiting
                else:
                    target.side_effect = [RuntimeError('temporary coordinator failure'),
                                          [] if method == 'poll_once' else False if method == 'quiet' else None]
                with mock.patch.object(app.stop_evt, 'wait', return_value=False) as pause, \
                        self.assertLogs('halo_battery', level='ERROR'):
                    app.loop()
                pause.assert_called_once_with(5)
                self.assertGreaterEqual(target.call_count, 2)
                self.assertTrue(app.stop_evt.is_set())

    def test_stop_interrupts_error_backoff_without_another_poll(self):
        app = self.app()
        app.poll_once.side_effect = RuntimeError('bad coordinator state')
        def interrupt(timeout):
            self.assertEqual(timeout, 5)
            app.stop_evt.set()
            return True
        with mock.patch.object(app.stop_evt, 'wait', side_effect=interrupt) as pause, \
                self.assertLogs('halo_battery', level='ERROR'):
            app.loop()
        app.poll_once.assert_called_once_with()
        app.apply.assert_not_called()
        pause.assert_called_once_with(5)

    def test_healthy_cycle_has_no_extra_wait_or_retry(self):
        app = self.app()
        with mock.patch.object(app.stop_evt, 'wait') as pause:
            app.loop()
        pause.assert_not_called()
        app.poll_once.assert_called_once_with()
        app.wait_next.assert_called_once_with(None)
