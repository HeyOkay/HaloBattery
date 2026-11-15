"""The approved application artwork shared by branded app surfaces."""
from functools import lru_cache
from pathlib import Path

from PIL import Image


@lru_cache(maxsize=1)
def _source() -> Image.Image:
    # PyInstaller places this module and the bundled assets under _internal.
    path = Path(__file__).resolve().parent.parent / "assets" / "app-icon.png"
    with Image.open(path) as image:
        return image.convert("RGBA")


@lru_cache(maxsize=16)
def _resized(size: int) -> Image.Image:
    source = _source()
    if source.size == (size, size):
        return source
    return source.resize((size, size), Image.Resampling.LANCZOS)


def render(size: int) -> Image.Image:
    """Return a new square RGBA image; callers may safely modify their copy."""
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("icon size must be a positive integer")
    return _resized(size).copy()
