"""A click-through, per-pixel-alpha header highlight; call on the owner's UI thread.

No DLLs load on import. No colour key or SetLayeredWindowAttributes is used.
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-updatelayeredwindow
https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-createdibsection
https://learn.microsoft.com/en-us/windows/win32/winmsg/window-features#layered-windows
"""
from __future__ import annotations

import ctypes
import math
import sys

from PIL import Image, ImageDraw


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_int32), ("cy", ctypes.c_int32)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 1)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ubyte) for name in
                ("BlendOp", "BlendFlags", "SourceConstantAlpha", "AlphaFormat")]


EX_STYLE = 0x080800A8  # NOACTIVATE | LAYERED | TOOLWINDOW | TRANSPARENT | TOPMOST


def _alpha(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("highlight opacity must be between zero and one")
    return round(value * 255)


def _colour(value):
    if isinstance(value, str) and len(value) == 7 and value.startswith("#"):
        try:
            value = tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    if not isinstance(value, (tuple, list)) or len(value) != 3 or any(
            type(c) is not int or not 0 <= c <= 255 for c in value):
        raise ValueError("highlight colour must contain three RGB bytes")
    return tuple(value)


def _pixels(width, height, radius, colour):
    """4x coverage mask, converted to premultiplied BGRA without matte pixels."""
    mask = Image.new("L", (width * 4, height * 4))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width * 4 - 1, height * 4 - 1),
        radius=min(radius, min(width, height) / 2) * 4, fill=255)
    coverage = mask.resize((width, height), Image.Resampling.LANCZOS).tobytes()
    red, green, blue = colour
    result = bytearray(width * height * 4)
    for index, alpha in enumerate(coverage):
        offset = index * 4
        result[offset:offset + 4] = bytes(((blue * alpha + 127) // 255,
            (green * alpha + 127) // 255, (red * alpha + 127) // 255, alpha))
    return bytes(result)


class _Backend:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("native highlights require Windows")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        hwnd, integer, uint = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32
        self.user.CreateWindowExW.argtypes = [uint, ctypes.c_wchar_p, ctypes.c_wchar_p, uint,
            integer, integer, integer, integer, hwnd, hwnd, hwnd, hwnd]
        self.user.CreateWindowExW.restype = hwnd
        self.user.IsWindow.argtypes, self.user.IsWindow.restype = [hwnd], integer
        self.user.DestroyWindow.argtypes, self.user.DestroyWindow.restype = [hwnd], integer
        self.user.ShowWindow.argtypes, self.user.ShowWindow.restype = [hwnd, integer], integer
        self.user.SetWindowPos.argtypes = [hwnd, hwnd, integer, integer, integer, integer, uint]
        self.user.SetWindowPos.restype = integer
        self.user.UpdateLayeredWindow.argtypes = [hwnd, hwnd, ctypes.POINTER(_POINT),
            ctypes.POINTER(_SIZE), hwnd, ctypes.POINTER(_POINT), uint, ctypes.POINTER(_BLENDFUNCTION), uint]
        self.user.UpdateLayeredWindow.restype = integer
        self.gdi.CreateCompatibleDC.argtypes, self.gdi.CreateCompatibleDC.restype = [hwnd], hwnd
        self.gdi.CreateDIBSection.argtypes = [hwnd, ctypes.POINTER(_BITMAPINFO), uint,
                                             ctypes.POINTER(hwnd), hwnd, uint]
        self.gdi.CreateDIBSection.restype = hwnd
        self.gdi.SelectObject.argtypes, self.gdi.SelectObject.restype = [hwnd, hwnd], hwnd
        self.gdi.DeleteObject.argtypes, self.gdi.DeleteObject.restype = [hwnd], integer
        self.gdi.DeleteDC.argtypes, self.gdi.DeleteDC.restype = [hwnd], integer
        kernel.GetModuleHandleW.argtypes, kernel.GetModuleHandleW.restype = [ctypes.c_wchar_p], hwnd
        self.instance = kernel.GetModuleHandleW(None)

    def create(self, owner):
        if not self.user.IsWindow(owner):
            raise OSError("highlight owner window no longer exists")
        window = self.user.CreateWindowExW(EX_STYLE, "STATIC", None, 0x80000000,
                                           0, 0, 0, 0, owner, None, self.instance, None)
        if not window:
            raise ctypes.WinError()
        return window

    def update(self, window, bounds, pixels, alpha):
        left, top, right, bottom = bounds
        width, height = right - left, bottom - top
        dc = bitmap = old = None
        try:
            dc = self.gdi.CreateCompatibleDC(None)
            if not dc:
                raise ctypes.WinError()
            info = _BITMAPINFO()
            info.bmiHeader = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width,
                -height, 1, 32, 0, len(pixels), 0, 0, 0, 0)  # top-down BI_RGB
            bits = ctypes.c_void_p()
            bitmap = self.gdi.CreateDIBSection(dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
            if not bitmap or not bits.value:
                raise ctypes.WinError()
            old = self.gdi.SelectObject(dc, bitmap)
            if not old or old == ctypes.c_void_p(-1).value:
                old = None
                raise ctypes.WinError()
            ctypes.memmove(bits, pixels, len(pixels))
            position, size, source = _POINT(left, top), _SIZE(width, height), _POINT(0, 0)
            blend = _BLENDFUNCTION(0, 0, alpha, 1)  # AC_SRC_OVER / AC_SRC_ALPHA
            if not self.user.UpdateLayeredWindow(window, None, ctypes.byref(position),
                    ctypes.byref(size), dc, ctypes.byref(source), 0, ctypes.byref(blend), 2):
                raise ctypes.WinError()
        finally:
            try:
                if dc:
                    try:
                        if old:
                            self.gdi.SelectObject(dc, old)
                    finally:
                        self.gdi.DeleteDC(dc)
            finally:
                if bitmap:
                    self.gdi.DeleteObject(bitmap)

    def reveal(self, window, bounds):
        left, top, right, bottom = bounds
        if not self.user.SetWindowPos(window, ctypes.c_void_p(-1), left, top,
                                      right - left, bottom - top, 0x0050):  # SHOWWINDOW | NOACTIVATE
            raise ctypes.WinError()

    def hide(self, window):
        self.user.ShowWindow(window, 0)

    def destroy(self, window):
        self.user.DestroyWindow(window)


class NativeHighlight:
    def __init__(self, owner_hwnd: int, *, _backend=None):
        if type(owner_hwnd) is not int or owner_hwnd <= 0:
            raise ValueError("highlight needs an owner window")
        self._backend = _backend if _backend is not None else _Backend()
        self._window = self._backend.create(owner_hwnd)
        if not self._window:
            raise OSError("Windows could not create the highlight")
        self._cache = None

    @property
    def hwnd(self):
        return self._window

    def show(self, bounds_screen, radius, colourRGB, alpha_float):
        if not self._window:
            return
        bounds = tuple(int(v) for v in bounds_screen)
        if len(bounds) != 4:
            raise ValueError("highlight needs a screen rectangle")
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        if width <= 0 or height <= 0 or width * height > 2_000_000:
            raise ValueError("highlight dimensions are invalid")
        if type(radius) not in (int, float) or not math.isfinite(radius) or radius < 0:
            raise ValueError("highlight radius must be nonnegative")
        colour, alpha = _colour(colourRGB), _alpha(alpha_float)
        key = (width, height, radius, colour)
        try:
            if self._cache is None or self._cache[0] != key:
                self._cache = (key, _pixels(width, height, radius, colour))
            self._backend.update(self._window, bounds, self._cache[1], alpha)
            self._backend.reveal(self._window, bounds)
        except Exception:
            self.destroy()
            raise

    def hide(self):
        if self._window:
            try:
                self._backend.hide(self._window)
            except Exception:
                self.destroy()
                raise

    def destroy(self):
        window, self._window = self._window, 0
        self._cache = None
        if window:
            try:
                self._backend.destroy(window)
            except OSError:
                pass
