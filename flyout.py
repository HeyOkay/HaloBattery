"""The tray menu: a Windows 11 style flyout drawn with tkinter.

pystray shows the classic Win32 popup menu. That menu uses the old small font, and
because the app was not DPI aware Windows drew it at 100 % and stretched the picture
on a scaled screen, so the text came out small and blurry. This module draws the
same pystray Menu (texts, checks, submenus) in its own borderless window:

* the process is made DPI aware (enable_dpi_awareness(), called before any window
  exists) and every size is multiplied by the monitor's scale, so text is sharp;
* Segoe UI Variable Text 13 px, Segoe Fluent Icons for the check mark, the submenu
  chevron and the - / + buttons;
* an acrylic (blurred, translucent) background from SetWindowCompositionAttribute,
  rounded corners from DWM on Windows 11, colours from the Windows app theme.

How the window is put together (all three are borderless, topmost tool windows):

* the panel: the menu itself. Its background is a colour key, so those pixels are
  transparent and the acrylic shows through; texts and the separators are drawn
  on it. Windows draws text in a translucent window without ClearType (grey
  scale), so it is a little softer than in a normal window - that is expected;
* the highlight: a small window over the hovered item with a white (dark theme)
  or black (light theme) rounded rectangle at 10 % / 7 % opacity, which is how
  the translucent #1AFFFFFF / #12000000 of the design is laid over the acrylic;
* the catcher: an invisible (1/255 opaque) window under the panel. The colour-key
  pixels of the panel let clicks and the mouse wheel through to whatever is
  underneath; the catcher takes them instead, so they stay in the menu.

Everything runs in one thread that owns tkinter (FlyoutHost). The tray icons live
in their own threads and hand a right-click over with FlyoutHost.show(); when the
flyout cannot be shown the icon falls back to pystray's classic menu.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Callable, List, Optional, Sequence, Tuple

from pystray import Menu, MenuItem as Item

log = logging.getLogger("halo_battery")

# ------------------------------------------------------------------ design
# sizes in pixels at 100 % (96 DPI); multiplied by the monitor's scale
MIN_WIDTH = 230
PAD_Y = 4                            # above the first and below the last item
ITEM_MARGIN_X, ITEM_MARGIN_Y = 4, 1  # around an item (its highlight)
ITEM_PAD_L, ITEM_PAD_T, ITEM_PAD_R, ITEM_PAD_B = 8, 6, 10, 6   # inside an item
HOVER_RADIUS = 4
CHECK_COLUMN = 24
CHEVRON_GAP = 16                     # between the text and the submenu chevron
SEP_MARGIN_X, SEP_MARGIN_Y = 12, 4
HEADER_PAD_T, HEADER_PAD_B = 8, 8     # inside the header (above its title, below its detail)
HEADER_LINE_GAP = 2                  # between the title and the detail
EDIT_GAP = 8                         # between the header's title and its pencil button
BUTTON_W, BUTTON_H, BUTTON_RADIUS = 28, 26, 4
VALUE_MIN_W = 34
COUNTER_GAP = 16                     # between the text and the - button
MENU_GAP = 8                         # between the taskbar and the menu
SUB_OVERLAP = 2                      # a submenu overlaps its parent by this much
SUB_RAISE = 5                        # a submenu's top is this much above its item
FADE_MS = 120
SUBMENU_DELAY_MS = 200               # hover this long to open / switch a submenu
POLL_MS = 30                         # outside clicks and focus, while the menu is open

TEXT_FONTS = ("Segoe UI Variable Text", "Segoe UI")
TEXT_PX = 13
ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
CHECK, CHEVRON, MINUS, PLUS, EDIT = "\uE73E", "\uE76C", "\uE738", "\uE710", "\uE70F"
CHECK_PX, CHEVRON_PX, BUTTON_PX = 12, 10, 11
# when neither icon font is there (not Windows 10 / 11)
FALLBACK_GLYPHS = {CHECK: "\u2713", CHEVRON: "\u203A", MINUS: "\u2212", PLUS: "+", EDIT: "\u270E"}

# ARGB, as in the design: the first two digits are the opacity
PALETTE = {
    False: {"tint": "#99202020", "text": "#F2F2F2", "muted": "#99F2F2F2",     # dark theme
            "faint": "#4DF2F2F2",
            "hover": "#1AFFFFFF", "separator": "#26FFFFFF", "border": "#33FFFFFF"},
    True: {"tint": "#99F3F3F3", "text": "#1A1A1A", "muted": "#991A1A1A",      # light theme
           "faint": "#4D1A1A1A",
           "hover": "#12000000", "separator": "#1A000000", "border": "#26000000"},
}
OVERLAY_KEY = "#FF00FE"      # transparent colour of the highlight window (its corners)


# ------------------------------------------------------------------ colours
def parse_argb(value: str) -> Tuple[int, int, int, int]:
    """'#AARRGGBB' or '#RRGGBB' -> (a, r, g, b)."""
    s = value.lstrip("#")
    if len(s) == 6:
        s = "FF" + s
    if len(s) != 8:
        raise ValueError(value)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4, 6))  # type: ignore[return-value]


def over(value: str, base: Tuple[int, int, int]) -> str:
    """An ARGB colour laid over an opaque one -> '#rrggbb'."""
    a, r, g, b = parse_argb(value)
    k = a / 255
    mix = [round(c * k + d * (1 - k)) for c, d in zip((r, g, b), base)]
    return "#%02x%02x%02x" % tuple(mix)


def abgr(value: str) -> int:
    """'#AARRGGBB' -> 0xAABBGGRR, the GradientColor of ACCENT_POLICY."""
    a, r, g, b = parse_argb(value)
    return (a << 24) | (b << 16) | (g << 8) | r


def colours(light: bool) -> dict:
    """The colours of one theme, ready for tkinter.

    tkinter has no partial transparency, so what is drawn on the panel is opaque:
    the text colours and the separator are the design's colours laid over the tint,
    which is what the acrylic looks like on average. The panel's background is a
    colour key one step away from the tint: the anti-aliased edges of the letters
    blend into it, and a colour close to the acrylic keeps them from showing a halo.
    The hover highlight keeps its real opacity (it is a window of its own)."""
    p = PALETTE[light]
    _a, r, g, b = parse_argb(p["tint"])
    base = (r, g, b)
    key = "#%02x%02x%02x" % (r, g, b + 1 if b < 255 else b - 1)
    ha, hr, hg, hb = parse_argb(p["hover"])
    return {
        "key": key,
        "text": over(p["text"], base),
        "muted": over(p["muted"], base),
        "faint": over(p["faint"], base),          # the header's pencil, when not hovered
        "separator": over(p["separator"], base),
        "border": over(p["border"], base),
        "hover": "#%02x%02x%02x" % (hr, hg, hb),
        "hover_alpha": ha / 255,
        "tint_abgr": abgr(p["tint"]),
    }


# ------------------------------------------------------------------ placement
Rect = Tuple[int, int, int, int]      # left, top, right, bottom


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(v, hi)) if hi >= lo else lo


def usable_area(work: Rect, taskbar: Optional[Rect]) -> Rect:
    """The work area without the taskbar.

    The monitor's work area leaves the taskbar out only while the taskbar reserves its
    space. An auto-hide taskbar does not, and neither does the taskbar over a full
    screen game after the Windows key brings it up: the work area is then the whole
    screen, and a menu placed in it can open behind the taskbar. The taskbar's own
    rectangle is cut off the side it sits on."""
    left, top, right, bottom = work
    if taskbar is None:
        return work
    tl, tt, tr, tb = taskbar
    if tr <= left or tl >= right or tb <= top or tt >= bottom:
        return work                                    # not on this area
    if tr - tl > tb - tt:                              # horizontal taskbar
        if tt > top:
            bottom = min(bottom, tt)                   # at the bottom
        else:
            top = max(top, tb)                         # at the top
    else:                                              # vertical taskbar
        if tl > left:
            right = min(right, tl)                     # on the right
        else:
            left = max(left, tr)                       # on the left
    if right <= left or bottom <= top:
        return work                                    # a taskbar that fills the area
    return left, top, right, bottom


def place_menu(cx: int, cy: int, w: int, h: int, work: Rect,
               taskbar: Optional[Rect] = None, scale: float = 1.0) -> Tuple[int, int]:
    """The menu's top-left corner for a click at (cx, cy), the way Windows 11 opens
    the menus of the taskbar: next to the taskbar with a gap, centred on the click
    (horizontally for a taskbar at the bottom or top, vertically for one at a side).
    Without a taskbar: centred above the cursor. The menu stays on the screen."""
    left, top, right, bottom = work
    gap = round(MENU_GAP * scale)
    if taskbar is None:
        x, y = cx - w // 2, cy - h - gap
    else:
        tl, tt, tr, tb = taskbar
        if tr - tl > tb - tt:                          # horizontal taskbar
            x = cx - w // 2
            y = tt - h - gap if tt > top else tb + gap  # at the bottom / at the top
        else:                                          # vertical taskbar
            y = cy - h // 2
            x = tl - w - gap if tl > left else tr + gap  # on the right / on the left
    x = max(left + gap, min(x, right - w - gap))
    y = max(top, min(y, bottom - h))
    return x, y


def place_submenu(parent_left: int, parent_right: int, item_top: int, w: int, h: int,
                  work: Rect, scale: float = 1.0) -> Tuple[int, int]:
    """A submenu to the right of its parent (to the left if there is no room),
    overlapping it a little, with its top a little above the item's top."""
    left, top, right, bottom = work
    overlap, rise = round(SUB_OVERLAP * scale), round(SUB_RAISE * scale)
    x = parent_right - overlap
    if x + w > right:
        x = parent_left - w + overlap
    y = item_top - rise
    return _clamp(x, left, right - w), _clamp(y, top, bottom - h)


