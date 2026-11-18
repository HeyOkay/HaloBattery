"""In-process Windows HID adapter with bounded, worker-owned native calls.

Compatibility reference: cython-hidapi 0.15.0 hid.pyx and its HIDAPI submodule
commit d6b2a974608dec3b76fb1e36c189f22b9cf3650c/windows/hid.c:
https://github.com/trezor/cython-hidapi/tree/0.15.0
https://github.com/libusb/hidapi/blob/d6b2a974608dec3b76fb1e36c189f22b9cf3650c/windows/hid.c
No protocol bytes are introduced. Windows caps determine report sizes. The
transport rejects buffers over 32 KiB. Native deadlines bound publication, not
uncooperative drivers; no thread is killed and unresolved resources stay owned.
CreateFile, caps, strings and HidD_SetFeature remain synchronous worker calls.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import threading
import time

from halo.io_scheduler import IOScheduler
from halo.native_io import (
    WindowsApi, Completion, MAX_BUFFER, IOCTL_HID_GET_FEATURE,
    ERROR_OPERATION_ABORTED, HANDLE, DWORD,
)

IOCTL_HID_GET_INPUT_REPORT = (0x0B << 16) | (104 << 2) | 2
_WAIT = object()


@dataclass
class ScopeStatus:
    failed: bool = False
    error: str = ""
    cancelled: bool = False
    unsafe: bool = False


@dataclass(frozen=True)
class ReportCaps:
    input: int
    output: int
    feature: int


class _Pending:
    def __init__(self, probe):
        self.probe = probe


class _Invocation:
    def __init__(self, runtime, callback):
        self.runtime = runtime
        self.callback = callback
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.requests = []
        self.finalizers = []
        self.abort_finalizers = []
        self.on_cancel = []
        self.key = None
        self.shutdown_cleanup = False
        self._dispose_done = False
        self.cleanup_only = False
        self.owner_id = "unscoped"
        self._closing_devices = set()
        self.deadline = None
        self.result = None
        self.error = None
        self.pending = None
        self.started = self.running = self.finished = False
        self.waiting = True
        self._cleanup = None

    def expired(self):
        return self.deadline is not None and time.monotonic() >= self.deadline

    def start(self):
        with self.lock:
            if self.started or self.finished:
                return
            self.started = self.running = True
        previous = getattr(self.runtime._local, "invocation", None)
        self.runtime._local.invocation = self
        try:
            self.runtime.check_cancelled()
            value = self.callback()
            with self.lock:
                if isinstance(value, _Pending):
                    self.pending = value
                else:
                    self.result, self.finished = value, True
        except BaseException as exc:
            self.error, self.finished = exc, True
        finally:
            self.runtime._local.invocation = previous
            with self.lock:
                self.running = False

    def cancel(self):
        self.cancelled.set()
        for callback in tuple(self.on_cancel):
            callback()
        with self.lock:
            requests = tuple(self.requests)
            if not self.started:
                self.finished = True
        # Never hold a Python ownership lock across a native call.
        for request in requests:
            request.cancel()
        return True

    def poll(self):
        with self.lock:
            if self.running:
                return None
            pending = self.pending if not self.finished else None
        if pending is not None:
            previous = getattr(self.runtime._local, "invocation", None)
            self.runtime._local.invocation = self
            try:
                value = pending.probe()
                if value is not _WAIT:
                    self.result, self.finished = value, True
            except BaseException as exc:
                self.error, self.finished = exc, True
            finally:
                self.runtime._local.invocation = previous
        if not self.finished:
            return None
        if self.expired():
            self.cancel()
        failed = self.cancelled.is_set() or self.error is not None
        return Completion(not failed, ERROR_OPERATION_ABORTED if failed else 0,
                          0, b"", self.cancelled.is_set())

    def dispose(self):
        if self.running or not self.finished:
            return False
        if self.cancelled.is_set() or self.expired():
            self.cancel()
        for request in tuple(self.requests):
            if self.cancelled.is_set():
                request.cancel()
            if request.poll() is None or not request.dispose():
                return False
            self.requests.remove(request)
        with self.runtime._lock:
            devices = [d for d in self.runtime._devices
                       if d._key == self.key and d not in self._closing_devices
                       and (self.runtime._closing or d._close_requested)]
            self._closing_devices.update(devices)
        if devices:
            previous = getattr(self.runtime._local, "invocation", None)
            self.runtime._local.invocation = self
            try:
                for device in devices:
                    device._close_operation()
            finally:
                self.runtime._local.invocation = previous
            self._cleanup = None
            if self.requests:
                return False
        if self._cleanup is None:
            self._cleanup = list(self.finalizers)
            if self.cancelled.is_set() or self.error is not None:
                self._cleanup.extend(self.abort_finalizers)
        finalizers = self._cleanup
        while finalizers:
            callback = finalizers[0]
            if callback() is False:
                return False
            finalizers.pop(0)
        self.finalizers.clear()
        self.abort_finalizers.clear()
        # Synchronize the last intent check with HidDevice.close(). A caller
        # arriving after this acknowledgment waits for cached retirement and
        # submits a new same-key cleanup; it never loses a close request.
        with self.runtime._lock:
            pending = any(d._key == self.key and d not in self._closing_devices
                          and (self.runtime._closing or d._close_requested)
                          for d in self.runtime._devices)
            if not pending:
                self._dispose_done = True
        if pending:
            return False
        if self.runtime._closing:
            with self.runtime._lock:
                empty = not self.runtime._devices
            if empty:
                self.runtime.scheduler.stop_admission()
        return True


class TransportRuntime:
    """Two native workers; callers await cached state on provider threads only.

    invoke callbacks must guard between successive synchronous native calls with
    check_cancelled(). Callback results are Python objects, never scheduler byte
    buffers. provider_scope catches transport failure even if a provider does.
    """
    def __init__(self, *, scheduler=None, timeout=1.5, api=None):
        self.scheduler = scheduler if scheduler is not None else IOScheduler()
        self.timeout = timeout
        self._api = api
        self._api_init_lock = threading.Lock()
        self._local = threading.local()
        self._lock = threading.RLock()
        self._calls = {}
        self._devices = set()
        self._closing = False
        self._installed = None
        self._scope_markers = {}
        self._enumeration_lock = threading.Lock()

    @property
    def api(self):
        # Access this only on a transport worker: WinDLL loading is native work.
        if self._api is None:
            # Initialization only, never an ownership/cancellation lock. Both
            # callers are fixed native workers; no coordinator enters here.
            with self._api_init_lock:
                if self._api is None:
                    self._api = WindowsApi()
        return self._api

    @contextmanager
    def provider_scope(self, owner):
        previous = getattr(self._local, "scope", None)
        marker = ScopeStatus()
        owner = str(owner)
        self._local.scope = (owner, marker)
        with self._lock:
            self._scope_markers.setdefault(owner, []).append(marker)
        try:
            yield marker
        finally:
            with self._lock:
                markers = self._scope_markers[owner]
                markers.remove(marker)
                if not markers:
                    self._scope_markers.pop(owner, None)
            self._local.scope = previous

    def _failure(self, exc, *, unsafe=False):
        scope = getattr(self._local, "scope", None)
        if scope:
            scope[1].failed, scope[1].error = True, str(exc)
            scope[1].unsafe |= unsafe or isinstance(exc, TimeoutError)

    def _sweep(self, *, exclude_key=None):
        # Serialize snapshots with admission/forget: a forgotten key can start
        # again at generation 1, so a snapshot taken before this lock is stale.
        with self._lock:
            snapshots = self.scheduler.snapshot()
            for key, (generation, owner, invocation) in tuple(self._calls.items()):
                state = snapshots.get(key)
                if not invocation.waiting and (state is None or (state.generation == generation and not state.busy)):
                    self._calls.pop(key, None)
                    self.scheduler.forget(key)
            if not self._closing:
                # A queued close can expire before either issue worker is free.
                # Retain the device owner and coalesce one retry per idle key.
                for device in tuple(self._devices):
                    if device._key == exclude_key or not device._close_requested or device._owner is None:
                        continue
                    if device._key in self._calls:
                        continue
                    state = self.scheduler.snapshot().get(device._key)
                    if state is not None and state.busy:
                        continue
                    self.scheduler.forget(device._key)
                    invocation = _Invocation(self, lambda: None)
                    invocation.key = device._key
                    invocation.owner_id = device._owner_id
                    invocation.cleanup_only, invocation.waiting = True, False
                    generation = self.scheduler.submit(device._key, lambda inv=invocation: inv,
                                                       timeout=self.timeout)
                    if generation is not None:
                        self._calls[device._key] = generation, device._owner_id, invocation
            return snapshots

    def transport_idle(self, owner):
        self._sweep()
        with self._lock:
            return (not any(value[1] == str(owner) for value in self._calls.values())
                    and not any(device._owner_id == str(owner) and device._close_requested
                                and device._owner is not None for device in self._devices))

    def cancel_owner(self, owner):
        with self._lock:
            for marker in self._scope_markers.get(str(owner), ()):
                marker.cancelled = marker.failed = marker.unsafe = True
                marker.error = "provider transport scope cancelled"
            keys = []
            for key, value in self._calls.items():
                if value[1] == str(owner) and not value[2].cleanup_only:
                    value[2].cancelled.set()  # Pure admission signal, no native call.
                    keys.append(key)
            # Scheduler cancellation is Python bookkeeping. Keep admission
            # locked until it targets this call, before the key can be reused.
            for key in keys:
                self.scheduler.cancel(key)

    def check_cancelled(self):
        invocation = getattr(self._local, "invocation", None)
        if invocation is not None and (invocation.cancelled.is_set() or invocation.expired()):
            raise TimeoutError("native invocation cancelled")

    def invoke(self, callback, key, timeout=None, *, _cleanup=False):
        if getattr(self._local, "invocation", None) is not None:
            raise RuntimeError("nested transport invocation would deadlock")
        if self._closing:
            error = OSError("native runtime is closing")
            self._failure(error, unsafe=True)
            raise error
        timeout = self.timeout if timeout is None else timeout
        invocation = _Invocation(self, callback)
        invocation.key = key
        invocation.cleanup_only = _cleanup
        invocation.deadline = time.monotonic() + timeout
        scope = getattr(self._local, "scope", None)
        owner = scope[0] if scope else "unscoped"
        invocation.owner_id = owner
        if scope and scope[1].cancelled and not _cleanup:
            raise TimeoutError("provider transport scope cancelled")
        with self._lock:
            # Shutdown and admission share a linearization point as well.
            if self._closing:
                error = OSError("native runtime is closing")
                self._failure(error, unsafe=True)
                raise error
            # Cancellation and admission share this lock. The fast check above
            # can race with cancel_owner() while this caller waits to enter.
            if scope and scope[1].cancelled and not _cleanup:
                raise TimeoutError("provider transport scope cancelled")
            self._sweep(exclude_key=key)
            existing = self._calls.get(key)
            if _cleanup and existing is not None and existing[2].cleanup_only:
                # A cached-idle check may already have admitted this same close.
                # Acknowledge its result rather than stacking another job.
                generation, owner, invocation = existing
                invocation.waiting = True
            else:
                generation = self.scheduler.submit(key, lambda: invocation, timeout=timeout)
                if generation is None:
                    error = OSError("native transport busy or at capacity")
                    self._failure(error, unsafe=True)
                    raise error
                self._calls[key] = generation, owner, invocation
        try:
            return self._await(key, generation, invocation, timeout)
        finally:
            invocation.waiting = False
            self._sweep()

    def _await(self, key, generation, invocation, timeout):
        deadline = time.monotonic() + timeout
        while True:
            state = self.scheduler.snapshot().get(key)
            if state is not None and state.generation == generation:
                result = state.last_result
                if result is not None and result.generation == generation:
                    if result.status != "success":
                        error = invocation.error or TimeoutError("native invocation " + result.status)
                        unsafe = (state.busy or invocation.cancelled.is_set()
                                  or invocation.expired()
                                  or result.status in ("expired", "shutdown", "error"))
                        self._failure(error, unsafe=unsafe)
                        raise error
                    if not state.busy:
                        with self._lock:
                            current = self._calls.get(key)
                            if (current is not None and current[0] == generation
                                    and current[2] is invocation):
                                self._calls.pop(key, None)
                                self.scheduler.forget(key)
                        return invocation.result
            if time.monotonic() >= deadline:
                with self._lock:
                    current = self._calls.get(key)
                    if (current is not None and current[0] == generation
                            and current[2] is invocation):
                        self.scheduler.cancel(key)
                error = TimeoutError("native invocation deadline exceeded")
                self._failure(error)
                raise error
            time.sleep(0.002)

    def defer_request(self, request, transform=lambda completion, request: completion.data):
        invocation = self._local.invocation
        with invocation.lock:
            invocation.requests.append(request)
        if invocation.cancelled.is_set():
            request.cancel()
        self.check_cancelled()
        request.start()
        if invocation.cancelled.is_set():
            request.cancel()
        def probe():
            completion = request.poll()
            if completion is None:
                return _WAIT
            if not completion.success:
                raise OSError(completion.error, "native I/O failed")
            self.check_cancelled()
            return transform(completion, request)
        return _Pending(probe)

    def device(self):
        return HidDevice(self)

    def install(self, hid_module=None):
        """Install HID compatibility hooks; retain original enumeration bytes."""
        if self._installed is not None:
            return self
        if hid_module is None:
            import hid as hid_module
        original_device, original_enumerate = hid_module.device, hid_module.enumerate
        def enumerate_devices(vendor_id=0, product_id=0):
            deadline = time.monotonic() + self.timeout
            while True:
                scope = getattr(self._local, "scope", None)
                if scope and scope[1].cancelled:
                    raise TimeoutError("provider transport scope cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    error = TimeoutError("HID enumeration admission deadline exceeded")
                    self._failure(error)
                    raise error
                if self._enumeration_lock.acquire(timeout=min(.01, remaining)):
                    break
            try:
                return self.invoke(lambda: original_enumerate(vendor_id, product_id),
                                   "hid-discovery", max(.001, deadline - time.monotonic()))
            finally:
                self._enumeration_lock.release()
        hid_module.device = self.device
        hid_module.enumerate = enumerate_devices
        self._installed = hid_module, original_device, original_enumerate
        return self

    def diagnostics(self):
        stats = self.scheduler.stats()
        with self._lock:
            devices = len(self._devices)
        return ["Native HID transport: " + ("enabled" if self._installed is not None else "HID backend not installed"),
                "Native workers: 2; report limit: 32768 bytes",
                "Native device handles: " + str(devices),
                "Native active calls: " + str(stats["active"]),
                "Native quarantined calls: " + str(stats["quarantined"])]

    def close(self, timeout=0.5):
        deadline = time.monotonic() + min(0.5, max(0.0, timeout))
        with self._lock:
            self._closing = True
            for key, value in tuple(self._calls.items()):
                value[2].cancelled.set()
                self.scheduler.cancel(key)
            devices = tuple(self._devices)
            snapshots = self.scheduler.snapshot()
            for device in devices:
                state = snapshots.get(device._key)
                if state is not None and state.busy:
                    continue  # Its eventual dispose performs shutdown cleanup.
                invocation = _Invocation(self, lambda: None)
                invocation.key, invocation.waiting = device._key, False
                generation = self.scheduler.submit(device._key, lambda inv=invocation: inv,
                                                   timeout=0.25)
                if generation is not None:
                    self._calls[device._key] = generation, "shutdown", invocation
        while True:
            with self._lock:
                clean_devices = not self._devices
            if clean_devices:
                return self.scheduler.close(max(0.0, deadline - time.monotonic()))
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.002)


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint16) for name in (
        "Usage", "UsagePage", "InputReportByteLength", "OutputReportByteLength",
        "FeatureReportByteLength")] + [("Reserved", ctypes.c_uint16 * 17)] + [
        (name, ctypes.c_uint16) for name in (
        "NumberLinkCollectionNodes", "NumberInputButtonCaps", "NumberInputValueCaps",
        "NumberInputDataIndices", "NumberOutputButtonCaps", "NumberOutputValueCaps",
        "NumberOutputDataIndices", "NumberFeatureButtonCaps", "NumberFeatureValueCaps",
        "NumberFeatureDataIndices")]


class MetadataApi:
    """HID metadata and synchronous HID helpers; WinDLL releases the GIL.

    https://learn.microsoft.com/windows-hardware/drivers/ddi/hidpi/ns-hidpi-_hidp_caps
    https://learn.microsoft.com/windows-hardware/drivers/ddi/hidsdi/nf-hidsdi-hidd_setfeature
    """
    def __init__(self):
        self.dll = ctypes.WinDLL("hid", use_last_error=True)
        signatures = {
            "HidD_SetNumInputBuffers": ([HANDLE, DWORD], ctypes.c_ubyte),
            "HidD_GetPreparsedData": ([HANDLE, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_ubyte),
            "HidD_FreePreparsedData": ([ctypes.c_void_p], ctypes.c_ubyte),
            "HidP_GetCaps": ([ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)], ctypes.c_int32),
            "HidD_SetFeature": ([HANDLE, ctypes.c_void_p, DWORD], ctypes.c_ubyte),
        }
        for name in ("Manufacturer", "Product", "SerialNumber"):
            signatures["HidD_Get" + name + "String"] = ([HANDLE, ctypes.c_void_p, DWORD], ctypes.c_ubyte)
        for name, (args, result) in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, result

    def caps(self, owner, guard):
        guard()
        if not self.dll.HidD_SetNumInputBuffers(owner.handle, 64):
            raise ctypes.WinError(ctypes.get_last_error())
        guard()
        preparsed = ctypes.c_void_p()
        if not self.dll.HidD_GetPreparsedData(owner.handle, ctypes.byref(preparsed)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            guard()
            caps = _HIDP_CAPS()
            if self.dll.HidP_GetCaps(preparsed, ctypes.byref(caps)) != 0x110000:
                raise OSError("HidP_GetCaps failed")
            return ReportCaps(caps.InputReportByteLength, caps.OutputReportByteLength,
                              caps.FeatureReportByteLength)
        finally:
            self.dll.HidD_FreePreparsedData(preparsed)

    def send_feature(self, owner, data):
        buffer = ctypes.create_string_buffer(data, len(data))
        return bool(self.dll.HidD_SetFeature(owner.handle, buffer, len(data)))

    def string(self, owner, name):
        # Conservative USB limit from upstream hidapi; 126 WCHARs including NUL.
        buffer = ctypes.create_unicode_buffer(126)
        if not getattr(self.dll, "HidD_Get" + name + "String")(
                owner.handle, buffer, ctypes.sizeof(buffer)):
            raise OSError("get " + name + " string error")
        return buffer.value


def _length(value, *, allow_zero=False):
    if type(value) is not int or not int(not allow_zero) <= value <= MAX_BUFFER:
        raise ValueError("report size must be within the transport 32 KiB limit")
    return value


class HidDevice:
    def __init__(self, runtime, *, api=None, metadata=None):
        self.runtime, self._api, self._metadata = runtime, api, metadata
        self._owner = None
        self._caps = None
        self._read = None
        self._state_lock = threading.Lock()
        self._nonblocking = False
        self._close_requested = False
        self._owner_id = "unscoped"
        self._key = "hid-unopened:" + str(id(self))

    @property
    def api(self):
        if self._api is None:
            self._api = self.runtime.api
        return self._api

    @property
    def metadata(self):
        if self._metadata is None:
            self._metadata = MetadataApi()
        return self._metadata

    def _require_open(self):
        if self._owner is None or self._owner.closing or self._close_requested:
            raise ValueError("not open")

    def open_path(self, path):
        if self._owner is not None:
            raise RuntimeError("already open")
        path = path.decode("utf-8") if isinstance(path, bytes) else path
        if not isinstance(path, str) or not path or "\0" in path:
            raise ValueError("invalid device path")
        self._key = "hid:" + hashlib.sha256(path.casefold().encode("utf-8")).hexdigest()
        def open_device():
            self.runtime.check_cancelled()
            try:
                owner = self.api.open_path(path)
            except OSError:
                self.runtime.check_cancelled()
                owner = self.api.open_path(path, access=0)
            invocation = self.runtime._local.invocation
            def abort():
                if self._owner is owner:
                    self._owner = None
                    with self.runtime._lock:
                        self.runtime._devices.discard(self)
                return owner.close()
            invocation.abort_finalizers.append(abort)
            self.runtime.check_cancelled()
            caps = self.metadata.caps(owner, self.runtime.check_cancelled)
            for size in (caps.input, caps.output, caps.feature):
                _length(size, allow_zero=True)
            self.runtime.check_cancelled()
            self._owner, self._caps, self._nonblocking = owner, caps, False
            self._close_requested = False
            self._owner_id = invocation.owner_id
            with self.runtime._lock:
                self.runtime._devices.add(self)
        try:
            self.runtime.invoke(open_device, self._key)
        except OSError as exc:
            raise OSError("open failed") from exc

    def set_nonblocking(self, value):
        self._require_open()
        self._nonblocking = bool(value)
        return 0

    def get_report_lengths(self):
        """Cached per-collection byte lengths, including report IDs; no I/O.

        HIDP_CAPS, filled once while opening the collection. Return a copy so
        provider code cannot change the transport's immutable size contract.
        """
        self._require_open()
        return {"input": self._caps.input, "output": self._caps.output,
                "feature": self._caps.feature}

    def write(self, data):
        self._require_open()
        data = bytes(data)
        if not data:
            return -1
        _length(len(data))
        padded = data.ljust(self._caps.output, b"\0")
        def operation():
            return self.runtime.defer_request(self.api.create_write(self._owner, padded),
                                               lambda completion, request: completion.transferred)
        try:
            return self.runtime.invoke(operation, self._key)
        except OSError:
            return -1

    def send_feature_report(self, data):
        self._require_open()
        data = bytes(data)
        if not data:
            return -1
        _length(len(data))
        padded = data.ljust(self._caps.feature, b"\0")
        def operation():
            self.runtime.check_cancelled()
            return len(data) if self.metadata.send_feature(self._owner, padded) else -1
        result = self.runtime.invoke(operation, self._key)
        if result < 0:
            self.runtime._failure(OSError("feature write failed"))
        return result

    def _get_report(self, report, size, code):
        self._require_open()
        _length(size)
        initial = bytes((report & 255,)) + bytes(size - 1)
        def operation():
            request = self.api.create_ioctl(self._owner, code, input_data=initial,
                                           output_size=size, output_initial=initial)
            def unpack(completion, native):
                # HIDAPI adds the zero report-ID to the driver's byte count.
                # Copy before disposal, while the invocation owns native storage.
                address = native.native_addresses["output"]
                raw = ctypes.string_at(address, size)
                count = completion.transferred + int(raw[0] == 0)
                if count > size:
                    raise OSError("HID report exceeds caller buffer")
                return list(raw[:count])
            return self.runtime.defer_request(request, unpack)
        try:
            return self.runtime.invoke(operation, self._key)
        except OSError as exc:
            kind = "feature" if code == IOCTL_HID_GET_FEATURE else "input"
            expected = self._caps.feature if kind == "feature" else self._caps.input
            message = (f"HID {kind} read (report={report}, buffer={size}, collection={expected}): {exc}")
            error = type(exc)(exc.errno, message) if exc.errno is not None else type(exc)(message)
            self.runtime._failure(error, unsafe=isinstance(exc, TimeoutError))
            raise error from exc

    def get_feature_report(self, report_num, max_length):
        return self._get_report(report_num, max_length, IOCTL_HID_GET_FEATURE)

    def get_input_report(self, report_num, max_length):
        return self._get_report(report_num, max_length, IOCTL_HID_GET_INPUT_REPORT)

    def read(self, max_length, timeout_ms=0):
        self._require_open()
        _length(max_length)
        # Binding uses hid_read for <=0; a positive explicit timeout overrides
        # nonblocking mode. Native input buffer always uses the collection caps.
        delay = timeout_ms / 1000 if timeout_ms > 0 else (0 if self._nonblocking else None)
        def operation():
            invocation = self.runtime._local.invocation
            def attach(request):
                with invocation.lock:
                    if request not in invocation.requests:
                        invocation.requests.append(request)
            def cancel_read():
                with self._state_lock:
                    request, self._read = self._read, None
                if request is not None:
                    attach(request)
                    request.cancel()
            invocation.on_cancel.append(cancel_read)
            self.runtime.check_cancelled()
            with self._state_lock:
                request = self._read
            if request is None:
                request = self.api.create_read(self._owner, _length(self._caps.input))
                with self._state_lock:
                    self._read = request
                if invocation.cancelled.is_set():
                    cancel_read()
                    self.runtime.check_cancelled()
                request.start()
                if invocation.cancelled.is_set():
                    cancel_read()
            deadline = None if delay is None else time.monotonic() + delay
            def probe():
                self.runtime.check_cancelled()
                completion = request.poll()
                if completion is None:
                    return [] if deadline is not None and time.monotonic() >= deadline else _WAIT
                with self._state_lock:
                    if self._read is request:
                        self._read = None
                attach(request)
                if not completion.success:
                    raise OSError("read error")
                data = completion.data
                if data[:1] == b"\0":
                    data = data[1:]
                return list(data[:max_length])
            return _Pending(probe)
        return self.runtime.invoke(operation, self._key)

    def _string(self, name):
        self._require_open()
        def operation():
            self.runtime.check_cancelled()
            return self.metadata.string(self._owner, name)
        return self.runtime.invoke(operation, self._key)

    def get_manufacturer_string(self):
        return self._string("Manufacturer")

    def get_product_string(self):
        return self._string("Product")

    def get_serial_number_string(self):
        return self._string("SerialNumber")

    def _close_operation(self):
        owner = self._owner
        if owner is None:
            return
        invocation = self.runtime._local.invocation
        invocation._closing_devices.add(self)
        if self._read is not None:
            request, self._read = self._read, None
            invocation.requests.append(request)
            request.cancel()
        def close_owner():
            if not owner.close():
                return False
            if self._owner is owner:
                self._owner = None
            with self.runtime._lock:
                self.runtime._devices.discard(self)
            return True
        invocation.finalizers.append(close_owner)

    def close(self):
        with self.runtime._lock:
            if self._owner is None:
                return
            self._close_requested = True
            active = self.runtime._calls.get(self._key)
            if active is not None and not active[2]._dispose_done:
                # The old native call still owns this key. Its worker cleanup
                # consumes this intent after draining I/O, before releasing it.
                active[2].cancelled.set()
                self.runtime.scheduler.cancel(self._key)
                return
        # A previously completed disposal can await coordinator acknowledgment.
        # Only cached Python state is touched while it retires.
        deadline = time.monotonic() + self.runtime.timeout
        while True:
            state = self.runtime.scheduler.snapshot().get(self._key)
            if state is None or not state.busy:
                break
            self.runtime.scheduler.pump()
            if time.monotonic() >= deadline:
                error = TimeoutError("native close retirement deadline exceeded")
                self.runtime._failure(error)
                raise error
            time.sleep(.002)
        if self._owner is not None:
            # Cleanup is permitted after provider cancellation. It issues no
            # device commands and must run even if its owner was disabled.
            self.runtime.invoke(self._close_operation, self._key, _cleanup=True)


def device(runtime, **kwargs):
    """Bound factory for the HID compatibility proxy's ``device()``."""
    return HidDevice(runtime, **kwargs)
