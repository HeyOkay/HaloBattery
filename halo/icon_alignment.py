"""Bounded optical translation for generic SVG glyphs, without resampling.

The persisted master remains geometrically centred. Placement uses alpha mass
only, so mixed theme variants share exactly the same integer translation.
"""
from __future__ import annotations

import math
from PIL import Image

PLACEMENT_VERSION = 'optical-translation-v1'
_DEADZONE = .1 / 16
_MAX_SHIFT = .75 / 16


def offset(alpha: Image.Image, *, radius_fraction: float = 5.9 / 16) -> tuple[int, int]:
    """Return a safe integer optical shift, or zero when translation cannot fit.

    All nonzero alpha counts as painted. For each row, its two endpoints suffice
    to constrain the entire row to a disk because squared distance is convex.
    Intersect the complete quadratic intervals: small previews can start outside
    the disk while a later point on the translation ray fits inside it.
    """
    if not isinstance(alpha, Image.Image) or alpha.mode != 'L':
        raise ValueError('Optical placement requires an L alpha image')
    if (isinstance(radius_fraction, bool) or not isinstance(radius_fraction, (int, float))
            or not math.isfinite(radius_fraction) or radius_fraction <= 0):
        raise ValueError('Optical placement radius must be positive and finite')
    width, height = alpha.size
    unit = min(width, height)
    if not unit:
        return 0, 0
    raw = alpha.tobytes()
    # Row/column sums run over bytes in C. Avoid a Python loop per pixel when
    # a catalogue page aligns many previews, or a 1024px master is resized.
    columns = alpha.transpose(Image.Transpose.TRANSPOSE).tobytes()
    x_mass = sum(x * sum(columns[x * height:(x + 1) * height]) for x in range(width))
    mass = y_mass = 0
    endpoints = []
    for y in range(height):
        row = raw[y * width:(y + 1) * width]
        row_mass = sum(row)
        if not row_mass:
            continue
        mass += row_mass
        y_mass += y * row_mass
        left = width - len(row.lstrip(b'\0'))
        right = len(row.rstrip(b'\0')) - 1
        endpoints.extend(((left + .5 - width / 2, y + .5 - height / 2),
                          (right + .5 - width / 2, y + .5 - height / 2)))
    if not mass:
        return 0, 0
    desired = [width / 2 - (x_mass / mass + .5),
               height / 2 - (y_mass / mass + .5)]
    for axis, value in enumerate(desired):
        desired[axis] = (0. if abs(value) <= _DEADZONE * unit else
                         max(-_MAX_SHIFT * unit, min(_MAX_SHIFT * unit, value)))
    dx, dy = desired
    quadratic = dx * dx + dy * dy
    if not quadratic:
        return 0, 0
    radius = radius_fraction * unit
    squared_radius = radius * radius
    low, high = 0., 1.
    for x, y in endpoints:
        linear = 2 * (x * dx + y * dy)
        constant = x * x + y * y - squared_radius
        discriminant = linear * linear - 4 * quadratic * constant
        if discriminant < 0:
            return 0, 0
        root = math.sqrt(discriminant)
        low = max(low, (-linear - root) / (2 * quadratic))
        high = min(high, (-linear + root) / (2 * quadratic))
        if low > high:
            return 0, 0
    target = dx * high, dy * high
    candidates = {(x, y) for x in (math.floor(target[0]), math.ceil(target[0]))
                  for y in (math.floor(target[1]), math.ceil(target[1]))}
    # Test snapped coordinates independently: rounding a safe continuous shift
    # can push a corner back across the boundary. Zero remains the fallback.
    candidates.discard((0, 0))
    candidates = sorted(candidates, key=lambda p: ((p[0] - target[0]) ** 2 +
                                                 (p[1] - target[1]) ** 2, p))
    cap = _MAX_SHIFT * unit
    for candidate in candidates:
        sx, sy = candidate
        if abs(sx) > cap or abs(sy) > cap:
            continue
        if all((x + sx) ** 2 + (y + sy) ** 2 <= squared_radius
               and 0 <= x + width / 2 + sx < width
               and 0 <= y + height / 2 + sy < height for x, y in endpoints):
            return candidate
    return 0, 0


def place(image: Image.Image, *, radius_fraction: float = 5.9 / 16) -> Image.Image:
    """Translate L/RGBA pixels into an equal canvas; balanced glyphs are unchanged."""
    if not isinstance(image, Image.Image) or image.mode not in ('L', 'RGBA'):
        raise ValueError('Optical placement requires an L or RGBA image')
    alpha = image.getchannel('A') if image.mode == 'RGBA' else image
    shift = offset(alpha, radius_fraction=radius_fraction)
    if shift == (0, 0):
        return image
    result = Image.new(image.mode, image.size)
    result.paste(image, shift)
    return result