# ------------------------------------------------------------------ counter item
class CounterItem(Item):
    """A menu item with - and + buttons that step through `choices`
    ((value, label), ...) without closing the menu; the mouse wheel steps too.

    The classic menu (and anything else that does not know this class) sees an
    ordinary submenu with one radio item per choice."""

    def __init__(self, text, choices: Sequence[Tuple[object, str]],
                 get: Callable[[], object], set_: Callable[[object], None], **kwargs):
        self.choices = tuple(choices)
        self._get, self._set = get, set_

        def pick(value):
            # pystray accepts only actions with 0-2 parameters
            return lambda icon, item: set_(value)

        def is_current(value):
            return lambda item: get() == value

        super().__init__(text, Menu(*[Item(label, pick(value), checked=is_current(value), radio=True)
                                      for value, label in self.choices]), **kwargs)

    def index(self) -> int:
        """The current value's place in the choices (the nearest one for a value
        that is not among them, e.g. from a hand-edited settings file)."""
        cur = self._get()
        values = [v for v, _l in self.choices]
        if cur in values:
            return values.index(cur)
        try:
            return min(range(len(values)), key=lambda i: abs(values[i] - cur))  # type: ignore[operator]
        except TypeError:
            return 0

    def label(self) -> str:
        return self.choices[self.index()][1] if self.choices else ""

    def can_step(self, delta: int) -> bool:
        return 0 <= self.index() + delta < len(self.choices)

    def step(self, delta: int) -> bool:
        """Go one choice down (-1) or up (+1). -> False at either end."""
        if not self.can_step(delta):
            return False
        self._set(self.choices[self.index() + delta][0])
        return True


# ------------------------------------------------------------------ header item
class HeaderItem(Item):
    """The heading at the top of a menu: a title (the device's name) and a detail
    (its level, charging, time left). The flyout shows the detail on the line below
    the title and wraps both to the width the other items need, so a long heading
    never makes the menu wider. It cannot be chosen.

    `edit` (called with the tray icon) adds a pencil button at the right of the title,
    dim until the mouse is over it; the classic menu cannot show it, so keep an
    ordinary item for it there (classic_only()).

    The classic menu (and anything else that does not know this class) sees one
    disabled item "title: detail"."""

    def __init__(self, title: Callable[[], str], detail: Callable[[], str] = lambda: "",
                 edit: Optional[Callable[[object], None]] = None, **kwargs):
        self._title, self._detail = title, detail
        self.edit = edit

        def text(_item):
            t, d = self.title(), self.detail()
            return f"{t}: {d}" if d else t

        kwargs.setdefault("enabled", False)
        super().__init__(text, None, **kwargs)

    def title(self) -> str:
        return str(self._title() or "")

    def detail(self) -> str:
        return str(self._detail() or "")


def classic_only(item: Item) -> Item:
    """Mark an item that only the classic menu shows (the flyout has another way to
    do the same, e.g. the pencil of a HeaderItem)."""
    item.flyout_hidden = True
    return item


