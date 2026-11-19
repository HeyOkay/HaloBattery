"""Bounded Windows overlapped I/O with explicit request and handle ownership.

This is a transport, not a scheduler or a replacement for all hidapi functions.
Call factory/open/start/cancel/dispose/owner.close on the scheduler's fixed issue
workers. poll() only uses GetOverlappedResult(wait=False), or cached Python state.
A deadline bounds the caller's wait; driver cancellation is not guaranteed.
Each input/output buffer is limited to 32 KiB; larger buffers are rejected,
never truncated. This transport ceiling is not a universal HID compatibility claim.

Handles must support FILE_FLAG_OVERLAPPED and must not be managed by another
completion dispatcher. Raw handles are borrowed: the caller must keep them open
until every associated request completes and is disposed. own_handle/open_path
transfer ownership; their close is deferred until all request leases are released.
No destructor or interpreter-exit handler frees unresolved native I/O storage.

Sources:
https://learn.microsoft.com/windows/win32/api/ioapiset/nf-ioapiset-deviceiocontrol
https://learn.microsoft.com/windows/win32/api/ioapiset/nf-ioapiset-cancelioex
https://learn.microsoft.com/windows/win32/api/minwinbase/ns-minwinbase-overlapped
https://learn.microsoft.com/windows/win32/fileio/canceling-pending-i-o-operations
https://learn.microsoft.com/windows/win32/api/heapapi/nf-heapapi-heapalloc
https://learn.microsoft.com/windows/win32/api/heapapi/nf-heapapi-heapfree
https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/shared/hidclass.h
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
import threading

DWORD = ctypes.c_uint32
BOOL = ctypes.c_int32
HANDLE = ctypes.c_void_p
ULONG_PTR = ctypes.c_size_t
MAX_BUFFER = 32768
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
FILE_FLAG_OVERLAPPED = 0x40000000
OPEN_EXISTING = 3
ERROR_IO_PENDING = 997
ERROR_IO_INCOMPLETE = 996
ERROR_OPERATION_ABORTED = 995
ERROR_NOT_FOUND = 1168
ERROR_INVALID_DATA = 13
ERROR_INVALID_HANDLE = 6
ERROR_INVALID_PARAMETER = 87
# Public HIDClass upper-edge IOCTLs, from Microsoft's hidclass.h.
IOCTL_HID_GET_FEATURE = (0x0B << 16) | (100 << 2) | 2  # METHOD_OUT_DIRECT
IOCTL_HID_SET_FEATURE = (0x0B << 16) | (100 << 2) | 1  # METHOD_IN_DIRECT


class OVERLAPPED(ctypes.Structure):
    # Offset/OffsetHigh occupy the same union storage as PVOID Pointer.
    _fields_ = [("Internal", ULONG_PTR), ("InternalHigh", ULONG_PTR),
                ("Offset", DWORD), ("OffsetHigh", DWORD), ("hEvent", HANDLE)]


@dataclass(frozen=True)
class Completion:
    success: bool
    error: int
    transferred: int
    data: bytes
    cancel_requested: bool

    @property
    def status(self):
        return "success" if self.success else (
            "cancelled" if self.error == ERROR_OPERATION_ABORTED else "error")


def _size(value, *, positive=False):
    if type(value) is not int or not (int(positive) <= value <= MAX_BUFFER):
        raise ValueError("buffer size must be within the transport's 32 KiB limit")
    return value


def _bytes(value):
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError("buffer must be bytes, bytearray or memoryview")
    _size(value.nbytes if isinstance(value, memoryview) else len(value))
    return bytes(value)


def _handle(value):
    value = value.value if isinstance(value, ctypes.c_void_p) else value
    maximum = (1 << (ctypes.sizeof(HANDLE) * 8)) - 1
    if type(value) is not int or not 0 < value < maximum:
        raise ValueError("invalid native handle")
    return value


class DeviceHandle:
    """Explicit native ownership; worker-only close may block in a driver.

    False means leases or an unfinished/failed close still retain ownership.
    close_error reports native close failure; closed is true only after success.
    """

    def __init__(self, api, handle, *, owned):
        self.api = api
        self.handle = _handle(handle)
        self.owned = owned
        self._lock = threading.Lock()
        self._leases = 0
        self._closing = False
        self._closed = False
        self._close_active = False
        self.close_error = None

    @property
    def closing(self):
        with self._lock:
            return self._closing

    @property
    def closed(self):
        with self._lock:
            return self._closed

    @property
    def active_requests(self):
        with self._lock:
            return self._leases

    def _acquire(self):
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("device handle is closing")
            self._leases += 1

    def _release(self):
        with self._lock:
            if self._leases <= 0:
                raise RuntimeError("device handle lease was released twice")
            self._leases -= 1
            closing = self._closing and not self._leases
        return self.close() if closing else True

    def close(self):
        with self._lock:
            self._closing = True
            if self._closed:
                return True
            if self._leases or self._close_active:
                return False
            self._close_active = True
        try:
            if self.owned and not self.api._dll.CloseHandle(self.handle):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException as exc:
            with self._lock:
                self.close_error = exc
                self._close_active = False
            return False
        with self._lock:
            self._closed = True
            self._close_active = False
        return True


class WindowsApi:
    """WinDLL releases the Python GIL during native calls; importing is inert."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("the native I/O transport requires Windows")
        self._dll = ctypes.WinDLL("kernel32", use_last_error=True)
        pointer = ctypes.POINTER(OVERLAPPED)
        prototypes = {
            "GetProcessHeap": ([], HANDLE),
            "HeapAlloc": ([HANDLE, DWORD, ctypes.c_size_t], ctypes.c_void_p),
            "HeapFree": ([HANDLE, DWORD, ctypes.c_void_p], BOOL),
            "CreateEventW": ([ctypes.c_void_p, BOOL, BOOL, ctypes.c_wchar_p], HANDLE),
            "CloseHandle": ([HANDLE], BOOL),
            "CreateFileW": ([ctypes.c_wchar_p, DWORD, DWORD, ctypes.c_void_p,
                             DWORD, DWORD, HANDLE], HANDLE),
            "ReadFile": ([HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), pointer], BOOL),
            "WriteFile": ([HANDLE, ctypes.c_void_p, DWORD, ctypes.POINTER(DWORD), pointer], BOOL),
            "DeviceIoControl": ([HANDLE, DWORD, ctypes.c_void_p, DWORD, ctypes.c_void_p,
                                 DWORD, ctypes.POINTER(DWORD), pointer], BOOL),
            "GetOverlappedResult": ([HANDLE, pointer, ctypes.POINTER(DWORD), BOOL], BOOL),
            "CancelIoEx": ([HANDLE, pointer], BOOL),
        }
        for name, (arguments, result) in prototypes.items():
            function = getattr(self._dll, name)
            function.argtypes, function.restype = arguments, result
        self._heap = self._dll.GetProcessHeap()
        if not self._heap:
            raise ctypes.WinError(ctypes.get_last_error())
        self._live_lock = threading.Lock()
        # Strong references outlive a scheduler timeout. Native HeapAlloc storage
        # additionally survives Python finalization if a driver never completes.
        self._live_requests = set()

    @property
    def live_request_count(self):
        with self._live_lock:
            return len(self._live_requests)

    def borrow_handle(self, handle):
        return DeviceHandle(self, handle, owned=False)

    def own_handle(self, handle):
        return DeviceHandle(self, handle, owned=True)

    def open_path(self, path, *, access=GENERIC_READ | GENERIC_WRITE,
                  share=FILE_SHARE_READ | FILE_SHARE_WRITE, flags=FILE_FLAG_OVERLAPPED):
        """Worker-only synchronous open; FILE_FLAG_OVERLAPPED does not time-limit open."""
        path = os.fspath(path)
        if not isinstance(path, str) or not path or "\0" in path:
            raise ValueError("path must be a nonempty Unicode path")
        for value in (access, share, flags):
            if type(value) is not int or not 0 <= value <= 0xffffffff:
                raise ValueError("invalid Windows open flags")
        raw = self._dll.CreateFileW(path, access, share, None, OPEN_EXISTING,
                                    flags | FILE_FLAG_OVERLAPPED, None)
        if raw == ctypes.c_void_p(-1).value or not raw:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return self.own_handle(raw)
        except BaseException:
            self._dll.CloseHandle(raw)
            raise

    def _owner(self, handle):
        if isinstance(handle, DeviceHandle):
            if handle.api is not self:
                raise ValueError("handle belongs to a different WindowsApi instance")
            return handle
        return self.borrow_handle(handle)

    def create_read(self, handle, size):
        return IoRequest(self, self._owner(handle), "read", b"", _size(size, positive=True))

    def create_write(self, handle, data):
        return IoRequest(self, self._owner(handle), "write", _bytes(data), 0)

    def create_ioctl(self, handle, code, input_data=b"", output_size=0, output_initial=b""):
        if type(code) is not int or not 0 <= code <= 0xffffffff:
            raise ValueError("invalid IOCTL code")
        initial = _bytes(output_initial)
        size = _size(output_size)
        if len(initial) > size:
            raise ValueError("initial output data exceeds its buffer")
        return IoRequest(self, self._owner(handle), "ioctl", _bytes(input_data), size,
                         code=code, output_initial=initial)

    def create_get_feature(self, handle, report_id, size):
        """Experimental report factory; no physical HID compatibility is claimed."""
        if type(report_id) is not int or not 0 <= report_id <= 255:
            raise ValueError("invalid HID report ID")
        size = _size(size, positive=True)
        return self.create_ioctl(handle, IOCTL_HID_GET_FEATURE, output_size=size,
                                 output_initial=bytes((report_id,)))

    def create_set_feature(self, handle, report):
        """Experimental; HIDClass documents zero output bytes for SET_FEATURE."""
        data = _bytes(report)
        if not data:
            raise ValueError("HID feature report must include its report ID")
        return self.create_ioctl(handle, IOCTL_HID_SET_FEATURE, input_data=data)


