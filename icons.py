"""Tray icon rendering: a battery ring with a device pictogram in the middle.

The ring fills clockwise from the top; underneath it there is a dim "track".
The centre shows the device silhouette: headset, mouse, gamepad or the Bluetooth rune.
Colours follow the system battery icon: normal charge uses the taskbar colour
(white on a dark taskbar, black on a light one), close to the threshold it is
amber, at or below the threshold it is red, and while charging the arc is
green and slowly "breathes" in brightness.
Asleep / no link: the icon is translucent and has no arc.
"""
from __future__ import annotations

import math
from functools import lru_cache
import sys
from typing import Optional

from PIL import Image, ImageDraw, ImageStat

SIZE = 64
SS = 4                      # supersampling for smooth edges
S = SIZE * SS

RED = (232, 17, 35)
AMBER = (255, 185, 0)
GREEN = (16, 196, 80)
CLEAR = (0, 0, 0, 0)

# device kind aliases (single letters are accepted too)
KINDS = {"H": "headset", "M": "mouse", "B": "bluetooth", "G": "gamepad",
         "headset": "headset", "mouse": "mouse", "bluetooth": "bluetooth", "gamepad": "gamepad"}


def taskbar_is_light() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "SystemUsesLightTheme")[0] == 1
    except OSError:
        return False


def _r(v: float) -> int:
    return int(round(v * SS))


def arc_color(level: Optional[int], charging: bool, low: int, fg: tuple) -> tuple:
    if charging:
        return GREEN
    thr = max(low, 10)
    if level is not None and level <= thr:
        return RED
    if level is not None and level <= thr + 10:
        return AMBER
    return fg


# ---------------------------------------------------------------- pictograms
def _headset(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, col):
    k = 0.8           # horizontal squeeze so the ear cups don't touch the ring
    d.arc((_r(cx - s * k), _r(cy - s), _r(cx + s * k), _r(cy + s)), 200, 340, fill=col, width=_r(s * 0.3))
    d.rounded_rectangle((_r(cx - s * 1.18 * k), _r(cy - s * 0.1), _r(cx - s * 0.58 * k), _r(cy + s * 0.75)),
                        radius=_r(s * 0.2), fill=col)
    d.rounded_rectangle((_r(cx + s * 0.58 * k), _r(cy - s * 0.1), _r(cx + s * 1.18 * k), _r(cy + s * 0.75)),
                        radius=_r(s * 0.2), fill=col)


def _mouse(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, col):
    w = s * 0.62
    d.rounded_rectangle((_r(cx - w), _r(cy - s), _r(cx + w), _r(cy + s)), radius=_r(w), fill=col)
    lw = _r(max(2.5, s * 0.18))
    # the button lines are cut out (transparent), so they show on any theme
    d.line((_r(cx), _r(cy - s), _r(cx), _r(cy - s * 0.2)), fill=CLEAR, width=lw)
    d.line((_r(cx - w), _r(cy - s * 0.2), _r(cx + w), _r(cy - s * 0.2)), fill=CLEAR, width=lw)


def _bluetooth(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, col):
    h, w = s, s * 0.52
    pts = [(cx - w, cy - h * 0.5), (cx + w, cy + h * 0.5), (cx, cy + h),
           (cx, cy - h), (cx + w, cy - h * 0.5), (cx - w, cy + h * 0.5)]
    d.line([(_r(x), _r(y)) for x, y in pts], fill=col, width=_r(s * 0.22), joint="curve")


# Right half of an Xbox controller outline, clockwise from the top centre, in a
# design grid about 40 units wide (x right, y down), traced from the Xbox
# controller glyph: flat top, rounded shoulders, straight sides flaring down to
# the grips, and a wide flat-bottomed notch between them. Mirrored for the left
# half and drawn as one smooth closed curve. Used for every Xbox-compatible
# (XInput / Windows.Gaming.Input) controller.
_PAD_HALF = [(0, -13.9), (5.5, -13.9), (9.3, -12.9), (12.8, -10.3), (15.2, -7.9), (16.3, -5.9),
             (18.1, -0.3), (19.7, 4.8), (20.0, 7.6), (19.5, 10.7), (17.9, 12.8), (15.3, 14.0),
             (14.5, 13.6), (9.0, 8.1), (6.0, 6.8), (0, 6.8)]
# the two sticks where they sit on an Xbox pad: left stick high and to the side,
# right stick lower and closer to the middle; nothing else is cut out
_PAD_STICKS = [(-9.7, -6.1), (5.2, -0.3)]
_PAD_STICK_R = 2.6