def wrap(text: str, width: int, measure) -> List[str]:
    """Split `text` into lines no wider than `width` (as `measure` counts it): at
    spaces, and inside a word only when the word alone does not fit."""
    lines: List[str] = []
    cur = ""
    for word in text.split():
        cand = f"{cur} {word}" if cur else word
        if measure(cand) <= width:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        while len(word) > 1 and measure(word) > width:
            n = 1
            while n < len(word) and measure(word[:n + 1]) <= width:
                n += 1
            lines.append(word[:n])
            word = word[n:]
        cur = word
    if cur or not lines:
        lines.append(cur)
    return lines


# ------------------------------------------------------------------ Windows
def enable_dpi_awareness() -> None:
    """Tell Windows the app draws for the monitor's real DPI. Without this Windows
    draws every window of the app at 100 % and stretches the picture on a scaled
    screen, which makes text blurry. Must run before the first window exists."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)     # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def apps_use_light_theme() -> bool:
    """The Windows app theme (Settings > Personalization > Colors)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1
    except OSError:
        return False


def is_windows_11() -> bool:
    try:
        return sys.platform == "win32" and sys.getwindowsversion().build >= 22000
    except Exception:
        return False


class _Win32:
    """The few user32 / dwmapi / shcore calls the flyout needs, with argument types
    set so 64-bit handles are not truncated."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        u = self.user32 = ctypes.windll.user32
        u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        u.MonitorFromPoint.restype = ctypes.c_void_p
        u.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetAsyncKeyState.restype = ctypes.c_short
        u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.swca = getattr(u, "SetWindowCompositionAttribute", None)
        if self.swca is not None:
            self.swca.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        try:
            self.dwm = ctypes.windll.dwmapi
            self.dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                      ctypes.c_void_p, wintypes.DWORD]
        except (OSError, AttributeError):
            self.dwm = None
        try:
            self.shcore = ctypes.windll.shcore
            self.shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                    ctypes.POINTER(wintypes.UINT),
                                                    ctypes.POINTER(wintypes.UINT)]
        except (OSError, AttributeError):
            self.shcore = None

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                        ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]

        class WINCOMPATTRDATA(ctypes.Structure):
            _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                        ("SizeOfData", ctypes.c_size_t)]

        self.MONITORINFO, self.ACCENT_POLICY, self.WINCOMPATTRDATA = (
            MONITORINFO, ACCENT_POLICY, WINCOMPATTRDATA)

    def cursor(self) -> Tuple[int, int]:
        pt = self.wintypes.POINT()
        self.user32.GetCursorPos(self.ctypes.byref(pt))
        return pt.x, pt.y

    def monitor(self, x: int, y: int) -> Tuple[Rect, float]:
        """(work area, scale) of the monitor at a point."""
        ctypes = self.ctypes
        hmon = self.user32.MonitorFromPoint(self.wintypes.POINT(x, y), 2)   # DEFAULTTONEAREST
        mi = self.MONITORINFO()
        mi.cbSize = ctypes.sizeof(mi)
        if hmon and self.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcWork
            work = (r.left, r.top, r.right, r.bottom)
        else:
            work = (0, 0, self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1))
        dpi = 96
        if hmon and self.shcore is not None:
            dx, dy = self.wintypes.UINT(0), self.wintypes.UINT(0)
            try:
                if self.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                    dpi = dx.value or 96                                      # MDT_EFFECTIVE_DPI
            except OSError:
                pass
        return work, dpi / 96

    def taskbar(self, x: int, y: int) -> Optional[Rect]:
        """The rectangle of the taskbar on the monitor of a point: the main one
        (Shell_TrayWnd) or, with several monitors, one of the others
        (Shell_SecondaryTrayWnd). None when there is none on that monitor."""
        ctypes, wintypes, u = self.ctypes, self.wintypes, self.user32
        u.FindWindowExW.restype = ctypes.c_void_p
        u.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        here = u.MonitorFromPoint(wintypes.POINT(x, y), 2)
        found = []
        for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            hwnd = None
            while True:
                hwnd = u.FindWindowExW(None, hwnd, cls, None)
                if not hwnd:
                    break
                r = wintypes.RECT()
                if u.GetWindowRect(hwnd, ctypes.byref(r)) and r.right > r.left and r.bottom > r.top:
                    found.append((u.MonitorFromWindow(hwnd, 2), (r.left, r.top, r.right, r.bottom)))
        for mon, rect in found:
            if mon == here:
                return rect
        return None

    def buttons_down(self) -> bool:
        return any(self.user32.GetAsyncKeyState(vk) & 0x8000 for vk in (1, 2, 4))

    def foreground(self) -> int:
        return self.user32.GetForegroundWindow() or 0

    def activate(self, hwnd: int) -> None:
        try:
            self.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def to_top(self, hwnd: int) -> None:
        """Put a topmost window in front of the other topmost windows, without moving,
        resizing or activating it."""
        try:
            # HWND_TOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            self.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass

    def dwm_int(self, hwnd: int, attr: int, value: int) -> None:
        if self.dwm is None:
            return
        v = self.ctypes.c_int(value)
        try:
            self.dwm.DwmSetWindowAttribute(hwnd, attr, self.ctypes.byref(v), 4)
        except OSError:
            pass

    def acrylic(self, hwnd: int, tint_abgr: int) -> bool:
        """Blurred translucent background (ACCENT_ENABLE_ACRYLICBLURBEHIND)."""
        if self.swca is None:
            return False
        ctypes = self.ctypes
        accent = self.ACCENT_POLICY(4, 2, tint_abgr, 0)
        data = self.WINCOMPATTRDATA(19, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                                    ctypes.sizeof(accent))                    # WCA_ACCENT_POLICY
        try:
            return bool(self.swca(hwnd, ctypes.byref(data)))
        except OSError:
            return False


# ------------------------------------------------------------------ layout
class _Style:
    """Fonts and pixel sizes for one monitor scale and theme."""

    def __init__(self, tk_font, root, families: set, scale: float, light: bool):
        self.scale = scale
        self.px = lambda v: max(1, round(v * scale))
        text = next((f for f in TEXT_FONTS if f in families), "TkDefaultFont")
        self.icon_family = next((f for f in ICON_FONTS if f in families), None)
        if text == "TkDefaultFont":
            self.font = tk_font.nametofont("TkDefaultFont").copy()
            self.font.configure(size=-self.px(TEXT_PX))
        else:
            self.font = tk_font.Font(root, family=text, size=-self.px(TEXT_PX))
        glyph_family = self.icon_family or self.font.actual("family")
        self.check_font = tk_font.Font(root, family=glyph_family, size=-self.px(CHECK_PX))
        self.chevron_font = tk_font.Font(root, family=glyph_family, size=-self.px(CHEVRON_PX))
        self.button_font = tk_font.Font(root, family=glyph_family, size=-self.px(BUTTON_PX))
        self.line = self.font.metrics("linespace")
        self.colours = colours(light)
        self.light = light

    def glyph(self, g: str) -> str:
        return g if self.icon_family else FALLBACK_GLYPHS[g]


class _Row:
    def __init__(self, kind: str, item=None):
        self.kind = kind            # "item", "sep", "counter" or "header"
        self.item = item
        self.text = ""
        self.title = ""             # a header: its title and detail ...
        self.detail = ""
        self.lines: List[Tuple[str, bool]] = []   # ... wrapped: (text, is the detail)
        self.edit = None            # a header's pencil button: its action
        self.checked: Optional[bool] = None
        self.enabled = True
        self.submenu = None
        self.y = 0
        self.h = 0

    @property
    def selectable(self) -> bool:
        if self.kind == "header":
            return self.edit is not None          # only its pencil button
        return self.kind != "sep" and self.enabled


def build_rows(menu) -> List[_Row]:
    """The visible items of a pystray Menu, with their texts and states read once."""
    rows: List[_Row] = []
    for it in menu:
        if getattr(it, "flyout_hidden", False):
            continue
        if it is Menu.SEPARATOR:
            if not (rows and rows[-1].kind == "header"):     # the header has its own line
                rows.append(_Row("sep"))
            continue
        if isinstance(it, HeaderItem):
            row = _Row("header", it)
            row.enabled = False
            row.edit = it.edit
            try:
                row.title, row.detail = it.title(), it.detail()
            except Exception as e:
                log.warning("menu header: %s", e)
            row.text = f"{row.title}: {row.detail}" if row.detail else row.title
            rows.append(row)
            continue
        row = _Row("counter" if isinstance(it, CounterItem) else "item", it)
        try:
            row.text = str(it.text)
            row.enabled = bool(it.enabled)
            row.checked = None if row.kind == "counter" else it.checked
            row.submenu = None if row.kind == "counter" else it.submenu
        except Exception as e:                     # a broken item must not break the menu
            log.warning("menu item: %s", e)
            row.enabled = False
        rows.append(row)
    return rows


def layout(rows: List[_Row], style: _Style, measure_text, measure_glyph) -> Tuple[int, int]:
    """Set each row's y and height; -> (width, height) of the panel. A header does not
    count for the width: its lines are wrapped to the width the other rows need."""
    px = style.px
    item_h = px(ITEM_MARGIN_Y) * 2 + px(ITEM_PAD_T) + style.line + px(ITEM_PAD_B)
    counter_h = max(item_h, px(ITEM_MARGIN_Y) * 2 + px(BUTTON_H))
    sep_h = px(SEP_MARGIN_Y) * 2 + px(1)
    content = 0
    for row in rows:
        if row.kind in ("sep", "header"):
            continue
        w = measure_text(row.text)
        if row.kind == "counter":
            w += px(COUNTER_GAP) + counter_width(row.item, style, measure_text)
        elif row.submenu is not None:
            w += px(CHEVRON_GAP) + measure_glyph(style.glyph(CHEVRON))
        content = max(content, w)
    width = max(px(MIN_WIDTH), px(ITEM_MARGIN_X) + px(ITEM_PAD_L) + px(CHECK_COLUMN) + content
                + px(ITEM_PAD_R) + px(ITEM_MARGIN_X))
    header_w = header_text_width(width, style)
    y = px(PAD_Y)
    for row in rows:
        row.y = y
        if row.kind == "sep":
            row.h = sep_h
        elif row.kind == "header":
            title_w = header_w - (px(EDIT_GAP) + px(BUTTON_W) if row.edit is not None else 0)
            row.lines = [(t, False) for t in wrap(row.title, title_w, measure_text)]
            if row.detail:
                row.lines += [(t, True) for t in wrap(row.detail, header_w, measure_text)]
            row.h = header_height(row, style)
        else:
            row.h = counter_h if row.kind == "counter" else item_h
        y += row.h
    return width, y + px(PAD_Y)


def header_text_width(width: int, style: _Style) -> int:
    """Room for a header's text in a panel `width` wide: from the items' text column
    to the right padding."""
    px = style.px
    return width - px(ITEM_MARGIN_X) * 2 - px(ITEM_PAD_L) - px(CHECK_COLUMN) - px(ITEM_PAD_R)


def header_height(row: _Row, style: _Style) -> int:
    """Padding, the lines (a small gap before the detail) and a separator below."""
    px = style.px
    gap = px(HEADER_LINE_GAP) if any(d for _t, d in row.lines) and len(row.lines) > 1 else 0
    return (px(HEADER_PAD_T) + style.line * len(row.lines) + gap + px(HEADER_PAD_B)
            + px(SEP_MARGIN_Y) * 2 + px(1))


def header_line_tops(row: _Row, style: _Style) -> List[int]:
    """The top of each of a header's lines, in panel coordinates."""
    px = style.px
    y, tops, seen_detail = row.y + px(HEADER_PAD_T), [], False
    for _text, is_detail in row.lines:
        if is_detail and not seen_detail:
            seen_detail = True
            if tops:
                y += px(HEADER_LINE_GAP)
        tops.append(y)
        y += style.line
    return tops


