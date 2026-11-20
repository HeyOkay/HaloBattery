"""Bind updater handoff to one process instance using fake Windows APIs."""
import ast
import ctypes
import os
from pathlib import Path
import tempfile
import threading
import types
import unittest
from unittest import mock

import _sandbox
from halo import updates

HERE = Path(__file__).resolve().parents[1]
BIRTH = 0x123456789ABCDEF0


class ProcessBirthTests(unittest.TestCase):
    def kernel(self, birth=BIRTH, query_ok=True, waits=(258, 0), opened=123, error=5,
               image=r'C:\Halo\HaloBattery.exe'):
        def process_times(handle, created, exited, system, user):
            self.assertEqual(handle, 123)
            output = ctypes.cast(created, ctypes.POINTER(ctypes.c_uint32))
            output[0], output[1] = birth & 0xFFFFFFFF, birth >> 32
            return int(query_ok)
        def image_name(handle, flags, buffer, length):
            self.assertEqual(handle, 123)
            buffer.value = image
            ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = len(buffer.value)
            return 1
        kernel = types.SimpleNamespace(
            GetCurrentProcess=mock.Mock(return_value=123),
            GetProcessTimes=mock.Mock(side_effect=process_times),
            OpenProcess=mock.Mock(return_value=opened),
            WaitForSingleObject=mock.Mock(side_effect=waits),
            QueryFullProcessImageNameW=mock.Mock(side_effect=image_name),
            TerminateProcess=mock.Mock(return_value=1),
            CloseHandle=mock.Mock(return_value=1))
        for owner, name, value in [(ctypes, 'WinDLL', mock.Mock(return_value=kernel)),
                                    (ctypes, 'get_last_error', mock.Mock(return_value=error)),
                                    (updates.sys, 'platform', 'win32')]:
            patcher = mock.patch.object(owner, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        return kernel

    def finish(self, birth=BIRTH, **kwargs):
        with mock.patch.object(updates, 'swap_files') as swap, \
                mock.patch.object(updates, 'start_detached') as start:
            result = updates.finish_update(r'C:\Halo', 42, 'new',
                                           expected_creation_time=birth, **kwargs)
        return result, swap, start

    def test_current_process_capture_preserves_all_64_bits_and_pseudo_handle(self):
        kernel = self.kernel()
        self.assertEqual(updates.current_process_creation_time(), BIRTH)
        kernel.GetCurrentProcess.assert_called_once_with()
        kernel.GetProcessTimes.assert_called_once()
        self.assertEqual(ctypes.sizeof(kernel.GetProcessTimes.argtypes[1]._type_), 8)
        kernel.OpenProcess.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_failed_current_capture_raises(self):
        kernel = self.kernel(query_ok=False)
        with self.assertRaises(OSError):
            updates.current_process_creation_time()
        kernel.CloseHandle.assert_not_called()

    def test_matching_birth_and_path_can_terminate_then_install(self):
        kernel = self.kernel()
        result, swap, _ = self.finish()
        self.assertTrue(result)
        swap.assert_called_once()
        kernel.OpenProcess.assert_called_once_with(0x00101001, 0, 42)
        kernel.TerminateProcess.assert_called_once_with(123, 1)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_same_path_reused_live_pid_never_waits_terminates_swaps_or_launches(self):
        kernel = self.kernel(birth=BIRTH + 1)
        result, swap, start = self.finish()
        self.assertFalse(result)
        kernel.WaitForSingleObject.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.QueryFullProcessImageNameW.assert_not_called()
        swap.assert_not_called()
        start.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_reused_pid_already_signalled_is_also_rejected(self):
        kernel = self.kernel(birth=BIRTH + 1, waits=(0,))
        result, swap, start = self.finish()
        self.assertFalse(result)
        kernel.WaitForSingleObject.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        swap.assert_not_called()
        start.assert_not_called()

    def test_equal_low_words_but_different_high_word_is_rejected(self):
        kernel = self.kernel(birth=BIRTH ^ (1 << 32))
        result, swap, start = self.finish()
        self.assertFalse(result)
        kernel.WaitForSingleObject.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        swap.assert_not_called()
        start.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_matched_natural_exit_needs_no_path_query_or_termination(self):
        kernel = self.kernel(waits=(0,))
        result, swap, _ = self.finish()
        self.assertTrue(result)
        swap.assert_called_once()
        kernel.GetProcessTimes.assert_called_once()
        kernel.QueryFullProcessImageNameW.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_failed_birth_query_fails_closed(self):
        kernel = self.kernel(query_ok=False)
        result, swap, start = self.finish()
        self.assertFalse(result)
        kernel.TerminateProcess.assert_not_called()
        kernel.WaitForSingleObject.assert_not_called()
        swap.assert_not_called()
        start.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_birth_match_still_requires_image_match_before_termination(self):
        kernel = self.kernel(image=r'C:\Other\HaloBattery.exe')
        result, swap, start = self.finish()
        self.assertFalse(result)
        kernel.QueryFullProcessImageNameW.assert_called_once()
        kernel.TerminateProcess.assert_not_called()
        swap.assert_not_called()
        start.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_birth_match_does_not_weaken_signalled_exit_requirement(self):
        kernel = self.kernel(waits=(258, 258))
        result, swap, start = self.finish()
        self.assertFalse(result)
        kernel.TerminateProcess.assert_called_once_with(123, 1)
        swap.assert_not_called()
        start.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_birth_mismatch_preserves_real_file_tree_byte_for_byte(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / 'old', Path(tmp) / 'new'
            kernel = self.kernel(birth=BIRTH + 1, image=str(old / updates.EXE_NAME))
            for folder, data in [(old, b'old'), (new, b'new')]:
                (folder / '_internal').mkdir(parents=True)
                (folder / updates.EXE_NAME).write_bytes(data)
                (folder / '_internal/runtime.dll').write_bytes(data + b' runtime')
            (old / 'config.json').write_bytes(b'config')
            (old / 'leftover.old').write_bytes(b'backup')
            def snapshot():
                return {p.relative_to(old).as_posix(): p.read_bytes()
                        for p in old.rglob('*') if p.is_file()}
            before = snapshot()
            start = mock.Mock()
            self.assertFalse(updates.finish_update(str(old), 42, str(new), start=start,
                                                  expected_creation_time=BIRTH))
            self.assertEqual(snapshot(), before)
            start.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_zero_birth_from_kernel_fails_closed(self):
        kernel = self.kernel(birth=0)
        result, swap, start = self.finish()
        self.assertFalse(result)
        kernel.TerminateProcess.assert_not_called()
        swap.assert_not_called()
        start.assert_not_called()

    def test_missing_pid_is_still_safe(self):
        kernel = self.kernel(opened=0, error=87)
        result, swap, _ = self.finish()
        self.assertTrue(result)
        swap.assert_called_once()
        kernel.GetProcessTimes.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_access_denied_is_not_missing_pid(self):
        kernel = self.kernel(opened=0)
        result, swap, start = self.finish()
        self.assertFalse(result)
        swap.assert_not_called()
        start.assert_not_called()
        kernel.GetProcessTimes.assert_not_called()

    def test_birth_only_wait_requests_query_right(self):
        kernel = self.kernel(waits=(0,))
        updates.wait_for_exit(42, expected_creation_time=BIRTH)
        kernel.OpenProcess.assert_called_once_with(0x00101001, 0, 42)

    def test_injected_wait_contract_remains_one_pid_and_none(self):
        wait = mock.Mock(return_value=None)
        result, swap, _ = self.finish(wait=wait)
        self.assertTrue(result)
        wait.assert_called_once_with(42)
        swap.assert_called_once()

    def test_invalid_direct_metadata_rejected_before_injected_wait_or_swap(self):
        for bad in [False, True, 0, -1, 1 << 64, 1.5, str(BIRTH), []]:
            with self.subTest(bad=bad), mock.patch.object(updates, 'swap_files') as swap:
                wait = mock.Mock()
                with self.assertRaises(ValueError):
                    updates.finish_update('app', 42, 'new', wait=wait,
                                          expected_creation_time=bad)
                wait.assert_not_called()
                swap.assert_not_called()

    def test_invalid_pid_with_metadata_cannot_skip_native_check(self):
        kernel = self.kernel()
        for bad in [0, -1, True, 1 << 32, '42']:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                updates.wait_for_exit(bad, expected_creation_time=BIRTH)
        kernel.OpenProcess.assert_not_called()


class CommandBirthTests(unittest.TestCase):
    argv = ['--finish-update', '--app-dir', 'app', '--new', 'new', '--pid', '42']

    def test_new_command_roundtrip_keeps_three_item_result(self):
        cmd = updates.finish_command('runner', 'new', 'app', 42,
                                     expected_creation_time=BIRTH)
        self.assertEqual(updates.parse_finish_args(cmd), ('app', 'new', 42))
        self.assertEqual(updates.parse_finish_creation_time(cmd), BIRTH)
        self.assertEqual(cmd[-4:], ['--pid-created', str(BIRTH), '--pid', '42'])

    def test_legacy_roundtrip_is_unchanged(self):
        cmd = updates.finish_command('runner', 'new', 'app', 42)
        self.assertEqual(updates.parse_finish_args(cmd), ('app', 'new', 42))
        self.assertIsNone(updates.parse_finish_creation_time(cmd))

    def test_metadata_boundaries_roundtrip_exactly(self):
        for value in [1, 0xFFFFFFFF, 0x100000000, (1 << 64) - 1]:
            with self.subTest(value=value):
                cmd = self.argv + ['--pid-created', str(value)]
                self.assertEqual(updates.parse_finish_creation_time(cmd), value)

    def test_malformed_metadata_is_rejected_by_legacy_parser_too(self):
        invalid = [[], [''], ['0'], ['-1'], ['+1'], ['1.0'], [' 1'], ['1 '],
                   ['１２'], ['1_000'], [str(1 << 64)], ['9' * 5000],
                   ['--pid'], ['1', '--pid-created', '2']]
        for tail in invalid:
            with self.subTest(tail=str(tail)[:80]), self.assertRaises(ValueError):
                updates.parse_finish_args(self.argv + ['--pid-created'] + tail)
        with self.assertRaises(ValueError):
            updates.parse_finish_args(self.argv + ['--pid-created=bad'])

    def test_metadata_requires_valid_old_pid(self):
        for bad in ['0', '-1', 'bad', str(1 << 32)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                updates.parse_finish_args(self.argv[:-1] + [bad, '--pid-created', str(BIRTH)])


class AppWiringTests(unittest.TestCase):
    def extracted(self, name):
        tree = ast.parse((HERE / 'halo_battery.pyw').read_text(encoding='utf-8'))
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        globals_ = {'updates': updates, 'sys': types.SimpleNamespace(argv=[]), 'os': os,
                    'VERSION': '1.0', 'BASE_DIR': 'app', 'log': mock.Mock(),
                    'save_config': mock.Mock(), 'flyout': mock.Mock()}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<update wiring>', 'exec'), globals_)
        return globals_[name], globals_

    def test_dispatch_forwards_birth_to_native_finish_path(self):
        main, env = self.extracted('main')
        env['sys'].argv = CommandBirthTests.argv + ['--pid-created', str(BIRTH)]
        with mock.patch.object(updates, 'finish_update') as finish:
            main()
        finish.assert_called_once_with('app', 42, 'new', expected_creation_time=BIRTH)

    def test_dispatch_rejects_malformed_metadata_before_finish(self):
        main, env = self.extracted('main')
        env['sys'].argv = CommandBirthTests.argv + ['--pid-created', 'bad']
        with mock.patch.object(updates, 'finish_update') as finish:
            main()
        finish.assert_not_called()

    def install(self, capture):
        install, env = self.extracted('install_update')
        app = types.SimpleNamespace(update=types.SimpleNamespace(zip_url='fake', version='2.0',
                                    notes='', url='', published=''), update_lock=threading.Lock(),
                                    lock=threading.Lock(), cfg={}, update_progress=0,
                                    notify_update=mock.Mock(), _update_progress=mock.Mock(),
                                    refresh_menus=mock.Mock(), open_update=mock.Mock(), quit=mock.Mock())
        with mock.patch.object(updates, 'staging_dir', return_value='stage'), \
                mock.patch.object(updates, 'download', return_value='new'), \
                mock.patch.object(updates, 'prepare_runner', return_value='runner'), \
                mock.patch.object(updates, 'current_process_creation_time', capture), \
                mock.patch.object(updates, 'start_detached') as launch:
            install(app)
        return app, launch

    def test_parent_capture_is_in_command_before_launch(self):
        app, launch = self.install(mock.Mock(return_value=BIRTH))
        launch.assert_called_once()
        cmd = launch.call_args.args[0]
        self.assertEqual(updates.parse_finish_creation_time(cmd), BIRTH)
        self.assertEqual(updates.parse_finish_args(cmd)[2], os.getpid())
        app.quit.assert_called_once()

    def test_parent_capture_failure_preserves_running_app(self):
        app, launch = self.install(mock.Mock(side_effect=OSError('cannot get birth')))
        launch.assert_not_called()
        app.quit.assert_not_called()
        self.assertIsNone(app.update_progress)
        self.assertNotIn('update_target', app.cfg)
        app.refresh_menus.assert_called_once()


if __name__ == '__main__':
    unittest.main()