def _smooth_closed(pts, steps: int = 12):
    """Catmull-Rom spline through a closed list of points."""
    out, n = [], len(pts)
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        for j in range(steps):
            t = j / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[c] + (-p0[c] + p2[c]) * t
                       + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2
                       + (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3)
                for c in (0, 1)))
    return out


def _gamepad(d: ImageDraw.ImageDraw, cx: float, cy: float, s: float, col):
    """Xbox controller silhouette with the two sticks cut out in the Xbox layout."""
    k = s / 18.0
    loop = _PAD_HALF + [(-x, y) for x, y in reversed(_PAD_HALF[1:-1])]
    d.polygon([(_r(cx + x * k), _r(cy + y * k)) for x, y in _smooth_closed(loop)], fill=col)
    r = _PAD_STICK_R * k
    for sx, sy in _PAD_STICKS:
        x, y = cx + sx * k, cy + sy * k
        d.ellipse((_r(x - r), _r(y - r), _r(x + r), _r(y + r)), fill=CLEAR)


PICTOS = {"headset": (_headset, 0, 2, 18), "mouse": (_mouse, 0, 0, 19.5),
          "bluetooth": (_bluetooth, 0, 0, 18), "gamepad": (_gamepad, 0, 0, 18.4)}


# ---------------------------------------------------------------- icon
def render(level: Optional[int], charging: bool, online: bool, low: int = 20,
           light_taskbar: Optional[bool] = None, badge: str = "",
           pulse: float = 1.0) -> Image.Image:
    """badge - device kind: headset / mouse / bluetooth (or H / M / B).
    pulse - arc brightness 0..1 (a frame of the charging "breathing" animation)."""
    if light_taskbar is None:
        light_taskbar = taskbar_is_light()
    fg = (0, 0, 0) if light_taskbar else (255, 255, 255)
    active = online and level is not None
    alpha = 255 if active else 110

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    R, w = 31.5, 6.5
    box = (_r(32 - R), _r(32 - R), _r(32 + R), _r(32 + R))

    # ring track
    d.ellipse(box, outline=fg + (70 if active else 45,), width=_r(w))

    # charge arc
    if active:
        c = arc_color(level, charging, low, fg) + (int(255 * max(0.0, min(1.0, pulse))),)
        lvl = max(0, min(100, level))
        if lvl >= 100:
            d.ellipse(box, outline=c, width=_r(w))
        elif lvl > 0:
            end = -90 + 360 * max(lvl, 2) / 100
            d.arc(box, -90, end, fill=c, width=_r(w))
            rr = R - w / 2                      # rounded arc ends
            for ang in (-90, end):
                t = math.radians(ang)
                x, y = 32 + rr * math.cos(t), 32 + rr * math.sin(t)
                d.ellipse((_r(x - w / 2), _r(y - w / 2), _r(x + w / 2), _r(y + w / 2)), fill=c)

    # device pictogram
    kind = KINDS.get(badge)
    if kind:
        fn, dx, dy, s = PICTOS[kind]
        fn(d, 32 + dx, 32 + dy, s, fg + (alpha,))

    return img.resize((SIZE, SIZE), Image.LANCZOS)


# ---------------------------------------------------------------- charging animation
BREATH_FRAMES = 30          # frames per cycle
BREATH_PERIOD = 3.0         # seconds per cycle
BREATH_DEPTH = 0.88         # at the bottom of the cycle the arc dims to 12%


def breath_level(phase: float) -> float:
    """Arc brightness 0..1 for phase 0..1: a smooth sine that lingers at the bright end."""
    k = (0.5 + 0.5 * math.cos(2 * math.pi * phase)) ** 0.7
    return (1 - BREATH_DEPTH) + BREATH_DEPTH * k


def charging_frames(level: Optional[int], online: bool, low: int = 20,
                    light_taskbar: Optional[bool] = None, badge: str = ""):
    """All "breathing" frames for the current state (rendered once and cached)."""
    return [render(level, True, online, low, light_taskbar, badge, breath_level(i / BREATH_FRAMES))
            for i in range(BREATH_FRAMES)]


# ---------------------------------------------------------------- top menu bar colour
# Desktop shells such as MyDockFinder draw their own macOS-style menu bar at the
# top of the screen and switch it between light and dark depending on the
# wallpaper or the full-screen app underneath; Windows' own theme setting does
# not change. In "top bar" mode the icon colour mirrors MyDockFinder's own rule:
# with windows right under the bar (maximized, full-screen or snapped side by
# side) it goes by their title bars (a small strip at the top right is
# sampled), otherwise by the wallpaper as a whole (a dark sky over a light
# landscape still counts as light).

