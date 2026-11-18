"""Exact offline rendering profiles for the app's existing Lucide glyphs.

Names are the explicit inverse of artwork/tray/manifest.json sources.lucide.glyphs
(lucide-static 1.17.0). This module does no source verification: the catalog must
call it only after verifying the Iconify name, source hash and license metadata.
Other collections and names retain the general real-ink SVG fitting policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math

from . import icon_artwork
from .svg_glyph import SvgGlyphError

_PROFILE_VERSION = 'builtin-lucide-profile-v1'
_BUILTIN_PROFILES = {
    'lucide:headphones': 'headset',
    'lucide:mouse': 'mouse',
    'lucide:keyboard': 'keyboard',
    'lucide:gamepad-2': 'gamepad',
    'lucide:bluetooth': 'bluetooth',
    'lucide:mic': 'microphone',
    'lucide:speaker': 'speaker',
    'lucide:webcam': 'webcam',
    'lucide:heart-pulse': 'health',
    'lucide:house': 'smart_home',
    'lucide:watch': 'watch',
    'lucide:radar': 'tracker',
    'lucide:tv-minimal': 'display',
}


@dataclass(frozen=True)
class _BuiltinProfile:
    """Keep the verified normalized PNG, but reuse exact reviewed tray pixels."""
    _glyph: object = field(repr=False)
    _kind: str
    digest: str = field(init=False)

    def __post_init__(self):
        identity = '\0'.join((_PROFILE_VERSION, self._kind, self._glyph.digest))
        object.__setattr__(self, 'digest', hashlib.sha256(identity.encode('utf-8')).hexdigest())

    @property
    def png_bytes(self):
        return self._glyph.png_bytes

    @property
    def uses_builtin_profile(self):
        return True

    @property
    def is_colored(self):
        return False

    @property
    def color_mode(self):
        return 'monochrome'

    @property
    def memory_bytes(self):
        memory=getattr(self._glyph,'memory_bytes',None)
        return (memory if memory is not None else len(self.png_bytes))+len(self.digest)

    def render(self, size: int, fg: tuple, alpha: int = 255,
               foreground_opacity: float = 230 / 255):
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 1024:
            raise SvgGlyphError('Glyph size must be within 1..1024')
        if (not isinstance(fg, tuple) or len(fg) != 3 or
                any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 255 for v in fg)):
            raise SvgGlyphError('Glyph foreground must be an RGB tuple')
        if isinstance(alpha, bool) or not isinstance(alpha, int) or not 0 <= alpha <= 255:
            raise SvgGlyphError('Glyph alpha must be within 0..255')
        if (isinstance(foreground_opacity, bool) or not isinstance(foreground_opacity, (int, float)) or
                not math.isfinite(foreground_opacity) or not 0 <= foreground_opacity <= 1):
            raise SvgGlyphError('Glyph foreground opacity must be within 0..1')
        # A specific Lucide outline was selected: the global filled preference
        # cannot turn that SVG into different artwork. Existing optical placement,
        # small-size coverage and neutral/dim alpha come from the reviewed masks.
        return icon_artwork.glyph(self._kind, size, fg, alpha,
                                  foreground_opacity=round(foreground_opacity * 255),
                                  filled=False)


def has_builtin_profile(icon_id):
    """Whether this exact verified Lucide name has an existing offline profile."""
    return isinstance(icon_id,str) and icon_id in _BUILTIN_PROFILES


def with_builtin_profile(icon_id, glyph):
    """Attach an exact existing profile only to an explicitly verified Lucide ID."""
    if getattr(glyph, 'is_colored', False):
        return glyph
    kind = _BUILTIN_PROFILES.get(icon_id) if isinstance(icon_id, str) else None
    if kind is None or isinstance(glyph, _BuiltinProfile) and glyph._kind == kind:
        return glyph
    return _BuiltinProfile(glyph, kind)
