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
preserving each master's recorded optical placement. The embedding generator does not modify renderer code
or apply optical offsets; reviewed offsets are baked by the SVG mask generators. PNG encoding uses Pillow; keep the same Pillow version
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
microphone generator's offline Chromium pipeline. Webcam retains uniform scale
0.5; its final visible ink bounds are [3.5,2.75,12.5,13.75] after the reviewed
0.25-logical-pixel downward nudge. Speaker uses uniform scale 0.455
for more clearance inside the ring; its render-only stroke is normalized to one
logical pixel. Speaker's measured visible ink is centered once during generation.
Speaker has no optical offset; neither glyph is stretched. The shared generator reuses
existing atlas rectangles, appends new rows only for new glyphs, and preserves
other SVG generation metadata; repeated microphone/speaker/webcam runs are safe.

## Health (Lucide heart-pulse)

`tools/make_health_artwork.py` uses the pinned `lucide-heart-pulse.svg` (ISC). Its
round-stroke ink is asymmetric inside the viewBox (approximately source bounds
[1,2.98385,23.00006,21.99968]). The shared generator measures the 1024px alpha
bounds and centers that visible ink on (512,512), recording the geometric
translation and final bounds in `svg_generation.health`. Uniform scale remains
0.5 and stroke remains one logical pixel. This source-derived centering is baked
once, followed by the reviewed 0.25-logical-pixel downward nudge described below.
Run the embedding generator next; there is no runtime or theme correction.

## Smart home (Lucide house)

`tools/make_smart_home_artwork.py` converts the pinned `lucide-house.svg` (ISC),
matching the requested Iconify Lucide House. As for Health, the shared pipeline
centers measured source ink once on [512,512], uses uniform scale 0.45 for ring
clearance, and normalizes the render-only stroke to one logical pixel. The measured
bounds and geometric translation are recorded in `svg_generation.smart_home`.
The reviewed 0.25-logical-pixel upward nudge is then baked into the mask;
there is no runtime or theme correction.

## Smartwatch, Display/TV and Tag/Tracker

Pinned `lucide-watch.svg`, `lucide-tv-minimal.svg` and `lucide-radar.svg` match the
requested Iconify Lucide references. Their semantic mask IDs are `watch`,
`display` and `tracker`. Run the corresponding `tools/make_watch_artwork.py`,
`tools/make_display_artwork.py` and `tools/make_tracker_artwork.py`, followed by
the embedding generator. Source-derived geometric centering is recorded in the
manifest. Watch and Tracker have no optical offset; Display receives the reviewed
downward nudge described below.

Speaker, Smart home and Display/TV use uniform source fits of 0.455, 0.45 and
0.43, respectively, to match the established device glyphs' clearance from the
ring. The generator normalizes each render-only SVG stroke to `1 / scale`, so
all three keep a one-logical-pixel line weight. The pinned source SVGs stay
byte-exact with stroke 2. Each wrapper requests measured visible-ink centering;
the resulting bounds and one-time geometric translation are in `svg_generation`.
No runtime translation, theme-specific offset or aspect-ratio change is added.
The radius 6.6 limit removes resampling fringe, rather than fitting the source by
cutting opaque ink. Webcam, Smartwatch and Tag/Tracker retain scale 0.5. The 66
pre-existing assets and Smartwatch/Tracker's 14 masks remain unchanged (80 payloads).

## Reviewed optical placement

After geometric centering, the user reviewed these vertical nudges: Health,
Display/TV and Webcam move down 0.25 logical px; Smart home moves up 0.25 logical
px. Sizes, proportions and stroke weights stay unchanged. Positive Y points down.
The mask generator applies the offset once as an integer 16-pixel translation on
the 1024px master, before downsampling to the seven physical sizes. It records
`logical_optical_offset` separately from geometric centering and records final
visible-ink bounds in `manifest.json`. Every theme, battery state and animation
frame uses that same prepositioned mask. The runtime renderer adds no translation
or DPI-specific offset. Speaker, Watch and Tracker have no optical nudge.