BAR_LIGHT_ABOVE = 150      # median luminance (0-255) above which the bar counts as light
BAR_DARK_BELOW = 105       # ... below which it counts as dark; in between keep the last answer


def median_luminance(img: Image.Image) -> int:
    hist = img.convert("L").histogram()
    total, acc = sum(hist), 0
    for value, count in enumerate(hist):
        acc += count
        if acc * 2 >= total:
            return value
    return 0


def classify_bar(img: Image.Image, prev: Optional[bool]) -> Optional[bool]:
    """True = light bar, False = dark bar. The median ignores the text and icons
    drawn on the bar; the gap between the thresholds stops flicker on mid tones."""
    median = median_luminance(img)
    if median >= BAR_LIGHT_ABOVE:
        return True
    if median <= BAR_DARK_BELOW:
        return False
    return prev


@lru_cache(maxsize=1)
def _win32():
    """user32 / gdi32 with argument types set, so 64-bit handles are not truncated."""
    import ctypes
    from ctypes import wintypes
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    for fn, res, args in (
            (user32.GetDC, ctypes.c_void_p, [ctypes.c_void_p]),
            (user32.ReleaseDC, ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p]),
            (user32.WindowFromPoint, ctypes.c_void_p, [wintypes.POINT]),
            (user32.GetAncestor, ctypes.c_void_p, [ctypes.c_void_p, wintypes.UINT]),
            (user32.IsZoomed, wintypes.BOOL, [ctypes.c_void_p]),
            (user32.GetWindowRect, wintypes.BOOL, [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]),
            (user32.GetClassNameW, ctypes.c_int, [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]),
            (user32.SystemParametersInfoW, wintypes.BOOL, [wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                                           wintypes.UINT]),
            (gdi32.CreateCompatibleDC, ctypes.c_void_p, [ctypes.c_void_p]),
            (gdi32.CreateCompatibleBitmap, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]),
            (gdi32.SelectObject, ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p]),
            (gdi32.DeleteObject, wintypes.BOOL, [ctypes.c_void_p]),
            (gdi32.DeleteDC, wintypes.BOOL, [ctypes.c_void_p]),
            (gdi32.SetStretchBltMode, ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
            (gdi32.StretchBlt, wintypes.BOOL, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_int, wintypes.DWORD]),
            (gdi32.GetDIBits, ctypes.c_int, [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                                             ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT])):
        fn.restype, fn.argtypes = res, args
    return ctypes, wintypes, user32, gdi32


def _capture(x: int, y: int, w: int, h: int, out_w: int, out_h: int) -> Optional[Image.Image]:
    """A screen region scaled down to out_w x out_h (StretchBlt, HALFTONE).
    No CAPTUREBLT: the cursor does not flicker, and a translucent (layered) bar
    is skipped, so this returns what lies underneath it."""
    if sys.platform != "win32" or w <= 0 or h <= 0:
        return None
    ctypes, wintypes, user32, gdi32 = _win32()

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    hdc = user32.GetDC(None)
    if not hdc:
        return None
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, out_w, out_h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        gdi32.SetStretchBltMode(mdc, 4)                                        # HALFTONE
        if not gdi32.StretchBlt(mdc, 0, 0, out_w, out_h, hdc, x, y, w, h, 0x00CC0020):   # SRCCOPY
            return None
        bmi = (ctypes.c_byte * (ctypes.sizeof(BITMAPINFOHEADER) + 16))()
        hdr = BITMAPINFOHEADER.from_buffer(bmi)
        hdr.biSize, hdr.biWidth, hdr.biHeight = ctypes.sizeof(BITMAPINFOHEADER), out_w, -out_h
        hdr.biPlanes, hdr.biBitCount, hdr.biCompression = 1, 32, 0
        buf = ctypes.create_string_buffer(out_w * out_h * 4)
        if gdi32.GetDIBits(mdc, bmp, 0, out_h, buf, bmi, 0) != out_h:
            return None
        return Image.frombuffer("RGBA", (out_w, out_h), buf.raw, "raw", "BGRA", 0, 1).convert("RGB")
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(None, hdc)


def _screen_size():
    ctypes, wintypes, user32, gdi32 = _win32()
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)       # SM_CXSCREEN, SM_CYSCREEN


