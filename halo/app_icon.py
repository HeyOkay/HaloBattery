"""The approved application artwork shared by branded app surfaces."""
from functools import lru_cache
from pathlib import Path

from PIL import Image


def _open(name: str) -> Image.Image:
    # PyInstaller places this module and the bundled assets under _internal.
    path = Path(__file__).resolve().parent.parent / "assets" / name
    with Image.open(path) as image:
        return image.convert("RGBA")


# Only the sizes in use are cached. The source is read again for each new size instead of
# being kept: the 1024 px artwork held 4 MB of RAM for the app's whole run, while the app
# asks for a handful of small sizes, each once.
@lru_cache(maxsize=16)
def _resized(size: int) -> Image.Image:
    source = _open("app-icon.png")
    if source.size == (size, size):
        return source
    return source.resize((size, size), Image.Resampling.LANCZOS)


def render(size: int) -> Image.Image:
    """Return a new square RGBA image; callers may safely modify their copy."""
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("icon size must be a positive integer")
    return _resized(size).copy()


@lru_cache(maxsize=16)
def _dark_header_resized(size: int) -> Image.Image:
    # Rasterized from app-icon-header-dark.svg; Settings uses that same SVG.
    return _open("app-icon-header-dark.png").resize((size, size), Image.Resampling.LANCZOS)


def render_header(size: int, light: bool) -> Image.Image:
    """The theme-specific header artwork; the regular app icon stays unchanged."""
    if light:
        return render(size)
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("icon size must be a positive integer")
    return _dark_header_resized(size).copy()
