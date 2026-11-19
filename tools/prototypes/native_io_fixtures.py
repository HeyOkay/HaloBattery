"""Local Windows named-pipe and synthetic fixtures; never HID/USB/network I/O.

Named pipes exercise Windows overlapped I/O and cancellation. They do not prove
that a HID, USB, Bluetooth, or third-party driver honors cancellation.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import os
import threading
import time
from types import SimpleNamespace
import uuid

INVALID_HANDLE = ctypes.c_void_p(-1).value
ERROR_IO_PENDING = 997
ERROR_PIPE_CONNECTED = 535
ERROR_IO_INCOMPLETE = 996
ERROR_OPERATION_ABORTED = 995


class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", W.DWORD), ("OffsetHigh", W.DWORD), ("hEvent", W.HANDLE)]


def _kernel():
    if os.name != "nt":
        raise OSError("Windows named-pipe fixtures require Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateNamedPipeW": ([W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD,
                              W.DWORD, W.DWORD, W.DWORD, ctypes.c_void_p], W.HANDLE),
        "CreateFileW": ([W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p,
                          W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
        "CreateEventW": ([ctypes.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE),
        "ConnectNamedPipe": ([W.HANDLE, ctypes.c_void_p], W.BOOL),
        "GetOverlappedResult": ([W.HANDLE, ctypes.c_void_p,
                                  ctypes.POINTER(W.DWORD), W.BOOL], W.BOOL),
        "WaitForSingleObject": ([W.HANDLE, W.DWORD], W.DWORD),
        "CancelIoEx": ([W.HANDLE, ctypes.c_void_p], W.BOOL),
        "DisconnectNamedPipe": ([W.HANDLE], W.BOOL),
        "CloseHandle": ([W.HANDLE], W.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


# If the OS fails to complete a fixture connection after cancellation, pin its
# storage rather than release an OVERLAPPED the kernel might still reference.
_PINNED_CONNECTIONS = []


class NamedPipePair:
    """Connected local duplex pipe handles, both FILE_FLAG_OVERLAPPED."""

    def __init__(self):
        self.kernel = _kernel()
        self.server = self.client = None
        self._requests = []
        self.name = rf"\\.\pipe\HaloBatteryNativeIO_{os.getpid()}_{uuid.uuid4().hex}"
        self.server = self.kernel.CreateNamedPipeW(
            self.name, 0x00000003 | 0x40000000 | 0x00080000,
            0x00000008, 1, 65536, 65536, 0, None)
        if self.server == INVALID_HANDLE:
            self.server = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.client = self.kernel.CreateFileW(
                self.name, 0x80000000 | 0x40000000, 0, None, 3, 0x40000000, None)
            if self.client == INVALID_HANDLE:
                self.client = None
                raise ctypes.WinError(ctypes.get_last_error())
            self._connect()
        except Exception:
            self.close()
            raise

    def _connect(self):
        event = self.kernel.CreateEventW(None, True, False, None)
        if not event:
            raise ctypes.WinError(ctypes.get_last_error())
        overlapped = _Overlapped()
        overlapped.hEvent = event
        pending = False
        try:
            if self.kernel.ConnectNamedPipe(self.server, ctypes.byref(overlapped)):
                return
            error = ctypes.get_last_error()
            if error == ERROR_PIPE_CONNECTED:
                return
            if error != ERROR_IO_PENDING:
                raise ctypes.WinError(error)
            pending = True
            transferred = W.DWORD()
            for _ in range(100):
                if self.kernel.GetOverlappedResult(
                        self.server, ctypes.byref(overlapped), ctypes.byref(transferred), False):
                    pending = False
                    return
                error = ctypes.get_last_error()
                if error != ERROR_IO_INCOMPLETE:
                    pending = False
                    raise ctypes.WinError(error)
                self.kernel.WaitForSingleObject(event, 10)
            self.kernel.CancelIoEx(self.server, ctypes.byref(overlapped))
            for _ in range(100):
                if self.kernel.GetOverlappedResult(
                        self.server, ctypes.byref(overlapped), ctypes.byref(transferred), False):
                    pending = False
                    raise TimeoutError("fixture connection timed out before cancellation")
                error = ctypes.get_last_error()
                if error != ERROR_IO_INCOMPLETE:
                    pending = False
                    raise ctypes.WinError(error)
                self.kernel.WaitForSingleObject(event, 10)
            raise TimeoutError("fixture connection cancellation did not complete")
        finally:
            if pending:
                _PINNED_CONNECTIONS.append((self.server, event, overlapped))
                self.server = None
            else:
                self.kernel.CloseHandle(event)

    def track(self, request):
        self._requests = [old for old in self._requests
                          if getattr(old, "state", "") != "disposed"]
        self._requests.append(request)
        return request

    def take_server(self):
        """Transfer server ownership to a transport DeviceHandle."""
        value, self.server = self.server, None
        return value

    def close(self):
        # Cleanup never closes a handle under unresolved native I/O.
        for request in self._requests:
            try:
                if request.poll() is None:
                    request.cancel()
                    wait_completion(request, timeout=1.0)
                if not request.dispose():
                    raise RuntimeError("fixture request disposal remains unconfirmed")
            except Exception:
                _PINNED_CONNECTIONS.append((self.server, self.client, self._requests))
                self.server = self.client = None
                return False
        self._requests.clear()
        if self.client is not None:
            self.kernel.CloseHandle(self.client)
            self.client = None
        if self.server is not None:
            self.kernel.DisconnectNamedPipe(self.server)
            self.kernel.CloseHandle(self.server)
            self.server = None
        return True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def wait_completion(request, timeout=3.0):
    deadline = time.monotonic() + timeout
    while True:
        result = request.poll()
        if result is not None:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError("fixture operation did not reach terminal completion")
        time.sleep(0.001)


class DelayedUncancellableOperation:
    """Synthetic operation: cancellation requests do not complete or stop it."""

    def __init__(self, data=b"late"):
        self.started = threading.Event()
        self.completed = threading.Event()
        self.data = data
        self.cancel_requested = False
        self.disposed = False
        self.dispose_attempts = 0

    def start(self):
        self.started.set()

    def cancel(self):
        self.cancel_requested = True
        return True

    def poll(self):
        if not self.completed.is_set():
            return None
        return SimpleNamespace(success=True, error=0, transferred=len(self.data),
                               data=self.data, cancel_requested=self.cancel_requested,
                               status="success")

    def dispose(self):
        self.dispose_attempts += 1
        if not self.completed.is_set():
            return False
        self.disposed = True
        return True


class BlockingStartOperation(DelayedUncancellableOperation):
    """Synthetic unreturning native issue call; used only in lifecycle child."""

    def start(self):
        self.started.set()
        self.completed.wait()



class BlockingWin32StartOperation(DelayedUncancellableOperation):
    """Real unreturning WinDLL wait in a synthetic request; no device I/O."""

    def __init__(self):
        super().__init__()
        self.kernel = _kernel()
        self.native_event = self.kernel.CreateEventW(None, True, False, None)
        if not self.native_event:
            raise ctypes.WinError(ctypes.get_last_error())

    def start(self):
        self.started.set()
        # ctypes.WinDLL releases the GIL for this genuine native wait.
        self.kernel.WaitForSingleObject(self.native_event, 0xffffffff)
