"""Optical alignment preserves source pixels and never trades size for position."""
from collections import Counter
import math
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from halo import icon_alignment as A


def heart(size=128):
    image = Image.new('L', (size, size))
    # A deliberately top-heavy silhouette, with a lower tip and symmetric lobes.
    points = [(30, 40), (40, 28), (58, 28), (64, 40), (70, 28),
              (88, 28), (98, 40), (98, 58), (64, 102), (30, 58)]
    ImageDraw.Draw(image).polygon([(x * size // 128, y * size // 128)
                                  for x, y in points], fill=255)
    return image


def centroid(image):
    pixels = image.load()
    mass = sum(image.tobytes())
    return tuple(sum((coordinate + .5) * pixels[x, y]
                     for y in range(image.height) for x in range(image.width)
                     for coordinate in [x if axis == 0 else y]) / mass
                 for axis in (0, 1))


class PlacementTests(unittest.TestCase):
    def assertInsideDisk(self, image):
        radius = 5.9 / 16 * min(image.size)
        for y in range(image.height):
            for x in range(image.width):
                if image.getpixel((x, y)):
                    self.assertLessEqual(math.hypot(x + .5 - image.width / 2,
                                                    y + .5 - image.height / 2), radius)

    def test_balanced_card_and_empty_return_exact_original(self):
        card = Image.new('L', (128, 128))
        draw = ImageDraw.Draw(card)
        draw.rectangle((35, 25, 92, 102), outline=255, width=6)
        # Small internal artwork remains comfortably within the optical deadzone.
        draw.rectangle((60, 59, 67, 65), fill=90)
        for image in (card, Image.new('L', (128, 128)), Image.new('RGBA', (128, 128))):
            with self.subTest(mode=image.mode):
                self.assertIs(A.place(image), image)

    def test_heart_moves_down_without_resampling_or_size_change(self):
        for size in (128, 256, 1024):
            with self.subTest(size=size):
                image = heart(size)
                shift = A.offset(image)
                self.assertEqual(shift[0], 0)
                self.assertGreater(shift[1], 0)
                self.assertLessEqual(shift[1], .75 / 16 * size)
                with mock.patch.object(Image.Image, 'resize', side_effect=AssertionError('resampling')):
                    result = A.place(image)
                self.assertEqual(result.size, image.size)
                self.assertEqual(result.mode, image.mode)
                self.assertEqual(result.histogram(), image.histogram())
                old_bounds, new_bounds = image.getbbox(), result.getbbox()
                self.assertEqual(new_bounds, tuple(value + shift[axis % 2]
                                                  for axis, value in enumerate(old_bounds)))
                self.assertEqual(result.crop(new_bounds).tobytes(), image.crop(old_bounds).tobytes())
                self.assertLess(abs(centroid(result)[1] - size / 2),
                                abs(centroid(image)[1] - size / 2))
                self.assertInsideDisk(result)

    def test_horizontal_and_negative_shifts_use_the_same_constraints(self):
        original = heart()
        for operation, expected in ((Image.Transpose.TRANSPOSE, (6, 0)),
                                    (Image.Transpose.ROTATE_180, (0, -6))):
            image = original.transpose(operation)
            with self.subTest(operation=operation):
                self.assertEqual(A.offset(image), expected)
                result = A.place(image)
                self.assertInsideDisk(result)
                self.assertEqual(result.histogram(), image.histogram())

    def test_rgba_colors_and_alpha_are_translated_byte_for_byte(self):
        alpha = heart()
        image = Image.new('RGBA', alpha.size)
        for y in range(128):
            for x in range(128):
                if alpha.getpixel((x, y)):
                    image.putpixel((x, y), (x, y, (x + y) % 256, 64 + x))
        result = A.place(image)
        self.assertEqual(Counter(zip(*(result.getchannel(c).tobytes() for c in result.getbands()))),
                         Counter(zip(*(image.getchannel(c).tobytes() for c in image.getbands()))))
        self.assertEqual(result.histogram(), image.histogram())
        self.assertEqual(result.crop(result.getbbox()).tobytes(),
                         image.crop(image.getbbox()).tobytes())
        self.assertInsideDisk(result.getchannel('A'))

    def test_same_alpha_gives_same_placement_for_mixed_theme_variants(self):
        alpha = heart()
        images = []
        for color in ((0, 0, 0), (255, 255, 255), (25, 170, 220)):
            image = Image.new('RGBA', alpha.size, color + (0,))
            image.putalpha(alpha)
            images.append(A.place(image))
        expected = A.place(alpha).tobytes()
        self.assertTrue(all(image.getchannel('A').tobytes() == expected for image in images))

    def test_initially_outside_disk_can_translate_inside(self):
        image = Image.new('L', (128, 128))
        ImageDraw.Draw(image).rectangle((24, 36, 103, 60), fill=255)
        self.assertGreater(math.hypot(24.5 - 64, 36.5 - 64), 5.9 / 16 * 128)
        self.assertEqual(A.offset(image), (0, 6))
        result = A.place(image)
        self.assertInsideDisk(result)
        self.assertEqual(result.histogram(), image.histogram())

    def test_faint_lower_tip_limits_shift_without_clipping_it(self):
        image = Image.new('L', (128, 128))
        ImageDraw.Draw(image).rectangle((50, 35, 77, 70), fill=255)
        image.putpixel((64, 108), 1)
        self.assertEqual(A.offset(image), (0, 2))
        result = A.place(image)
        self.assertEqual(result.getpixel((64, 110)), 1)
        self.assertEqual(result.histogram(), image.histogram())
        self.assertInsideDisk(result)

    def test_no_safe_integer_shift_leaves_pixels_untouched(self):
        for endpoint in ((64, 110), (96, 97)):
            with self.subTest(endpoint=endpoint):
                image = Image.new('L', (128, 128))
                ImageDraw.Draw(image).rectangle((50, 35, 77, 70), fill=255)
                image.putpixel(endpoint, 1)
                self.assertEqual(A.offset(image), (0, 0))
                self.assertIs(A.place(image), image)

    def test_small_canvas_without_integer_budget_stays_unchanged(self):
        image = Image.new('L', (16, 16))
        ImageDraw.Draw(image).rectangle((5, 4, 10, 7), fill=255)
        self.assertIs(A.place(image), image)

    def test_alpha_mass_and_deadzone_ignore_balanced_geometry_noise(self):
        image = Image.new('L', (128, 128))
        ImageDraw.Draw(image).rectangle((40, 40, 87, 87), fill=255)
        image.putpixel((64, 20), 1)
        self.assertIs(A.place(image), image)

    def test_invalid_modes_and_radius_fail_explicitly(self):
        with self.assertRaises(ValueError):
            A.place(Image.new('RGB', (128, 128)))
        with self.assertRaises(ValueError):
            A.offset(Image.new('RGBA', (128, 128)))
        for radius in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                A.place(heart(), radius_fraction=radius)


if __name__ == '__main__':
    unittest.main()
