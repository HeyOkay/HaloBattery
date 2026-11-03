"""ATK keyboard activity from Windows Raw Input headers; no key contents retained.

Uses RegisterRawInputDevices (keyboard, RIDEV_INPUTSINK), GetRawInputData
(RID_HEADER only), and GetRawInputDeviceInfoW (RIDI_DEVICENAME), as documented
by Microsoft Win32 Raw Input. No HID device is opened or written by this listener.
"""
import sys
import threading
import time

# How long recent ATK input permits an active battery request.
ACTIVE_WINDOW = 10.0


class KeyboardActivity:
    def __init__(self):
        self.paths = frozenset()
        self.last = None
        self.wake = None
        self._lock = threading.Lock()
        self._thread = None
        self._hwnd = None
        self._ready = threading.Event()
        self._ok = False

    def configure(self, paths):
        paths = frozenset(self.normalize(p) for p in paths)
        with self._lock:
            if paths != self.paths:
                self.paths = paths
                self.last = None

    @staticmethod
    def normalize(path):
        return (path.decode("utf-8", errors="replace") if isinstance(path, bytes)
                else str(path)).lower().split("#{", 1)[0]

    def observe(self, kind, path):
        # Vendor battery reports and other keyboards never count as typing.
        with self._lock:
            if kind != 1 or self.normalize(path) not in self.paths:
                return
            now = time.monotonic()
            resumed = self.last is None or now - self.last >= ACTIVE_WINDOW
            self.last = now
        if resumed and self.wake is not None:
            self.wake()

    def active(self):
        with self._lock:
            return self.last is not None and 0 <= time.monotonic() - self.last < ACTIVE_WINDOW

    def start(self):
        if self._thread is None and sys.platform == "win32":
            self._thread = threading.Thread(target=self._run, daemon=True, name="atk-activity")
            self._thread.start()
            self._ready.wait(2)
        return self._ok

    def close(self):
        if self._hwnd:
            import ctypes
            user = ctypes.WinDLL("user32", use_last_error=True)
            user.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
            user.PostMessageW(self._hwnd, 0x0010, 0, 0)
        if self._thread:
            self._thread.join(2)

    def _run(self):
        import ctypes as C
        from ctypes import wintypes as W
        from .base import log
        user = C.WinDLL("user32", use_last_error=True)
        kernel = C.WinDLL("kernel32", use_last_error=True)
        PROC = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        class WC(C.Structure):
            _fields_ = [("style", W.UINT), ("proc", PROC), ("ce", C.c_int), ("we", C.c_int),
                        ("instance", W.HINSTANCE), ("icon", W.HICON), ("cursor", W.HANDLE),
                        ("brush", W.HBRUSH), ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]
        class RID(C.Structure):
            _fields_ = [("page", W.USHORT), ("usage", W.USHORT), ("flags", W.DWORD), ("target", W.HWND)]
        class HEADER(C.Structure):
            _fields_ = [("kind", W.DWORD), ("size", W.DWORD), ("device", W.HANDLE), ("param", W.WPARAM)]
        kernel.GetModuleHandleW.argtypes = [W.LPCWSTR]
        kernel.GetModuleHandleW.restype = W.HMODULE
        user.RegisterClassW.argtypes = [C.POINTER(WC)]
        user.RegisterClassW.restype = W.ATOM
        user.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, C.c_int, C.c_int,
                                        C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p]
        user.CreateWindowExW.restype = W.HWND
        user.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        user.DefWindowProcW.restype = C.c_ssize_t
        user.GetRawInputData.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT), W.UINT]
        user.GetRawInputData.restype = W.UINT
        user.GetRawInputDeviceInfoW.argtypes = [W.HANDLE, W.UINT, C.c_void_p, C.POINTER(W.UINT)]
        user.GetRawInputDeviceInfoW.restype = W.UINT
        user.RegisterRawInputDevices.argtypes = [C.POINTER(RID), W.UINT, W.UINT]
        user.RegisterRawInputDevices.restype = W.BOOL
        user.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
        user.DispatchMessageW.argtypes = [C.POINTER(W.MSG)]
        user.DispatchMessageW.restype = C.c_ssize_t
        user.TranslateMessage.argtypes = [C.POINTER(W.MSG)]
        user.DestroyWindow.argtypes = [W.HWND]
        user.UnregisterClassW.argtypes = [W.LPCWSTR, W.HINSTANCE]
        user.PostQuitMessage.argtypes = [C.c_int]
        def proc(hwnd, message, wp, lp):
            try:
                if message == 0x00FF:  # WM_INPUT: inspect only the header, never key data
                    header = HEADER()
                    size = W.UINT(C.sizeof(header))
                    if user.GetRawInputData(lp, 0x10000005, C.byref(header), C.byref(size), C.sizeof(header)) == C.sizeof(header) and header.kind == 1:
                        length = W.UINT()
                        if user.GetRawInputDeviceInfoW(header.device, 0x20000007, None, C.byref(length)) != 0xFFFFFFFF and length.value:
                            name = C.create_unicode_buffer(length.value + 1)
                            if user.GetRawInputDeviceInfoW(header.device, 0x20000007, name, C.byref(length)) != 0xFFFFFFFF:
                                self.observe(header.kind, name.value)
                elif message == 0x0010:
                    user.DestroyWindow(hwnd)
                    return 0
                elif message == 0x0002:
                    user.PostQuitMessage(0)
                    return 0
            except Exception:
                log.exception("ATK activity callback")
            return user.DefWindowProcW(hwnd, message, wp, lp)
        callback = PROC(proc)
        instance = kernel.GetModuleHandleW(None)
        name = "HaloBatteryAtkActivity_" + str(id(self))
        wc = WC(0, callback, 0, 0, instance, None, None, None, None, name)
        registered = False
        try:
            if not user.RegisterClassW(C.byref(wc)):
                raise C.WinError(C.get_last_error())
            self._hwnd = user.CreateWindowExW(0, name, name, 0, 0, 0, 0, 0, W.HWND(-3), None, instance, None)
            if not self._hwnd:
                raise C.WinError(C.get_last_error())
            rid = RID(1, 6, 0x100, self._hwnd)
            if not user.RegisterRawInputDevices(C.byref(rid), 1, C.sizeof(rid)):
                raise C.WinError(C.get_last_error())
            registered = True
            self._ok = True
            self._ready.set()
            msg = W.MSG()
            while user.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                user.TranslateMessage(C.byref(msg))
                user.DispatchMessageW(C.byref(msg))
        except Exception:
            log.exception("ATK activity listener unavailable; battery queries remain paused")
        finally:
            self._ok = False
            self._ready.set()
            if registered:
                rid = RID(1, 6, 1, None)
                user.RegisterRawInputDevices(C.byref(rid), 1, C.sizeof(rid))
            if self._hwnd:
                user.DestroyWindow(self._hwnd)
                self._hwnd = None
            user.UnregisterClassW(name, instance)
