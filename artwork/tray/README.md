# Tray artwork sources

These repository-native images reproduce the embedded glyphs without the external
icon lab or handoff directory. The newer device glyphs are generated from pinned local
Lucide SVGs, rather than extracted from the historical handoff. `masks.png` is a lossless L-mode alpha atlas:
sixteen glyph rows (112 masks), seven physical sizes per row (16, 20, 24, 32, 40, 48, 64).
`manifest.json` records every crop, semantic name, upstream source and license.
The three RGBA color masters are 256 x 256. Xbox has two accepted optical
placements: `xbox-color.png` uses canonical centered placement for light taskbars;
`xbox-color-dark.png` retains the historical (-6,+2)-pixel correction for dark
taskbars. The dark PNG is byte-identical to `6795db2:artwork/tray/xbox-color.png`.
The existing renderer foreground (black/light taskbar or white/dark taskbar)
selects the prealigned master. No runtime translation or DPI correction is added.
PlayStation remains centered. The masters contain no ring or battery state.

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
preserving each master's recorded optical placement. The generator does not modify renderer code
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
All non-microphone masks and all three color masters are preserved. The SVG hash, source URL,
geometry and generation method are recorded in the manifest. Neither Chromium nor
an SVG library is required by the application. Keep browser/Pillow versions stable
for byte-identical regeneration and verify the seven sizes on both backgrounds.

## Speaker and webcam

Pinned `lucide-static` 1.17.0 Speaker and Webcam SVGs match the requested Iconify
Lucide references. Run `tools/make_speaker_artwork.py` and
`tools/make_webcam_artwork.py`, then `tools/make_icon_artwork.py`. Both use the
microphone generator's offline Chromium pipeline: uniform scale 0.5, one logical
pixel stroke, visible ink bounds [3.5,2.5,12.5,13.5], centered at [8,8].
No stretching or optical translation is applied. The shared generator reuses
existing atlas rectangles, appends new rows only for new glyphs, and preserves
other SVG generation metadata; repeated microphone/speaker/webcam runs are safe.

## Health (Lucide heart)

`tools/make_health_artwork.py` uses the pinned `lucide-heart.svg` (ISC). Its
round-stroke ink is asymmetric inside the viewBox (approximately source bounds
[1,2.98385,23.00006,21.99968]). The shared generator measures the 1024px alpha
bounds and centers that visible ink on (512,512), recording the geometric
translation and final bounds in `svg_generation.health`. Uniform scale remains
0.5 and stroke remains one logical pixel. This source-derived centering is baked
once; no optical or theme correction is applied. Run the embedding generator next.

## Smart home (Lucide house)

`tools/make_smart_home_artwork.py` converts the pinned `lucide-house.svg` (ISC),
matching the requested Iconify Lucide House. As for Health, the shared pipeline
centers measured source ink once on [512,512], preserves uniform scale 0.5 and
one logical pixel stroke, and records the measured bounds and geometric
translation in `svg_generation.smart_home`. It applies no optical/theme offset.

## Smartwatch, Display/TV and Tag/Tracker

Pinned `lucide-watch.svg`, `lucide-tv-minimal.svg` and `lucide-radar.svg` match the
requested Iconify Lucide references. Their semantic mask IDs are `watch`,
`display` and `tracker`. Run the corresponding `tools/make_watch_artwork.py`,
`tools/make_display_artwork.py` and `tools/make_tracker_artwork.py`, followed by
the embedding generator. Source-derived geometric centering is recorded in the
manifest; no optical/theme offset is applied.

Display's upper rounded corners at scale 0.5 reach radius 6.8151 logical px,
beyond the 6.6 glyph region. Its paths therefore use a uniform 0.48 source fit,
with render-only SVG stroke 1/0.48 = 2.083333333333 to retain one logical pixel
line weight. The pinned SVG remains byte-exact at stroke 2. The fitted ink radius
is about 6.5625; seven-size verification removes no pixels of alpha >=128 at the
radial limit. Only antialias fringe is clipped. All other glyphs retain scale 0.5.
