# Settings glyphs

Official Lucide outline SVGs from `lucide-static@1.17.0` are vendored unchanged.
`sources.json` records each upstream URL and SHA-256. License:
[`lucide-1.17.0-LICENSE.txt`](../../../docs/licenses/lucide-1.17.0-LICENSE.txt).

The native WinUI 3 companion renders these outlines as vector geometry in
`ui/HaloBattery.Settings/Lucide.cs`. The Python backend supplies tray preview
images separately. Settings do not use a Pillow glyph atlas.

The Discord and Ko-fi shortcuts use the Qlementine `discord-16` and
`coffee-cup-16` outlines. Their original SVGs, source URLs and SHA-256 are
recorded in `qlementine-sources.json`. The MIT license
is included in [`qlementine-icons-LICENSE.txt`](../../../docs/licenses/qlementine-icons-LICENSE.txt)
and bundled with both the app and its native settings companion.

Run `python -m unittest discover -s tests -p test_settings_sources.py` to check
the pinned SVG hashes and source geometry offline. The native project bundles
its static fonts and SIL OFL licenses directly from `assets/fonts`.