class IoRequest:
    """One native operation; no lock spans a native call.

    NEW cancel is entirely local and immediately terminal, so late factories can
    be cancelled/disposed without issuing late device traffic. Unexpected Python
    exceptions during native issue/probe leave an uncertain, pinned operation;
    they must never be interpreted as proof that storage can be freed.
    """

    def __init__(self, api, owner, kind, input_data, output_size, *, code=0, output_initial=b""):
        self.api, self.owner, self.kind = api, owner, kind
        self.code = code
        self.input_size, self.output_size = len(input_data), output_size
        self._lock = threading.Lock()
        self._state = "new"
        self._completion = None
        self._cancel_requested = False
        self._native_users = 0
        self._lease_released = False
        self.cancel_error = None
        self.start_error = None
        self._allocation = self._event = self._input = self._output = 0
        owner._acquire()
        try:
            total = ctypes.sizeof(OVERLAPPED) + len(input_data) + output_size
            self._allocation = api._dll.HeapAlloc(api._heap, 8, total)  # HEAP_ZERO_MEMORY
            if not self._allocation:
                raise MemoryError("native I/O allocation failed")
            self._overlapped = ctypes.cast(self._allocation, ctypes.POINTER(OVERLAPPED))
            self._input = self._allocation + ctypes.sizeof(OVERLAPPED) if input_data else 0
            self._output = self._allocation + ctypes.sizeof(OVERLAPPED) + len(input_data) if output_size else 0
            if input_data:
                ctypes.memmove(self._input, input_data, len(input_data))
            if output_initial:
                ctypes.memmove(self._output, output_initial, len(output_initial))
            self._event = api._dll.CreateEventW(None, True, False, None)
            if not self._event:
                raise ctypes.WinError(ctypes.get_last_error())
            self._overlapped.contents.hEvent = self._event
        except BaseException:
            # Construction has not issued I/O, so immediate rollback is safe.
            if self._event:
                api._dll.CloseHandle(self._event)
            if self._allocation:
                api._dll.HeapFree(api._heap, 0, self._allocation)
            owner._release()
            raise
        with api._live_lock:
            api._live_requests.add(self)

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def completion(self):
        with self._lock:
            return self._completion

    @property
    def native_addresses(self):
        with self._lock:
            return dict(allocation=self._allocation, overlapped=self._allocation,
                        input=self._input, output=self._output, event=self._event)

    def _finish(self, error, transferred=0):
        limit = self.input_size if self.kind == "write" else self.output_size
        if not 0 <= transferred <= limit:
            error, transferred = ERROR_INVALID_DATA, 0
        data = (ctypes.string_at(self._output, transferred)
                if not error and self._output and transferred else b"")
        with self._lock:
            if self._completion is None:
                self._completion = Completion(not error, error, transferred, data,
                                              self._cancel_requested)
                self._state = "completed"
            return self._completion

    def _probe(self):
        transferred = DWORD()
        ok = self.api._dll.GetOverlappedResult(self.owner.handle, self._overlapped,
                                              ctypes.byref(transferred), False)
        error = 0 if ok else ctypes.get_last_error()
        if error in (ERROR_IO_PENDING, ERROR_IO_INCOMPLETE):
            return None
        if error in (ERROR_INVALID_HANDLE, ERROR_INVALID_PARAMETER):
            # A caller closing a borrowed handle breaks the ownership contract;
            # that error does not prove the kernel stopped using the buffers.
            with self._lock:
                self._state = "uncertain"
            return None
        return self._finish(error, transferred.value)

    def start(self):
        with self._lock:
            if self._state != "new":
                if self._completion and self._completion.error == ERROR_OPERATION_ABORTED:
                    return
                raise RuntimeError("native request can only be started once")
            if self.owner.closing:
                self._cancel_requested = True
                self._completion = Completion(False, ERROR_OPERATION_ABORTED, 0, b"", True)
                self._state = "completed"
                return
            self._state = "issuing"
            self._native_users += 1
        try:
            dll, handle = self.api._dll, self.owner.handle
            if self.kind == "read":
                ok = dll.ReadFile(handle, self._output, self.output_size, None, self._overlapped)
            elif self.kind == "write":
                ok = dll.WriteFile(handle, self._input or None, self.input_size, None, self._overlapped)
            else:
                ok = dll.DeviceIoControl(handle, self.code, self._input or None, self.input_size,
                                         self._output or None, self.output_size, None, self._overlapped)
            error = 0 if ok else ctypes.get_last_error()
            if ok:
                self._probe()
            elif error != ERROR_IO_PENDING:
                self._finish(error)
            with self._lock:
                # Only expose pending after classifying the issue outcome. A
                # failed, never-issued zeroed OVERLAPPED can look completed.
                if self._completion is None and self._state == "issuing":
                    self._state = "pending"
                retry_cancel = self._cancel_requested and self._completion is None
            if retry_cancel:
                # A cancel during issuance can race before an IRP exists.
                self.cancel()
        except BaseException as exc:
            with self._lock:
                self.start_error = exc
                if self._completion is None:
                    self._state = "uncertain"
            raise
        finally:
            with self._lock:
                self._native_users -= 1

    def cancel(self):
        with self._lock:
            if self._completion is not None or self._state == "disposed":
                return False
            self._cancel_requested = True
            if self._state == "new":
                self._completion = Completion(False, ERROR_OPERATION_ABORTED, 0, b"", True)
                self._state = "completed"
                return True
            self._native_users += 1
        try:
            ok = self.api._dll.CancelIoEx(self.owner.handle, self._overlapped)
            error = 0 if ok else ctypes.get_last_error()
            with self._lock:
                self.cancel_error = error
            return bool(ok)
        finally:
            with self._lock:
                self._native_users -= 1

    def poll(self):
        with self._lock:
            if self._completion is not None:
                return self._completion
            if self._state != "pending":
                return None
            self._native_users += 1
        try:
            return self._probe()
        except BaseException as exc:
            with self._lock:
                self.start_error = exc
                self._state = "uncertain"
            raise
        finally:
            with self._lock:
                self._native_users -= 1

    def result_bytes(self):
        completion = self.completion
        if completion is None:
            raise RuntimeError("native operation is not complete")
        if not completion.success:
            raise ctypes.WinError(completion.error)
        return completion.data

    def dispose(self):
        """Worker-only; False means storage/lease remain pinned and must be retained."""
        with self._lock:
            if self._state == "disposed":
                return True
            if self._completion is None or self._native_users:
                return False
            self._state = "disposing"
            allocation, event = self._allocation, self._event
            # The disposing state excludes concurrent disposal while native
            # cleanup runs without a Python lock.
            self._native_users += 1
        try:
            if event:
                if not self.api._dll.CloseHandle(event):
                    raise ctypes.WinError(ctypes.get_last_error())
                with self._lock:
                    self._event = 0
            if allocation:
                if not self.api._dll.HeapFree(self.api._heap, 0, allocation):
                    raise ctypes.WinError(ctypes.get_last_error())
                with self._lock:
                    self._allocation = self._input = self._output = 0
                    self._overlapped = None
            if not self._lease_released:
                closed = self.owner._release()
                self._lease_released = True
            else:
                closed = (self.owner.close() if self.owner.closing and
                          not self.owner.active_requests else True)
            if not closed:
                # Another worker may already be completing the owner's native
                # close. Keep this request registered until close is confirmed.
                with self._lock:
                    self._state = "completed"
                return False
            with self._lock:
                self._state = "disposed"
            with self.api._live_lock:
                self.api._live_requests.discard(self)
            return True
        except BaseException:
            with self._lock:
                self._state = "completed"
            raise
        finally:
            with self._lock:
                self._native_users -= 1
