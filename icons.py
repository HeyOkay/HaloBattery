"""Tray icon rendering: a battery ring with a device pictogram in the middle.

The ring fills clockwise from the top; underneath it there is a dim "track".
The centre shows the device silhouette: headset, mouse or the Bluetooth rune.
Colours follow the system battery icon: normal charge uses the taskbar colour
(white on a dark taskbar, black on a light one), close to the threshold it is
amber, at or below the threshold it is red, and while charging the arc is
green and slowly "breathes" in brightness.
Asleep / no link: the icon is translucent and has no arc.
"""
from __future__ import annotations

import math
import sys
from typing import Optional

from PIL import Image, ImageDraw

SIZE = 64
SS = 4                      # supersampling for smooth edges
S = SIZE * SS

RED = (232, 17, 35)
AMBER = (255, 185, 0)
GREEN = (16, 196, 80)
CLEAR = (0, 0, 0, 0)

# device kind aliases (single letters are accepted too)
KINDS = {"H": "headset", "M": "mouse", "B": "bluetooth",
         "headset": "headset", "mouse": "mouse", "bluetooth": "bluetooth"}


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


PICTOS = {"headset": (_headset, 0, 2, 18), "mouse": (_mouse, 0, 0, 19.5),
          "bluetooth": (_bluetooth, 0, 0, 18)}


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
