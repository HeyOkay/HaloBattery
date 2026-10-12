"""The "What's new in version X" window, shown once after an update.

The same look as the tray menu (flyout.py): a borderless window with an acrylic
(blurred, translucent) background, rounded corners on Windows 11 and the Windows app
theme (light or dark). It shows the app logo, the version, what it was updated from,
the release notes in short (each section with the first sentence of each point; the
summary paragraph is left out), a "Full release notes" link and a "Got it" button.

The notes are the release text from GitHub, which is the CHANGELOG.md section of that
version (tools/release_notes.py). parse_notes() turns that Markdown into plain blocks
and leaves out what only developers need (device ids, issue numbers, code marks).

The window runs in the tkinter thread of flyout.FlyoutHost. As in the menu, an almost
invisible "catcher" window lies under it: the transparent pixels of the window would
otherwise let clicks through to whatever is below.
"""
from __future__ import annotations

import datetime
import logging
import re
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

import flyout

log = logging.getLogger("halo_battery")

# ------------------------------------------------------------------ design
# sizes in pixels at 100 % (96 DPI); multiplied by the monitor's scale
WIDTH = 460
PAD = 28                      # around the content
LOGO = 44                     # the app logo, left of the title
LOGO_GAP = 16
CLOSE_BOX = 32                # the close button (top right)
BODY_MAX_H = 330              # the notes scroll beyond this
SECTION_GAP = 16              # above a section heading
ITEM_GAP = 8                  # between two points
BULLET_W = 16                 # the bullet column
BUTTON_H, BUTTON_PAD_X, BUTTON_RADIUS = 32, 22, 5
FOOTER_GAP = 22               # between the notes and the footer
SCROLL_W, SCROLL_W_HOVER = 2, 6   # the scroll thumb: a thin line, wider under the mouse
SCROLL_ZONE = 22              # the strip right of the notes that the scroll bar reacts to
SCROLL_MIN = 28               # the shortest thumb
SCROLL_TRACK_W = 12           # the faint track that shows under a wide thumb
SCROLL_ANIM_MS = 180          # thin <-> wide, as Windows 11 scroll bars do
SCROLL_COLLAPSE_MS = 350      # the bar stays wide this long after the mouse leaves
BAR_STEPS = 12                # the in-between looks that are drawn (and kept)
CHIP_RADIUS = 5               # the hover / press background of the close button and link
SENTENCE_GAP = 6              # between the sentences of the summary
PRESS_INSET = 1               # a pressed button shrinks by this much on each side
FADE_MS = 160
THEME_TRANSITION_MS = 360     # light <-> dark: every colour flows to the new theme
WHEEL_STEP = 48
ACCENT_CHECK_MS = 1000        # how often the open window looks for a new accent colour
SCROLL_EASE = 0.25            # each 1/60 s moves this part of the way left to the target
SCROLL_FRAME_MS = 7           # ~144 frames a second while gliding (with a 1 ms timer)
FRAME_60 = 1000 / 60

TEXT_FONTS = flyout.TEXT_FONTS
SEMIBOLD_TEXT = ("Segoe UI Variable Text Semibold", "Segoe UI Semibold")
SEMIBOLD_DISPLAY = ("Segoe UI Variable Display Semib", "Segoe UI Variable Display Semibold",
                    "Segoe UI Semibold")
TITLE_PX, EYEBROW_PX, LABEL_PX, TEXT_PX, HEAD_PX, BUTTON_TEXT_PX, CLOSE_PX = 24, 14, 12, 13, 14, 13, 10
CLOSE_GLYPH = ""        # Segoe Fluent Icons: ChromeClose
LINK_GLYPH = ""         # OpenInNewWindow

# ARGB: the first two digits are the opacity. A stronger tint than the menu, so a
# longer text stays easy to read over a busy background.
PALETTE = {
    False: {"tint": "#B81C1C1C", "text": "#F2F2F2", "muted": "#99F2F2F2",           # dark
            "line": "#1FFFFFFF", "hover": "#14FFFFFF", "accent": "#4CC2FF",
            "accent_hover": "#5FCAFF", "on_accent": "#000000",
            "link": "#99EBFF",
            "thumb": "#59FFFFFF", "thumb_hover": "#8CFFFFFF", "track": "#14000000",
            "border": "#33FFFFFF"},
    True: {"tint": "#C8F3F3F3", "text": "#1A1A1A", "muted": "#991A1A1A",            # light
           "line": "#14000000", "hover": "#0F000000", "accent": "#005FB8",
           "accent_hover": "#1A6EC0", "on_accent": "#FFFFFF",
           "link": "#005FB8",
           "thumb": "#4D000000", "thumb_hover": "#80000000", "track": "#08000000",
           "border": "#26000000"},
}