def grab_top_bar(bar_height: int = 24) -> Optional[Image.Image]:
    """The right 40% of a thin strip at the top of the primary screen."""
    if sys.platform != "win32":
        return None
    sw, _sh = _screen_size()
    x0 = int(sw * 0.6)
    h = max(4, bar_height - 8)
    return _capture(x0, 4, sw - x0, h, min(sw - x0, 400), h)


_pid_names: dict = {}


def _process_name(pid: int) -> str:
    """Lower-case executable name of a process, cached per pid."""
    if pid in _pid_names:
        return _pid_names[pid]
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_wchar_p,
                                               ctypes.POINTER(wintypes.DWORD)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    name = ""
    h = k32.OpenProcess(0x1000, False, pid)                   # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        try:
            buf = ctypes.create_unicode_buffer(520)
            size = wintypes.DWORD(520)
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        finally:
            k32.CloseHandle(h)
    if len(_pid_names) > 256:
        _pid_names.clear()
    _pid_names[pid] = name
    return name


UNDER_BAR_Y = 48          # logical px: just below the menu bar, where windows' title bars are
UNDER_BAR_XS = (0.1, 0.3, 0.5, 0.7, 0.9)

# Windows that are not application windows: the desktop and the taskbar, plus
# shell overlays drawn over everything - Task View, Snap Assist (the window
# thumbnails shown next to a snapped window), Alt+Tab, the Start menu, search,
# notifications, menus and tooltips. MyDockFinder ignores them and keeps going
# by what is underneath, so they must not count as windows under the bar.
SHELL_CLASSES = {
    "progman", "workerw", "shell_traywnd", "shell_secondarytraywnd",
    "xamlexplorerhostislandwindow", "multitaskingviewframe", "taskswitcherwnd",
    "foregroundstaging", "windows.ui.core.corewindow", "applicationframeinputsinkwindow",
    "tasklistthumbnailwnd", "notifyiconoverflowwindow", "toplevelwindowforoverflowxamlisland",
    "xaml_windowedpopupclass", "#32768", "tooltips_class32", "syshadow",
}
SHELL_EXES = {
    "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe", "searchapp.exe",
    "searchui.exe", "textinputhost.exe", "lockapp.exe", "shellhost.exe",
}
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080


def _under_bar_points():
    """(x, class, exe, counts_as_window) for each point just under the menu bar."""
    ctypes, wintypes, user32, gdi32 = _win32()
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    get_ex = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
    get_ex.restype = ctypes.c_ssize_t
    get_ex.argtypes = [ctypes.c_void_p, ctypes.c_int]
    sw, _sh = _screen_size()
    out = []
    for fx in UNDER_BAR_XS:
        x = int(sw * fx)
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, UNDER_BAR_Y))
        if not hwnd:
            out.append((x, "", "", False))
            continue
        root = user32.GetAncestor(hwnd, 2) or hwnd                          # GA_ROOT
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(root, cls, 128)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(root, ctypes.byref(pid))
        exe = _process_name(pid.value)
        ex = get_ex(root, -20)                                              # GWL_EXSTYLE
        counts = not (cls.value.lower() in SHELL_CLASSES
                      or exe in SHELL_EXES
                      or is_mydockfinder(exe)                               # its own bar / dock
                      or ex & (_WS_EX_TOOLWINDOW | _WS_EX_TRANSPARENT))     # popups, overlays
        out.append((x, cls.value, exe, counts))
    return out


def windows_under_bar() -> bool:
    """True when application windows (maximized, full-screen or snapped side by
    side) cover the whole area right under the menu bar; then MyDockFinder
    colours the bar by their title bars. False when the desktop shows anywhere
    there; then it goes by the wallpaper. MyDockFinder's own windows, the
    desktop and shell overlays (Task View, Snap Assist, Start...) do not count."""
    if sys.platform != "win32":
        return False
    # MyDockFinder goes by the windows only when they cover the whole top of the
    # screen; as soon as the desktop shows anywhere under the bar (e.g. a single
    # window snapped to one half) it goes by the wallpaper
    return all(p[3] for p in _under_bar_points())


_wall_cache = {"key": None, "mean": None}


