"""Pinned Lucide device glyph source geometry and offline rendering states."""
import hashlib
import json
import math
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from PIL import Image
from halo import icon_artwork, icons
from tools.make_microphone_artwork import _place_masks

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64)
SOURCES = {
    "speaker": "a7169d896cf1f1dcadebd67a8a3c9d650c84e27b9395fa85e63462154b2052c3",
    "webcam": "5301b9ff75048743f5436594c0c7ce1fada1653620b1c9f6f34fa3c667b09b60",
    "health": "5822440c5dd334ead1855bc676cdccda748cf61a914083a94339cecd4c88a400",
    "smart_home": "b2a1a51bb3ed4f996d086f6ec9c15d09256c6a70ca7f41f5a00d892c55b03d7a",
    "watch": "896fb08ab4056a89f174aec886d54fb9501aa157d998bc94069286a3717fe6ae",
    "display": "fb1260e517492839289f2607871a055b9eac9b82f765a88f897d3f27db1a2141",
    "tracker": "6092285ad2f49aecbd4521a31ac0ad3a17fa75a2406f5d98b8695df0205e0c13"}


class NewLucideArtworkTests(unittest.TestCase):
    def test_pinned_sources_keep_uniform_stroke_and_centered_geometry(self):
        manifest = json.loads((ROOT / "artwork/tray/manifest.json").read_text(encoding="utf-8"))
        for kind, expected_hash in SOURCES.items():
            with self.subTest(kind=kind):
                source_name = {"health": "heart", "smart_home": "house", "display": "tv-minimal", "tracker": "radar"}.get(kind, kind)
                source = (ROOT / f"artwork/tray/lucide-{source_name}.svg").read_bytes()
                self.assertNotIn(b"\r", source)
                self.assertEqual(hashlib.sha256(source).hexdigest(), expected_hash)
                svg = ET.fromstring(source)
                self.assertEqual(svg.attrib["viewBox"], "0 0 24 24")
                self.assertEqual(svg.attrib["stroke-width"], "2")
                self.assertEqual(svg.attrib["fill"], "none")
                self.assertEqual(svg.attrib["stroke-linecap"], "round")
                meta = manifest["svg_generation"][kind]
                self.assertEqual(meta["logical_scale"], .48 if kind == "display" else .5)
                if kind == "display":
                    self.assertEqual(meta["source_stroke_width"], 2)
                    self.assertAlmostEqual(meta["render_stroke_width"] * meta["logical_scale"], 1)
                    self.assertEqual(meta["logical_stroke_width"], 1)
                    self.assertLess((math.sqrt(113) + 2) * .48 + .5, 6.6)
                if kind not in ("speaker", "webcam"):
                    bounds = meta["logical_visible_ink_bounds"]
                    self.assertAlmostEqual((bounds[0]+bounds[2])/2, 8, delta=.008)
                    self.assertAlmostEqual((bounds[1]+bounds[3])/2, 8, delta=.008)
                    if kind == "health":
                        self.assertAlmostEqual(meta["logical_geometric_centering_translation"][1],
                                               -.24588317, delta=.015625)
                    elif kind == "smart_home":
                        self.assertAlmostEqual(meta["logical_geometric_centering_translation"][1],
                                               .25012, delta=.015625)
                else:
                    self.assertEqual(meta["logical_visible_ink_bounds"], [3.5, 2.5, 12.5, 13.5])
                self.assertEqual(meta["logical_optical_offset"], [0, 0])

    def test_all_sizes_preserve_proportions_center_and_ring_clearance(self):
        for kind in SOURCES:
            for size in SIZES:
                with self.subTest(kind=kind, size=size):
                    mask = icon_artwork._image(kind, size)
                    self.assertEqual((mask.mode, mask.size), ("L", (size, size)))
                    visible = mask.point(lambda value: 255 if value >= 32 else 0)
                    x0, y0, x1, y1 = visible.getbbox()
                    self.assertAlmostEqual((x0+x1)/2, size/2, delta=.6)
                    self.assertAlmostEqual((y0+y1)/2, size/2, delta=.6)
                    width, height = {"health": (11, 9.50792), "smart_home": (10, 10.5),
                                     "watch": (7, 10.985), "display": (10.6, 9.64), "tracker": (11, 11)}.get(kind, (9, 11))
                    self.assertAlmostEqual((x1-x0)/(y1-y0), width/height, delta=.14)
                    # A half-pixel source extent can cover both adjacent pixels
                    # at the 32-alpha threshold; allow one physical pixel per edge.
                    expected = ((16-width)/2, (16-height)/2,
                                (16+width)/2, (16+height)/2)
                    for edge, source_edge in zip((x0,y0,x1,y1), expected):
                        self.assertAlmostEqual(edge, source_edge*size/16, delta=1)
                    for y in range(size):
                        for x in range(size):
                            if math.hypot(x+.5-size/2, y+.5-size/2) > 6.6*size/16:
                                self.assertEqual(mask.getpixel((x, y)), 0)
                    dark = icon_artwork.glyph(kind, size, (255,255,255), 255)
                    light = icon_artwork.glyph(kind, size, (0,0,0), 255)
                    faded = icon_artwork.glyph(kind, size, (255,255,255), 110)
                    self.assertEqual(dark.getchannel("A").tobytes(), light.getchannel("A").tobytes())
                    self.assertEqual(faded.getchannel("A").tobytes(), mask.point(lambda a: round(a*110/255)).tobytes())

    def test_live_states_and_ninety_frames_keep_glyph_position(self):
        for kind in SOURCES:
            for light in (False, True):
                for size in SIZES:
                    settings = dict(light_taskbar=light, size=size)
                    fg = (0,0,0) if light else (255,255,255)
                    for level, charging, online, forced, alpha in (
                            (60,False,True,False,255), (60,False,False,False,110),
                            (None,False,True,False,110), (None,False,True,True,255)):
                        track = icons.render(level,charging,online,force_active=forced,**settings)
                        glyph = icon_artwork.glyph(kind,size,fg,alpha)
                        actual = icons.render(level,charging,online,badge=kind,force_active=forced,**settings)
                        self.assertEqual(actual.tobytes(), Image.alpha_composite(track,glyph).tobytes())
                    glyph = icon_artwork.glyph(kind,size,fg,255)
                    frames = icons.charging_frames(None,True,light_taskbar=light,badge=kind,size=size)
                    self.assertEqual(len(frames),90)
                    for i,frame in enumerate(frames):
                        track = icons.render(None,True,True,animation_phase=i/90,**settings)
                        self.assertEqual(frame.tobytes(),Image.alpha_composite(track,glyph).tobytes())
                    self.assertEqual(icons.render(60,False,True,badge=kind,text="60",**settings).tobytes(),
                                     icons.render(60,False,True,text="60",**settings).tobytes())

    def test_shared_generator_reuses_rows_without_changing_other_masks(self):
        manifest = json.loads((ROOT / "artwork/tray/manifest.json").read_text(encoding="utf-8"))
        original = Image.open(ROOT / "artwork/tray/masks.png").copy()
        synthetic = Image.new("L",(1024,1024),128)
        for kind in ("microphone", "speaker", "webcam", "health", "smart_home", "watch", "display", "tracker"):
            with self.subTest(kind=kind):
                updated, masks = _place_masks(manifest,original,synthetic,kind)
                self.assertEqual(masks,manifest["masks"])
                self.assertEqual(updated.size,original.size)
                for entry in masks:
                    if entry["name"] != kind:
                        box = tuple(entry["box"])
                        self.assertEqual(updated.crop(box).tobytes(),original.crop(box).tobytes())
                repeated, again = _place_masks(dict(manifest,masks=masks),updated,synthetic,kind)
                self.assertEqual(again,masks)
                self.assertEqual(repeated.tobytes(),updated.tobytes())


if __name__ == "__main__":
    unittest.main()