def _hex(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def _rgb(value: str) -> Tuple[int, int, int]:
    return flyout.parse_argb(value)[1:]


def _mix(a, b, k: float) -> Tuple[int, int, int]:
    """a moved k of the way to b."""
    return tuple(round(x + (y - x) * k) for x, y in zip(a, b))


# Windows' accent colour in its eight shades (Settings > Personalization > Colors):
# Light3, Light2, Light1, the accent itself, Dark1, Dark2, Dark3 and one unused. The
# default blue is 99EBFF 4CC2FF 0091F8 0078D4 005FB8 003E92 001A68: the colours above.
# Like Windows 11's own controls, the dark theme takes Light2 for the button and Light3
# for links, the light theme Dark1 for both.
LIGHT3, LIGHT2, LIGHT1, BASE, DARK1, DARK2, DARK3 = range(7)


def parse_accent_palette(data) -> Optional[List[Tuple[int, int, int]]]:
    """The AccentPalette registry value (32 bytes, RGBA x 8) -> 8 colours, or None."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 28:
        return None
    return [tuple(data[i:i + 3]) for i in range(0, min(len(data), 32), 4)]


def palette_from_accent(rgb: Tuple[int, int, int]) -> List[Tuple[int, int, int]]:
    """The eight shades worked out from the accent alone (when Windows has no palette)."""
    white, black = (255, 255, 255), (0, 0, 0)
    return [_mix(rgb, white, 0.6), _mix(rgb, white, 0.3), _mix(rgb, white, 0.15), tuple(rgb),
            _mix(rgb, black, 0.2), _mix(rgb, black, 0.48), _mix(rgb, black, 0.78), tuple(rgb)]


def windows_accent_palette() -> Optional[List[Tuple[int, int, int]]]:
    """The user's accent colour shades from the registry; None when not on Windows or
    nothing is set (the default blue is used then)."""
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent") as k:
            pal = parse_accent_palette(winreg.QueryValueEx(k, "AccentPalette")[0])
            if pal:
                return pal
    except OSError:
        pass
    try:                                     # Windows 10 without a palette: the accent only
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
            v = int(winreg.QueryValueEx(k, "AccentColor")[0]) & 0xFFFFFFFF     # 0xAABBGGRR
        return palette_from_accent((v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF))
    except (OSError, ValueError, TypeError):
        return None


def _luminance(rgb) -> float:
    """Relative luminance (WCAG) of an sRGB colour."""
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def text_on(rgb) -> Tuple[int, int, int]:
    """Black or white, whichever has more contrast on the colour."""
    lum = _luminance(rgb)
    return (0, 0, 0) if (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) else (255, 255, 255)


def colours(light: bool, accent: Optional[List[Tuple[int, int, int]]] = None) -> dict:
    """The theme's colours, laid over the tint (tkinter draws only opaque colours).
    accent: the eight shades of Windows' accent colour; None = the default blue."""
    p = dict(PALETTE[light])
    if accent:
        button = accent[DARK1] if light else accent[LIGHT2]
        link = accent[DARK1] if light else accent[LIGHT3]
        p.update(accent=_hex(button), link=_hex(link),
                 accent_hover=_hex(_mix(button, (255, 255, 255), 0.12)))
    _a, r, g, b = flyout.parse_argb(p["tint"])
    base = (r, g, b)
    out = {k: flyout.over(v, base) for k, v in p.items() if k != "tint"}
    # "Got it": the text colour follows the button, not the theme - dark on a light
    # button, white on a dark one - and turns white while the button is held down. A
    # soft dark shadow one pixel under the text, the same at rest and pressed.
    button_rgb = flyout.parse_argb(p["accent"])[1:]
    white, black = (255, 255, 255), (0, 0, 0)
    out["on_accent"] = _hex(text_on(button_rgb))
    out["emboss"] = _hex(_mix(button_rgb, black, 0.3))
    pressed_face = _mix(button_rgb, black, 0.12)
    out["on_accent_down"] = _hex(white)
    out["emboss_down"] = _hex(_mix(pressed_face, black, 0.3))
    out["base"] = "#%02x%02x%02x" % base
    out["key"] = "#%02x%02x%02x" % (r, g, b + 1 if b < 255 else b - 1)
    out["tint_abgr"] = flyout.abgr(p["tint"])
    out["base_rgb"] = base
    return out


def blend_colours(a: dict, b: dict, t: float) -> dict:
    """Two colour sets (colours()) mixed: t = 0 is a, t = 1 is b. The colour key is not
    mixed (it must stay one exact colour): a's until half way, then b's."""
    out = {}
    for k, va in a.items():
        vb = b.get(k, va)
        if k == "key":
            out[k] = va if t < 0.5 else vb
        elif k == "tint_abgr":
            out[k] = sum(round(((va >> s) & 0xFF) + (((vb >> s) & 0xFF) - ((va >> s) & 0xFF)) * t) << s
                         for s in (0, 8, 16, 24))
        elif k == "base_rgb":
            out[k] = _mix(va, vb, t)
        elif isinstance(va, str) and va.startswith("#") and len(va) == 7:
            out[k] = _hex(_mix(_rgb(va), _rgb(vb), t))
        else:
            out[k] = vb if t >= 0.5 else va
    return out


# ------------------------------------------------------------------ the notes
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_REF_PARENS = re.compile(r"\s*\((?:[^()]*?(?:#\d+|\b[0-9A-Fa-f]{4}:[0-9A-Fa-f]{4}\b)[^()]*)\)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"(])")


def clean(text: str) -> str:
    """Markdown inline marks out; device ids and issue numbers in brackets out."""
    t = _LINK.sub(r"\1", text)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    t = _REF_PARENS.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    return t


def sentences(text: str) -> List[str]:
    """A paragraph -> its sentences (the summary is shown one sentence per line)."""
    return [p.strip() for p in _SENTENCE_END.split(text) if p.strip()]


def first_sentence(text: str) -> str:
    parts = _SENTENCE_END.split(text, maxsplit=1)
    return parts[0].strip()


def parse_notes(md: str) -> List[Tuple[str, str]]:
    """The release text -> [("para" | "heading" | "item", text), ...].

    Paragraphs are kept whole; each point of a list is cut to its first sentence."""
    blocks: List[Tuple[str, str]] = []
    cur_kind, cur = "", []

    def flush():
        nonlocal cur_kind, cur
        if cur_kind and cur:
            text = clean(" ".join(cur))
            if cur_kind == "item":
                text = first_sentence(text)
            if text:
                blocks.append((cur_kind, text))
        cur_kind, cur = "", []

    for raw in (md or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", stripped)
        if m:
            flush()
            head = clean(m.group(1))
            if head:
                blocks.append(("heading", head))
            continue
        m = re.match(r"^[-*+]\s+(.*)$", stripped)
        if m and not line.startswith("    "):
            flush()
            cur_kind, cur = "item", [m.group(1)]
            continue
        if not cur_kind:
            cur_kind = "para"
        cur.append(stripped)
    flush()
    return blocks


def visible_blocks(blocks: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """What the window shows: the sections and their points, without the summary
    paragraph. Notes that are only paragraphs (no list) are shown as they are."""
    if any(kind == "item" for kind, _ in blocks):
        return [(kind, text) for kind, text in blocks if kind != "para"]
    return blocks


def nice_date(iso: str) -> str:
    """'2026-10-05' -> '5 October 2026'; anything else -> ''."""
    try:
        d = datetime.date.fromisoformat((iso or "")[:10])
    except ValueError:
        return ""
    return f"{d.day} {d.strftime('%B')} {d.year}"


def subtitle(from_version: str, date: str) -> str:
    parts = []
    if from_version:
        parts.append(f"Updated from {from_version}")
    when = nice_date(date)
    if when:
        parts.append(when)
    return "  ·  ".join(parts)


def flatten(img, background: Tuple[int, int, int], key: Tuple[int, int, int], cutoff: int = 24):
    """An RGBA picture for a window with a colour key: the pixels that are (almost)
    transparent become the key, so the acrylic shows through them; the rest are laid
    over the window's average colour, so their smooth edge has no fringe."""
    from PIL import Image
    flat = Image.new("RGBA", img.size, background + (255,))
    flat.alpha_composite(img)
    flat = flat.convert("RGB")
    hole = img.getchannel("A").point(lambda a: 255 if a < cutoff else 0)
    flat.paste(Image.new("RGB", img.size, key), mask=hole)
    return flat


def app_logo(size: int, background: Tuple[int, int, int], key: Tuple[int, int, int]):
    """The app's logo (the exe icon: a green ring on a dark disc) as an RGB picture whose
    corners are the colour key."""
    from PIL import Image, ImageDraw
    import icons
    ss = 4
    big = size * ss
    disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, big - 1, big - 1), fill=(32, 32, 32, 255))
    ring = icons.render(75, True, True, 20, light_taskbar=False, badge="")
    ring = ring.resize((int(big * 0.84),) * 2, Image.LANCZOS)
    off = (big - ring.width) // 2
    disc.alpha_composite(ring, (off, off))
    return flatten(disc.resize((size, size), Image.LANCZOS), background, key)


def button_image(w: int, h: int, radius: int, fill: Tuple[int, int, int], state: str,
                 scale: float = 1.0, strength: float = 1.0):
    """The "Got it" button as a smooth RGBA picture, w x h pixels.

    A gentle bevel: the face is a touch lighter at the top than at the bottom, and a one
    pixel rim is light along the top edge and darker along the bottom, as on Windows 11
    accent buttons. state is "normal", "hover" or "pressed"; pressed is a little smaller,
    darker, and its light falls the other way (the face looks pushed in). strength < 1
    makes all of it softer (the close button's and the link's background)."""
    from PIL import Image, ImageChops, ImageDraw
    ss = 4
    W, H = w * ss, h * ss
    inset = round(PRESS_INSET * scale) * ss if state == "pressed" else 0
    box = (inset, inset, W - 1 - inset, H - 1 - inset)
    r = max(1, radius * ss - inset)
    rim = max(ss, round(scale * ss))
    white, black = (255, 255, 255), (0, 0, 0)
    k = strength
    if state == "pressed":
        face = _mix(fill, black, 0.12 * k)
        top, bottom = _mix(face, black, 0.05 * k), _mix(face, white, 0.03 * k)
        rim_top, rim_bottom = _mix(face, black, 0.22 * k), _mix(face, white, 0.10 * k)
    else:
        face = fill
        top, bottom = _mix(face, white, 0.10 * k), _mix(face, black, 0.06 * k)
        rim_top, rim_bottom = _mix(face, white, 0.38 * k), _mix(face, black, 0.24 * k)

    def gradient(c0, c1):
        g = Image.new("RGB", (1, H))
        for y in range(H):
            g.putpixel((0, y), _mix(c0, c1, y / max(1, H - 1)))
        return g.resize((W, H))

    outer = Image.new("L", (W, H), 0)
    ImageDraw.Draw(outer).rounded_rectangle(box, r, fill=255)
    inner = Image.new("L", (W, H), 0)
    ib = (box[0] + rim, box[1] + rim, box[2] - rim, box[3] - rim)
    ImageDraw.Draw(inner).rounded_rectangle(ib, max(1, r - rim), fill=255)
    ring = ImageChops.subtract(outer, inner)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.paste(gradient(top, bottom), mask=outer)
    img.paste(gradient(rim_top, rim_bottom), mask=ring)
    return img.resize((w, h), Image.LANCZOS)


def chip_image(w: int, h: int, radius: int, fill: Tuple[int, int, int], state: str,
               light: bool, scale: float = 1.0):
    """The hover background of the close button and the link, like the buttons of the
    Windows 11 taskbar: an even face with a thin outline all round, a little brighter
    along the top and the bottom than at the sides (dark theme; darker in the light
    theme). Pressed: one pixel smaller, a quieter face and outline."""
    from PIL import Image, ImageChops, ImageDraw
    ss = 4
    W, H = w * ss, h * ss
    inset = round(PRESS_INSET * scale) * ss if state == "pressed" else 0
    box = (inset, inset, W - 1 - inset, H - 1 - inset)
    r = max(1, radius * ss - inset)
    rim = max(ss, round(scale * ss))
    edge = (0, 0, 0) if light else (255, 255, 255)
    k = 0.6 if state == "pressed" else 1.0
    face = _mix(fill, (0, 0, 0) if not light else (255, 255, 255), 0.04) if state == "pressed" else fill
    ends, sides = _mix(face, edge, 0.12 * k), _mix(face, edge, 0.06 * k)
    # the outline: bright ends fading to the quieter sides
    g = Image.new("RGB", (1, H))
    for y in range(H):
        d = min(y, H - 1 - y) / max(1, H / 2)          # 0 at the top and bottom edge
        g.putpixel((0, y), _mix(ends, sides, min(1.0, d * 6)))
    rim_colour = g.resize((W, H))
    outer = Image.new("L", (W, H), 0)
    ImageDraw.Draw(outer).rounded_rectangle(box, r, fill=255)
    inner = Image.new("L", (W, H), 0)
    ImageDraw.Draw(inner).rounded_rectangle((box[0] + rim, box[1] + rim, box[2] - rim, box[3] - rim),
                                            max(1, r - rim), fill=255)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.paste(Image.new("RGB", (W, H), face), mask=outer)
    img.paste(rim_colour, mask=ImageChops.subtract(outer, inner))
    return img.resize((w, h), Image.LANCZOS)


def pill_image(w: int, h: int, fill: Tuple[int, int, int]):
    """A smooth bar with fully round ends (the scroll thumb), as RGBA."""
    from PIL import Image, ImageDraw
    ss = 4
    img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), w * ss // 2,
                                          fill=fill + (255,))
    return img.resize((w, h), Image.LANCZOS)


