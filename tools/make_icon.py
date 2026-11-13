"""Generate the EXE icon from the approved transparent application artwork.

The PNG is rendered from assets/app-icon.svg and checked in so regular builds
need only Pillow. Device tray icons continue to use their own renderer.

Usage: python tools/make_icon.py [output.ico]
       python tools/make_icon.py docs/logo.png   (256 px)
"""
from pathlib import Path
import sys

from PIL import Image

ARTWORK = Path(__file__).resolve().parents[1] / "assets" / "app-icon.png"
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def app_icon(size: int = 256) -> Image.Image:
    with Image.open(ARTWORK) as source:
        image = source.convert("RGBA")
    return image.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else "halo.ico"
    if out.lower().endswith(".png"):
        app_icon(256).save(out)
    else:
        app_icon(256).save(out, sizes=[(s, s) for s in SIZES])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
