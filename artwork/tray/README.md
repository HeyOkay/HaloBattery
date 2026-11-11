# Tray artwork sources

These repository-native images reproduce the embedded glyphs without the external
icon lab or handoff directory. The microphone is generated from the pinned local
`lucide-mic.svg`, rather than extracted from the historical handoff. `masks.png` is a lossless L-mode alpha atlas:
nine glyph rows (63 masks), seven physical sizes per row (16, 20, 24, 32, 40, 48, 64).
`manifest.json` records every crop, semantic name, upstream source and license.
The two RGBA color masters are 256 x 256. Their optical offsets are already
applied; do not translate them again. They contain no ring or battery state.

Regenerate only the `_PNG` literal in `halo/icon_artwork.py`:

```
python tools/make_icon_artwork.py
python tools/make_icon_artwork.py --check
```

To update a mask, edit its exact atlas rectangle while preserving mode L, square
physical size and transparent padding. To add a glyph or size, allocate a new
rectangle and add its name, size, box and source to the manifest; never overlap
existing rectangles. Preserve proportions and optical placement. Update provenance
and licensing when adding artwork. Update the color PNGs directly when needed,
keeping offsets baked exactly once. The generator does not modify renderer code
or apply optical offsets. PNG encoding uses Pillow; keep the same Pillow version
when byte-for-byte base64 stability matters. Verify any encoder changes with
pixel comparisons, then run `tests/test_icon_artwork.py` and inspect a contact sheet.

## Regenerating the Lucide microphone from SVG

```
python tools/make_microphone_artwork.py
python tools/make_icon_artwork.py
python tools/make_icon_artwork.py --check
```

The SVG step requires a local Chromium browser (Chrome/Edge), selectable with
`--browser PATH`. It uses an isolated headless profile under `build/mic-artwork`,
no input, a transparent 1024-square canvas and a centered 768-square SVG viewport.
The source has viewBox 0 0 24 24 and stroke width 2: uniform scale 0.5 gives visible
ink 8 by 11 logical pixels centered on (8,8), with a 1-pixel logical stroke.
No optical offset or aspect-ratio change is applied. LANCZOS alpha masks are clipped
at radius 6.6 logical pixels only to remove resampling halos near the ring.
The other 56 masks and both color masters are preserved. The SVG hash, source URL,
geometry and generation method are recorded in the manifest. Neither Chromium nor
an SVG library is required by the application. Keep browser/Pillow versions stable
for byte-identical regeneration and verify the seven sizes on both backgrounds.
