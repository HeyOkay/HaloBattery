"""Give memory that a finished one-off job freed back to Windows.

Python keeps freed memory for reuse: a small-object arena stays while any object in it is alive, and
the C heap keeps freed blocks committed. After a Settings window - icon search index, previews, HTTP -
that is about 30 MB the tray never touches again, yet Task Manager keeps counting it until Windows
trims the process under memory pressure. Trimming right away does the same thing earlier; pages the
app still uses come back on first touch (a soft page fault, microseconds each).
"""
import gc
import logging
import sys

log = logging.getLogger("halo_battery")


def release_unused_memory():
    """Collect reference cycles, compact the C heap and trim the working set. Never raises."""
    gc.collect()                    # a closed window's catalog can sit in a reference cycle
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetProcessHeap.restype = wintypes.HANDLE
        kernel32.HeapCompact.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.HeapCompact.restype = ctypes.c_size_t
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetProcessWorkingSetSize.argtypes = [wintypes.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
        kernel32.SetProcessWorkingSetSize.restype = wintypes.BOOL
        # Python's malloc is the CRT's, which allocates from the default process heap.
        kernel32.HeapCompact(kernel32.GetProcessHeap(), 0)
        # (SIZE_T)-1 as both limits removes as many pages as possible from the working set:
        # https://learn.microsoft.com/windows/win32/api/memoryapi/nf-memoryapi-setprocessworkingsetsize
        everything = ctypes.c_size_t(-1)
        return bool(kernel32.SetProcessWorkingSetSize(kernel32.GetCurrentProcess(), everything, everything))
    except (OSError, AttributeError, ValueError):
        log.debug("memory: working set trim unavailable", exc_info=True)
        return False
