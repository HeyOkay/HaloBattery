"""Generate the README previews: docs/icons.png and docs/charging.gif.

Both are drawn with icons.render(), the same code as the tray icons, so run
this again whenever a pictogram or the ring changes.

Usage: python tools/make_previews.py [output folder, default docs]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from halo import icons  # noqa: E402

DARK_BG = (32, 32, 32)
LIGHT_BG = (243, 243, 243)

ROWS = [("Headset", "headset"), ("Mouse", "mouse"), ("Keyboard", "keyboard"),
        ("Xbox controller", "gamepad"), ("PS controller", "dualshock"),
        ("Bluetooth", "bluetooth")]
# (header, level, charging, online)
COLUMNS = [("100%", 100, False, True), ("75%", 75, False, True), ("50%", 50, False, True),
           ("30%", 30, False, True), ("15%", 15, False, True), ("Charging", 60, True, True),
           ("Asleep", None, False, False)]

BIG, SMALL = 64, 24            # a large icon, and the tray size at 150% scaling
X0, PITCH = 134, 92            # left edge of the first large icon, column pitch
TOP, ROW_H = 64, 104           # first large icon, row pitch


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (["segoeuib.ttf", "Roboto-Medium.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"]
             if bold else
             ["segoeui.ttf", "Roboto-Regular.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"])
    folders = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
               "/usr/share/fonts/truetype/liberation", "/usr/share/fonts/truetype/dejavu", ""]
    for name in names:
        for folder in folders:
            try:
                return ImageFont.truetype(os.path.join(folder, name), size)
            except OSError:
                pass
    return ImageFont.load_default()


def _icon(level, charging, online, light, badge, size):
    return icons.render(level, charging, online, 20, light_taskbar=light,
                        badge=badge).resize((size, size), Image.LANCZOS)


def _section(title: str, light: bool) -> Image.Image:
    bg = LIGHT_BG if light else DARK_BG
    fg = (0, 0, 0) if light else (255, 255, 255)
    grey = (110, 110, 110) if light else (170, 170, 170)
    w = X0 + PITCH * (len(COLUMNS) - 1) + BIG + 30
    h = TOP + ROW_H * len(ROWS) - 2
    img = Image.new("RGBA", (w, h), bg + (255,))
    d = ImageDraw.Draw(img)
    d.text((16, 20), title, font=_font(16, True), fill=fg, anchor="lm")
    small = _font(12)
    for c, (head, *_rest) in enumerate(COLUMNS):
        d.text((X0 + c * PITCH + BIG / 2, 47), head, font=small, fill=grey, anchor="mm")
    label = _font(15)
    for r, (name, badge) in enumerate(ROWS):
        y = TOP + r * ROW_H
        d.text((16, y + BIG / 2), name, font=label, fill=fg, anchor="lm")
        for c, (_h, level, charging, online) in enumerate(COLUMNS):
            x = X0 + c * PITCH
            img.alpha_composite(_icon(level, charging, online, light, badge, BIG), (x, y))
            img.alpha_composite(_icon(level, charging, online, light, badge, SMALL),
                                (x + (BIG - SMALL) // 2, y + BIG + 6))
    return img


def icon_sheet() -> Image.Image:
    dark, light = _section("Dark taskbar", False), _section("Light taskbar", True)
    out = Image.new("RGB", (dark.width, dark.height + light.height))
    out.paste(dark.convert("RGB"), (0, 0))
    out.paste(light.convert("RGB"), (0, dark.height))
    return out


# (label, badge, level) of the charging animation
ANIM = [("Headset", "headset", 40), ("Keyboard", "keyboard", 8), ("Controller", "gamepad", 75)]


def charging_gif(path: str) -> None:
    cell, w, h = 130, 400, 150
    font = _font(13)
    frames = []
    for i in range(icons.BREATH_FRAMES):
        pulse = icons.breath_level(i / icons.BREATH_FRAMES)
        img = Image.new("RGBA", (w, h), DARK_BG + (255,))
        d = ImageDraw.Draw(img)
        for k, (name, badge, level) in enumerate(ANIM):
            cx = (w - cell * len(ANIM)) // 2 + k * cell + cell // 2
            big = icons.render(level, True, True, 20, False, badge, pulse).resize((90, 90), Image.LANCZOS)
            small = icons.render(level, True, True, 20, False, badge, pulse).resize((SMALL, SMALL), Image.LANCZOS)
            img.alpha_composite(big, (cx - 45, 5))
            img.alpha_composite(small, (cx - SMALL // 2, 100))
            d.text((cx, 138), f"{name} {level}%", font=font, fill=(230, 230, 230), anchor="mm")
        frames.append(img.convert("RGB").quantize(colors=128, method=Image.MEDIANCUT, dither=Image.NONE))
    duration = int(icons.BREATH_PERIOD * 1000 / icons.BREATH_FRAMES)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration, loop=0,
                   optimize=True, disposal=1)


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(HERE), "docs")
    os.makedirs(out, exist_ok=True)
    icon_sheet().save(os.path.join(out, "icons.png"), optimize=True)
    charging_gif(os.path.join(out, "charging.gif"))
    print("written:", os.path.join(out, "icons.png"), os.path.join(out, "charging.gif"))


if __name__ == "__main__":
    main()