def edit_box(row: _Row, width: int, style: _Style) -> Tuple[int, int, int, int]:
    """A header's pencil button, in panel coordinates: at the right, level with the
    first line of the title."""
    px = style.px
    right = width - px(ITEM_MARGIN_X) - px(ITEM_PAD_R) + px(BUTTON_W) // 4
    bw, bh = px(BUTTON_W), px(BUTTON_H)
    mid = header_line_tops(row, style)[0] + style.line // 2
    top = mid - bh // 2
    return right - bw, top, right, top + bh


def counter_width(item: CounterItem, style: _Style, measure_text) -> int:
    px = style.px
    return px(BUTTON_W) * 2 + value_width(item, style, measure_text)


def value_width(item: CounterItem, style: _Style, measure_text) -> int:
    widest = max((measure_text(label) for _v, label in item.choices), default=0)
    return max(style.px(VALUE_MIN_W), widest + style.px(8))


# ------------------------------------------------------------------ the host
class FlyoutHost:
    """Owns the tkinter thread and shows the flyout for a tray icon."""

    def __init__(self, win32: Optional[_Win32] = None):
        self._w = win32
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._failed = False
        self._root = None
        self._tk = None
        self._font = None
        self._families: set = set()
        self._styles: dict = {}
        self.panels: List["_Panel"] = []
        self._icon = None
        self._timer = None
        self._timer_panel: Optional["_Panel"] = None
        self._last_pt: Optional[Tuple[int, int]] = None
        self._poll_id = None
        self._was_down = False
        self._had_focus = False

    # ---------------- thread
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._main, name="flyout", daemon=True)
            self._thread.start()

    def _main(self) -> None:
        try:
            import tkinter as tk
            from tkinter import font as tk_font
            if self._w is None and sys.platform == "win32":
                self._w = _Win32()
            root = tk.Tk()
            root.withdraw()
            self._tk, self._font, self._root = tk, tk_font, root
            self._families = set(tk_font.families(root))
            root.bind_all("<Motion>", lambda e: self._track(e.x_root, e.y_root), add="+")
            for b in ("<ButtonRelease-1>", "<ButtonRelease-3>"):
                root.bind_all(b, lambda e: self._click(e.x_root, e.y_root), add="+")
            root.bind_all("<MouseWheel>", self._wheel, add="+")
            root.bind_all("<Key>", self._key, add="+")
        except Exception as e:
            log.warning("menu: tkinter is not available (%s), using the classic menu", e)
            self._failed = True
            self._ready.set()
            return
        self._ready.set()
        try:
            root.mainloop()
        except Exception as e:
            log.warning("menu: %s", e)
        finally:
            self._failed = True

    def stop(self) -> None:
        root = self._root
        if root is not None and not self._failed:
            try:
                root.after(0, root.quit)
            except Exception:
                pass

    def call(self, fn: Callable[[], None]) -> bool:
        """Run fn in the tkinter thread (another window of the app, e.g. "What's new").
        -> False when tkinter is not available."""
        if self._failed:
            return False
        self.start()
        if not self._ready.wait(5) or self._failed:
            return False
        try:
            self._root.after(0, fn)
        except Exception as e:
            log.warning("menu: %s", e)
            return False
        return True

    def show(self, menu, icon, point: Optional[Tuple[int, int]] = None) -> bool:
        """Open the flyout for a tray icon (called from the icon's thread).
        -> False when it cannot be shown; the caller then shows the classic menu."""
        if self._failed:
            return False
        self.start()
        if not self._ready.wait(5) or self._failed:
            return False
        if point is None and self._w is not None:
            point = self._w.cursor()
        done = threading.Event()
        result = [True]

        def run():
            try:
                self._show(menu, icon, point)
            except Exception as e:
                log.exception("menu: %s", e)
                result[0] = False
                self._failed = True       # from now on the classic menu
                try:
                    self.close()
                except Exception:
                    pass
            finally:
                done.set()

        try:
            self._root.after(0, run)
        except Exception as e:           # e.g. a Tcl built without threads
            log.warning("menu: %s", e)
            self._failed = True
            return False
        # a slow first open (fonts) is still an open menu; only a failure says no
        done.wait(2)
        return result[0]

    # ---------------- opening and closing (tkinter thread from here on)
    def style(self, scale: float, light: bool) -> _Style:
        key = (round(scale, 3), light)
        if key not in self._styles:
            self._styles[key] = _Style(self._font, self._root, self._families, scale, light)
        return self._styles[key]

    def _show(self, menu, icon, point) -> None:
        self.close()
        self._icon = icon
        cx, cy = point if point is not None else (self._root.winfo_pointerx(), self._root.winfo_pointery())
        taskbar = None
        if self._w is not None:
            work, scale = self._w.monitor(cx, cy)
            try:
                taskbar = self._w.taskbar(cx, cy)
            except Exception as e:
                log.info("menu: taskbar not found (%s)", e)
        else:
            work = (0, 0, self._root.winfo_screenwidth(), self._root.winfo_screenheight())
            scale = 1.0
        work = usable_area(work, taskbar)            # submenus use it too (panel.work)
        style = self.style(scale, apps_use_light_theme())
        panel = _Panel(self, menu, style, None, None)
        if not panel.rows:
            return
        # the size is known before the window exists: layout() measured every row
        x, y = place_menu(cx, cy, panel.w, panel.h, work, taskbar, scale)
        panel.work = work
        panel.open(x, y)
        self.panels.append(panel)
        self._was_down = self._w.buttons_down() if self._w else False
        self._had_focus = False
        self._last_pt = None
        if self._w is not None:
            self._w.activate(panel.hwnd)
        try:
            panel.win.focus_force()
        except Exception:
            pass
        self._poll_id = self._root.after(POLL_MS, self._poll)

    def close(self) -> None:
        self._cancel_timer()
        if self._poll_id is not None:
            try:
                self._root.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        for p in reversed(self.panels):
            p.destroy()
        self.panels = []

    def _close_after(self, panel: "_Panel") -> None:
        """Close the submenus below `panel`."""
        if panel not in self.panels:
            return
        while self.panels and self.panels[-1] is not panel:
            self.panels.pop().destroy()
        panel.child_row = None
        panel.update_highlight()

    def _open_child(self, panel: "_Panel", index: int, select_first: bool = False) -> None:
        self._cancel_timer()
        if panel not in self.panels:
            return
        if panel.child_row == index and self.panels[-1] is not panel:
            return
        self._close_after(panel)
        row = panel.rows[index]
        if row.submenu is None or not row.enabled:
            return
        child = _Panel(self, row.submenu, panel.style, panel, index)
        if not child.rows:
            return
        item_top = panel.y + row.y + panel.style.px(ITEM_MARGIN_Y)
        x, y = place_submenu(panel.x, panel.x + panel.w, item_top, child.w, child.h,
                             panel.work, panel.style.scale)
        child.work = panel.work
        panel.child_row = index
        child.open(x, y)
        self.panels.append(child)
        if select_first:
            child.move_hover(+1)
        panel.update_highlight()

    def _schedule(self, panel: "_Panel", fn, *args) -> None:
        self._cancel_timer()
        self._timer_panel = panel
        self._timer = self._root.after(SUBMENU_DELAY_MS, self._fire, fn, args)

    def _fire(self, fn, args) -> None:
        self._timer = self._timer_panel = None
        fn(*args)

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            try:
                self._root.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None
        self._timer_panel = None

    # ---------------- input
    def _panel_at(self, x: int, y: int) -> Optional["_Panel"]:
        for p in reversed(self.panels):            # submenus are on top
            if p.contains(x, y):
                return p
        return None

    def _track(self, x: int, y: int) -> None:
        """The mouse moved: highlight what is under it and open / switch submenus
        after a short pause, like the Windows menu does."""
        if not self.panels or (x, y) == self._last_pt:
            return                     # not moved: keep what the keyboard selected
        self._last_pt = (x, y)
        hit = self._panel_at(x, y)
        for p in self.panels:
            if p is not hit and (p.hover is not None or p.part is not None):
                p.set_hover(None, None)
        if hit is None:
            return
        if self._timer_panel is not None and self._timer_panel is not hit:
            self._cancel_timer()       # e.g. moved on into the submenu: keep it open
        index, part = hit.hit(x, y)
        if (index, part) == (hit.hover, hit.part):
            return
        hit.set_hover(index, part)
        if index is None:
            return
        row = hit.rows[index]
        if hit.child_row == index:
            self._cancel_timer()       # back on the item of the open submenu
        elif row.submenu is not None and row.enabled:
            self._schedule(hit, self._open_child, hit, index)
        elif hit.child_row is not None:
            self._schedule(hit, self._close_after, hit)

    def _click(self, x: int, y: int) -> None:
        panel = self._panel_at(x, y)
        if panel is None:
            return
        index, part = panel.hit(x, y)
        if index is None:
            return
        self._activate(panel, index, part)

    def _activate(self, panel: "_Panel", index: int, part: Optional[str]) -> None:
        row = panel.rows[index]
        if not row.selectable:
            return
        if row.kind == "header":
            edit, icon = row.edit, self._icon
            self.close()
            threading.Thread(target=self._run_edit, args=(edit, icon), daemon=True).start()
            return
        if row.kind == "counter":
            if part in ("minus", "plus"):
                self._step(panel, index, -1 if part == "minus" else +1)
            return
        if row.submenu is not None:
            self._open_child(panel, index, select_first=part == "keyboard")
            return
        item, icon = row.item, self._icon
        self.close()
        # the action may wait (an input box, a notification): not in this thread
        threading.Thread(target=self._run, args=(item, icon), daemon=True).start()

    @staticmethod
    def _run_edit(edit, icon) -> None:
        try:
            edit(icon)
        except Exception as e:
            log.exception("menu header: %s", e)

    @staticmethod
    def _run(item, icon) -> None:
        try:
            item(icon)
        except Exception as e:
            log.exception("menu action: %s", e)

    def _step(self, panel: "_Panel", index: int, delta: int) -> None:
        row = panel.rows[index]
        try:
            if row.item.step(delta):
                panel.draw()
                panel.update_highlight()   # a button at its end is no longer highlighted
        except Exception as e:
            log.exception("menu counter: %s", e)

    def _wheel(self, event) -> None:
        panel = self._panel_at(event.x_root, event.y_root)
        if panel is None:
            return
        index, _part = panel.hit(event.x_root, event.y_root)
        if index is not None and panel.rows[index].kind == "counter" and panel.rows[index].enabled:
            self._step(panel, index, +1 if event.delta > 0 else -1)

    def _key(self, event) -> None:
        """Keyboard: arrows, Enter / Space, Esc; Left / Right also step a counter."""
        if not self.panels:
            return
        panel = self.panels[-1]
        k = event.keysym
        row = panel.rows[panel.hover] if panel.hover is not None else None
        if k == "Escape":
            if panel.parent is not None:
                self._back(panel)
            else:
                self.close()
        elif k in ("Down", "Up"):
            panel.move_hover(+1 if k == "Down" else -1)
        elif row is not None and row.kind == "counter" and k in ("Right", "Left"):
            self._step(panel, panel.hover, +1 if k == "Right" else -1)
        elif k == "Right" and row is not None and row.submenu is not None and row.enabled:
            self._open_child(panel, panel.hover, select_first=True)
        elif k == "Left" and panel.parent is not None:
            self._back(panel)
        elif k in ("Return", "KP_Enter", "space") and panel.hover is not None:
            self._activate(panel, panel.hover, "keyboard")

    def _back(self, panel: "_Panel") -> None:
        """Close a submenu and select its item in the parent."""
        parent, index = panel.parent, panel.parent_row
        self._close_after(parent)
        parent.set_hover(index, "keyboard")

    def _poll(self) -> None:
        """While the menu is open: a click anywhere else, or another window taking
        the focus (Alt+Tab, the Start menu), closes it."""
        self._poll_id = None
        if not self.panels:
            return
        w = self._w
        if w is not None:
            x, y = w.cursor()
            self._track(x, y)
            down = w.buttons_down()
            if down and not self._was_down and self._panel_at(x, y) is None:
                self.close()
                return
            self._was_down = down
            fg = w.foreground()
            ours = {h for p in self.panels for h in p.hwnds()}
            if fg in ours:
                self._had_focus = True
            elif self._had_focus and fg:
                self.close()
                return
        self._poll_id = self._root.after(POLL_MS, self._poll)


