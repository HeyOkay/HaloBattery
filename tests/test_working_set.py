import ctypes
import gc
import sys
import unittest
from unittest import mock

from halo import working_set


class Function:
    def __init__(self, name, calls, result):
        self.name, self.calls, self.result = name, calls, result

    def __call__(self, *args):
        self.calls.append((self.name, tuple(getattr(arg, "value", arg) for arg in args)))
        return self.result


class Kernel32:
    def __init__(self, calls, trimmed=1):
        self.GetProcessHeap = Function("GetProcessHeap", calls, 0x1234)
        self.HeapCompact = Function("HeapCompact", calls, 4096)
        self.GetCurrentProcess = Function("GetCurrentProcess", calls, -1)
        self.SetProcessWorkingSetSize = Function("SetProcessWorkingSetSize", calls, trimmed)


class ReleaseUnusedMemoryTests(unittest.TestCase):
    def test_collects_compacts_the_process_heap_and_trims_the_whole_working_set(self):
        calls = []
        with mock.patch.object(working_set.sys, "platform", "win32"), \
                mock.patch.object(working_set.gc, "collect", lambda: calls.append(("collect", ()))), \
                mock.patch("ctypes.WinDLL", return_value=Kernel32(calls)) as load:
            self.assertTrue(working_set.release_unused_memory())
        load.assert_called_once_with("kernel32", use_last_error=True)
        everything = ctypes.c_size_t(-1).value
        self.assertEqual([call for call in calls if call[0] != "GetProcessHeap" and call[0] != "GetCurrentProcess"], [
            ("collect", ()),                                    # cycles first, so their memory is free
            ("HeapCompact", (0x1234, 0)),
            ("SetProcessWorkingSetSize", (-1, everything, everything)),
        ])

    def test_a_refused_trim_reports_false(self):
        with mock.patch.object(working_set.sys, "platform", "win32"), \
                mock.patch("ctypes.WinDLL", return_value=Kernel32([], trimmed=0)):
            self.assertFalse(working_set.release_unused_memory())

    def test_never_raises_when_the_api_is_unavailable(self):
        collected = []
        with mock.patch.object(working_set.sys, "platform", "win32"), \
                mock.patch.object(working_set.gc, "collect", lambda: collected.append(True)), \
                mock.patch("ctypes.WinDLL", side_effect=OSError("kernel32 unavailable")):
            self.assertFalse(working_set.release_unused_memory())
        self.assertEqual(collected, [True])

    def test_only_collects_off_windows(self):
        collected = []
        with mock.patch.object(working_set.sys, "platform", "linux"), \
                mock.patch.object(working_set.gc, "collect", lambda: collected.append(True)), \
                mock.patch("ctypes.WinDLL") as load:
            self.assertFalse(working_set.release_unused_memory())
        self.assertEqual(collected, [True])
        load.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows working sets")
    def test_really_shrinks_this_process_working_set(self):
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]

        def working_set_size():
            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            self.assertTrue(psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters),
                                                       counters.cb))
            return counters.WorkingSetSize

        touched = bytearray(32 * 1024 * 1024)               # 32 MB of pages this process really used
        touched[::4096] = b"\1" * len(touched[::4096])
        before = working_set_size()
        del touched
        gc.collect()
        self.assertTrue(working_set.release_unused_memory())
        self.assertLess(working_set_size(), before - 16 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
