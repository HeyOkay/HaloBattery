"""Physical Windows tray metrics and explicit-size HICON loading.

Keep tray sizing separate from the renderer's default export/notification size.
Like the icon lab, use the taskbar window's DPI rather than the caller's DPI.
"""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def tray_pixels() -> int:
    """Return the taskbar's small icon width in physical pixels (16 elsewhere)."""
    if sys.platform != "win32":
        # HaloBattery targets Windows; this is a safe test/import fallback,
        # not a native menu-bar size recommendation for other platforms.
        return 16
    dpi = 96
    try:
        user32 = _user32()
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.FindWindowW.restype = wintypes.HWND
        hwnd = user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            try:
                user32.GetDpiForWindow.argtypes = [wintypes.HWND]
                user32.GetDpiForWindow.restype = wintypes.UINT
                dpi = user32.GetDpiForWindow(hwnd) or 96
            except (AttributeError, OSError):
                pass
        try:
            user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, wintypes.UINT]
            user32.GetSystemMetricsForDpi.restype = ctypes.c_int
            pixels = user32.GetSystemMetricsForDpi(49, dpi)  # SM_CXSMICON
            if pixels > 0:
                return pixels
        except (AttributeError, OSError):
            pass
    except (AttributeError, OSError):
        pass
    return max(1, round(16 * dpi / 96))


def load_icon_handle(image):
    """Load exactly the physical pixels supplied by the renderer.

    The caller owns the resulting HICON and must eventually DestroyIcon it.
    Never use LR_DEFAULTSIZE: that would select and rescale to a system default
    even when the renderer already supplied the taskbar's physical dimensions.
    """
    width, height = image.size
    if not (1 <= width <= 256 and 1 <= height <= 256):
        raise ValueError("ICO dimensions must be between 1 and 256 pixels")
    user32 = _user32()
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                 wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    descriptor, filename = tempfile.mkstemp(suffix=".ico", prefix="halo-tray-")
    os.close(descriptor)
    try:
        image.save(filename, format="ICO", sizes=[(width, height)])
        handle = user32.LoadImageW(None, filename, 1, width, height, 0x10)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle
    finally:
        Path(filename).unlink(missing_ok=True)


def destroy_icon_handle(handle) -> None:
    """Release an owned HICON without truncating a 64-bit Windows handle."""
    if not handle:
        return
    user32 = _user32()
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.DestroyIcon(handle)
