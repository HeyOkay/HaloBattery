"""Outfit application wordmark, using the same static faces as native Settings.

Fonts stay in the existing native payload in packaged builds; no duplicate font
files or system-wide font installation is needed. Their SIL OFL notices ship there.
"""
from functools import lru_cache
from pathlib import Path
import colorsys
import math

from PIL import Image, ImageDraw, ImageFont


def accent_colour(rgb, light):
    h, luminance, saturation = colorsys.rgb_to_hls(*(value / 255 for value in rgb))
    if not light:
        luminance = .76
    return tuple(round(value * 255) for value in
                 colorsys.hls_to_rgb(h, luminance, saturation * .8))


def _font_path(name):
    root = Path(__file__).resolve().parent.parent
    for directory in (root / "assets" / "fonts", root / "settings-ui" / "Assets" / "Fonts"):
        path = directory / name
        if path.is_file():
            return path
    raise FileNotFoundError("Bundled Outfit wordmark font is unavailable: " + name)


@lru_cache(maxsize=8)
def _masks(size):
    ss = 4
    tracking = -.012 * size * ss  # MainWindow's CharacterSpacing=-12 (1/1000 em).
    glyphs = []
    cursor = 0.0
    for index, (word, filename) in enumerate((("Halo", "Outfit-Medium.ttf"),
                                               ("Battery", "Outfit-SemiBold.ttf"))):
        font = ImageFont.truetype(str(_font_path(filename)), size * ss)
        if index == 0:
            cap_box = font.getbbox("H", anchor="ls")
        for offset, char in enumerate(word):
            x = cursor + font.getlength(word[:offset + 1]) - font.getlength(char) + offset * tracking
            glyphs.append((index, font, char, x, font.getbbox(char, anchor="ls")))
        cursor += font.getlength(word) + len(word) * tracking
    left = math.floor(min(x + box[0] for _, _, _, x, box in glyphs))
    top = min(box[1] for _, _, _, _, box in glyphs)
    right = math.ceil(max(x + box[2] for _, _, _, x, box in glyphs))
    bottom = max(box[3] for _, _, _, _, box in glyphs)
    padding = ss
    width, height = right - left + 2 * padding, bottom - top + 2 * padding
    masks = [Image.new("L", (width, height), 0) for _ in range(2)]
    painters = [ImageDraw.Draw(mask) for mask in masks]
    for index, font, char, x, _ in glyphs:
        painters[index].text((x - left + padding, padding - top), char,
                             font=font, fill=255, anchor="ls")
    output_size = (math.ceil(width / ss), math.ceil(height / ss))
    # Settings centres the ring slightly above the visible capital-letter centre.
    # The native 20 px reference places it 1 px above the H (0.05 em).
    icon_center = (padding - top + (cap_box[1] + cap_box[3]) / 2) / ss - size * .05
    return (tuple(mask.resize(output_size, Image.Resampling.LANCZOS) for mask in masks),
            (padding - top) / ss, icon_center)


def wordmark(size, foreground, accent):
    """Transparent artwork and baseline in physical pixels; never modify cached masks."""
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("wordmark size must be a positive integer")
    masks, baseline, _ = _masks(size)
    picture = Image.new("RGBA", masks[0].size)
    for mask, colour in zip(masks, (foreground, accent)):
        layer = Image.new("RGBA", picture.size, colour)
        layer.putalpha(mask)
        picture.alpha_composite(layer)
    return picture, baseline


def icon_center(size):
    """Vertical icon centre in wordmark pixels, matching the native Settings header."""
    return _masks(size)[2]
