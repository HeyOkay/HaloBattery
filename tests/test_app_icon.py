"""Approved application branding, without Windows, hardware or network access."""
from pathlib import Path
import sys
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from halo import app_icon, whatsnew


class AppIconTests(unittest.TestCase):
    def test_render_preserves_approved_artwork_at_requested_sizes(self):
        path = Path(__file__).resolve().parent.parent / "assets" / "app-icon.png"
        with Image.open(path) as image:
            source = image.convert("RGBA")
        for size in (16, 44, 128, source.width):
            with self.subTest(size=size):
                actual = app_icon.render(size)
                expected = source.resize((size, size), Image.Resampling.LANCZOS)
                self.assertEqual(actual.mode, "RGBA")
                self.assertEqual(actual.size, (size, size))
                self.assertEqual(actual.tobytes(), expected.tobytes())
                for corner in ((0, 0), (size - 1, 0), (0, size - 1), (size - 1, size - 1)):
                    self.assertEqual(actual.getpixel(corner)[3], 0)

    def test_callers_cannot_mutate_the_cached_artwork(self):
        for size in (44, 1024):
            with self.subTest(size=size):
                first = app_icon.render(size)
                original = first.tobytes()
                first.paste((255, 0, 255, 255), (0, 0, size, size))
                second = app_icon.render(size)
                self.assertIsNot(first, second)
                self.assertEqual(second.tobytes(), original)
        for size in (0, -1, 1.5, True, None):
            with self.subTest(size=size), self.assertRaises(ValueError):
                app_icon.render(size)

    def test_whatsnew_flattens_artwork_on_light_and_dark_backgrounds(self):
        source = app_icon.render(44)
        before = source.tobytes()
        for light in (False, True):
            colours = whatsnew.colours(light)
            background, key = colours["base_rgb"], whatsnew._rgb(colours["key"])
            actual = whatsnew.app_logo(44, background, key)
            composite = Image.new("RGBA", source.size, background + (255,))
            composite.alpha_composite(source)
            self.assertEqual(actual.mode, "RGB")
            for y in range(44):
                for x in range(44):
                    expected = key if source.getpixel((x, y))[3] < 24 else composite.getpixel((x, y))[:3]
                    self.assertEqual(actual.getpixel((x, y)), expected, (light, x, y))
        self.assertEqual(app_icon.render(44).tobytes(), before)


if __name__ == "__main__":
    unittest.main()
