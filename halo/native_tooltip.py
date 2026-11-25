"""A native, non-activating tracking tooltip, owned by the flyout's UI thread.

No DLLs are loaded at import time. The tooltip keeps Windows' own tooltip font.
Microsoft API references (Unicode TOOLINFO and tracking/absolute positioning):
https://learn.microsoft.com/en-us/windows/win32/api/commctrl/ns-commctrl-tttoolinfow
https://learn.microsoft.com/en-us/windows/win32/controls/ttm-trackposition
https://learn.microsoft.com/en-us/windows/win32/controls/ttm-getbubblesize
https://learn.microsoft.com/en-us/windows/win32/controls/ttm-addtool
https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-initcommoncontrolsex
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowpos
https://learn.microsoft.com/en-us/windows/win32/api/uxtheme/nf-uxtheme-setwindowtheme
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-systemparametersinfofordpi
https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-nonclientmetricsw
https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-createfontindirectw
https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-deleteobject
https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
"""
from __future__ import annotations

import ctypes
import math
import sys


class _RECT(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class _TOOLINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("uFlags", ctypes.c_uint32),
                ("hwnd", ctypes.c_void_p), ("uId", ctypes.c_size_t), ("rect", _RECT),
                ("hinst", ctypes.c_void_p), ("lpszText", ctypes.c_wchar_p),
                ("lParam", ctypes.c_ssize_t), ("lpReserved", ctypes.c_void_p)]


class _INITCOMMONCONTROLSEX(ctypes.Structure):
    _fields_ = [("dwSize", ctypes.c_uint32), ("dwICC", ctypes.c_uint32)]


class _LOGFONT(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in
                ("lfHeight", "lfWidth", "lfEscapement", "lfOrientation", "lfWeight")] + [
                (name, ctypes.c_ubyte) for name in
                ("lfItalic", "lfUnderline", "lfStrikeOut", "lfCharSet", "lfOutPrecision",
                 "lfClipPrecision", "lfQuality", "lfPitchAndFamily")] + [
                ("lfFaceName", ctypes.c_wchar * 32)]


class _NONCLIENTMETRICS(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32)] + [(name, ctypes.c_int32) for name in
                ("iBorderWidth", "iScrollWidth", "iScrollHeight", "iCaptionWidth", "iCaptionHeight")] + [
                ("lfCaptionFont", _LOGFONT), ("iSmCaptionWidth", ctypes.c_int32),
                ("iSmCaptionHeight", ctypes.c_int32), ("lfSmCaptionFont", _LOGFONT),
                ("iMenuWidth", ctypes.c_int32), ("iMenuHeight", ctypes.c_int32),
                ("lfMenuFont", _LOGFONT), ("lfStatusFont", _LOGFONT),
                ("lfMessageFont", _LOGFONT), ("iPaddedBorderWidth", ctypes.c_int32)]


WM_USER = 0x0400
TTM_ADDTOOLW = WM_USER + 50
TTM_TRACKACTIVATE = WM_USER + 17
TTM_TRACKPOSITION = WM_USER + 18
TTM_SETMAXTIPWIDTH = WM_USER + 24
TTM_GETBUBBLESIZE = WM_USER + 30
TTF_FLAGS = 0x0020 | 0x0080 | 0x0100  # TRACK | ABSOLUTE | TRANSPARENT
EX_STYLE = 0x00000008 | 0x00000080 | 0x08000000  # TOPMOST | TOOLWINDOW | NOACTIVATE
STYLE = 0x80000000 | 0x01 | 0x02  # WS_POPUP | TTS_ALWAYSTIP | TTS_NOPREFIX


def _rect(value):
    if hasattr(value, "left"):
        result = tuple(int(getattr(value, name)) for name in ("left", "top", "right", "bottom"))
    else:
        result = tuple(int(part) for part in value)
    if len(result) != 4 or result[2] <= result[0] or result[3] <= result[1]:
        raise ValueError("tooltip rectangles must have positive dimensions")
    return result


