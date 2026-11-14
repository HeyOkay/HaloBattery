"""Battery ring thickness retains its outer size, signals and independent centre."""
import hashlib
import math
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from halo import icons


# Recorded before adding ring_style: current thin output must stay pixel-identical.
_THIN_BASELINE = {
    (100, False, True, False, 16, False): 'e5168059bf769971ee434273e7ec6150833c1c5b5fe4e47b8dfbc482e69e9f0d',
    (75, False, True, False, 20, True): 'd348c81cfec6c1ccc5023ce91c321903b260cc8e97d78ddc9adf88d537764abd',
    (0, False, True, False, 32, False): 'be1ec7d5671b708934ed0d0b54761b35211bf3063ed7d382edbd5719bb547efc',
    (10, False, True, False, 24, False): 'bc3f414bbfa0c9dc7c56fa99c3b441ab74db3fe8360b2c1f48b9d816368fe76e',
    (25, False, True, False, 40, True): 'fde2879fdfdda6a174332fb4ec7d2479e22d4476ed48ec06515d90c91dc2a5f7',
    (None, True, True, False, 32, False): '6fe6e10da74f9e8eac382f1975dccb368566588ae5ef10ee41a47ce8b5f30f0f',
    (None, False, True, True, 20, True): '38cc510f88029eeb83c7e4291d783272824695fb0865a3813b9ca8653546c0ea',
    (60, False, False, False, 64, False): 'dd9f62c1c19b477b44bddf68a027bfa50208fe2dd42e3159db69ac8ea98a89e7',
}
_SIZES = (16, 20, 24, 32, 40, 48, 64, 80)
_STATES = ((0, False, True, False), (100, False, True, False),
           (10, False, True, False), (25, False, True, False),
           (75, False, True, False), (60, True, True, False),
           (None, True, True, False), (60, False, False, False),
           (None, False, True, False), (None, False, True, True))


def coverage(image):
    return sum(value * count for value, count in enumerate(image.getchannel('A').histogram()))


class ColorCentre:
    def __init__(self):
        self.calls = []

    def render(self, size, foreground, alpha, foreground_opacity):
        self.calls.append((size, foreground, alpha, foreground_opacity))
        image = Image.new('RGBA', (size, size))
        draw = ImageDraw.Draw(image)
        radius = size / 8
        draw.rectangle((size/2-radius, size/2-radius, size/2+radius, size/2+radius),
                       fill=(221, 46, 68, alpha))
        return image


