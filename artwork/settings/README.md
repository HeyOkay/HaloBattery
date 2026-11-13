# Settings glyphs

Official Lucide outline SVGs from `lucide-static@1.17.0` are vendored unchanged.
`sources.json` records each upstream URL and SHA-256. License:
[`lucide-1.17.0-LICENSE.txt`](../../docs/licenses/lucide-1.17.0-LICENSE.txt).

The native WinUI 3 companion renders these outlines as vector geometry in
`ui/HaloBattery.Settings/Lucide.cs`. The Python backend supplies tray preview
images separately. Settings do not use a Pillow glyph atlas.

Run `python -m unittest discover -s tests -p test_settings_sources.py` to check
the pinned SVG hashes and source geometry offline. The native project bundles
its static fonts and SIL OFL licenses directly from `assets/fonts`.