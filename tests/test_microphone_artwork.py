"""Lucide microphone geometry and live states; offline rasters only."""
import math
import unittest

from PIL import Image

from halo import icon_artwork, icons


SIZES = (16, 20, 24, 32, 40, 48, 64)


def pixels_where(image, predicate):
    return [image.getpixel((x, y)) for y in range(image.height) for x in range(image.width)
            if predicate(x + .5 - image.width / 2, y + .5 - image.height / 2)]


class MicrophoneArtworkTests(unittest.TestCase):
    def test_aliases_and_controller_style_do_not_change_microphone(self):
        for light in (False, True):
            expected = icons.render(60, False, True, light_taskbar=light,
                                    badge='microphone', size=20).tobytes()
            for alias in ('microphone', 'mic', 'C'):
                for brand, color in (('default', False), ('xbox', True),
                                     ('playstation', True), ('nintendo', False)):
                    with self.subTest(light=light, alias=alias, brand=brand, color=color):
                        self.assertEqual(icons.render(60, False, True, light_taskbar=light,
                                                      badge=alias, size=20, brand=brand,
                                                      brand_color=color).tobytes(), expected)

    def test_all_physical_sizes_keep_the_same_ring(self):
        for size in SIZES:
            for light in (False, True):
                with self.subTest(size=size, light=light):
                    plain = icons.render(75, False, True, light_taskbar=light, size=size)
                    microphone = icons.render(75, False, True, light_taskbar=light,
                                              badge='microphone', size=size)
                    ring = lambda x, y: math.hypot(x, y) > 6.8 * size / 16
                    self.assertEqual(pixels_where(microphone, ring), pixels_where(plain, ring))
                    self.assertNotEqual(microphone.tobytes(), plain.tobytes())

    def test_visible_ink_is_centered_narrow_and_separate_from_ring(self):
        # Agreed Lucide geometry: uniform scaling, 8 x 11 logical pixels of ink,
        # centered at (8, 8), with no optical translation or square stretching.
        for size in SIZES:
            with self.subTest(size=size):
                mask = icon_artwork._image('microphone', size)
                self.assertEqual((mask.mode, mask.size), ('L', (size, size)))
                visible = mask.point(lambda alpha: 255 if alpha >= 32 else 0)
                box = visible.getbbox()
                self.assertIsNotNone(box)
                x0, y0, x1, y1 = box
                self.assertAlmostEqual((x0 + x1) / 2, size / 2, delta=.6)
                self.assertAlmostEqual((y0 + y1) / 2, size / 2, delta=.6)
                # One physical pixel of bbox uncertainty is significant at 16px.
                self.assertAlmostEqual((x1-x0) / (y1-y0), 8/11, delta=.13)
                self.assertAlmostEqual((y1-y0) * 16 / size, 11, delta=1.1)
                radii = [math.hypot(x + .5-size/2, y + .5-size/2) * 16 / size
                         for y in range(size) for x in range(size)
                         if visible.getpixel((x, y))]
                self.assertGreaterEqual(6.875 - max(radii), .85)
                # Even faint antialias pixels must stay out of the shared ring.
                self.assertFalse(any(pixels_where(mask, lambda x, y:
                                                  math.hypot(x, y) > 6.6 * size / 16)))

    def test_active_dim_unknown_and_forced_active_use_the_new_mask(self):
        for level, charging, online, forced, alpha in (
                (60, False, True, False, 255),
                (60, False, False, False, 110),
                (None, False, True, False, 110),
                (None, True, True, False, 255),
                (None, False, False, True, 110),
                (None, False, True, True, 255)):
            with self.subTest(level=level, charging=charging, online=online, forced=forced):
                settings = dict(light_taskbar=False, size=32, force_active=forced)
                track = icons.render(level, charging, online, **settings)
                glyph = icon_artwork.glyph('microphone', 32, (255, 255, 255), alpha,
                                          foreground_opacity=icons.NEUTRAL_OPACITY)
                expected = Image.alpha_composite(track, glyph)
                actual = icons.render(level, charging, online, badge='microphone', **settings)
                self.assertEqual(actual.tobytes(), expected.tobytes())
                self.assertEqual(glyph.getchannel('A').getextrema()[1], icons.neutral_alpha(alpha))

    def test_number_badges_off_and_charging_preserve_existing_behavior(self):
        for size in (20, 32):
            with self.subTest(size=size):
                settings = dict(light_taskbar=False, size=size)
                self.assertEqual(icons.render(60, False, True, badge='microphone', text='60',
                                              **settings).tobytes(),
                                 icons.render(60, False, True, text='60', **settings).tobytes())
                self.assertEqual(icons.render(60, False, True, badge='', brand='playstation',
                                              brand_color=True, **settings).tobytes(),
                                 icons.render(60, False, True, **settings).tobytes())
                frames = icons.charging_frames(None, True, 20, False, 'microphone', size=size)
                self.assertEqual(len(frames), icons.BREATH_FRAMES)
                self.assertNotEqual(frames[0].tobytes(), frames[icons.BREATH_FRAMES // 2].tobytes())
                glyph = icon_artwork.glyph('microphone', size, (255, 255, 255), 255,
                                          foreground_opacity=icons.NEUTRAL_OPACITY)
                for index in range(icons.BREATH_FRAMES):
                    phase = index / icons.BREATH_FRAMES
                    track = icons.render(None, True, True, pulse=icons.breath_level(phase),
                                         animation_phase=phase, **settings)
                    self.assertEqual(frames[index].tobytes(),
                                     Image.alpha_composite(track, glyph).tobytes())
                offline = icons.charging_frames(60, False, 20, False, 'microphone', size=size)
                self.assertEqual(offline[0].tobytes(), offline[icons.BREATH_FRAMES // 2].tobytes())


if __name__ == '__main__':
    unittest.main()