def scroll_step(pos: float, target: float, ease: float = SCROLL_EASE) -> Tuple[float, float]:
    """One frame of a smooth scroll -> (new position, target). It eases out: fast at
    first, slower near the end, and lands exactly on the target."""
    diff = target - pos
    if abs(diff) < 0.5:
        return target, target
    return pos + diff * ease, target


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def bar_look(k: float, thin: int, wide: int) -> Tuple[float, int]:
    """The scroll bar between thin (k = 0) and wide (k = 1): (k rounded to one of the
    drawn steps, width in pixels)."""
    q = round(max(0.0, min(1.0, k)) * BAR_STEPS) / BAR_STEPS
    return q, int(round(thin + (wide - thin) * q))


def ease_for(dt_ms: float, ease: float = SCROLL_EASE) -> float:
    """The ease of one frame that took dt_ms: the motion depends on the time passed,
    not on the number of frames, so a slow frame does not slow the scroll down."""
    return 1.0 - (1.0 - ease) ** (max(0.0, dt_ms) / FRAME_60)


def pointer_inside(x: int, y: int, left: int, top: int, w: int, h: int) -> bool:
    return left <= x < left + w and top <= y < top + h


def thumb_geometry(track: int, view: int, full: int, scroll: int, min_h: int) -> Tuple[int, int]:
    """(top within the track, height) of the scroll thumb."""
    h = max(min_h, track * view // max(1, full))
    h = min(h, track)
    span = max(1, full - view)
    return (track - h) * scroll // span, h


# ------------------------------------------------------------------ the window
_open: List["WhatsNewWindow"] = []


def show(host: "flyout.FlyoutHost", version: str, notes_md: str, url: str,
         from_version: str = "", date: str = "",
         open_url: Optional[Callable[[str], None]] = None) -> threading.Event:
    """Show the window (from any thread). -> an event that is set once it is closed,
    or at once when it cannot be shown."""
    closed = threading.Event()
    if not host.call(lambda: _create(host, version, notes_md, url, from_version, date,
                                     open_url, closed)):
        log.info("what's new: no window (tkinter is not available)")
        closed.set()
    return closed


def _create(host, version, notes_md, url, from_version, date, open_url, closed) -> None:
    try:
        WhatsNewWindow(host, version, notes_md, url, from_version, date, open_url, closed)
    except Exception as e:
        log.exception("what's new: %s", e)
        closed.set()


class WhatsNewWindow:
    def __init__(self, host, version: str, notes_md: str, url: str, from_version: str,
                 date: str, open_url, closed: threading.Event):
        self.host, self.url, self.open_url, self.closed = host, url, open_url, closed
        tk, root, w32 = host._tk, host._root, host._w
        self.tk, self.root, self.w32 = tk, root, w32
        self.light = flyout.apps_use_light_theme()
        self.accent = windows_accent_palette()
        self.c = colours(self.light, self.accent)
        # the monitor the mouse is on, and its scale
        if w32 is not None:
            cx, cy = w32.cursor()
            work, scale = w32.monitor(cx, cy)
        else:
            work, scale = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight()), 1.0
        self.scale = scale
        px = self.px = lambda v: max(1, round(v * scale))
        fams = host._families
        tkf = host._font

        def font(names, size, bold=False):
            fam = next((f for f in names if f in fams), None)
            if fam is None:                  # no semibold family here: the text font in bold
                fam = next((f for f in TEXT_FONTS if f in fams), "TkDefaultFont")
                bold = bold or names is not TEXT_FONTS
            if fam == "TkDefaultFont":
                f = tkf.nametofont("TkDefaultFont").copy()
                f.configure(size=-px(size), weight="bold" if bold else "normal")
                return f
            return tkf.Font(root, family=fam, size=-px(size), weight="bold" if bold else "normal")

        self.f_title = font(SEMIBOLD_DISPLAY, TITLE_PX)
        self.f_eyebrow = font(SEMIBOLD_TEXT, EYEBROW_PX)
        # the notes in the regular weight; the date and the link one step heavier
        self.f_label = font(SEMIBOLD_TEXT, LABEL_PX)
        self.f_text = font(TEXT_FONTS, TEXT_PX)
        self.f_link = font(SEMIBOLD_TEXT, TEXT_PX)
        self.f_head = font(SEMIBOLD_TEXT, HEAD_PX)
        self.f_button = font(SEMIBOLD_TEXT, BUTTON_TEXT_PX)
        icon_fam = next((f for f in flyout.ICON_FONTS if f in fams), None)
        self.f_icon = tkf.Font(root, family=icon_fam, size=-px(CLOSE_PX)) if icon_fam else None
        self.version, self.from_version, self.date = version, from_version, date
        self.blocks = visible_blocks(parse_notes(notes_md))
        if not self.blocks:
            self.blocks = [("para", "The release notes could not be loaded. "
                                    "They are on the release page.")]
        self.hover: Optional[str] = None       # "close" / "link" / "button"
        self.pressed: Optional[str] = None     # the part the mouse went down on
        self.thumb_drag: Optional[Tuple[int, int]] = None   # (y at the press, scroll then)
        self.thumb_box = (0, 0, 0, 0)
        self.scroll_target = 0.0                # where a smooth scroll is heading
        self._scroll_pos = 0.0                  # the exact (fractional) position
        self._scroll_job = None
        self._glide_t = 0.0                     # time of the last frame of a glide
        self._hires = False                     # the 1 ms timer is on
        self._pills = {}
        self._chips = {}                        # (part, state) -> picture of its background
        self._theme_move = None                 # (from, to, light, start, key switched)
        self._bar_k = 0.0                       # the scroll bar: 0 thin ... 1 wide
        self._bar_anim = None                   # (from, to, start time) while it changes
        self._bar_job = None
        self._collapse_job = None
        self._track = None
        self.scroll = 0
        self.drag: Optional[Tuple[int, int, int, int]] = None
        self._closing = False
        self._build(work)
        _open.append(self)

    # ---------------- building
    def _toplevel(self, bg: str):
        t = self.tk.Toplevel(self.root)
        t.withdraw()
        t.overrideredirect(True)
        t.configure(bg=bg, bd=0, highlightthickness=0)
        return t

    def _build(self, work) -> None:
        tk, c, px = self.tk, self.c, self.px
        self.w = px(WIDTH)
        inner = self.w - 2 * px(PAD)
        # the body is drawn first (on its own canvas) to know its height
        self.win = self._toplevel(c["key"])
        flyout._attr(self.win, "-transparentcolor", c["key"])
        self.win.attributes("-alpha", 0.0)
        self.cv = tk.Canvas(self.win, bg=c["key"], highlightthickness=0, bd=0)
        self.body = tk.Canvas(self.win, bg=c["key"], highlightthickness=0, bd=0,
                              width=inner, height=1)
        body_h = self._draw_body(inner)
        self.body_h_full = body_h
        self.body_h = min(body_h, px(BODY_MAX_H))
        self.body.configure(height=self.body_h, scrollregion=(0, 0, inner, body_h))

        # header
        top = px(PAD)
        x_text = px(PAD) + px(LOGO) + px(LOGO_GAP)
        cv = self.cv
        self._logo = None
        try:
            from PIL import ImageTk
            self._logo = ImageTk.PhotoImage(app_logo(px(LOGO), c["base_rgb"], _rgb(c["key"])),
                                            master=self.win)
        except Exception as e:
            log.info("what's new: no logo (%s)", e)
        label_h = self.f_label.metrics("linespace")
        eyebrow_h = self.f_eyebrow.metrics("linespace")
        title_h = self.f_title.metrics("linespace")
        sub = subtitle(self.from_version, self.date)
        header_h = eyebrow_h + title_h + (label_h if sub else 0)
        y = top
        cv.create_text(x_text, y, text="What's new", font=self.f_eyebrow, fill=c["link"], anchor="nw",
                       tags="accent")
        y += eyebrow_h
        cv.create_text(x_text, y - px(2), text=f"Halo Battery {self.version}", font=self.f_title,
                       fill=c["text"], anchor="nw", tags="text")
        y += title_h
        if sub:
            cv.create_text(x_text, y - px(2), text=sub, font=self.f_label, fill=c["muted"], anchor="nw",
                           tags="muted")
            y += label_h
        self._logo_item = None
        if self._logo is not None:
            self._logo_item = cv.create_image(px(PAD), top + (header_h - px(LOGO)) // 2,
                                              image=self._logo, anchor="nw")
        # the close button, top right
        cb = px(CLOSE_BOX)
        self.close_box = (self.w - px(12) - cb, px(12), self.w - px(12), px(12) + cb)
        y = top + max(header_h, px(LOGO)) + px(20)
        cv.create_rectangle(px(PAD), y, self.w - px(PAD), y + max(1, px(1)) - 1,
                            outline="", fill=c["line"], tags="line")
        y += px(20)
        self.body_top = y
        cv.create_window(px(PAD), y, window=self.body, anchor="nw")
        y += self.body_h + px(FOOTER_GAP)
        # footer: the link on the left, the button on the right
        bw = self.f_button.measure("Got it") + 2 * px(BUTTON_PAD_X)
        bh = px(BUTTON_H)
        self.button_box = (self.w - px(PAD) - bw, y, self.w - px(PAD), y + bh)
        self._make_buttons()
        link_text = "Full release notes"
        lw = self.f_link.measure(link_text)
        gw = self.f_icon.measure(LINK_GLYPH) + px(6) if self.f_icon else 0
        mid = y + bh // 2
        self.link_box = (px(PAD) - px(6), mid - px(14), px(PAD) + lw + gw + px(6), mid + px(14))
        self.link_text = (px(PAD), mid, link_text, gw)
        self.h = y + bh + px(PAD)
        cv.configure(width=self.w, height=self.h)
        cv.pack(fill="both", expand=True)
        self._draw_chrome()

        # where: the middle of the work area, a little above the centre
        left, t, right, bottom = work
        x = left + (right - left - self.w) // 2
        yy = t + max(0, (bottom - t - self.h) * 2 // 5)
        self.x, self.y = x, yy
        geo = f"{self.w}x{self.h}+{x}+{yy}"
        self.catcher = None
        if sys.platform == "win32":
            self.catcher = self._toplevel(c["key"])
            self.catcher.attributes("-alpha", 0.005)
            self.catcher.geometry(geo)
        self.win.geometry(geo)
        for t_ in (self.catcher, self.win):
            if t_ is not None:
                t_.deiconify()
        self.win.update_idletasks()
        self._decorate()
        self._bind()
        self._bring_to_front()
        try:
            self.win.focus_force()
            if self.w32 is not None and self.hwnd:
                self.w32.activate(self.hwnd)
        except Exception:
            pass
        self._fade_start = time.perf_counter()
        self._fade_in()
        self.win.after(ACCENT_CHECK_MS, self._watch_accent)

    def _draw_body(self, width: int) -> int:
        """Draw the notes on the body canvas -> their height."""
        c, px, b = self.c, self.px, self.body
        y = 0
        first = True
        line = self.f_text.metrics("linespace")
        for kind, text in self.blocks:
            if kind == "heading":
                y += 0 if first else px(SECTION_GAP)
                tid = b.create_text(0, y, text=text, font=self.f_head, fill=c["text"],
                                    anchor="nw", width=width, tags="text")
                y = b.bbox(tid)[3] + px(8)
            elif kind == "item":
                b.create_text(px(4), y, text="•", font=self.f_text, fill=c["link"], anchor="nw",
                              tags="accent")
                tid = b.create_text(px(BULLET_W), y, text=text, font=self.f_text, fill=c["text"],
                                    anchor="nw", width=width - px(BULLET_W) - px(8), tags="text")
                y = b.bbox(tid)[3] + px(ITEM_GAP)
            else:
                # the summary: one sentence per line, the first one in the text colour
                y += 0 if first else px(4)
                for n, sentence in enumerate(sentences(text)):
                    role = "text" if first and n == 0 else "muted"
                    tid = b.create_text(0, y, text=sentence, font=self.f_text, fill=c[role],
                                        anchor="nw", width=width - px(8), tags=role)
                    y = b.bbox(tid)[3] + px(SENTENCE_GAP)
                y += px(12) - px(SENTENCE_GAP)
            first = False
        return max(line, y - px(ITEM_GAP))

    def _draw_chrome(self) -> None:
        """The parts that change on hover: the close button, the link, the button and
        the scroll indicator."""
        cv, c, px = self.cv, self.c, self.px
        cv.delete("chrome")
        # close: smooth and pushed in while held like "Got it", with the outline of the
        # Windows 11 taskbar buttons
        x0, y0, x1, y1 = self.close_box
        state = self._chip_state("close")
        self._draw_chip("close", self.close_box, state)
        shift = px(1) if state == "pressed" else 0
        glyph = CLOSE_GLYPH if self.f_icon else "✕"
        cv.create_text((x0 + x1) // 2, (y0 + y1) // 2 + shift, text=glyph,
                       font=self.f_icon or self.f_label,
                       fill=c["text"] if state == "hover" else c["muted"],
                       anchor="center", tags="chrome")
        # link: the same
        lx, mid, text, gw = self.link_text
        state = self._chip_state("link")
        self._draw_chip("link", self.link_box, state)
        shift = px(1) if state == "pressed" else 0
        cv.create_text(lx, mid + shift, text=text, font=self.f_link, fill=c["link"], anchor="w",
                       tags="chrome")
        if self.f_icon:
            cv.create_text(lx + self.f_link.measure(text) + px(6), mid + px(1) + shift,
                           text=LINK_GLYPH, font=self.f_icon, fill=c["link"], anchor="w",
                           tags="chrome")
        # button
        bx0, by0, bx1, by1 = self.button_box
        down = self.pressed == "button" and self.hover == "button"
        state = "pressed" if down else "hover" if self.hover == "button" else "normal"
        if self._buttons:
            cv.create_image(bx0, by0, image=self._buttons[state], anchor="nw", tags="chrome")
        else:
            fill = c["accent_hover"] if state == "hover" else c["accent"]
            self._tagged(lambda: flyout.rounded_rect(cv, bx0, by0, bx1, by1, px(BUTTON_RADIUS),
                                                     fill), True)
        shift = px(1) if down else 0
        tx, ty = (bx0 + bx1) // 2, (by0 + by1) // 2 + shift
        cv.create_text(tx, ty + px(1), text="Got it", font=self.f_button,
                       fill=c["emboss_down"] if down else c["emboss"], anchor="center", tags="chrome")
        cv.create_text(tx, ty, text="Got it", font=self.f_button,
                       fill=c["on_accent_down"] if down else c["on_accent"],
                       anchor="center", tags="chrome")
        self._thumb = self._track = None         # redrawn below
        self._draw_thumb()

    def _draw_thumb(self) -> None:
        """The scroll bar, as in Windows 11: a thin dim line at rest; under the mouse it
        grows wider and brighter over a faint track (animated by _bar_to). While
        scrolling only its position changes, so the items are moved, not drawn again."""
        if self.body_h_full <= self.body_h:
            return
        cv, c, px = self.cv, self.c, self.px
        top, h = thumb_geometry(self.body_h, self.body_h, self.body_h_full, self.scroll,
                                px(SCROLL_MIN))
        top += self.body_top
        wide_w = px(SCROLL_W_HOVER)
        q, tw = bar_look(self._bar_k, px(SCROLL_W), wide_w)
        cx = self.w - px(PAD) + px(PAD) // 2
        right = cx + wide_w // 2                 # the thin line hugs the right edge
        x = right - tw
        self.thumb_box = (cx - px(SCROLL_ZONE) // 2, top, cx + px(SCROLL_ZONE) // 2, top + h)
        look = (q, h)
        if self._thumb is not None and self._thumb[1] == look:
            cv.coords(self._thumb[0], x, top)
            return
        for item in (self._thumb, self._track):
            if item is not None:
                cv.delete(item[0])
        self._thumb = self._track = None
        if q > 0:                                # the track fades in under a wide thumb
            track_full = _rgb(c["track"])
            tcol = _hex(_mix(c["base_rgb"], track_full, q))
            tw_track = px(SCROLL_TRACK_W)
            img = self._pill(tw_track, self.body_h, tcol)
            if img is not None:
                item = cv.create_image(right - wide_w // 2 - tw_track // 2, self.body_top,
                                       image=img, anchor="nw", tags="chrome")
                self._track = (item, look)
        colour = _hex(_mix(_rgb(c["thumb"]), _rgb(c["thumb_hover"]), q))
        img = self._pill(tw, h, colour)
        if img is not None:
            item = cv.create_image(x, top, image=img, anchor="nw", tags="chrome")
        else:
            item = cv.create_rectangle(x, top, x + tw, top + h, outline="", fill=colour,
                                       tags="chrome")
        self._thumb = (item, look)

    def _bar_want(self, wide: bool) -> None:
        """The mouse came to the scroll bar (wide) or left it (thin, after a pause)."""
        if self._collapse_job is not None:
            self.win.after_cancel(self._collapse_job)
            self._collapse_job = None
        if wide:
            self._bar_to(1.0)
        else:
            self._collapse_job = self.win.after(SCROLL_COLLAPSE_MS, self._collapse)

    def _collapse(self) -> None:
        self._collapse_job = None
        if self.hover != "scroll" and self.thumb_drag is None:
            self._bar_to(0.0)

    def _bar_to(self, target: float) -> None:
        if self._bar_anim is not None and self._bar_anim[1] == target:
            return
        if self._bar_anim is None and self._bar_k == target:
            return
        self._bar_anim = (self._bar_k, target, time.perf_counter())
        if self._bar_job is None:
            self._bar_job = self.win.after(0, self._bar_frame)

    def _bar_frame(self) -> None:
        self._bar_job = None
        if self._bar_anim is None or self._closing:
            return
        start, target, t0 = self._bar_anim
        t = (time.perf_counter() - t0) * 1000 / SCROLL_ANIM_MS
        self._bar_k = start + (target - start) * smoothstep(t)
        if t >= 1.0:
            self._bar_k, self._bar_anim = target, None
        try:
            self._draw_thumb()
        except Exception:
            return
        if self._bar_anim is not None:
            self._bar_job = self.win.after(10, self._bar_frame)

    def _chip_state(self, part: str) -> str:
        if self.hover != part:
            return "normal"
        return "pressed" if self.pressed == part else "hover"

    def _draw_chip(self, part: str, box, state: str) -> None:
        """The background of the close button or the link: nothing at rest, a smooth
        rounded patch with a thin outline under the mouse (chip_image), smaller and
        quieter while pressed."""
        if state == "normal":
            return
        key = (part, state)
        if key not in self._chips:
            x0, y0, x1, y1 = box
            try:
                from PIL import ImageTk
                img = chip_image(x1 - x0, y1 - y0, self.px(CHIP_RADIUS), _rgb(self.c["hover"]),
                                 state, self.light, self.scale)
                self._chips[key] = ImageTk.PhotoImage(
                    flatten(img, self.c["base_rgb"], _rgb(self.c["key"]), cutoff=40),
                    master=self.win)
            except Exception as e:
                log.info("what's new: plain hover (%s)", e)
                self._chips[key] = None
        img = self._chips[key]
        if img is not None:
            self.cv.create_image(box[0], box[1], image=img, anchor="nw", tags="chrome")
        else:
            self._tagged(lambda: flyout.rounded_rect(self.cv, *box, self.px(CHIP_RADIUS),
                                                     self.c["hover"]), True)

    def _make_buttons(self, only: Optional[str] = None) -> None:
        """The pictures of "Got it" (normal, hover, pressed) in the current colours;
        only: just that one (each frame of a theme change)."""
        c, px = self.c, self.px
        bx0, by0, bx1, by1 = self.button_box
        if only is None or not getattr(self, "_buttons", None):
            self._buttons = {}
        try:
            from PIL import ImageTk
            for state, fill in (("normal", c["accent"]), ("hover", c["accent_hover"]),
                                ("pressed", c["accent"])):
                if only is not None and state != only:
                    continue
                img = button_image(bx1 - bx0, by1 - by0, px(BUTTON_RADIUS), _rgb(fill), state,
                                   self.scale)
                self._buttons[state] = ImageTk.PhotoImage(
                    flatten(img, c["base_rgb"], _rgb(c["key"]), cutoff=40), master=self.win)
        except Exception as e:
            log.info("what's new: plain button (%s)", e)

    def _watch_accent(self) -> None:
        """While open: follow a new accent colour at once (a new wallpaper with the
        automatic accent, or one picked in Settings)."""
        if self._closing:
            return
        try:
            if self._theme_move is not None:     # a theme change is still flowing
                self.win.after(ACCENT_CHECK_MS, self._watch_accent)
                return
            light = flyout.apps_use_light_theme()
            if light != self.light:
                self.theme_transition(light)
                self.win.after(ACCENT_CHECK_MS, self._watch_accent)
                return
            accent = windows_accent_palette()
            if accent != self.accent:
                self.accent = accent
                self.recolour()
        except Exception as e:
            log.info("what's new: accent colour: %s", e)
        self.win.after(ACCENT_CHECK_MS, self._watch_accent)

    def theme_transition(self, light: bool) -> None:
        """Windows switched light <-> dark: the same window changes, every colour
        flowing from the old theme to the new one - the blurred background's tint, the
        text, the line, the button, the logo's edge."""
        self._theme_move = (self.c, colours(light, self.accent), light, time.perf_counter(), False)
        self._theme_frame()

    def _theme_frame(self) -> None:
        if self._closing or self._theme_move is None:
            return
        start, end, light, t0, switched = self._theme_move
        t = (time.perf_counter() - t0) * 1000 / THEME_TRANSITION_MS
        done = t >= 1.0
        k = 1.0 if done else smoothstep(t)
        try:
            self.c = end if done else blend_colours(start, end, k)
            if self.w32 is not None and self.hwnd:
                self.w32.acrylic(self.hwnd, self.c["tint_abgr"])
            if not switched and k >= 0.5:
                # half way: the colour key and the frame move to the new theme at once
                switched = True
                self.light = light
                key = end["key"]
                for widget in (self.win, self.cv, self.body):
                    widget.configure(bg=key)
                flyout._attr(self.win, "-transparentcolor", key)
                if self.w32 is not None and self.hwnd:
                    self.w32.dwm_int(self.hwnd, 20, 0 if light else 1)
                self._chips = {}
            self._theme_move = (start, end, light, t0, switched)
            self._paint_roles()
            self._make_logo()
            self._pills = {}
            if done:
                self._make_buttons()
            else:
                down = self.pressed == "button" and self.hover == "button"
                self._make_buttons("pressed" if down else
                                   "hover" if self.hover == "button" else "normal")
            self._draw_chrome()
        except Exception as e:
            log.info("what's new: theme change: %s", e)
            self._theme_move = None
            return
        if done:
            self._theme_move = None
        else:
            self.win.after(15, self._theme_frame)

    def _paint_roles(self) -> None:
        """Every drawn item in the colour of its role (its tag) in self.c."""
        for canvas in (self.cv, self.body):
            for role, key in (("text", "text"), ("muted", "muted"), ("accent", "link"),
                              ("line", "line")):
                canvas.itemconfigure(role, fill=self.c[key])
        self.cv.itemconfigure("border", outline=self.c["border"])

    def _make_logo(self) -> None:
        if self._logo_item is None:
            return
        from PIL import ImageTk
        self._logo = ImageTk.PhotoImage(
            app_logo(self.px(LOGO), self.c["base_rgb"], _rgb(self.c["key"])), master=self.win)
        self.cv.itemconfigure(self._logo_item, image=self._logo)

    def recolour(self) -> None:
        self.c = colours(self.light, self.accent)
        self._make_buttons()
        self._pills = {}
        self._chips = {}
        for canvas in (self.cv, self.body):
            canvas.itemconfigure("accent", fill=self.c["link"])
        self._draw_chrome()

    def _pill(self, w: int, h: int, colour: str):
        key = (w, h, colour)
        if key not in self._pills:
            try:
                from PIL import ImageTk
                img = flatten(pill_image(w, h, _rgb(colour)), self.c["base_rgb"],
                              _rgb(self.c["key"]), cutoff=40)
                self._pills[key] = ImageTk.PhotoImage(img, master=self.win)
            except Exception as e:
                log.info("what's new: plain scroll bar (%s)", e)
                self._pills[key] = None
        return self._pills[key]

    def _tagged(self, draw, when: bool) -> None:
        """Run a drawing call and tag what it drew as "chrome" (redrawn on hover)."""
        if not when:
            return
        before = set(self.cv.find_all())
        draw()
        for item in set(self.cv.find_all()) - before:
            self.cv.addtag_withtag("chrome", item)

    def _set_pos(self, hwnd: int, after: int) -> None:
        """Move a window in the z-order only (no move, no resize, no activation)."""
        try:
            # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            self.w32.user32.SetWindowPos(hwnd, after, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass

    def _bring_to_front(self) -> None:
        """In front of every window when it opens, an ordinary window after that (other
        windows can cover it). Made topmost and at once not topmost again, which leaves it
        at the top of the normal windows even when Windows does not let a background app
        take the focus. The catcher goes right under it."""
        if self.w32 is None or not self.hwnd:
            return
        self._set_pos(self.hwnd, -1)                 # HWND_TOPMOST
        self._set_pos(self.hwnd, -2)                 # HWND_NOTOPMOST
        self._restack()

    def _restack(self, _event=None) -> None:
        """Keep the invisible catcher directly under the window (a click on either one
        brings it up alone, and the window's see-through pixels would then pass clicks
        to whatever is below)."""
        if self.w32 is None or not self.hwnd or not self.catcher_hwnd:
            return
        self._set_pos(self.hwnd, 0)                  # HWND_TOP: above the catcher
        self._set_pos(self.catcher_hwnd, self.hwnd)  # the catcher right under it

    def _decorate(self) -> None:
        w32 = self.w32
        self.hwnd = self.catcher_hwnd = 0
        try:
            self.hwnd = int(self.win.wm_frame(), 16)
        except Exception:
            pass
        catcher_hwnd = 0
        if self.catcher is not None:
            try:
                catcher_hwnd = int(self.catcher.wm_frame(), 16)
            except Exception:
                pass
        self.catcher_hwnd = catcher_hwnd
        if w32 is None or not self.hwnd:
            return
        if not w32.acrylic(self.hwnd, self.c["tint_abgr"]):
            log.info("what's new: no acrylic on this Windows")
        w32.dwm_int(self.hwnd, 33, 2)                       # rounded corners (Windows 11)
        if not self.light:
            w32.dwm_int(self.hwnd, 20, 1)                   # dark frame
        if catcher_hwnd:
            w32.dwm_int(catcher_hwnd, 33, 1)
        if not flyout.is_windows_11():                      # Windows 10: a thin frame
            self.cv.create_rectangle(0, 0, self.w - 1, self.h - 1, outline=self.c["border"],
                                     tags="border")

    # ---------------- input
    def _bind(self) -> None:
        # only the two windows: an event in a canvas reaches its window's binding as well
        # (the window is one of the canvas' bindtags), so binding both would run it twice
        widgets = [self.win] + ([self.catcher] if self.catcher else [])
        for wdg in widgets:
            wdg.bind("<Motion>", self._motion, add="+")
            wdg.bind("<ButtonPress-1>", self._press, add="+")
            wdg.bind("<ButtonPress-1>", self._restack, add="+")
            wdg.bind("<FocusIn>", self._restack, add="+")
            wdg.bind("<B1-Motion>", self._drag_to, add="+")
            wdg.bind("<ButtonRelease-1>", self._release, add="+")
            wdg.bind("<MouseWheel>", self._wheel, add="+")
            wdg.bind("<Leave>", self._leave, add="+")
        for t in (self.win,) + ((self.catcher,) if self.catcher else ()):
            t.bind("<Escape>", lambda e: self.close(), add="+")
            t.bind("<Return>", lambda e: self._press_key(), add="+")
            t.bind("<KP_Enter>", lambda e: self._press_key(), add="+")

    def _leave(self, _event=None) -> None:
        """The mouse left one of the two windows. That also happens without the mouse
        moving: the see-through pixels pass it to the catcher underneath, so when a hover
        background appears under the mouse it goes from the catcher to the window, and
        when it goes away, back. Only a mouse that is really outside counts, or the close
        button's background flickered on and off."""
        if self.thumb_drag is not None:
            return
        try:
            x, y = self.root.winfo_pointerxy()
        except Exception:
            x = y = -1
        if pointer_inside(x, y, self.x, self.y, self.w, self.h):
            return
        self._set_hover(None)

    def _local(self, event) -> Tuple[int, int]:
        return event.x_root - self.x, event.y_root - self.y

    def _hit(self, x: int, y: int) -> Optional[str]:
        for name, box in (("close", self.close_box), ("link", self.link_box),
                          ("button", self.button_box)):
            x0, y0, x1, y1 = box
            if x0 <= x < x1 and y0 <= y < y1:
                return name
        if self.body_h_full > self.body_h:
            cx = self.w - self.px(PAD) + self.px(PAD) // 2
            half = self.px(SCROLL_ZONE) // 2
            if cx - half <= x < cx + half and self.body_top <= y < self.body_top + self.body_h:
                return "scroll"
        return None

    def _set_hover(self, part: Optional[str]) -> None:
        if part != self.hover:
            if part == "scroll" or self.hover == "scroll":
                self._bar_want(part == "scroll")
            self.hover = part
            cursor = "hand2" if part in ("link", "button") else ""
            for wdg in (self.cv, self.body, self.catcher):
                if wdg is not None:
                    try:
                        wdg.configure(cursor=cursor)
                    except Exception:
                        pass
            self._draw_chrome()

    def _motion(self, event) -> None:
        if self.drag is None and self.thumb_drag is None:
            self._set_hover(self._hit(*self._local(event)))

    def _press(self, event) -> None:
        x, y = self._local(event)
        part = self._hit(x, y)
        if part is None:
            self.drag = (event.x_root, event.y_root, self.x, self.y)
            return
        if part == "scroll":
            x0, y0, x1, y1 = self.thumb_box
            if y0 <= y < y1:                         # on the thumb: drag it
                self.set_scroll(self._scroll_pos)    # stop a glide where it is
                self.thumb_drag = (event.y_root, self.scroll)
            else:                                    # beside it: a page up or down
                page = self.body_h - self.px(WHEEL_STEP)
                self.set_scroll(self.scroll_target + (page if y >= y1 else -page), smooth=True)
            self._set_hover("scroll")
            self._draw_chrome()
            return
        self.pressed = part
        self.hover = part
        self._draw_chrome()

    def set_scroll(self, value: float, smooth: bool = False) -> None:
        """Scroll the notes to value (pixels from the top). smooth: glide there."""
        span = max(0, self.body_h_full - self.body_h)
        self.scroll_target = max(0.0, min(float(span), float(value)))
        if smooth:
            if self._scroll_job is None:
                self._fine_timer(True)
                self._glide_t = 0.0
                self._scroll_job = self.win.after(0, self._glide)
            return
        if self._scroll_job is not None:
            self.win.after_cancel(self._scroll_job)
            self._scroll_job = None
            self._fine_timer(False)
        self._move_to(self.scroll_target)

    def _move_to(self, pos: float) -> None:
        self._scroll_pos = pos
        self.scroll = int(round(pos))
        self.body.yview_moveto(pos / max(1, self.body_h_full))
        self._draw_thumb()                       # only the thumb moves: no full redraw

    def _glide(self) -> None:
        """One frame of a smooth scroll: an ease-out towards scroll_target."""
        self._scroll_job = None
        now = time.perf_counter()
        dt = (now - self._glide_t) * 1000 if self._glide_t else FRAME_60
        self._glide_t = now
        pos, target = scroll_step(self._scroll_pos, self.scroll_target, ease_for(dt))
        try:
            self._move_to(pos)
        except Exception:
            self._fine_timer(False)
            return                                   # the window is gone
        if pos != target:
            self._scroll_job = self.win.after(SCROLL_FRAME_MS, self._glide)
        else:
            self._glide_t = 0.0
            self._fine_timer(False)

    def _fine_timer(self, on: bool) -> None:
        """Windows' timer ticks every 15.6 ms by default, so the frames of a glide came
        unevenly (one at 16 ms, the next at 31). While gliding it is asked for 1 ms."""
        if on == self._hires or sys.platform != "win32":
            return
        try:
            import ctypes
            winmm = ctypes.windll.winmm
            (winmm.timeBeginPeriod if on else winmm.timeEndPeriod)(1)
            self._hires = on
        except Exception:
            pass

    def _drag_to(self, event) -> None:
        if self.thumb_drag is not None:
            y_start, scroll_start = self.thumb_drag
            _top, h = thumb_geometry(self.body_h, self.body_h, self.body_h_full, 0,
                                     self.px(SCROLL_MIN))
            room = max(1, self.body_h - h)                   # how far the thumb can travel
            span = self.body_h_full - self.body_h
            self.set_scroll(scroll_start + (event.y_root - y_start) * span / room)
            return
        if self.drag is None:
            if self.pressed is not None:             # held down: pressed only while over it
                self._set_hover(self._hit(*self._local(event)))
            return
        sx, sy, wx, wy = self.drag
        self.x, self.y = wx + event.x_root - sx, wy + event.y_root - sy
        geo = f"+{self.x}+{self.y}"
        for t in (self.catcher, self.win):
            if t is not None:
                t.geometry(geo)

    def _release(self, event) -> None:
        if self.thumb_drag is not None:
            self.thumb_drag = None
            self._set_hover(self._hit(*self._local(event)))
            self._draw_chrome()
            return
        if self.drag is not None:
            self.drag = None
            return
        part = self._hit(*self._local(event))
        pressed, self.pressed = self.pressed, None
        self._draw_chrome()
        if part != pressed:                          # let go somewhere else: nothing
            return
        if part == "link":
            self._open_link()
        elif part in ("button", "close"):
            self.close()

    def _wheel(self, event) -> None:
        if self.body_h_full <= self.body_h:
            return
        # a precision touchpad sends small deltas, a wheel notch 120
        step = self.px(WHEEL_STEP) * -event.delta / 120
        self.set_scroll(self.scroll_target + step, smooth=True)

    def _press_key(self) -> None:
        """Enter: the button goes down for a moment, then the window closes."""
        self.pressed = self.hover = "button"
        self._draw_chrome()
        self.win.after(90, self.close)

    def _open_link(self) -> None:
        if self.open_url is not None:
            threading.Thread(target=self.open_url, args=(self.url,), daemon=True).start()

    # ---------------- showing and closing
    def _fade_in(self) -> None:
        k = min(1.0, (time.perf_counter() - self._fade_start) * 1000 / FADE_MS)
        try:
            self.win.attributes("-alpha", k)
        except Exception:
            return
        if k < 1.0:
            self.win.after(15, self._fade_in)

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        start = time.perf_counter()

        def step():
            k = 1.0 - min(1.0, (time.perf_counter() - start) * 1000 / (FADE_MS * 0.7))
            try:
                self.win.attributes("-alpha", max(0.0, k))
            except Exception:
                k = 0.0
            if k > 0:
                self.win.after(15, step)
            else:
                self.destroy()

        step()

    def destroy(self, notify: bool = True) -> None:
        self._fine_timer(False)
        for t in (self.win, self.catcher):
            if t is not None:
                try:
                    t.destroy()
                except Exception:
                    pass
        if self in _open:
            _open.remove(self)
        if notify:                               # not when a new window takes its place
            self.closed.set()
