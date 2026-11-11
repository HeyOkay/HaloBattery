"""Renderer states/DPI with offline handoff glyphs; no device or network access."""
import base64
import hashlib
import math
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox
_sandbox.install_guards()
from halo import icons, icon_artwork


class ArtworkTests(unittest.TestCase):
    def test_sizes_and_existing_positional_api(self):
        self.assertEqual(icons.render(61, False, True, 20, False, "mouse", 1, "", False).size, (64, 64))
        for size in (16,20,24,32,40,48,64):
            for kind in icons.PICTOS:
                image = icons.render(61, False, True, badge=kind, size=size)
                self.assertEqual(image.size, (size,size))
                self.assertEqual(image.mode, "RGBA")
                self.assertTrue(image.getbbox())
        for size in (0, -1, True, 20.5):
            with self.assertRaises(ValueError):
                icons.render(60, False, True, size=size)

    def test_ring_is_one_logical_pixel_and_independent_of_brand(self):
        for size in (16,20,24,32,40,48,64):
            plain=icons.render(100,False,True,light_taskbar=False,size=size)
            thickness=sum(plain.getpixel((size//2,y))[3]/255 for y in range(size//4))
            self.assertAlmostEqual(thickness,size/16,delta=0.65)
            for brand in ("default","xbox","playstation","nintendo"):
                image=icons.render(100,False,True,light_taskbar=False,size=size,
                                   badge="gamepad",brand=brand,brand_color=True)
                for y in range(size):
                    for x in range(size):
                        if math.hypot(x+.5-size/2,y+.5-size/2)>6.8*size/16:
                            self.assertEqual(image.getpixel((x,y)),plain.getpixel((x,y)))

    def test_legacy_controller_ids_remain_generic_until_manual_brand_selection(self):
        generic=icons.render(61, False, True, badge="gamepad")
        for badge in ("dualshock", "dualsense", "ps4", "ps5", "xbox", "G"):
            self.assertEqual(icons.render(61, False, True, badge=badge).tobytes(), generic.tobytes())
        self.assertEqual(icons.render(61, False, True, badge="gamepad", brand="invalid").tobytes(), generic.tobytes())

    def test_brand_selection_only_changes_controller_glyphs(self):
        plain=icons.render(61, False, True, badge="mouse")
        for brand in ("default", "xbox", "playstation", "nintendo"):
            self.assertEqual(icons.render(61, False, True, badge="mouse",brand=brand,brand_color=True).tobytes(),plain.tobytes())
        variants=[icons.render(61, False, True,badge="gamepad",brand=b).tobytes()
                  for b in ("default","xbox","playstation","nintendo")]
        self.assertEqual(len(set(variants)),4)
        for light in (False,True):
            self.assertEqual(icons.render(61,False,True,badge="gamepad",light_taskbar=light,brand_color=True).tobytes(),
                             icons.render(61,False,True,badge="gamepad",light_taskbar=light).tobytes())

    def test_glyph_only_masks_exclude_track_and_demo_arcs(self):
        for size in (16,20,24,32,40,48,64):
            for name in ("headset","mouse","keyboard","microphone","gamepad","bluetooth","xbox","playstation","nintendo"):
                mask=icon_artwork._image(name,size)
                self.assertEqual(mask.mode,"L")
                for y in range(size):
                    for x in range(size):
                        if math.hypot(x+.5-size/2,y+.5-size/2)>6.6*size/16:
                            self.assertEqual(mask.getpixel((x,y)),0)

    def test_battery_arc_uses_live_levels_and_unknown_states(self):
        frames=[icons.render(level,False,True,light_taskbar=False,badge="mouse",size=32)
                for level in (0,12,28,60,75,100,None)]
        self.assertEqual(len({f.tobytes() for f in frames}),7)
        self.assertEqual(icons.render(None,False,True).tobytes(),icons.render(None,False,False).tobytes())
        self.assertNotEqual(icons.render(None,True,True).tobytes(),icons.render(None,False,True).tobytes())
        self.assertEqual(icons.arc_color(20,False,20,(255,255,255)),icons.RED)
        self.assertEqual(icons.arc_color(30,False,20,(255,255,255)),icons.AMBER)
        self.assertEqual(icons.arc_color(60,True,20,(255,255,255)),icons.GREEN)

    def test_brand_color_and_offline_alpha_preserve_shape(self):
        for brand in ("xbox","playstation","nintendo"):
            glyph=icon_artwork.glyph("gamepad",64,(255,255,255),255,brand,True)
            faded=icon_artwork.glyph("gamepad",64,(255,255,255),110,brand,True)
            self.assertEqual(faded.getchannel("A").getextrema()[1],110)
            # Alpha-1 antialias pixels can round to zero when dimmed; every
            # pixel must still retain the same color and proportional opacity.
            for y in range(64):
                for x in range(64):
                    active_pixel=glyph.getpixel((x,y))
                    faded_pixel=faded.getpixel((x,y))
                    self.assertEqual(active_pixel[:3],faded_pixel[:3])
                    self.assertAlmostEqual(faded_pixel[3],active_pixel[3]*110/255,delta=0.5)
        for brand,color in (("xbox",(16,124,16)),("nintendo",(230,0,18))):
            glyph=icon_artwork.glyph("gamepad",64,(255,255,255),255,brand,True)
            colors={glyph.getpixel((x,y))[:3] for y in range(64) for x in range(64) if glyph.getpixel((x,y))[3]>200}
            self.assertIn(color,colors)
        ps=icon_artwork.glyph("gamepad",256,(255,255,255),255,"playstation",True)
        colors={ps.getpixel((x,y))[:3] for y in range(256) for x in range(256) if ps.getpixel((x,y))[3]>200}
        for color in ((222,0,41),(243,194,2),(50,109,179),(0,170,158)):
            self.assertIn(color,colors)
        self.assertEqual(icon_artwork._image("xbox-color",256).getchannel("A").getbbox(),(33,33,223,223))
        self.assertEqual(icon_artwork._image("playstation-color",256).getchannel("A").getbbox(),(34,54,222,202))

    def test_dark_xbox_master_preserves_historical_source_bytes(self):
        encoded = icon_artwork._PNG["xbox-color-dark", 256]
        self.assertEqual(hashlib.sha256(base64.b64decode(encoded)).hexdigest(),
                         "b60a398bb2871a8baf2b70714eeff04028083c691f623263c4ecdc69da7617f6")
        self.assertEqual(icon_artwork._image("xbox-color-dark", 256).getchannel("A").getbbox(),
                         (27, 35, 217, 225))

    def test_xbox_color_uses_accepted_optical_placement_for_each_theme(self):
        def alpha_centroid(image):
            alpha = image.getchannel("A")
            pixels = [(x + .5, y + .5, alpha.getpixel((x, y)))
                      for y in range(image.height) for x in range(image.width)]
            mass = sum(a for x, y, a in pixels)
            return (sum(x * a for x, y, a in pixels) / mass,
                    sum(y * a for x, y, a in pixels) / mass)

        for size in (16, 20, 24, 32, 40, 48, 64):
            with self.subTest(size=size):
                dark = icon_artwork.glyph("gamepad", size, (255, 255, 255), 255, "xbox", True)
                light = icon_artwork.glyph("gamepad", size, (0, 0, 0), 255, "xbox", True)
                mono = icon_artwork.glyph("gamepad", size, (255, 255, 255), 255, "xbox", False)
                self.assertEqual(light.tobytes(), icon_artwork._image("xbox-color", size).tobytes())
                self.assertEqual(dark.tobytes(), icon_artwork._image("xbox-color-dark", size).tobytes())
                self.assertNotEqual(dark.tobytes(), light.tobytes())
                # Historical per-size masks differ slightly at antialiased edges.
                # Compare ink placement rather than requiring identical alpha bytes.
                for actual, expected in zip(alpha_centroid(light), alpha_centroid(mono)):
                    self.assertAlmostEqual(actual, expected, delta=.025)
                # The historical dark placement is baked once into its master;
                # per-size antialiasing changes its measured centroid by <.05 px.
                for actual, centered, offset in zip(alpha_centroid(dark), alpha_centroid(mono),
                                                   (-.375, .125)):
                    self.assertAlmostEqual(actual - centered, offset * size / 16, delta=.05)
                for theme in (False, True):
                    plain = icons.render(75, False, True, light_taskbar=theme, size=size)
                    branded = icons.render(75, False, True, light_taskbar=theme, size=size,
                                           badge="gamepad", brand="xbox", brand_color=True)
                    for y in range(size):
                        for x in range(size):
                            if math.hypot(x + .5 - size / 2, y + .5 - size / 2) > 6.8 * size / 16:
                                self.assertEqual(branded.getpixel((x, y)), plain.getpixel((x, y)))

    def test_charging_animation_changes_ring_without_modulating_brand(self):
        frames=icons.charging_frames(None,True,20,False,"gamepad","",size=20,brand="playstation",brand_color=True)
        self.assertEqual(len(frames),icons.BREATH_FRAMES)
        self.assertNotEqual(frames[0].tobytes(),frames[icons.BREATH_FRAMES // 2].tobytes())
        self.assertEqual(frames[0].crop((5,5,15,15)).tobytes(),frames[icons.BREATH_FRAMES // 2].crop((5,5,15,15)).tobytes())
        offline=icons.charging_frames(60,False,20,False,"gamepad","",size=20,brand="xbox",brand_color=True)
        self.assertEqual(offline[0].tobytes(),offline[icons.BREATH_FRAMES // 2].tobytes())

    def test_unknown_charging_rotates_clockwise_without_pulsing(self):
        self.assertEqual(icons.BREATH_PERIOD, 3.0)
        self.assertEqual(icons.BREATH_FRAMES / icons.BREATH_PERIOD, 30)
        for light in (False, True):
            for size in (16, 20, 24):
                frames = icons.charging_frames(None, True, light_taskbar=light, size=size)
                self.assertEqual(len({frame.tobytes() for frame in frames}), icons.BREATH_FRAMES)
                for i, frame in enumerate(frames):
                    with self.subTest(light=light, size=size, frame=i):
                        # Green-only mass locates the arc independently of the neutral track.
                        weights = [(x + .5 - size / 2, y + .5 - size / 2,
                                    max(0, g - max(r, b)) * a)
                                   for y in range(size) for x in range(size)
                                   for r, g, b, a in [frame.getpixel((x, y))]]
                        dx = sum(x * weight for x, y, weight in weights)
                        dy = sum(y * weight for x, y, weight in weights)
                        angle = (math.degrees(math.atan2(dy, dx)) + 90) % 360
                        expected = 360 * i / icons.BREATH_FRAMES + 45
                        self.assertLess(abs((angle - expected + 180) % 360 - 180), 8)
                        # Changing the old breathing input cannot dim this arc.
                        dim = icons.render(None, True, True, light_taskbar=light, size=size,
                                           pulse=0, animation_phase=i / icons.BREATH_FRAMES)
                        self.assertEqual(frame.tobytes(), dim.tobytes())
                self.assertEqual(frames[0].tobytes(), icons.render(
                    None, True, True, light_taskbar=light, size=size, animation_phase=1).tobytes())

    def test_unknown_charging_preserves_every_glyph_layer_and_central_pixels(self):
        styles = [(kind, "default", False) for kind in
                  ("mouse", "keyboard", "headset", "microphone", "bluetooth", "gamepad")]
        styles += [("gamepad", brand, color) for brand in
                   ("xbox", "playstation", "nintendo") for color in (False, True)]
        real_glyph = icon_artwork.glyph
        for light in (False, True):
            for size in (16, 20, 24):
                for badge, brand, color in styles:
                    with self.subTest(light=light, size=size, badge=badge, brand=brand, color=color):
                        layers = []
                        def capture(*args, **kwargs):
                            layer = real_glyph(*args, **kwargs)
                            layers.append(layer.tobytes())
                            return layer
                        with mock.patch.object(icon_artwork, "glyph", side_effect=capture):
                            frames = icons.charging_frames(None, True, light_taskbar=light,
                                badge=badge, size=size, brand=brand, brand_color=color)
                        self.assertEqual(len(layers), icons.BREATH_FRAMES)
                        expected = real_glyph(icons.KINDS[badge], size,
                            (0, 0, 0) if light else (255, 255, 255), 255, brand, color)
                        self.assertEqual(set(layers), {expected.tobytes()})
                        box = (math.ceil(size * .31),) * 2 + (math.floor(size * .69),) * 2
                        self.assertEqual(len({frame.crop(box).tobytes() for frame in frames}), 1)

    def test_unknown_track_stays_neutral_outside_the_rotating_arc(self):
        for light in (False, True):
            for size in (16, 20, 24):
                # A quarter-turn moves the arc by exactly the same amount; the
                # opposite half-ring remains the same neutral track throughout.
                reference = icons.render(0, False, True, light_taskbar=light, size=size)
                for phase in (0, .25, .5, .75):
                    image = icons.render(None, True, True, light_taskbar=light,
                                         size=size, animation_phase=phase)
                    for y in range(size):
                        for x in range(size):
                            angle = (math.degrees(math.atan2(y + .5 - size / 2,
                                                           x + .5 - size / 2)) + 90) % 360
                            relative = (angle - phase * 360) % 360
                            if 150 < relative < 300:
                                self.assertEqual(image.getpixel((x, y)), reference.getpixel((x, y)))

    def test_known_level_breathing_ignores_rotation_phase(self):
        for level in (0, 1, 20, 50, 99, 100):
            for light in (False, True):
                frames = icons.charging_frames(level, True, light_taskbar=light, badge="mouse", size=20)
                for i, frame in enumerate(frames):
                    expected = icons.render(level, True, True, light_taskbar=light, badge="mouse",
                        size=20, pulse=icons.breath_level(i / icons.BREATH_FRAMES), animation_phase=.37)
                    self.assertEqual(frame.tobytes(), expected.tobytes())
                # Every old 10 FPS phase is retained at the corresponding third frame.
                for old_index in range(30):
                    old_sample = icons.render(level, True, True, light_taskbar=light,
                        badge="mouse", size=20, pulse=icons.breath_level(old_index / 30))
                    self.assertEqual(frames[old_index * 3].tobytes(), old_sample.tobytes())
                if level:
                    self.assertNotEqual(frames[0].tobytes(), frames[icons.BREATH_FRAMES // 2].tobytes())

    def test_unknown_offline_idle_and_paused_ignore_rotation_phase(self):
        for charging, online, paused in ((True, False, False), (False, True, False),
                                        (False, False, False), (False, True, True)):
            baseline = icons.render(None, charging, online, light_taskbar=False,
                                    badge="mouse", size=20, force_active=paused)
            for phase in (.25, .5, .75, 1):
                self.assertEqual(baseline.tobytes(), icons.render(None, charging, online,
                    light_taskbar=False, badge="mouse", size=20, force_active=paused,
                    animation_phase=phase).tobytes())
        offline = icons.charging_frames(None, False, light_taskbar=False, size=20)
        self.assertEqual(len({image.tobytes() for image in offline}), 1)

    def test_number_and_disabled_badges_override_artwork(self):
        for brand in ("default","xbox","playstation","nintendo"):
            self.assertEqual(icons.render(60,False,True,badge="gamepad",text="60",brand=brand,brand_color=True).tobytes(),
                             icons.render(60,False,True,text="60").tobytes())
            self.assertEqual(icons.render(60,False,True,badge="",brand=brand,brand_color=True).tobytes(),
                             icons.render(60,False,True).tobytes())

if __name__ == "__main__":
    unittest.main()
