"""Small browse glyphs retain full-renderer safety, colour and centre geometry."""
import json
from pathlib import Path
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from PIL import Image,ImageDraw

from halo import svg_glyph as G,icon_artwork,icons
from halo.iconify_thumbnails import prepare_preview,_sized
from halo.custom_icon_profile import with_builtin_profile

MONO='<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" fill="currentColor"/></svg>'
COLOR=MONO.replace('currentColor','#dd2e44')
MIXED='<svg viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" fill="currentColor"/><circle cx="12" cy="12" r="3" fill="#2f88ff"/></svg>'


class Raster:
    def __init__(self,mode='mono',bad=False):
        self.mode=mode;self.bad=bad;self.calls=[]

    def __call__(self,markup,size):
        self.calls.append((markup,size))
        tree=ET.fromstring(markup)
        white=tree.get('color')=='#ffffff'
        theme=(255,255,255) if white else (0,0,0)
        fill=(221,46,68) if self.mode=='color' else (theme if self.mode=='mixed' else (255,255,255))
        image=Image.new('RGBA',(size,size))
        draw=ImageDraw.Draw(image)
        draw.rectangle((size//8,size//4,size*7//8-1,size*3//4-1),fill=(*fill,255))
        if self.mode=='mixed':
            draw.rectangle((size*3//8,size*3//8,size*5//8-1,size*5//8-1),fill=(47,136,255,255))
        if self.bad and white:
            image.putpixel((size//2,size//2),(255,255,255,0))
        return image


class PreviewGlyphTests(unittest.TestCase):
    def test_only_requested_small_surfaces_are_rendered_without_full_png_work(self):
        for size in (128,256,512):
            raster=Raster()
            with self.subTest(size=size),mock.patch.object(G.CustomGlyph,'from_png',side_effect=AssertionError('full PNG decode')),mock.patch.object(Image.Image,'save',side_effect=AssertionError('PNG encode')):
                glyph=prepare_preview(MONO,raster_size=size,rasterizer=raster)
            self.assertEqual([n for _,n in raster.calls],[size,size])
            for markup,n in raster.calls:
                self.assertEqual(ET.fromstring(markup).get('width'),str(size))
                self.assertEqual(ET.fromstring(markup).get('height'),str(size))
            self.assertFalse(hasattr(glyph,'png_bytes'))
            self.assertEqual(glyph.memory_bytes,size*size+len(glyph.digest))

    def test_shared_fit_keeps_aspect_centre_and_reserved_ring_region(self):
        full=G.prepare_glyph(MONO,rasterizer=Raster())
        for raster_size in (128,256,512):
            preview=prepare_preview(MONO,raster_size=raster_size,rasterizer=Raster())
            for size in (16,20,24,32,80):
                with self.subTest(raster_size=raster_size,size=size):
                    expected=full.render(size,(255,255,255)).getchannel('A')
                    actual=preview.render(size,(255,255,255)).getchannel('A')
                    box=actual.point(lambda a:255 if a>=32 else 0).getbbox()
                    reference=expected.point(lambda a:255 if a>=32 else 0).getbbox()
                    self.assertAlmostEqual((box[0]+box[2])/2,size/2,delta=.5)
                    self.assertAlmostEqual((box[1]+box[3])/2,size/2,delta=.5)
                    for a,b in zip(box,reference):
                        self.assertLessEqual(abs(a-b),1)
                    self.assertEqual(actual.getpixel((0,0)),0)
                    self.assertEqual(actual.getpixel((size//2,0)),0)

    def test_fixed_color_preserves_rgb_themes_and_dim_alpha(self):
        glyph=prepare_preview(COLOR,rasterizer=Raster('color'))
        self.assertEqual(glyph.color_mode,'color');self.assertTrue(glyph.is_colored)
        black=glyph.render(32,(0,0,0));white=glyph.render(32,(255,255,255),foreground_opacity=0)
        self.assertEqual(black.tobytes(),white.tobytes())
        self.assertEqual(black.getpixel((16,16)),(221,46,68,255))
        self.assertEqual(glyph.render(32,(255,255,255),110).getpixel((16,16)),(221,46,68,110))
        self.assertLess(glyph.memory_bytes,90*1024)

    def test_mixed_current_color_and_fixed_layers_use_same_fit_and_alpha(self):
        raster=Raster('mixed');glyph=prepare_preview(MIXED,rasterizer=raster)
        self.assertEqual(glyph.color_mode,'mixed');self.assertEqual(len(raster.calls),3)
        black=glyph.render(32,(0,0,0));white=glyph.render(32,(255,255,255))
        self.assertEqual(black.getchannel('A').tobytes(),white.getchannel('A').tobytes())
        self.assertEqual(black.getpixel((16,16)),(47,136,255,255))
        self.assertEqual(white.getpixel((16,16)),(47,136,255,255))
        self.assertEqual(black.getpixel((8,16)),(0,0,0,255))
        self.assertEqual(white.getpixel((8,16)),(255,255,255,255))
        self.assertEqual(glyph.render(32,(20,100,200)).getpixel((8,16)),(20,100,200,255))
        self.assertLess(glyph.memory_bytes,150*1024)
        with self.assertRaisesRegex(G.SvgGlyphError,'alpha'):
            prepare_preview(MIXED,rasterizer=Raster('mixed',bad=True))

    def test_builtin_profiles_remain_exact_and_do_not_require_master_png(self):
        raw=prepare_preview(MONO,rasterizer=Raster())
        manifest=json.loads((Path(__file__).resolve().parents[1]/'artwork/tray/manifest.json').read_text(encoding='utf-8'))
        for kind,name in manifest['sources']['lucide']['glyphs'].items():
            glyph=with_builtin_profile('lucide:'+name,raw)
            self.assertGreaterEqual(glyph.memory_bytes,raw.memory_bytes)
            for size in (16,20,24,32,40,48,64,80):
                for fg in ((0,0,0),(255,255,255)):
                    with self.subTest(name=name,size=size,fg=fg):
                        expected=icon_artwork.glyph(kind,size,fg,110,foreground_opacity=230)
                        self.assertEqual(glyph.render(size,fg,110).tobytes(),expected.tobytes())
        self.assertIs(with_builtin_profile('tabler:mouse',raw),raw)

    def test_existing_ring_styles_battery_signals_and_dim_alpha_stay_independent(self):
        glyph=prepare_preview(MIXED,rasterizer=Raster('mixed'))
        for style in ('thin','classic'):
            for light in (False,True):
                fg=(0,0,0) if light else (255,255,255)
                for level,charging,online,paused in ((10,False,True,False),(75,True,True,False),
                        (None,True,True,False),(None,False,True,True),(60,False,False,False)):
                    with self.subTest(style=style,light=light,state=(level,charging,online,paused)):
                        options=dict(size=32,ring_style=style,light_taskbar=light,
                                     force_active=paused,pulse=.4,animation_phase=.25)
                        ring=icons.render(level,charging,online,**options)
                        active=online and (level is not None or charging or paused)
                        if paused:active=True
                        layer=glyph.render(32,fg,255 if active else 110)
                        expected=Image.alpha_composite(ring,layer)
                        actual=icons.render(level,charging,online,custom_glyph=glyph,**options)
                        self.assertEqual(actual.tobytes(),expected.tobytes())

    def test_digest_is_stable_bounded_and_images_are_owned(self):
        a=prepare_preview(COLOR,rasterizer=Raster('color'))
        b=prepare_preview(COLOR,rasterizer=Raster('color'))
        c=prepare_preview(COLOR,raster_size=256,rasterizer=Raster('color'))
        self.assertEqual(a.digest,b.digest);self.assertNotEqual(a.digest,c.digest)
        self.assertFalse(hasattr(_sized,'cache_info'))
        expected=a.render(32,(255,255,255)).tobytes()
        changed=a.render(32,(255,255,255));changed.putalpha(0)
        self.assertEqual(a.render(32,(255,255,255)).tobytes(),expected)

    def test_input_safety_and_surface_limits_run_before_rasterization(self):
        for size in (0,64,129,1024,True,128.,None):
            raster=mock.Mock()
            with self.subTest(size=size),self.assertRaises(G.SvgGlyphError):
                prepare_preview(MONO,raster_size=size,rasterizer=raster)
            raster.assert_not_called()
        for source in ('<svg><script/></svg>','<!DOCTYPE svg><svg/>',
                       '<svg><image href="https://host/picture"/></svg>',
                       '<svg><path fill="url(#gradient)" d="M0 0L10 10"/></svg>'):
            raster=mock.Mock()
            with self.subTest(source=source),self.assertRaises(G.SvgGlyphError):
                prepare_preview(source,rasterizer=raster)
            raster.assert_not_called()

    def test_invalid_or_empty_raster_and_bad_render_arguments_are_refused(self):
        for raster in (lambda markup,size:Image.new('L',(1024,1024)),
                       lambda markup,size:Image.new('RGBA',(size,size)),
                       lambda markup,size:Image.new('RGBA',(size,size),(255,255,255,255))):
            with self.assertRaises(G.SvgGlyphError):prepare_preview(MONO,rasterizer=raster)
        with self.assertRaisesRegex(G.SvgGlyphError,'RGBA'):
            prepare_preview(COLOR,rasterizer=lambda markup,size:Raster()(markup,size).getchannel('A'))
        glyph=prepare_preview(MONO,rasterizer=Raster())
        for change in (dict(size=True),dict(size=0),dict(fg=(256,0,0)),dict(alpha=-1),dict(foreground_opacity=float('nan'))):
            options=dict(size=32,fg=(255,255,255));options.update(change)
            with self.subTest(change=change),self.assertRaises(G.SvgGlyphError):glyph.render(**options)


if __name__=='__main__':unittest.main()
