# Tray artwork sources

These repository-native images reproduce the embedded glyphs without the external
icon lab or handoff directory. `masks.png` is a lossless L-mode alpha atlas:
eight glyph rows, seven physical sizes per row (16, 20, 24, 32, 40, 48, 64).
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
