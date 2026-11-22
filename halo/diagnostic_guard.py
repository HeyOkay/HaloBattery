"""Hold the existing app-instance name during a targeted device diagnostic."""
import ctypes
import sys


INSTANCE_NAME = "Global\\HaloBattery_single_instance"
ERROR_ALREADY_EXISTS = 183


class InstanceGuard:
    """An owned kernel handle, retained until explicit successful close.

    Creating the named object is sufficient: ordinary startup checks whether
    that name exists. We do not acquire/release mutex ownership or wait on it.
    The optional kernel is an injectable fake used by hardware-free tests.
    """

    def __init__(self, *, kernel=None, set_error=None, get_error=None):
        if kernel is None:
            if sys.platform != "win32":
                raise OSError("Windows instance guard unavailable")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel, self._handle = kernel, None
        self._set_error = set_error if set_error is not None else ctypes.set_last_error
        self._get_error = get_error if get_error is not None else ctypes.get_last_error
        # HANDLE is pointer-sized; DWORD and BOOL are 32-bit on Windows.
        kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int32, ctypes.c_wchar_p)
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.GetLastError.argtypes = ()
        kernel.GetLastError.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel.CloseHandle.restype = ctypes.c_int32

    def acquire(self):
        if self._handle is not None:
            raise RuntimeError("instance guard already acquired")
        self._set_error(0)
        self._handle = self._kernel.CreateMutexW(None, False, INSTANCE_NAME)
        error = self._get_error()
        if not self._handle:
            self._handle = None
            raise OSError("cannot create app-instance guard")
        # Read ctypes' private last error immediately. Even a handle
        # returned for an existing mutex belongs to us and must be closed.
        return error != ERROR_ALREADY_EXISTS

    def close(self):
        if not self._handle:
            self._handle = None
            return True
        if not self._kernel.CloseHandle(self._handle):
            return False
        self._handle = None
        return True
