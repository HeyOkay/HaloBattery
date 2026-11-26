"""PID reuse must not make the updater terminate another executable.

The DLL loader, process APIs and launches are all fakes.
"""
import ctypes
import os
from pathlib import Path
import shutil
import types
import unittest
from unittest import mock

import _sandbox
from halo import updates


class ProcessIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_sandbox.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.old, self.new = self.tmp / "app", self.tmp / "new"
        for directory, data in [(self.old, b"old"), (self.new, b"new")]:
            directory.mkdir()
            (directory / updates.EXE_NAME).write_bytes(data + b" exe")
            (directory / "_internal").mkdir()
            (directory / "_internal" / "runtime.dll").write_bytes(data + b" dll")
        (self.old / "config.json").write_bytes(b"user config")
        self.exe = str(self.old / updates.EXE_NAME)
        self.start = mock.Mock()

    def kernel(self, image=None, query_ok=True, waits=(258, 0), opened=123, error=5):
        image = self.exe if image is None else image
        def query(handle, flags, buffer, length):
            self.assertEqual((handle, flags), (123, 0))
            self.assertGreaterEqual(len(buffer), 32768)
            self.assertEqual(ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0], len(buffer))
            if not query_ok:
                return 0
            buffer.value = image
            ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = len(image)
            return 1
        kernel = types.SimpleNamespace(
            OpenProcess=mock.Mock(return_value=opened),
            WaitForSingleObject=mock.Mock(side_effect=waits),
            TerminateProcess=mock.Mock(return_value=1),
            QueryFullProcessImageNameW=mock.Mock(side_effect=query),
            CloseHandle=mock.Mock(return_value=1),
        )
        for owner, name, value in [
            (ctypes, "WinDLL", mock.Mock(return_value=kernel)),
            (ctypes, "get_last_error", mock.Mock(return_value=error)),
            (updates.sys, "platform", "win32"),
        ]:
            patcher = mock.patch.object(owner, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        return kernel

    def snapshot(self):
        return {p.relative_to(self.old).as_posix(): p.read_bytes()
                for p in self.old.rglob("*") if p.is_file()}

    def finish(self):
        return updates.finish_update(str(self.old), 42, str(self.new), start=self.start)

    def test_reused_pid_different_executable_is_never_terminated_or_installed(self):
        kernel = self.kernel(image=str(self.tmp / "OtherApp.exe"))
        before = self.snapshot()
        result = self.finish()
        kernel.TerminateProcess.assert_not_called()
        self.assertFalse(result)
        self.assertEqual(self.snapshot(), before)
        self.start.assert_not_called()
        kernel.QueryFullProcessImageNameW.assert_called_once()
        kernel.WaitForSingleObject.assert_called_once_with(123, 30000)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_same_basename_in_other_directory_is_not_the_old_app(self):
        kernel = self.kernel(image=str(self.tmp / "other" / updates.EXE_NAME))
        before = self.snapshot()
        self.assertFalse(self.finish())
        self.assertEqual(self.snapshot(), before)
        self.start.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_query_failure_fails_closed_before_termination_swap_or_launch(self):
        kernel = self.kernel(query_ok=False)
        before = self.snapshot()
        self.assertFalse(self.finish())
        self.assertEqual(self.snapshot(), before)
        self.start.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_empty_query_result_fails_closed(self):
        kernel = self.kernel(image="")
        self.assertFalse(self.finish())
        kernel.TerminateProcess.assert_not_called()
        self.start.assert_not_called()

    def test_matching_case_insensitive_path_can_terminate_and_install(self):
        kernel = self.kernel(image=self.exe.upper())
        self.assertTrue(self.finish())
        kernel.OpenProcess.assert_called_once_with(0x00101001, 0, 42)
        kernel.QueryFullProcessImageNameW.assert_called_once()
        kernel.TerminateProcess.assert_called_once_with(123, 1)
        kernel.CloseHandle.assert_called_once_with(123)
        self.assertEqual((self.old / updates.EXE_NAME).read_bytes(), b"new exe")
        self.start.assert_called_once_with([self.exe])

    def test_natural_exit_needs_no_query_or_termination(self):
        kernel = self.kernel(image="other.exe", query_ok=False, waits=(0,))
        self.assertTrue(self.finish())
        kernel.QueryFullProcessImageNameW.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_missing_pid_still_allows_installation(self):
        kernel = self.kernel(opened=0, error=87)
        self.assertTrue(self.finish())
        kernel.WaitForSingleObject.assert_not_called()
        kernel.QueryFullProcessImageNameW.assert_not_called()
        kernel.TerminateProcess.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_explicit_wait_still_receives_only_pid_and_accepts_none(self):
        wait = mock.Mock(return_value=None)
        self.assertTrue(updates.finish_update(str(self.old), 42, str(self.new),
                                             start=self.start, wait=wait))
        wait.assert_called_once_with(42)

    def test_unicode_image_path_larger_than_max_path_is_not_truncated(self):
        # The API buffer is measured in wchar characters, including space for NUL.
        image = os.path.join(str(self.tmp), "unicodé" * 80, updates.EXE_NAME)
        kernel = self.kernel(image=image)
        self.assertIsNone(updates.wait_for_exit(42, expected_exe=image))
        kernel.QueryFullProcessImageNameW.assert_called_once()
        kernel.TerminateProcess.assert_called_once_with(123, 1)
        kernel.CloseHandle.assert_called_once_with(123)
