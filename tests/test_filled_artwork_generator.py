"""Offline reproducibility checks for native-size filled mask generation."""
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from tools import make_filled_artwork as generator


class FilledArtworkGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.kinds = tuple(f"glyph{index}" for index in range(11))
        self.masks = {}
        for kind in self.kinds:
            for size in generator.SIZES:
                mask = Image.new("L", (size, size))
                mask.putpixel((size // 2, size // 2), 255)
                self.masks[kind, size] = mask

    def pngs(self, compression=0):
        result = {}
        for key, mask in self.masks.items():
            stream = io.BytesIO()
            mask.save(stream, format="PNG", compress_level=compression)
            result[key] = base64.b64encode(stream.getvalue()).decode("ascii")
        return result

    def source(self, pngs=None, kinds=None, profiles=None):
        return ("KINDS = frozenset(" + repr(self.kinds if kinds is None else kinds) + ")\n"
                + "OPTICAL_PROFILE = " + repr(
                    {size: generator.profile(size) for size in generator.SIZES}
                    if profiles is None else profiles) + "\n"
                + "_PNG = " + repr(self.pngs() if pngs is None else pngs) + "\n")

    def test_same_pixels_with_different_png_encoding_are_accepted(self):
        uncompressed, compressed = self.pngs(0), self.pngs(9)
        self.assertNotEqual(uncompressed, compressed)
        generator.check_sources(self.source(uncompressed), self.kinds, self.masks)
        generator.check_sources(self.source(compressed), self.kinds, self.masks)

    def test_pixel_drift_is_rejected(self):
        reference = self.source()
        first = next(iter(self.masks))
        self.masks[first].putpixel((0, 0), 1)
        with self.assertRaisesRegex(ValueError, "pixels differ"):
            generator.check_sources(reference, self.kinds, self.masks)

    def test_missing_keys_and_metadata_drift_are_rejected(self):
        pngs = self.pngs()
        pngs.pop(next(iter(pngs)))
        cases = ((self.source(pngs), "77 reviewed"),
                 (self.source(kinds=self.kinds[:-1]), "KINDS"),
                 (self.source(profiles={}), "OPTICAL_PROFILE"))
        for source, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                generator.check_sources(source, self.kinds, self.masks)

    def test_check_mode_does_not_rewrite_matching_or_stale_module(self):
        reference = self.source()
        with tempfile.TemporaryDirectory() as temporary:
            module = Path(temporary) / "icon_artwork_filled.py"
            browser = Path(temporary) / "browser.exe"
            browser.touch()
            for source, expected in ((reference, 0), (self.source(profiles={}), 1)):
                module.write_text(source, encoding="utf-8")
                before = module.read_bytes()
                with patch.object(generator, "MODULE", module), \
                     patch.object(generator, "prepare_sources", return_value=(self.kinds, "unused")), \
                     patch.object(generator, "render_masks", return_value=self.masks), \
                     patch("sys.argv", ["make_filled_artwork", "--check", "--browser", str(browser)]):
                    self.assertEqual(generator.main(), expected)
                self.assertEqual(module.read_bytes(), before)

    def test_pinned_source_hash_drift_is_rejected_before_browser(self):
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            browser = assets / "browser.exe"
            browser.touch()
            (assets / "glyph.svg").write_text("changed SVG", encoding="utf-8")
            manifest = {"format": 1, "sizes": list(generator.SIZES), "glyphs": {
                kind: {"file": "glyph.svg", "sha256": "0" * 64} for kind in self.kinds}}
            (assets / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            before = {path.name: path.read_bytes() for path in assets.iterdir()}
            with patch.object(generator, "ASSETS", assets), \
                 patch.object(generator, "render_masks") as render, \
                 patch("sys.argv", ["make_filled_artwork", "--check", "--browser", str(browser)]):
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    generator.main()
                render.assert_not_called()
            self.assertEqual({path.name: path.read_bytes() for path in assets.iterdir()}, before)


if __name__ == "__main__":
    unittest.main()
