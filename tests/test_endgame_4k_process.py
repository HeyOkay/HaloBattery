from pathlib import Path
import sys
import time
import unittest
from endgame4k_transaction import Transactions


class ProcessBoundaryTests(unittest.TestCase):
    def test_worker_directory_has_explicit_idempotent_cleanup(self):
        from endgame4k_transaction import WorkerDirectory
        temporary = WorkerDirectory()
        path = Path(temporary.name)
        self.assertTrue(path.is_dir())
        temporary.cleanup()
        temporary.cleanup()
        self.assertFalse(path.exists())

    def test_worker_directory_has_no_automatic_exit_finalizer(self):
        import tempfile
        import subprocess
        with tempfile.TemporaryDirectory() as parent:
            script = ('import sys; import endgame4k_transaction as t; '
                      't.tempfile.gettempdir=lambda:sys.argv[1]; '
                      'directory=t.WorkerDirectory()')
            result = subprocess.run([sys.executable, '-c', script, parent],
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Even interpreter exit must not implicitly delete a worker's files.
            self.assertEqual(len(list(Path(parent).glob('halobattery-4k-*'))), 1)

    def test_shutdown_cleans_reaped_child_directory(self):
        from unittest.mock import Mock, patch
        import endgame4k_transaction as module
        process, temporary = Mock(), Mock()
        process.poll.return_value = 0
        with patch.object(module, '_children', {process: temporary}), patch.object(module, '_stopping', False):
            module.shutdown()
            process.wait.assert_called_once_with(timeout=1)
            temporary.cleanup.assert_called_once()
            self.assertEqual(module._children, {})

    def test_shutdown_retains_directory_while_child_is_unreaped(self):
        from unittest.mock import Mock, patch
        import subprocess
        import endgame4k_transaction as module
        process, temporary = Mock(), Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired('worker', 1)
        with patch.object(module, '_children', {process: temporary}), patch.object(module, '_stopping', False):
            module.shutdown()
            temporary.cleanup.assert_not_called()
            self.assertIs(module._children[process], temporary)

    def test_real_child_success_and_validation(self):
        boundary = Transactions()
        result = boundary.run({'path': b'fake', 'product_id': 0x1970}, simulate='success')
        self.assertEqual(result['status']['level'], 45, result)
        self.assertTrue(result['simulation'])
        self.assertIsNone(boundary.pending)

    def test_real_child_status_rejection(self):
        result = Transactions().run({'path': b'fake', 'product_id': 0x1970}, simulate='status08')
        self.assertIsNone(result['status'])
        self.assertIn('device status 0x08', ' '.join(result['diagnostics']))

    def test_real_child_unstable_samples(self):
        result = Transactions().run({'path': b'fake', 'product_id': 0x1970}, simulate='unstable')
        self.assertIsNone(result['status'])
        self.assertIn('[30, 35, 30]', ' '.join(result['diagnostics']))

    def test_all_native_stages_can_timeout_and_next_poll_recovers(self):
        for mode in ('hang_open', 'hang_send', 'hang_read', 'hang_close'):
            with self.subTest(mode=mode):
                boundary = Transactions(deadline=2.0)
                started = time.monotonic()
                result = boundary.run({'path': b'fake', 'product_id': 0x1970}, simulate=mode)
                self.assertIsNone(result['status'])
                self.assertIn('transaction timeout', ' '.join(result['diagnostics']))
                operation = mode.removeprefix('hang_')
                details = ' '.join(result['diagnostics'])
                self.assertIn('last recorded worker operation=' + operation, details)
                if operation in ('send', 'read'):
                    self.assertIn('command=0x0e', details)
                if operation == 'close':
                    self.assertIn('command=0xb4', details)
                self.assertNotIn('fake', details)
                self.assertLess(time.monotonic() - started, 5)
                self.assertIsNone(boundary.pending)
                boundary.deadline = 8
                result = boundary.run({'path': b'fake', 'product_id': 0x1970}, simulate='success')
                self.assertEqual(result['status']['level'], 45, result)

    def test_unreaped_worker_prevents_a_new_request(self):
        class Alive:
            def poll(self): return None
        boundary = Transactions()
        boundary.pending = (Alive(), None)
        result = boundary.run({'path': b'fake', 'product_id': 0x1970}, simulate='success')
        self.assertIn('no new request sent', ' '.join(result['diagnostics']))

    def test_concurrent_poll_never_starts_second_worker(self):
        boundary = Transactions()
        boundary.busy.acquire()
        try:
            result = boundary.run({'path': b'fake', 'product_id': 0x1970}, simulate='success')
            self.assertIn('already running', ' '.join(result['diagnostics']))
        finally:
            boundary.busy.release()

    def test_closed_provider_never_starts_worker(self):
        boundary = Transactions()
        boundary.close()
        result = boundary.run({'path': b'fake', 'product_id': 0x1970}, simulate='success')
        self.assertIsNone(result['status'])
        self.assertIn('shutting down', ' '.join(result['diagnostics']))

    def test_v2_worker_wireless_and_wired(self):
        for pid in (0x1970, 0x1982):
            result = Transactions().run({'path': b'fake', 'product_id': pid}, simulate='success_v2')
            self.assertEqual(result['status']['key'], 'endgame_4k:xm2w4kv2')
            self.assertEqual(result['status']['level'], 45 if pid == 0x1970 else None)

    def test_close_during_active_work_reaps_child(self):
        import threading
        boundary = Transactions(deadline=8)
        results = []
        thread = threading.Thread(target=lambda: results.append(boundary.run(
            {'path': b'fake', 'product_id': 0x1970}, simulate='hang_read')))
        thread.start()
        try:
            until = time.monotonic() + 5
            while boundary.active is None and time.monotonic() < until:
                time.sleep(0.01)
            child = boundary.active
            self.assertIsNotNone(child)
            boundary.close()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertIsNotNone(child.poll())
            self.assertIsNone(results[0]['status'])
        finally:
            boundary.close()
            thread.join(10)

    def test_completed_pending_child_is_reaped_even_if_device_disappeared(self):
        from unittest.mock import Mock
        boundary = Transactions()
        process = Mock()
        process.poll.return_value = 0
        temporary = Mock()
        boundary.pending = (process, temporary)
        boundary.close()
        process.wait.assert_called_once()
        temporary.cleanup.assert_called_once()
        self.assertIsNone(boundary.pending)
