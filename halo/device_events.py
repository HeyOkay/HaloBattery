"""Instant notice of device arrivals, removals and resume from sleep.

The poll loop already compares a cheap USB signature every few seconds, so a
receiver plugged in or a mouse moved to its cable is noticed without waiting
for the whole poll interval. That still costs a wake-up every few seconds and
misses changes which do not alter the HID tree (a Bluetooth link coming up, the
machine resuming). This module listens to the same Windows broadcasts the
vendor tools use and wakes the poll loop at once:

  * WM_DEVICECHANGE for USB device, HID and Bluetooth radio interfaces, and for
    the generic DBT_DEVNODES_CHANGED which follows any child-device change;
  * WM_POWERBROADCAST for resume from sleep.

Nothing is injected into other processes; the window is never shown. Events are
coalesced: a receiver exposes several interfaces and Windows sends one message
per node, so the callback runs once, 1.2 s after the last event (3 s after a
resume, when the devices need a moment before they answer). Windows only;
start() is a no-op elsewhere and returns False, and the signature check in the
app remains the fallback.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable, Optional

WM_DEVICECHANGE = 0x0219
WM_POWERBROADCAST = 0x0218
WM_DESTROY = 0x0002
WM_QUIT = 0x0012

DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVNODES_CHANGED = 0x0007
DBT_DEVTYP_DEVICEINTERFACE = 0x00000005

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000

# Interface classes worth a notification: every HID collection, every USB
# device node (a receiver's audio/consumer interfaces included) and the
# Bluetooth radio, whose port interface changes when a link comes up.
GUID_HID = "{4D1E55B2-F16F-11CF-88CB-001111000030}"
GUID_USB = "{A5DCBF10-6530-11D2-901F-00C04FB951ED}"
GUID_BTHPORT = "{0850302A-B344-4FDA-9BE9-40549B7312D6}"
INTERFACES = (GUID_HID, GUID_USB, GUID_BTHPORT)

DEVICE_DELAY = 1.2       # s after the last device message
RESUME_DELAY = 3.0       # s after resume: devices need a moment to answer


def classify(message: int, wparam: int) -> Optional[str]:
    """The reason to poll for a window message, or None when it is not ours.

    Kept pure so the mapping can be tested without a window or a device.
    """
    if message == WM_POWERBROADCAST:
        if wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
            return "resume"
        return None
    if message == WM_DEVICECHANGE:
        if wparam in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE, DBT_DEVNODES_CHANGED):
            return "device"
    return None


class DeviceEventWatcher:
    """Sets a callback when a device arrives, leaves or the PC resumes.

    `callback(reason)` runs on a short-lived timer thread, never inside the
    window procedure: the poll thread may do native I/O, and an event storm (a
    dock exposing six interfaces) must not run it once per node.
    """

    def __init__(self, callback: Callable[[str], None],
                 *, device_delay: float = DEVICE_DELAY,
                 resume_delay: float = RESUME_DELAY):
        self.callback = callback
        self.device_delay = max(0.0, float(device_delay))
        self.resume_delay = max(0.0, float(resume_delay))
        self.count = 0
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._hwnd = 0
        self._ok = False
        self._lock = threading.Lock()
        self._generation = 0
        self._reason = ""
        self._timer: Optional[threading.Timer] = None
        self._wndproc = None            # keep the ctypes callback alive

    # ---------------------------------------------------------------- public
    def start(self) -> bool:
        if sys.platform != "win32" or self._thread is not None:
            return self._ok
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True,
                                        name="device-events")
        self._thread.start()
        ready.wait(2.0)
        return self._ok

    def running(self) -> bool:
        return self._ok and self._thread is not None and self._thread.is_alive()

    def stop(self) -> None:
        with self._lock:
            self._generation += 1       # drop a pending debounce
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if self._thread_id:
            try:
                import ctypes
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass

    # ---------------------------------------------------------------- events
    def _signal(self, reason: str) -> None:
        with self._lock:
            # A resume wins over the device messages it produces, and a pending
            # device message is replaced rather than queued: one poll sees both.
            if reason == "resume" or self._reason != "resume":
                self._reason = reason
            self.count += 1
            self._generation += 1
            generation = self._generation
            delay = self.resume_delay if self._reason == "resume" else self.device_delay
            old, self._timer = self._timer, None
        if old is not None:
            old.cancel()
        timer = threading.Timer(delay, self._fire, args=(generation,))
        timer.daemon = True
        with self._lock:
            if generation == self._generation:
                self._timer = timer
        timer.start()

    def _fire(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            reason, self._reason = self._reason, ""
            self._timer = None
        try:
            self.callback(reason)
        except Exception:               # pragma: no cover - callback is app code
            pass

    # ---------------------------------------------------------------- window
    def _run(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        class DEV_BROADCAST_DEVICEINTERFACE(ctypes.Structure):
            _fields_ = [("dbcc_size", wintypes.DWORD),
                        ("dbcc_devicetype", wintypes.DWORD),
                        ("dbcc_reserved", wintypes.DWORD),
                        ("dbcc_classguid", GUID),
                        ("dbcc_name", ctypes.c_wchar * 1)]

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT),
                        ("lpfnWndProc", ctypes.c_void_p),
                        ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int),
                        ("hInstance", ctypes.c_void_p),
                        ("hIcon", ctypes.c_void_p),
                        ("hCursor", ctypes.c_void_p),
                        ("hbrBackground", ctypes.c_void_p),
                        ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR)]

        def guid(text: str) -> GUID:
            import uuid
            return GUID.from_buffer_copy(uuid.UUID(text).bytes_le)

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        wndproc_type = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                          ctypes.c_void_p, ctypes.c_void_p)
        user32.DefWindowProcW.restype = ctypes.c_void_p
        user32.DefWindowProcW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                          ctypes.c_void_p, ctypes.c_void_p]
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                           wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        user32.RegisterDeviceNotificationW.restype = ctypes.c_void_p
        user32.RegisterDeviceNotificationW.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                                       wintypes.DWORD]
        user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                       wintypes.UINT]
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p

        def on_message(hwnd, message, wparam, lparam):
            try:
                reason = classify(message, int(wparam or 0))
                if reason:
                    self._signal(reason)
                elif message == WM_DESTROY:
                    user32.PostQuitMessage(0)
            except Exception:
                pass
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)

        self._wndproc = wndproc_type(on_message)
        instance = kernel32.GetModuleHandleW(None)
        class_name = "HaloBatteryDeviceEvents"
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = instance
        wc.lpszClassName = class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            ready.set()
            return
        hwnd = user32.CreateWindowExW(0, class_name, "", 0, 0, 0, 0, 0,
                                      None, None, instance, None)
        if not hwnd:
            ready.set()
            return
        self._hwnd = hwnd
        handles = []
        try:
            for text in INTERFACES:
                size = ctypes.sizeof(DEV_BROADCAST_DEVICEINTERFACE)
                filt = DEV_BROADCAST_DEVICEINTERFACE()
                filt.dbcc_size = size
                filt.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
                filt.dbcc_classguid = guid(text)
                h = user32.RegisterDeviceNotificationW(hwnd, ctypes.byref(filt),
                                                       DEVICE_NOTIFY_WINDOW_HANDLE)
                if h:
                    handles.append(h)
            self._thread_id = kernel32.GetCurrentThreadId()
            self._ok = bool(handles)
            ready.set()
            if not handles:
                return
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for h in handles:
                try:
                    user32.UnregisterDeviceNotification(h)
                except Exception:
                    pass
            try:
                user32.DestroyWindow(hwnd)
            except Exception:
                pass
            self._ok = False
