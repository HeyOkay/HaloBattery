"""Offline Phone Link artwork checks; no phone, network or browser required."""
import hashlib
import json
import math
from pathlib import Path
import unittest

from halo import icon_artwork, icon_artwork_filled, icons

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64)
SOURCE_HASH = "04e49a72309fe04fc063b6ddb5a0c4f7bb4843d8ae640df2af603162688c8776"


class PhoneArtworkTests(unittest.TestCase):
    def test_pinned_phone_source_and_filled_derivation_keep_provenance(self):
        source = (ROOT / "assets/artwork/tray/lucide-smartphone.svg").read_bytes()
        self.assertEqual(hashlib.sha256(source).hexdigest(), SOURCE_HASH)
        self.assertEqual(source, (ROOT / "assets/artwork/settings/lucide-smartphone.svg").read_bytes())
        manifest = json.loads((ROOT / "assets/artwork/tray/filled/manifest.json").read_text(encoding="utf-8"))
        record = manifest["glyphs"]["phone"]
        self.assertEqual(record["upstream_sha256"], SOURCE_HASH)
        filled = ROOT / "assets/artwork/tray/filled" / record["file"]
        self.assertEqual(hashlib.sha256(filled.read_bytes()).hexdigest(), record["sha256"])
        self.assertTrue((ROOT / "docs/licenses/lucide-1.17.0-LICENSE.txt").is_file())

    def test_native_phone_masks_are_centered_and_distinct_in_both_styles(self):
        self.assertEqual(icons.KINDS["phone"], "phone")
        self.assertIn("phone", icon_artwork_filled.KINDS)
        for size in SIZES:
            outline = icon_artwork._image("phone", size)
            filled = icon_artwork_filled.image("phone", size)
            with self.subTest(size=size):
                self.assertNotEqual(outline.tobytes(), filled.tobytes())
                for mask in (outline, filled):
                    self.assertEqual(mask.mode, "L")
                    self.assertEqual(mask.size, (size, size))
                    # Measure visible ink, excluding LANCZOS alpha-1 halos.
                    x0, y0, x1, y1 = mask.point(lambda value: 255 if value >= 32 else 0).getbbox()
                    self.assertAlmostEqual((x0 + x1) / 2, size / 2, delta=.5)
                    self.assertAlmostEqual((y0 + y1) / 2, size / 2, delta=.5)
                    self.assertGreater(y1 - y0, x1 - x0)

    def test_phone_never_changes_the_charge_ring_across_states_and_themes(self):
        for size in SIZES:
            for style in ("thin", "classic"):
                for light in (False, True):
                    for filled in (False, True):
                        for level, charging, online in ((0, False, True), (100, False, True),
                                                         (None, True, True), (None, False, False)):
                            with self.subTest(size=size, style=style, light=light, filled=filled,
                                              level=level, charging=charging, online=online):
                                options = dict(size=size, ring_style=style, light_taskbar=light,
                                               filled=filled, animation_phase=.4)
                                plain = icons.render(level, charging, online, **options)
                                phone = icons.render(level, charging, online, badge="phone", **options)
                                self.assertNotEqual(plain.tobytes(), phone.tobytes())
                                for y in range(size):
                                    for x in range(size):
                                        if math.hypot(x + .5 - size / 2, y + .5 - size / 2) > 6.8 * size / 16:
                                            self.assertEqual(phone.getpixel((x, y)), plain.getpixel((x, y)))


if __name__ == "__main__":
    unittest.main()
