"""Half-pixel Classic trial preserves per-DPI artwork and original thin pixels."""
import hashlib
import math
import unittest

from PIL import Image, ImageDraw

from halo import icons


def area(image):
    return sum(value*count for value,count in enumerate(image.getchannel('A').histogram())) / 255


class Square:
    """A coloured 8x8 logical-pixel square, independent of built-in glyph data."""
    def render(self,size,fg,alpha,foreground_opacity):
        image=Image.new('RGBA',(size,size))
        ImageDraw.Draw(image).rectangle((size//4,size//4,size*3//4-1,size*3//4-1),
                                       fill=(221,46,68,alpha))
        return image


class ClassicHalfPixelTests(unittest.TestCase):
    def test_half_pixel_area_and_partial_edges_are_preserved(self):
        source=Square().render(16,(255,255,255),255,1)
        before=source.tobytes()
        fitted=icons._classic_glyph(source)
        # An 8px square contracts to 7.5px. Each boundary moves 0.25px;
        # corner coverage is 0.75**2*255, with integer alpha rounding.
        self.assertAlmostEqual(area(fitted),7.5**2,delta=.1)
        self.assertEqual(fitted.getpixel((4,4))[3],143)
        self.assertEqual(fitted.getpixel((8,8)),(221,46,68,255))
        self.assertEqual(source.tobytes(),before)

    def test_half_logical_pixel_scales_with_dpi_and_keeps_proportions(self):
        for size in (16,20,24,32,48,64,80):
            with self.subTest(size=size):
                source=Square().render(size,(255,255,255),255,1)
                actual=icons._classic_glyph(source)
                expected_side=size/2-.5*size/16
                self.assertAlmostEqual(math.sqrt(area(actual)),expected_side,delta=.03)
                # Symmetry detects an accidental shift or unequal axis scaling.
                alpha=actual.getchannel('A')
                self.assertEqual(alpha.tobytes(),alpha.transpose(Image.Transpose.FLIP_LEFT_RIGHT).tobytes())
                self.assertEqual(alpha.tobytes(),alpha.transpose(Image.Transpose.FLIP_TOP_BOTTOM).tobytes())

    def test_empty_layer_is_owned_and_safe(self):
        source=Image.new('RGBA',(20,20))
        actual=icons._classic_glyph(source)
        self.assertIsNot(actual,source)
        self.assertEqual(actual.tobytes(),source.tobytes())

    def test_original_colours_are_retained_without_mutating_source(self):
        source=Square().render(16,(255,255,255),255,1)
        ImageDraw.Draw(source).rectangle((8,4,11,11),fill=(47,136,255,255))
        before=source.tobytes()
        actual=icons._classic_glyph(source)
        self.assertEqual(actual.getpixel((6,8)),(221,46,68,255))
        self.assertEqual(actual.getpixel((10,8)),(47,136,255,255))
        self.assertEqual(source.tobytes(),before)

    def test_state_dimming_and_theme_do_not_reposition_the_symbol(self):
        reference=None
        for light in (False,True):
            for level,charging,online in ((75,False,True),(10,False,True),
                                          (60,True,True),(None,True,True),(60,False,False)):
                image=icons.render(level,charging,online,size=16,light_taskbar=light,
                                   ring_style='classic',custom_glyph=Square()).crop((4,4,12,12))
                if reference is None:
                    reference=image
                expected=reference.copy()
                if not online:
                    expected.putalpha(expected.getchannel('A').point(lambda value:round(value*110/255)))
                self.assertEqual(image.tobytes(),expected.tobytes())

    def test_known_and_unknown_charging_keep_still_centre(self):
        for level in (60,None):
            frames=icons.charging_frames(level,True,size=16,light_taskbar=False,
                                         ring_style='classic',custom_glyph=Square())
            self.assertEqual(len(frames),90)
            centre=frames[0].crop((4,4,12,12)).tobytes()
            self.assertTrue(all(frame.crop((4,4,12,12)).tobytes()==centre for frame in frames))

    def test_thin_keyboard_matches_owner_accepted_pixels(self):
        goldens={
            (16,False):'a783cda89f3597e331313854d05124d92265b4d83adeeee7222db29edf84c293',
            (16,True):'e70d451ef69ce9d91bab3706f506abfadf9dfc67acf679ad23a68315d836e0c4',
            (20,False):'397e332d3568d0cbde36eada16e516c0ee9ec2472c83d7ddc29cceb054455370',
            (20,True):'c14c60ef739d5bec2d42a8117c0f9b0b82c14fc6c393a5508e368c1dc771f174',
        }
        for (size,light),expected in goldens.items():
            with self.subTest(size=size,light=light):
                image=icons.render(75,False,True,size=size,light_taskbar=light,badge='keyboard')
                self.assertEqual(hashlib.sha256(image.tobytes()).hexdigest(),expected)


if __name__=='__main__':
    unittest.main()
