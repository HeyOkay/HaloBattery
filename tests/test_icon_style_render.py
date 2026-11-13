"""Style changes affect only supported glyphs, leaving states and brands intact."""
import math
import unittest

from halo import icons, icon_artwork_filled


class IconStyleRenderTests(unittest.TestCase):
    def test_only_the_central_glyph_changes(self):
        for size in (16, 20, 24, 32, 40, 48, 64):
            for kind in icon_artwork_filled.KINDS:
                for light in (False, True):
                    for level, charging, online in ((75, False, True), (12, False, True),
                                                    (60, True, True), (None, True, True),
                                                    (None, False, False)):
                        with self.subTest(size=size, kind=kind, light=light,
                                          level=level, charging=charging, online=online):
                            args = (level, charging, online)
                            options = dict(size=size, badge=kind, light_taskbar=light)
                            outline = icons.render(*args, **options)
                            filled = icons.render(*args, **options, filled=True)
                            self.assertEqual(filled.size, (size, size))
                            for y in range(size):
                                for x in range(size):
                                    if math.hypot(x+.5-size/2, y+.5-size/2) >= 7*size/16:
                                        self.assertEqual(outline.getpixel((x, y)),
                                                         filled.getpixel((x, y)))

    def test_every_supported_filled_glyph_is_distinct(self):
        for kind in icon_artwork_filled.KINDS:
            with self.subTest(kind=kind):
                self.assertNotEqual(icons.render(75, False, True, badge=kind).tobytes(),
                                    icons.render(75, False, True, badge=kind, filled=True).tobytes())

    def test_branded_glyphs_and_numbers_are_unchanged(self):
        for size in (16, 20, 24, 32, 40, 48, 64):
            for light in (False, True):
                for brand in ('xbox', 'playstation', 'nintendo'):
                    for colored in (False, True):
                        kw = dict(size=size, light_taskbar=light, badge='gamepad',
                                  brand=brand, brand_color=colored)
                        self.assertEqual(icons.render(75, False, True, **kw).tobytes(),
                                         icons.render(75, False, True, **kw, filled=True).tobytes())
                for text in ('0', '75', '100'):
                    kw = dict(size=size, light_taskbar=light, badge='mouse', text=text)
                    self.assertEqual(icons.render(75, True, True, **kw).tobytes(),
                                     icons.render(75, True, True, **kw, filled=True).tobytes())

    def test_other_glyphs_keep_their_original_images(self):
        other = set(icons.KINDS.values())-set(icon_artwork_filled.KINDS)-{'dualshock','dualsense'}
        for kind in other:
            self.assertEqual(icons.render(75, False, True, badge=kind).tobytes(),
                             icons.render(75, False, True, badge=kind, filled=True).tobytes())

    def test_filled_animation_keeps_timing_and_does_not_mutate_masks(self):
        before = icon_artwork_filled.image('mouse', 20).tobytes()
        for level in (60, None):
            frames = icons.charging_frames(level, True, badge='mouse', size=20, filled=True)
            self.assertEqual(len(frames), icons.BREATH_FRAMES)
            self.assertNotEqual(frames[0].tobytes(), frames[len(frames)//2].tobytes())
        icons.render(20, False, False, badge='mouse', size=20,
                     light_taskbar=True, filled=True)
        self.assertEqual(before, icon_artwork_filled.image('mouse', 20).tobytes())


if __name__ == '__main__':
    unittest.main()