class RingStyleTests(unittest.TestCase):
    def test_default_thin_matches_prechange_pixels(self):
        for state, expected in _THIN_BASELINE.items():
            level, charging, online, paused, size, light = state
            with self.subTest(state=state):
                options = dict(force_active=paused, size=size, light_taskbar=light)
                default = icons.render(level, charging, online, **options)
                thin = icons.render(level, charging, online, ring_style='thin', **options)
                self.assertEqual(default.tobytes(), thin.tobytes())
                self.assertEqual(hashlib.sha256(default.tobytes()).hexdigest(), expected)

    def test_classic_matches_historical_6_5_stroke_area_and_inner_edge(self):
        # Historical R=31.5,w=6.5 in the 64-unit canvas: logical16 radii
        # 7.875 outside and 6.25 inside. Thin retains its existing 6.875 inside.
        expected_area = math.pi * (7.875**2 - 6.25**2)
        for light in (False, True):
            for size in _SIZES:
                with self.subTest(light=light, size=size):
                    classic = icons.render(100, False, True, light_taskbar=light, size=size, ring_style='classic')
                    thin = icons.render(100, False, True, light_taskbar=light, size=size)
                    expected = expected_area * (size/16)**2
                    self.assertAlmostEqual(coverage(classic)/icons.NEUTRAL_OPACITY, expected,
                                           delta=max(.5, expected*.025))
                    self.assertEqual(classic.getchannel('A').getbbox(), thin.getchannel('A').getbbox())
                    self.assertGreater(coverage(classic), coverage(thin)*1.4)
        classic = icons.render(100, False, True, size=64, light_taskbar=False, ring_style='classic')
        thin = icons.render(100, False, True, size=64, light_taskbar=False)
        self.assertEqual(classic.getpixel((32,6)), (255,255,255,icons.NEUTRAL_OPACITY))
        self.assertEqual(thin.getpixel((32,6))[3], 0)
        self.assertEqual(classic.getpixel((32,9))[3], 0)

    def test_all_states_and_themes_only_add_inward_ring_coverage(self):
        for level, charging, online, paused in _STATES:
            for light in (False, True):
                for size in (16,20,32,80):
                    with self.subTest(state=(level,charging,online,paused), light=light, size=size):
                        options = dict(light_taskbar=light, size=size, force_active=paused,
                                       pulse=.4, animation_phase=.25)
                        thin = icons.render(level,charging,online,**options)
                        classic = icons.render(level,charging,online,ring_style='classic',**options)
                        self.assertGreater(coverage(classic),coverage(thin))
                        self.assertEqual(classic.size,thin.size)
                        # No state can turn the untouched centre into ring ink.
                        box=(round(size*.35),round(size*.35),round(size*.65),round(size*.65))
                        self.assertIsNone(classic.crop(box).getchannel('A').getbbox())
                        self.assertIsNone(thin.crop(box).getchannel('A').getbbox())

    def test_rounded_ends_and_battery_colors_follow_classic_width(self):
        for style in ('thin','classic'):
            for level, charging, color in ((10,False,icons.RED),(25,False,icons.AMBER),
                                           (75,False,(255,255,255)),(60,True,icons.GREEN),
                                           (None,True,icons.GREEN)):
                with self.subTest(style=style, level=level, charging=charging):
                    image = icons.render(level,charging,True,light_taskbar=False,size=64,ring_style=style)
                    self.assertTrue(any((r,g,b)==color and a>0 for count,(r,g,b,a) in image.getcolors(64*64)))
                    # At the starting cap the outer radius stays unchanged even
                    # though its centreline moves inward with the wider stroke.
                    self.assertGreater(image.getpixel((32,1))[3],0)
                    self.assertEqual(image.getpixel((32,11))[3],0)

    def test_animation_cycle_forwards_style_and_keeps_known_unknown_timing(self):
        for style in ('thin','classic'):
            for level, online in ((60,True),(None,True),(60,False),(None,False)):
                with self.subTest(style=style,level=level,online=online):
                    frames=icons.charging_frames(level,online,light_taskbar=False,size=20,ring_style=style)
                    self.assertEqual(len(frames),90)
                    for index in (0,23,45,89):
                        phase=index/icons.BREATH_FRAMES
                        expected=icons.render(level,True,online,light_taskbar=False,size=20,
                            ring_style=style,pulse=icons.breath_level(phase),
                            animation_phase=phase if level is None else 0.)
                        self.assertEqual(frames[index].tobytes(),expected.tobytes())
                    if online:
                        self.assertNotEqual(frames[0].tobytes(),frames[45].tobytes())
                    else:
                        self.assertEqual(frames[0].tobytes(),frames[45].tobytes())

    def test_default_animation_omits_new_keyword_for_strict_legacy_render_callers(self):
        with mock.patch.object(icons,'render',return_value=object()) as render:
            icons.charging_frames(60,True,ring_style='thin')
            self.assertEqual(render.call_count,90)
            self.assertTrue(all('ring_style' not in call.kwargs for call in render.call_args_list))
        with mock.patch.object(icons,'render',return_value=object()) as render:
            icons.charging_frames(60,True,ring_style='classic')
            self.assertTrue(all(call.kwargs['ring_style']=='classic' for call in render.call_args_list))

    def test_custom_rgb_centre_geometry_and_dim_color_are_unchanged(self):
        for level, charging, online, paused in _STATES:
            for light in (False,True):
                with self.subTest(state=(level,charging,online,paused),light=light):
                    centre=ColorCentre()
                    options=dict(size=32,light_taskbar=light,force_active=paused,custom_glyph=centre)
                    thin=icons.render(level,charging,online,**options)
                    classic=icons.render(level,charging,online,ring_style='classic',**options)
                    self.assertEqual(centre.calls[0],centre.calls[1])
                    self.assertEqual(thin.crop((12,12,20,20)).tobytes(),classic.crop((12,12,20,20)).tobytes())
                    self.assertEqual(classic.getpixel((16,16))[:3],(221,46,68))

    def test_invalid_styles_are_rejected_even_if_renderer_is_mocked(self):
        for value in (None,False,True,0,1,4.0,[],{},'','thick','Thin','classic '):
            with self.subTest(value=value),self.assertRaises(ValueError):
                icons.render(60,False,True,ring_style=value)
            with self.subTest(animation=value),mock.patch.object(icons,'render') as render,self.assertRaises(ValueError):
                icons.charging_frames(60,True,ring_style=value)
            render.assert_not_called()


if __name__=='__main__':
    unittest.main()
