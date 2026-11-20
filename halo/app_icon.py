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


@lru_cache(maxsize=1)
def _dark_header_source() -> Image.Image:
    # Rasterized from app-icon-header-dark.svg; Settings uses that same SVG.
    path = Path(__file__).resolve().parent.parent / "assets" / "app-icon-header-dark.png"
    with Image.open(path) as image:
        return image.convert("RGBA")


@lru_cache(maxsize=16)
def _dark_header_resized(size: int) -> Image.Image:
    return _dark_header_source().resize((size, size), Image.Resampling.LANCZOS)


def render_header(size: int, light: bool) -> Image.Image:
    """The theme-specific header artwork; the regular app icon stays unchanged."""
    if light:
        return render(size)
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("icon size must be a positive integer")
    return _dark_header_resized(size).copy()
