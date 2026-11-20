"""An updater must confirm process exit before touching the installed files.

All kernel32 calls and process launches are fakes; no process is opened or ended.
"""
import ctypes
from pathlib import Path
import shutil
import types
import unittest
from unittest import mock

import _sandbox
from halo import updates


class ExitConfirmationTests(unittest.TestCase):
    def kernel(self, waits=(0,), opened=123, ended=1, error=5):
        kernel = types.SimpleNamespace(
            OpenProcess=mock.Mock(return_value=opened),
            WaitForSingleObject=mock.Mock(side_effect=waits),
            TerminateProcess=mock.Mock(return_value=ended),
            GetLastError=mock.Mock(return_value=error),
            CloseHandle=mock.Mock(return_value=1),
        )
        for owner, name, value in [
            (ctypes, "windll", types.SimpleNamespace(kernel32=kernel)),
            (ctypes, "WinDLL", mock.Mock(return_value=kernel)),
            (ctypes, "get_last_error", kernel.GetLastError),
            (updates.sys, "platform", "win32"),
        ]:
            patcher = mock.patch.object(owner, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        return kernel

    def test_natural_exit_closes_handle_without_termination(self):
        kernel = self.kernel()
        self.assertIsNone(updates.wait_for_exit(42, timeout=0.25))
        ctypes.WinDLL.assert_called_once_with("kernel32", use_last_error=True)
        kernel.OpenProcess.assert_called_once_with(0x00100001, 0, 42)
        kernel.WaitForSingleObject.assert_called_once_with(123, 250)
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_missing_pid_is_already_gone(self):
        kernel = self.kernel(opened=0, error=87)
        self.assertIsNone(updates.wait_for_exit(42))
        kernel.GetLastError.assert_called_once_with()
        kernel.WaitForSingleObject.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_open_access_denied_does_not_mean_already_gone(self):
        kernel = self.kernel(opened=0, error=5)
        with self.assertRaises(OSError):
            updates.wait_for_exit(42)
        kernel.GetLastError.assert_called_once_with()
        kernel.WaitForSingleObject.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_failed_first_wait_does_not_terminate(self):
        kernel = self.kernel(waits=(0xFFFFFFFF, 0xFFFFFFFF), error=6)
        with self.assertRaises(OSError):
            updates.wait_for_exit(42)
        kernel.WaitForSingleObject.assert_called_once_with(123, 30000)
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_timeout_can_terminate_only_after_exit_is_confirmed(self):
        kernel = self.kernel(waits=(258, 0))
        self.assertIsNone(updates.wait_for_exit(42))
        self.assertEqual(kernel.WaitForSingleObject.call_args_list,
                         [mock.call(123, 30000), mock.call(123, 5000)])
        kernel.TerminateProcess.assert_called_once_with(123, 1)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_refused_termination_can_race_with_natural_exit(self):
        kernel = self.kernel(waits=(258, 0), ended=0, error=5)
        self.assertIsNone(updates.wait_for_exit(42))
        kernel.TerminateProcess.assert_called_once_with(123, 1)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_successful_termination_without_signalled_exit_is_not_success(self):
        kernel = self.kernel(waits=(258, 258))
        with self.assertRaises(OSError):
            updates.wait_for_exit(42)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_refused_termination_without_exit_is_not_success(self):
        kernel = self.kernel(waits=(258, 258), ended=0, error=5)
        with self.assertRaises(OSError):
            updates.wait_for_exit(42)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_failed_final_wait_is_not_success(self):
        kernel = self.kernel(waits=(258, 0xFFFFFFFF), error=6)
        with self.assertRaises(OSError):
            updates.wait_for_exit(42)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_unconfirmed_exit_preserves_every_file_and_never_launches(self):
        # Even unlocked program files must be preserved; sharing violations are
        # not a substitute for knowing the old process ended.
        for waits, opened, ended, error in [
            ((0,), 0, 1, 5),
            ((0xFFFFFFFF, 0xFFFFFFFF), 123, 1, 6),
            ((258, 258), 123, 0, 5),
            ((258, 258), 123, 1, 0),
            ((258, 0xFFFFFFFF), 123, 1, 6),
        ]:
            with self.subTest(waits=waits, opened=opened, ended=ended):
                kernel = self.kernel(waits, opened, ended, error)
                tmp = Path(_sandbox.mkdtemp())
                self.addCleanup(shutil.rmtree, tmp, True)
                old, new = tmp / "app", tmp / "new"
                old.mkdir()
                new.mkdir()
                (old / updates.EXE_NAME).write_bytes(b"old exe")
                (old / "config.json").write_bytes(b"user config")
                (old / "_internal").mkdir()
                (old / "_internal" / "runtime.dll").write_bytes(b"old runtime")
                (old / "leftover.old").write_bytes(b"prior backup")
                (new / updates.EXE_NAME).write_bytes(b"new exe")
                (new / "_internal").mkdir()
                (new / "_internal" / "runtime.dll").write_bytes(b"new runtime")
                def snapshot():
                    return {p.relative_to(old).as_posix(): p.read_bytes()
                            for p in old.rglob("*") if p.is_file()}
                before = snapshot()
                start = mock.Mock()
                self.assertFalse(updates.finish_update(
                    str(old), 42, str(new), start=start, wait=updates.wait_for_exit))
                self.assertEqual(snapshot(), before)
                start.assert_not_called()
                if opened:
                    kernel.CloseHandle.assert_called_once_with(123)

    def test_wait_exception_does_not_restart_old_app(self):
        start = mock.Mock()
        with mock.patch.object(updates, "swap_files") as swap:
            self.assertFalse(updates.finish_update(
                "app", 42, "new", start=start,
                wait=mock.Mock(side_effect=OSError("still running"))))
        swap.assert_not_called()
        start.assert_not_called()

    def test_successful_none_wait_keeps_existing_injected_wait_contract(self):
        start = mock.Mock()
        wait = mock.Mock(return_value=None)
        with mock.patch.object(updates, "swap_files") as swap:
            self.assertTrue(updates.finish_update(
                "app", 42, "new", start=start, wait=wait))
        wait.assert_called_once_with(42)
        swap.assert_called_once()
        swap.call_args.kwargs["start"]()
        start.assert_called_once()