def _placement(anchor, work, size, gap):
    left, top, right, bottom = _rect(work)
    a_left, a_top, a_right, _ = _rect(anchor)
    width, height = size
    if width <= 0 or height <= 0:
        raise OSError("Windows returned an empty tooltip size")
    width, height = min(width, right - left), min(height, bottom - top)
    x = max(left, min((a_left + a_right - width) // 2, right - width))
    y = max(top, min(a_top - max(0, int(gap)) - height, bottom - height))
    return x, y, width, height


class _Backend:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("native tooltips require Windows")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.common = ctypes.WinDLL("comctl32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd, uint, integer = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
        self.user.CreateWindowExW.argtypes = [uint, ctypes.c_wchar_p, ctypes.c_wchar_p, uint,
                                             integer, integer, integer, integer, hwnd, hwnd, hwnd, hwnd]
        self.user.CreateWindowExW.restype = hwnd
        self.user.SendMessageW.argtypes = [hwnd, uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.user.SendMessageW.restype = ctypes.c_ssize_t
        self.user.SetWindowPos.argtypes = [hwnd, hwnd, integer, integer, integer, integer, uint]
        self.user.SetWindowPos.restype = integer
        self.user.DestroyWindow.argtypes = [hwnd]
        self.user.DestroyWindow.restype = integer
        self.user.IsWindow.argtypes = [hwnd]
        self.user.IsWindow.restype = integer
        self.user.GetWindowRect.argtypes = [hwnd, ctypes.POINTER(_RECT)]
        self.user.GetWindowRect.restype = integer
        self.kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.kernel.GetModuleHandleW.restype = hwnd
        self.common.InitCommonControlsEx.argtypes = [ctypes.POINTER(_INITCOMMONCONTROLSEX)]
        self.common.InitCommonControlsEx.restype = integer
        init = _INITCOMMONCONTROLSEX(ctypes.sizeof(_INITCOMMONCONTROLSEX), 0x000000FF)
        if not self.common.InitCommonControlsEx(ctypes.byref(init)):
            raise ctypes.WinError()

    def create(self, owner):
        if not self.user.IsWindow(owner):
            raise OSError("tooltip owner window no longer exists")
        rect = _RECT()
        if not self.user.GetWindowRect(owner, ctypes.byref(rect)):
            raise ctypes.WinError()
        window = self.user.CreateWindowExW(EX_STYLE, "tooltips_class32", None, STYLE,
                                           rect.left, rect.top, 0, 0, owner, None,
                                           self.kernel.GetModuleHandleW(None), None)
        if not window:
            raise ctypes.WinError()
        return window

    def font(self, window, owner):
        """Use the system tooltip/status font at the owner's DPI, if available."""
        try:
            dpi_for_window = self.user.GetDpiForWindow
            metrics_for_dpi = self.user.SystemParametersInfoForDpi
        except AttributeError:  # older Windows keeps the common control's default
            return 0
        dpi_for_window.argtypes, dpi_for_window.restype = [ctypes.c_void_p], ctypes.c_uint32
        metrics_for_dpi.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                                   ctypes.c_uint32, ctypes.c_uint32]
        metrics_for_dpi.restype = ctypes.c_int
        dpi = dpi_for_window(owner)
        metrics = _NONCLIENTMETRICS()
        metrics.cbSize = ctypes.sizeof(metrics)
        if not dpi or not metrics_for_dpi(0x0029, metrics.cbSize, ctypes.byref(metrics), 0, dpi):
            return 0
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi.GetObjectW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        gdi.GetObjectW.restype = ctypes.c_int
        gdi.CreateFontIndirectW.argtypes = [ctypes.POINTER(_LOGFONT)]
        gdi.CreateFontIndirectW.restype = ctypes.c_void_p
        current = self.user.SendMessageW(window, 0x0031, 0, 0)  # WM_GETFONT
        font = _LOGFONT()
        if current and gdi.GetObjectW(current, ctypes.sizeof(font), ctypes.byref(font)):
            if bytes(font) == bytes(metrics.lfStatusFont):
                return 0
        created = gdi.CreateFontIndirectW(ctypes.byref(metrics.lfStatusFont))
        if created:
            try:
                self.user.SendMessageW(window, 0x0030, created, 0)  # WM_SETFONT
            except Exception:
                self.delete_font(created)
                raise
        return created or 0

    def delete_font(self, font):
        gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi.DeleteObject.restype = ctypes.c_int
        gdi.DeleteObject(font)

    def theme(self, window, light):
        # Theme names are best effort; unsupported Windows themes retain the native default.
        theme = ctypes.WinDLL("uxtheme", use_last_error=True)
        theme.SetWindowTheme.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        theme.SetWindowTheme.restype = ctypes.c_int32
        theme.SetWindowTheme(window, "Explorer" if light else "DarkMode_Explorer", None)

    def add(self, window, info):
        if not self.user.SendMessageW(window, TTM_ADDTOOLW, 0, ctypes.addressof(info)):
            raise OSError("Windows could not register the tooltip")

    def max_width(self, window, width):
        self.user.SendMessageW(window, TTM_SETMAXTIPWIDTH, 0, width)

    def size(self, window, info):
        size = self.user.SendMessageW(window, TTM_GETBUBBLESIZE, 0, ctypes.addressof(info))
        return size & 0xFFFF, (size >> 16) & 0xFFFF

    def position(self, window, x, y):
        # TRACKPOSITION packs two signed 16-bit coordinates. SetWindowPos below also
        # sets the full coordinates, including very large virtual desktop offsets.
        packed = (x & 0xFFFF) | ((y & 0xFFFF) << 16)
        self.user.SendMessageW(window, TTM_TRACKPOSITION, 0, packed)

    def activate(self, window, info, enabled):
        self.user.SendMessageW(window, TTM_TRACKACTIVATE, int(enabled), ctypes.addressof(info))

    def cloak(self, window, enabled):
        # DWMWA_CLOAK keeps first activation invisible while comctl initializes
        # the current tool's text and dimensions. Available since Windows 8.
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                             ctypes.c_void_p, ctypes.c_uint32]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_int32
        value = ctypes.c_int(int(enabled))
        result = dwm.DwmSetWindowAttribute(window, 13, ctypes.byref(value), ctypes.sizeof(value))
        if result < 0:
            raise OSError("Windows could not cloak the tooltip for measurement")

    def move(self, window, x, y, width, height):
        if not self.user.SetWindowPos(window, ctypes.c_void_p(-1), x, y, width, height, 0x0010):
            raise ctypes.WinError()  # SWP_NOACTIVATE, HWND_TOPMOST

    def opacity(self, window, alpha):
        # LONG_PTR is pointer-sized even though GWL_EXSTYLE contains style bits.
        get_style = getattr(self.user, "GetWindowLongPtrW", None) or self.user.GetWindowLongW
        set_style = getattr(self.user, "SetWindowLongPtrW", None) or self.user.SetWindowLongW
        get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        ctypes.set_last_error(0)
        style = get_style(window, -20)
        if not style and ctypes.get_last_error():
            raise ctypes.WinError()
        if not style & 0x00080000:
            if alpha == 255:
                return  # preserve the common control's default opaque rendering
            ctypes.set_last_error(0)
            previous = set_style(window, -20, style | 0x00080000)
            if not previous and ctypes.get_last_error():
                raise ctypes.WinError()
        self.user.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                         ctypes.c_ubyte, ctypes.c_uint32]
        self.user.SetLayeredWindowAttributes.restype = ctypes.c_int
        if not self.user.SetLayeredWindowAttributes(window, 0, alpha, 0x02):
            raise ctypes.WinError()

    def destroy(self, window):
        # The owner may already have destroyed this owned popup.
        self.user.DestroyWindow(window)


class NativeTooltip:
    """Create/show/destroy on the owning window's UI thread. Failures raise OSError."""

    def __init__(self, owner_hwnd: int, text: str, light: bool, *, _backend=None):
        if not isinstance(owner_hwnd, int) or isinstance(owner_hwnd, bool) or owner_hwnd <= 0:
            raise ValueError("tooltip needs an owner window")
        if not isinstance(text, str) or not text or "\0" in text:
            raise ValueError("tooltip needs nonempty Unicode text without NUL")
        self._backend = _backend if _backend is not None else _Backend()
        self._window = 0
        self._font = 0
        self._text = ctypes.create_unicode_buffer(text)
        self._tool = _TOOLINFO()
        self._tool.cbSize = ctypes.sizeof(_TOOLINFO)
        self._tool.uFlags, self._tool.hwnd, self._tool.uId = TTF_FLAGS, owner_hwnd, 1
        self._tool.lpszText = ctypes.cast(self._text, ctypes.c_wchar_p)
        try:
            self._window = self._backend.create(owner_hwnd)
            if not self._window:
                raise OSError("Windows could not create the tooltip")
            try:
                self._backend.theme(self._window, light)
            except (OSError, AttributeError):
                pass
            self._font = self._backend.font(self._window, owner_hwnd)
            self._backend.add(self._window, self._tool)
        except Exception:
            self.destroy()
            raise

    @property
    def hwnd(self):
        return self._window

    def set_opacity(self, opacity):
        if type(opacity) not in (int, float) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
            raise ValueError("tooltip opacity must be between zero and one")
        if self._window:
            try:
                self._backend.opacity(self._window, round(opacity * 255))
            except Exception:
                self.destroy()
                raise

    def show_above(self, anchor, work, gap: int = 6, *, opacity=1.0):
        if not self._window:
            return
        try:
            bounds = _rect(work)
            self._backend.max_width(self._window, max(1, bounds[2] - bounds[0] - 16))
            self._backend.cloak(self._window, True)
            initial_x, initial_y, _, _ = _placement(anchor, bounds, (1, 1), gap)
            self._backend.position(self._window, initial_x, initial_y)
            self._backend.activate(self._window, self._tool, True)
            # GETBUBBLESIZE can return a small empty placeholder until activation
            # selects the tool. Measure only after activating it while cloaked.
            size = self._backend.size(self._window, self._tool)
            x, y, width, height = _placement(anchor, bounds, size, gap)
            self._backend.position(self._window, x, y)
            self._backend.move(self._window, x, y, width, height)
            self.set_opacity(opacity)
            self._backend.cloak(self._window, False)
        except Exception:
            self.destroy()
            raise

    def destroy(self):
        window, self._window = self._window, 0
        if not window:
            return
        try:
            self._backend.destroy(window)
        except OSError:
            pass
        finally:
            font, self._font = self._font, 0
            if font:
                try:
                    self._backend.delete_font(font)
                except OSError:
                    pass
