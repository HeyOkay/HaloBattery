"""Generate halo.ico, the icon of HaloBattery.exe.

A green charge ring on a dark disc, so it reads well on both light and dark
Explorer backgrounds. Rendered with the same code as the tray icons.

Usage: python tools/make_icon.py [output.ico]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image, ImageDraw  # noqa: E402

import icons  # noqa: E402

SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def app_icon(size: int = 256) -> Image.Image:
    ss = 4
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((0, 0, big - 1, big - 1), fill=(32, 32, 32, 255))
    ring = icons.render(75, True, True, 20, light_taskbar=False, badge="")
    ring = ring.resize((int(big * 0.84),) * 2, Image.LANCZOS)
    off = (big - ring.width) // 2
    img.alpha_composite(ring, (off, off))
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "halo.ico"
    app_icon(256).save(out, sizes=[(s, s) for s in SIZES])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