def wallpaper_mean() -> Optional[int]:
    """Mean luminance (0-255) of the whole desktop wallpaper image, cached until
    the wallpaper file changes. Falls back to a scaled-down capture of the
    whole screen if the file cannot be read."""
    if sys.platform != "win32":
        return None
    import os
    ctypes, wintypes, user32, gdi32 = _win32()
    buf = ctypes.create_unicode_buffer(1024)
    path = buf.value if user32.SystemParametersInfoW(0x0073, 1024, buf, 0) else ""  # SPI_GETDESKWALLPAPER
    try:
        st = os.stat(path) if path else None
    except OSError:
        st = None
    if st is not None:
        key = (path, st.st_mtime, st.st_size)
        if _wall_cache["key"] != key:
            try:
                with Image.open(path) as im:
                    im.draft("L", (160, 90))
                    small = im.convert("L").resize((64, 36))
                _wall_cache.update(key=key, mean=round(ImageStat.Stat(small).mean[0]))
            except Exception:
                _wall_cache.update(key=key, mean=None)
        if _wall_cache["mean"] is not None:
            return _wall_cache["mean"]
    sw, sh = _screen_size()
    img = _capture(0, 0, sw, sh, 64, 36)
    return None if img is None else round(ImageStat.Stat(img.convert("L")).mean[0])


WALL_LIGHT_ABOVE = 130     # mean wallpaper luminance above which the bar counts as light
WALL_DARK_BELOW = 115      # ... below which dark; in between keep the last answer


def shell_bar_is_light(prev: Optional[bool]) -> Optional[bool]:
    """Mirror of how MyDockFinder picks its bar colour: by the windows right
    under the bar (maximized, full-screen or snapped), otherwise by the
    wallpaper as a whole."""
    try:
        if windows_under_bar():
            img = grab_top_bar()
            return prev if img is None else classify_bar(img, prev)
        mean = wallpaper_mean()
    except Exception:
        return prev
    if mean is None:
        return prev
    if mean >= WALL_LIGHT_ABOVE:
        return True
    if mean <= WALL_DARK_BELOW:
        return False
    return prev


def top_bar_is_light(prev: Optional[bool]) -> Optional[bool]:
    return shell_bar_is_light(prev)


# ---------------------------------------------------------------- MyDockFinder detection
# The top-bar mode is only switched on when MyDockFinder is running; with the
# standard Windows shell the icons keep following the Windows theme.
MYDOCKFINDER_EXES = {"dock_64.exe", "dock_32.exe", "mydockfinder.exe"}
_shell_cache = {"at": 0.0, "running": False}


def is_mydockfinder(name: str) -> bool:
    """Known executable names plus any variant with "mydock" in it."""
    n = name.lower()
    return n in MYDOCKFINDER_EXES or "mydock" in n


def running_process_names() -> set:
    """Lower-case executable names of running processes (Toolhelp snapshot)."""
    if sys.platform != "win32":
        return set()
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)      # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        return set()
    names = set()
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.add(entry.szExeFile.lower())
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return names


def mydockfinder_running(max_age: float = 10.0) -> bool:
    """Cached for a few seconds: listing processes is cheap but not free."""
    import time
    now = time.time()
    if now - _shell_cache["at"] >= max_age:
        try:
            _shell_cache["running"] = any(is_mydockfinder(n) for n in running_process_names())
        except Exception:
            _shell_cache["running"] = False
        _shell_cache["at"] = now
    return _shell_cache["running"]


def theme_report() -> list:
    """Lines for the diagnostics report: what the icon colour is based on."""
    lines = []
    try:
        names = running_process_names()
    except Exception as e:
        names = set()
        lines.append(f"process list failed: {e}")
    shells = sorted(n for n in names if "dock" in n or "finder" in n)
    lines.append(f"MyDockFinder detected: {any(is_mydockfinder(n) for n in names)} "
                 f"(looking for {', '.join(sorted(MYDOCKFINDER_EXES))} or 'mydock' in the name)")
    lines.append(f"processes with 'dock' or 'finder' in the name: {', '.join(shells) or 'none'}")
    lines.append(f"Windows theme: {'light' if taskbar_is_light() else 'dark'}")
    try:
        img = grab_top_bar()
    except Exception as e:
        img = None
        lines.append(f"top bar sample failed: {e}")
    if img is not None:
        lines.append(f"top bar sample: {img.width}x{img.height}, median luminance {median_luminance(img)} "
                     f"(light >= {BAR_LIGHT_ABOVE}, dark <= {BAR_DARK_BELOW})")
    try:
        for x, cls, exe, counts in _under_bar_points():
            lines.append(f"under the bar at x={x}: {cls or '-'} ({exe or '?'}) "
                         f"{'window' if counts else 'ignored'}")
        lines.append(f"windows under the menu bar: {windows_under_bar()}")
        lines.append(f"wallpaper mean luminance: {wallpaper_mean()} "
                     f"(light >= {WALL_LIGHT_ABOVE}, dark <= {WALL_DARK_BELOW})")
    except Exception as e:
        lines.append(f"window / wallpaper check failed: {e}")
    return lines