class _Panel:
    """One menu level: the panel window, its highlight and its catcher."""

    def __init__(self, host: FlyoutHost, menu, style: _Style, parent: Optional["_Panel"],
                 parent_row: Optional[int]):
        self.host, self.menu, self.style = host, menu, style
        self.parent, self.parent_row = parent, parent_row
        self.rows = build_rows(menu)
        self.w, self.h = layout(self.rows, style, style.font.measure,
                                lambda g: self._glyph_font(g).measure(g))
        self.x = self.y = 0
        self.work: Rect = (0, 0, 0, 0)
        self.hover: Optional[int] = None
        self.part: Optional[str] = None       # "minus" / "plus" on a counter
        self.child_row: Optional[int] = None
        self.win = self.canvas = self.catcher = self.overlay = self.ocanvas = None
        self.hwnd = 0
        self._overlay_hwnd = 0
        self._hwnds: List[int] = []
        self._overlay_geo = None
        self._fade_start = 0.0

    def _glyph_font(self, g: str):
        return self.style.chevron_font if g in (CHEVRON, FALLBACK_GLYPHS[CHEVRON]) else self.style.check_font

    # ---------------- windows
    def _toplevel(self, bg: str):
        tk = self.host._tk
        t = tk.Toplevel(self.host._root)
        t.withdraw()
        t.overrideredirect(True)
        t.configure(bg=bg, bd=0, highlightthickness=0)
        try:
            t.attributes("-topmost", True)
        except Exception:
            pass
        return t

    def open(self, x: int, y: int) -> None:
        tk, c = self.host._tk, self.style.colours
        self.x, self.y = x, y
        geo = f"{self.w}x{self.h}+{x}+{y}"
        # the catcher first, the panel over it, the highlight over the panel. Only
        # Windows needs the catcher: elsewhere the colour key is not transparent
        if sys.platform == "win32":
            self.catcher = self._toplevel(c["key"])
            self.catcher.attributes("-alpha", 0.005)             # 1/255: invisible, but clickable
            self.catcher.geometry(geo)
        self.win = self._toplevel(c["key"])
        _attr(self.win, "-transparentcolor", c["key"])
        self.win.attributes("-alpha", 0.0)
        self.win.geometry(geo)
        self.canvas = tk.Canvas(self.win, width=self.w, height=self.h, bg=c["key"],
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.overlay = self._toplevel(OVERLAY_KEY)
        _attr(self.overlay, "-transparentcolor", OVERLAY_KEY)
        self.overlay.attributes("-alpha", 0.0)
        self.overlay.geometry(f"1x1+{x}+{y}")
        self.ocanvas = tk.Canvas(self.overlay, bg=OVERLAY_KEY, highlightthickness=0, bd=0)
        self.ocanvas.pack(fill="both", expand=True)
        self.draw()
        for t in (self.catcher, self.win, self.overlay):
            if t is not None:
                t.deiconify()
        self.win.update_idletasks()
        self._decorate()
        self._fade_start = time.perf_counter()
        self._fade()

    def _decorate(self) -> None:
        """Acrylic, rounded corners and the dark frame (Windows only)."""
        w = self.host._w
        frames = {}
        for name in ("catcher", "win", "overlay"):
            t = getattr(self, name)
            try:
                frames[name] = int(t.wm_frame(), 16)
            except Exception:
                pass
        self._hwnds = list(frames.values())
        self.hwnd = frames.get("win", 0)
        self._overlay_hwnd = frames.get("overlay", 0)
        if w is None or not self.hwnd:
            return
        c = self.style.colours
        if not w.acrylic(self.hwnd, c["tint_abgr"]):
            log.info("menu: no acrylic on this Windows")
        w.dwm_int(self.hwnd, 33, 2)                            # DWMWA_WINDOW_CORNER_PREFERENCE = ROUND
        if not self.style.light:
            w.dwm_int(self.hwnd, 20, 1)                        # DWMWA_USE_IMMERSIVE_DARK_MODE
        for name in ("catcher", "overlay"):
            if frames.get(name):
                w.dwm_int(frames[name], 33, 1)                                # DWMWCP_DONOTROUND: no frame on them

    def _fade(self) -> None:
        if self.win is None:
            return
        k = min(1.0, (time.perf_counter() - self._fade_start) * 1000 / FADE_MS)
        try:
            self.win.attributes("-alpha", k)
        except Exception:
            return
        if k < 1.0:
            self.win.after(15, self._fade)
        else:
            self.update_highlight()

    def hwnds(self) -> List[int]:
        return self._hwnds

    def destroy(self) -> None:
        for t in (self.overlay, self.win, self.catcher):
            if t is not None:
                try:
                    t.destroy()
                except Exception:
                    pass
        self.overlay = self.win = self.catcher = None

    # ---------------- drawing
    def draw(self) -> None:
        cv, st, c, px = self.canvas, self.style, self.style.colours, self.style.px
        if cv is None:
            return
        cv.delete("all")
        if not is_windows_11():                    # Windows 10: no rounded frame from DWM
            cv.create_rectangle(0, 0, self.w - 1, self.h - 1, outline=c["border"])
        x_check = px(ITEM_MARGIN_X) + px(ITEM_PAD_L)
        x_text = x_check + px(CHECK_COLUMN)
        x_right = self.w - px(ITEM_MARGIN_X) - px(ITEM_PAD_R)
        for row in self.rows:
            mid = row.y + row.h // 2
            if row.kind == "sep":
                y = row.y + px(SEP_MARGIN_Y)
                cv.create_rectangle(px(SEP_MARGIN_X), y, self.w - px(SEP_MARGIN_X) - 1,
                                    y + px(1) - 1, outline="", fill=c["separator"])
                continue
            if row.kind == "header":
                for (text, is_detail), top in zip(row.lines, header_line_tops(row, st)):
                    cv.create_text(x_text, top, text=text, font=st.font,
                                   fill=c["muted"] if is_detail else c["text"], anchor="nw")
                if row.edit is not None:
                    x0, y0, x1, y1 = edit_box(row, self.w, st)
                    lit = self.hover == self.rows.index(row) and self.part in ("edit", "keyboard")
                    cv.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=st.glyph(EDIT),
                                   font=st.button_font, fill=c["text"] if lit else c["faint"],
                                   anchor="center")
                y = row.y + row.h - px(SEP_MARGIN_Y) - px(1)
                cv.create_rectangle(px(SEP_MARGIN_X), y, self.w - px(SEP_MARGIN_X) - 1,
                                    y + px(1) - 1, outline="", fill=c["separator"])
                continue
            fg = c["text"] if row.enabled else c["muted"]
            if row.checked:
                cv.create_text(x_check + px(CHECK_COLUMN) // 2, mid,
                               text=st.glyph(CHECK), font=st.check_font, fill=fg, anchor="center")
            cv.create_text(x_text, mid, text=row.text, font=st.font, fill=fg, anchor="w")
            if row.kind == "counter":
                self._draw_counter(row, x_right, mid, fg)
            elif row.submenu is not None:
                cv.create_text(x_right, mid, text=st.glyph(CHEVRON), font=st.chevron_font,
                               fill=c["muted"], anchor="e")

    def _counter_boxes(self, row: _Row) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int],
                                                   Tuple[int, int]]:
        """(minus box, plus box, value span) in panel coordinates."""
        px = self.style.px
        right = self.w - px(ITEM_MARGIN_X) - px(ITEM_PAD_R)
        bw, bh = px(BUTTON_W), px(BUTTON_H)
        vw = value_width(row.item, self.style, self.style.font.measure)
        top = row.y + (row.h - bh) // 2
        plus = (right - bw, top, right, top + bh)
        minus = (right - bw - vw - bw, top, right - bw - vw, top + bh)
        return minus, plus, (right - bw - vw, right - bw)

    def _draw_counter(self, row: _Row, x_right: int, mid: int, fg: str) -> None:
        cv, st, c = self.canvas, self.style, self.style.colours
        minus, plus, (v0, v1) = self._counter_boxes(row)
        try:
            label, dec, inc = row.item.label(), row.item.can_step(-1), row.item.can_step(+1)
        except Exception:
            label, dec, inc = "?", False, False
        live = row.enabled
        for box, glyph, ok in ((minus, MINUS, dec), (plus, PLUS, inc)):
            cv.create_text((box[0] + box[2]) // 2, (box[1] + box[3]) // 2, text=st.glyph(glyph),
                           font=st.button_font, fill=c["text"] if (ok and live) else c["muted"],
                           anchor="center")
        cv.create_text((v0 + v1) // 2, mid, text=label, font=st.font, fill=fg, anchor="center")

    # ---------------- hover
    def contains(self, x: int, y: int) -> bool:
        return self.win is not None and self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def hit(self, x: int, y: int) -> Tuple[Optional[int], Optional[str]]:
        """(row index, part) under a screen point; separators and disabled rows count
        as nothing."""
        lx, ly = x - self.x, y - self.y
        px = self.style.px
        if not (px(ITEM_MARGIN_X) <= lx < self.w - px(ITEM_MARGIN_X)):
            return None, None
        for i, row in enumerate(self.rows):
            if row.y <= ly < row.y + row.h:
                if not row.selectable:
                    return None, None
                if row.kind == "header":
                    x0, y0, x1, y1 = edit_box(row, self.w, self.style)
                    if x0 <= lx < x1 and y0 <= ly < y1:
                        return i, "edit"
                    return None, None
                if row.kind == "counter":
                    minus, plus, _v = self._counter_boxes(row)
                    for name, (x0, y0, x1, y1) in (("minus", minus), ("plus", plus)):
                        if x0 <= lx < x1 and y0 <= ly < y1:
                            return i, name
                    return i, None
                return i, None
        return None, None

    def set_hover(self, index: Optional[int], part: Optional[str]) -> None:
        was = self._pencil_lit()
        self.hover, self.part = index, part
        if self._pencil_lit() != was:
            self.draw()                    # the pencil is brighter under the mouse
        self.update_highlight()

    def _pencil_lit(self) -> bool:
        return (self.hover is not None and self.rows[self.hover].kind == "header"
                and self.part in ("edit", "keyboard"))

    def move_hover(self, delta: int) -> None:
        """Keyboard: the next / previous item that can be chosen."""
        sel = [i for i, r in enumerate(self.rows) if r.selectable]
        if not sel:
            return
        cur = self.hover if self.hover is not None else self.child_row
        if cur not in sel:
            nxt = sel[0] if delta > 0 else sel[-1]
        else:
            nxt = sel[(sel.index(cur) + delta) % len(sel)]
        self.set_hover(nxt, "keyboard")

    def highlight_box(self) -> Optional[Tuple[int, int, int, int]]:
        """What the highlight covers now, in panel coordinates, or None."""
        index = self.hover if self.hover is not None else self.child_row
        if index is None:
            return None
        row = self.rows[index]
        if not row.selectable:
            return None
        px = self.style.px
        if row.kind == "header":
            return edit_box(row, self.w, self.style)
        if row.kind == "counter" and self.part != "keyboard":
            if self.part not in ("minus", "plus"):
                return None
            try:
                if not row.item.can_step(-1 if self.part == "minus" else +1):
                    return None
            except Exception:
                return None
            minus, plus, _v = self._counter_boxes(row)
            return minus if self.part == "minus" else plus
        return (px(ITEM_MARGIN_X), row.y + px(ITEM_MARGIN_Y),
                self.w - px(ITEM_MARGIN_X), row.y + row.h - px(ITEM_MARGIN_Y))

    def update_highlight(self) -> None:
        ov = self.overlay
        if ov is None:
            return
        box = self.highlight_box()
        c = self.style.colours
        try:
            if box is None or self._fade_running():
                ov.attributes("-alpha", 0.0)
                return
            x0, y0, x1, y1 = box
            w, h = x1 - x0, y1 - y0
            geo = (w, h, self.x + x0, self.y + y0)
            if geo != self._overlay_geo:
                ov.geometry(f"{w}x{h}+{geo[2]}+{geo[3]}")
                self.ocanvas.configure(width=w, height=h)
                self.ocanvas.delete("all")
                rounded_rect(self.ocanvas, 0, 0, w, h, self.style.px(HOVER_RADIUS), c["hover"])
                self._overlay_geo = geo
                # Tk moves the window later, when idle; raising it before that would
                # make Tk keep the old position
                ov.update_idletasks()
            self._raise_overlay()
            ov.attributes("-alpha", c["hover_alpha"])
        except Exception:
            pass

    def _raise_overlay(self) -> None:
        """The highlight in front of the panel. Opening the menu gives the panel the focus,
        which brings it to the front, and its acrylic then hides a highlight behind it.
        Tk's lift() is not used: on these borderless windows it also moved the highlight
        back to the corner of the panel."""
        w = self.host._w
        hwnd = self._overlay_hwnd
        if w is not None and hwnd:
            w.to_top(hwnd)

    def _fade_running(self) -> bool:
        return (time.perf_counter() - self._fade_start) * 1000 < FADE_MS


def _attr(win, name: str, value) -> None:
    """A window attribute that only some Tk builds know (-transparentcolor is Windows only)."""
    try:
        win.attributes(name, value)
    except Exception as e:
        log.debug("menu: %s %s: %s", name, value, e)


def rounded_rect(canvas, x0: int, y0: int, x1: int, y1: int, r: int, fill: str) -> None:
    """A filled rectangle with rounded corners (no anti-aliasing: its pixels are either
    the colour or the window's transparent key)."""
    r = max(0, min(r, (x1 - x0) // 2, (y1 - y0) // 2))
    if r == 0:
        canvas.create_rectangle(x0, y0, x1, y1, outline="", fill=fill)
        return
    d = 2 * r
    canvas.create_rectangle(x0 + r, y0, x1 - r, y1, outline="", fill=fill)
    canvas.create_rectangle(x0, y0 + r, x1, y1 - r, outline="", fill=fill)
    for ax, ay in ((x0, y0), (x1 - d, y0), (x0, y1 - d), (x1 - d, y1 - d)):
        canvas.create_oval(ax, ay, ax + d, ay + d, outline="", fill=fill)
