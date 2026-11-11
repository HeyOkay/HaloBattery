"""Renderer states/DPI with offline handoff glyphs; no device or network access."""
import math
import unittest
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
        self.assertEqual(icon_artwork._image("xbox-color",256).getchannel("A").getbbox(),(27,35,217,225))
        self.assertEqual(icon_artwork._image("playstation-color",256).getchannel("A").getbbox(),(34,54,222,202))

    def test_charging_pulse_changes_ring_without_modulating_brand(self):
        frames=icons.charging_frames(None,True,20,False,"gamepad","",size=20,brand="playstation",brand_color=True)
        self.assertEqual(len(frames),icons.BREATH_FRAMES)
        self.assertNotEqual(frames[0].tobytes(),frames[15].tobytes())
        self.assertEqual(frames[0].crop((5,5,15,15)).tobytes(),frames[15].crop((5,5,15,15)).tobytes())
        offline=icons.charging_frames(60,False,20,False,"gamepad","",size=20,brand="xbox",brand_color=True)
        self.assertEqual(offline[0].tobytes(),offline[15].tobytes())

    def test_number_and_disabled_badges_override_artwork(self):
        for brand in ("default","xbox","playstation","nintendo"):
            self.assertEqual(icons.render(60,False,True,badge="gamepad",text="60",brand=brand,brand_color=True).tobytes(),
                             icons.render(60,False,True,text="60").tobytes())
            self.assertEqual(icons.render(60,False,True,badge="",brand=brand,brand_color=True).tobytes(),
                             icons.render(60,False,True).tobytes())

if __name__ == "__main__":
    unittest.main()
