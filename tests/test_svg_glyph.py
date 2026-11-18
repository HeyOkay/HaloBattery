"""Offline SVG validation and mask tests: no native renderer, network or hardware."""
import io
import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
import math
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageColor

from halo import svg_glyph as G


SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="none" stroke="currentColor" stroke-width="2" d="M 4 4 L 20 20"/></svg>'


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


class FakeRaster:
    def __init__(self, kind='wide'):
        self.calls = []
        self.kind = kind

    def __call__(self, markup, size):
        self.calls.append((markup, size))
        image = Image.new('RGBA', (size, size), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)
        if self.kind == 'wide':
            # Deliberately off-centre source ink: placement must come from alpha.
            draw.rectangle((91, 299, 890, 498), fill=(255, 255, 255, 255))
        elif self.kind == 'tall':
            draw.rectangle((413, 49, 612, 848), fill=(255, 255, 255, 255))
        elif self.kind == 'round':
            draw.ellipse((200, 90, 799, 689), fill=(255, 255, 255, 255))
        elif self.kind == 'clipped':
            draw.rectangle((0, 0, 1023, 1023), fill=(255, 255, 255, 255))
        return image


class ColorRaster:
    def __init__(self,mixed=False,bad_alpha=False):
        self.calls=[]
        self.mixed=mixed
        self.bad_alpha=bad_alpha

    def __call__(self,markup,size):
        self.calls.append(markup)
        tree=ET.fromstring(markup)
        white=tree.get('color')=='#ffffff'
        theme=(255,255,255) if white else (0,0,0)
        image=Image.new('RGBA',(size,size),(0,0,0,0))
        draw=ImageDraw.Draw(image)
        draw.rectangle((112,200,911,799),fill=(*theme,255) if self.mixed else (221,46,68,255))
        if self.mixed:
            draw.rectangle((400,350,623,649),fill=(47,136,255,255))
        if self.bad_alpha and white:
            image.putpixel((112,200),(255,255,255,0))
        return image


class ValidationTests(unittest.TestCase):
    def test_static_geometry_is_whitened_and_rasterized_twice(self):
        raster = FakeRaster()
        glyph = G.prepare_glyph(SVG, rasterizer=raster)
        self.assertEqual(len(raster.calls), 2)
        for markup, size in raster.calls:
            self.assertEqual(size, 1024)
            tree = ET.fromstring(markup)
            self.assertEqual(tree.attrib['width'], '1024')
            self.assertNotIn('currentColor', markup)
            self.assertIn('stroke="#ffffff"', markup)
        self.assertNotEqual(raster.calls[0][0], raster.calls[1][0])
        self.assertIsInstance(glyph, G.CustomGlyph)

    def test_unsafe_and_unsupported_content_never_reaches_renderer(self):
        bodies = [
            '<script>alert(1)</script>', '<foreignObject/>', '<style>path{fill:red}</style>',
            '<text>font</text>', '<image href="data:image/png;base64,AAAA"/>',
            '<use href="#p"/>', '<defs/>', '<filter/>', '<mask/>', '<clipPath/>',
            '<linearGradient/>', '<animate/>', '<svg/>',
            '<path onload="alert(1)" d="M0 0L10 10"/>',
            '<path style="fill:red" d="M0 0L10 10"/>',
            '<path fill="url(https://host/icon)" d="M0 0L10 10"/>',
            '<path href="file:///secret" d="M0 0L10 10"/>',
            '<path filter="url(#x)" d="M0 0L10 10"/>',
            '<path vector-effect="non-scaling-stroke" d="M0 0L10 10"/>',
            '<x:path xmlns:x="https://attacker" d="M0 0L10 10"/>',
        ]
        bad = ['<svg>' + body + '</svg>' for body in bodies]
        bad.extend(['<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///secret">]><svg>&e;</svg>',
                    '<?xml-stylesheet href="https://host/style"?><svg/>',
                    '<!DOCTYPE svg SYSTEM "https://host/x"><svg/>',
                    '<svg>&unknown;</svg>', '<svg><path></svg>', '<html/>'])
        for svg in bad:
            with self.subTest(svg=svg):
                raster = mock.Mock()
                with self.assertRaises(G.SvgGlyphError):
                    G.prepare_glyph(svg, rasterizer=raster)
                raster.assert_not_called()

    def test_numeric_path_transform_and_dimension_errors_are_rejected(self):
        attributes = ['width="0"', 'width="4097"', 'height="12px"',
                      'viewBox="0 0 0 24"', 'viewBox="0 0 nan 24"',
                      'transform="translate(nan)"', 'transform="matrix(1 0 0 1 0)"',
                      'transform="skewX(90)"', 'opacity="1.01"', 'stroke-width="-1"',
                      'stroke-miterlimit="100"', 'fill="rgba(0,0,0,0.2)"']
        paths = ['', 'L0 0', 'M0', 'M0 0 C1 2', 'M0 0LNaN 2', 'M0 0 U1 2',
                 'M0 0A1 1 0 2 0 3 3', 'M0 0A-1 1 0 0 0 3 3', 'M0 0L1e999 0',
                 'M0 0L1000001 1', 'M0 0L']
        cases = ['<svg '+a+'><path d="M0 0L10 10"/></svg>' for a in attributes]
        cases += ['<svg><path d="'+p+'"/></svg>' for p in paths]
        for svg in cases:
            with self.subTest(svg=svg), self.assertRaises(G.SvgGlyphError):
                G.prepare_glyph(svg, rasterizer=mock.Mock(side_effect=AssertionError('unsafe raster')))

    def test_input_types_and_invalid_unicode_are_refused(self):
        for value in (None,b'<svg/>',123,'<svg>\ud800</svg>'):
            with self.subTest(value=repr(value)),self.assertRaises(G.SvgGlyphError):
                G.prepare_glyph(value,rasterizer=mock.Mock())

    def test_complexity_limits(self):
        cases = [' ' * (G.MAX_SVG_BYTES+1),
                 '<svg>'+('<g>'*G.MAX_DEPTH)+'<path d="M0 0L1 1"/>'+('</g>'*G.MAX_DEPTH)+'</svg>',
                 '<svg>'+('<circle r="1"/>'*G.MAX_NODES)+'</svg>',
                 '<svg><path d="M0 0'+('z'*(G.MAX_PATH_OPS+1))+'"/></svg>',
                 '<svg><path d="M0 0'+('L1 1'*(G.MAX_PATH_OPS+1))+'"/></svg>']
        for value in cases:
            with self.subTest(length=len(value)), self.assertRaises(G.SvgGlyphError):
                G.prepare_glyph(value,rasterizer=mock.Mock(side_effect=AssertionError('unsafe raster')))

    def test_invisible_empty_and_clipped_artwork_are_refused(self):
        for svg in ('<svg/>', '<svg><path fill="none" d="M0 0L1 1"/></svg>',
                    '<svg opacity="0"><circle r="10"/></svg>'):
            with self.assertRaises(G.SvgGlyphError):
                G.prepare_glyph(svg,rasterizer=mock.Mock())
        for kind in ('empty','clipped'):
            with self.subTest(kind=kind), self.assertRaises(G.SvgGlyphError):
                G.prepare_glyph(SVG,rasterizer=FakeRaster(kind))

    def test_renderer_errors_and_wrong_outputs_are_wrapped(self):
        for raster in (mock.Mock(side_effect=OSError('driver failure')),
                       mock.Mock(return_value=Image.new('RGB',(1024,1024))),
                       mock.Mock(return_value=Image.new('L',(16,16))),
                       mock.Mock(return_value=None)):
            with self.assertRaises(G.SvgGlyphError):
                G.prepare_glyph(SVG,rasterizer=raster)

    def test_comments_metadata_basic_shapes_and_stroke_rules(self):
        svg = '''<!-- license --><svg class="icon" id="root" viewBox="-100 -100 4000 4000">
          <g transform="translate(5,-7) rotate(15) scale(1.2 .8) skewY(10)" opacity=".7"
             fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square"
             stroke-linejoin="bevel" stroke-dasharray="1 2" stroke-dashoffset=".5">
            <circle cx="2" cy="3" r="4"/><ellipse rx="3" ry="4"/>
            <rect x="2" y="3" width="4" height="5" rx="1"/>
            <line x1="0" y1="1" x2="10" y2="11"/>
            <polygon points="0,0 5,10 10,0" fill="#abc" fill-rule="evenodd"/>
            <polyline points="0,0 3,4 8,2"/>
          </g></svg>'''
        self.assertIsInstance(G.prepare_glyph(svg,rasterizer=FakeRaster()),G.CustomGlyph)

    def test_conservative_bounds_include_nested_transform_and_stroke(self):
        svg='<svg transform="translate(100 200)"><g transform="scale(2)"><rect x="10" y="20" width="30" height="40" fill="none" stroke="black" stroke-width="4" stroke-miterlimit="1"/></g></svg>'
        tree,bounds=G._sanitize(svg)
        radius=2*math.sqrt(2)*2
        for actual,expected in zip(bounds,(120-radius,240-radius,180+radius,320+radius)):
            self.assertAlmostEqual(actual,expected)
        # Root transforms become group transforms, supported by Direct2D SVG1.1.
        self.assertNotIn('transform',tree.attrib)
        self.assertEqual(list(tree)[0].attrib['transform'],'translate(100 200)')

    def test_all_path_commands_and_effective_arc_radii_have_bounds(self):
        svg='<svg><path d="M0 0h4v3l2 1C6 4 8 9 10 10s3 2 4 0Q20 10 22 6t2-4a1 2 30 0 1 20 10z"/></svg>'
        _,bounds=G._sanitize(svg)
        self.assertTrue(all(math.isfinite(v) for v in bounds))
        self.assertLessEqual(bounds[0],0)
        self.assertGreaterEqual(bounds[2],44)
        # Effective radii expand when the supplied radius cannot reach endpoints.
        points=G._arc_bounds((0,0),(100,0),1,1,0,0,1)
        self.assertLessEqual(points[0][1],-50)
        self.assertGreaterEqual(points[1][1],50)


class MaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wide=G.prepare_glyph(SVG,rasterizer=FakeRaster('wide'))
        cls.tall=G.prepare_glyph(SVG,rasterizer=FakeRaster('tall'))

    def test_real_ink_is_centred_and_uniformly_fitted_at_every_size(self):
        for glyph,ratio in ((self.wide,4),(self.tall,.25)):
            for size in (16,20,24,32,40,48,64,80,128):
                with self.subTest(size=size,ratio=ratio):
                    alpha=glyph.render(size,(255,255,255)).getchannel('A')
                    box=alpha.getbbox()
                    self.assertAlmostEqual((box[0]+box[2])/2,size/2,delta=.5)
                    self.assertAlmostEqual((box[1]+box[3])/2,size/2,delta=.5)
                    width,height=box[2]-box[0],box[3]-box[1]
                    # Coverage boxes quantize each edge by up to a physical pixel.
                    self.assertLessEqual(abs(width-ratio*height),2+2*ratio)
                    with Image.open(io.BytesIO(glyph.png_bytes)) as master:
                        mx0,my0,mx1,my1=master.getbbox()
                        self.assertAlmostEqual((mx1-mx0)/(my1-my0),ratio,delta=.03)
                    for y in range(size):
                        for x in range(size):
                            if alpha.getpixel((x,y)):
                                self.assertLessEqual(math.hypot(x+.5-size/2,y+.5-size/2),G.RADIUS*size)

    def test_theme_alpha_and_owned_images_preserve_cached_master(self):
        before=self.wide.png_bytes
        dark=self.wide.render(32,(255,255,255))
        light=self.wide.render(32,(0,0,0))
        dim=self.wide.render(32,(255,255,255),110)
        self.assertEqual(dark.getchannel('A').tobytes(),light.getchannel('A').tobytes())
        self.assertEqual(dark.getchannel('A').getextrema()[1],230)
        self.assertEqual(dim.getchannel('A').getextrema()[1],round(110*230/255))
        self.assertIsNone(self.wide.render(32,(255,255,255),0).getchannel('A').getbbox())
        self.assertIsNone(self.wide.render(32,(255,255,255),foreground_opacity=0).getchannel('A').getbbox())
        dark.putalpha(0)
        self.assertEqual(self.wide.render(32,(255,255,255)).getchannel('A').getextrema()[1],230)
        self.assertEqual(before,self.wide.png_bytes)

    def test_cache_reload_canonicalizes_encoding_and_preserves_digest(self):
        original=self.wide
        restored=G.CustomGlyph.from_png(original.png_bytes)
        self.assertEqual(restored.digest,original.digest)
        self.assertEqual(restored.render(80,(1,2,3)).tobytes(),original.render(80,(1,2,3)).tobytes())
        with Image.open(io.BytesIO(original.png_bytes)) as mask:
            rgba=Image.new('RGBA',mask.size,(255,255,255,0));rgba.putalpha(mask)
        alternate=G.CustomGlyph.from_png(png(rgba),renderer_version=G.PREVIOUS_RENDERER_VERSION)
        self.assertEqual(alternate.digest,original.digest)
        self.assertNotEqual(self.tall.digest,original.digest)

    def test_invalid_cached_pngs_are_refused(self):
        empty=Image.new('L',(1024,1024))
        shifted=empty.copy();ImageDraw.Draw(shifted).rectangle((100,100,200,200),fill=255)
        ring=empty.copy();ImageDraw.Draw(ring).ellipse((0,0,1023,1023),outline=255,width=5)
        images=[empty,shifted,ring,Image.new('L',(16,16)),Image.new('RGB',(1024,1024),'white'),
                Image.new('RGBA',(1024,1024),(0,0,0,255))]
        for data in [b'not PNG',b'x'*(G.MAX_PNG_BYTES+1),None]+[png(im) for im in images]:
            with self.subTest(type=type(data)),self.assertRaises(G.SvgGlyphError):
                G.CustomGlyph.from_png(data)

    def test_row_extremes_match_every_painted_pixel_for_sparse_masks(self):
        for width,height,points in (
                (17,11,[(0,0,1),(16,0,255),(3,10,1),(14,10,2),(8,5,255)]),
                (18,12,[(0,4,2),(17,4,1),(3,0,1),(14,11,255)]),
                (19,13,[(1,1,255),(2,1,1),(18,12,1)])):
            with self.subTest(size=(width,height)):
                mask=Image.new('L',(width,height))
                for x,y,alpha in points:
                    mask.putpixel((x,y),alpha)
                expected=max(math.hypot(x+.5-width/2,y+.5-height/2)
                             for y in range(height) for x in range(width)
                             if mask.getpixel((x,y)))
                self.assertEqual(G._ink_radius(mask.tobytes(),width,height),expected)
        self.assertEqual(G._ink_radius(bytes(100),10,10),0)

    def test_cached_radial_guard_keeps_sparse_alpha_one_extremes(self):
        # Symmetric bounds pass the centring check. Sparse alpha-1 paint must
        # still be checked, including rows without any painted middle pixel.
        safe=Image.new('L',(1024,1024))
        safe.putpixel((89,500),1); safe.putpixel((934,523),1)
        self.assertIsInstance(G.CustomGlyph.from_png(png(safe),renderer_version=G.LEGACY_RENDERER_VERSION),G.CustomGlyph)
        outside=Image.new('L',(1024,1024))
        outside.putpixel((89,480),1); outside.putpixel((934,543),1)
        with self.assertRaisesRegex(G.SvgGlyphError,'boundary'):
            G.CustomGlyph.from_png(png(outside),renderer_version=G.LEGACY_RENDERER_VERSION)

    def test_comfortable_span_and_radial_budget_for_round_tall_and_wide_ink(self):
        for kind in ('round','tall','wide'):
            glyph=G.prepare_glyph(SVG,rasterizer=FakeRaster(kind))
            with self.subTest(kind=kind),Image.open(io.BytesIO(glyph.png_bytes)) as master:
                bounds=master.getbbox()
                self.assertLessEqual(max(bounds[2]-bounds[0],bounds[3]-bounds[1]),G.MAX_INK_SPAN*1024+1)
                self.assertLessEqual(G._ink_radius(master.tobytes(),1024,1024),G.RADIUS*1024+1)
                self.assertGreaterEqual(6.875-16*G._ink_radius(master.tobytes(),1024,1024)/1024,.95)
                if kind=='round':
                    self.assertLessEqual(G._ink_radius(master.tobytes(),1024,1024)*16/1024,5.55)

    def test_v1_png_refits_offline_and_validates_original_source_before_refit(self):
        old=Image.new('L',(1024,1024))
        ImageDraw.Draw(old).ellipse((100,100,923,923),fill=255)
        data=png(old)
        with self.assertRaises(G.SvgGlyphError):
            G.CustomGlyph.from_png(data)
        migrated=G.CustomGlyph.from_png(data,renderer_version=G.LEGACY_RENDERER_VERSION)
        with Image.open(io.BytesIO(migrated.png_bytes)) as master:
            box=master.getbbox()
            self.assertLessEqual(max(box[2]-box[0],box[3]-box[1]),G.MAX_INK_SPAN*1024+1)
        self.assertNotEqual(migrated.png_bytes,data)
        self.assertEqual(G.CustomGlyph.from_png(migrated.png_bytes).digest,migrated.digest)
        invalid=old.copy()
        invalid.putpixel((0,0),1);invalid.putpixel((1023,1023),1)
        with self.assertRaisesRegex(G.SvgGlyphError,'boundary'):
            G.CustomGlyph.from_png(png(invalid),renderer_version=G.LEGACY_RENDERER_VERSION)
        for version in ('svg-glyph-v0','direct2d-static-centre-v0','direct2d-static-centre-v4','future',123,False):
            with self.subTest(version=version),self.assertRaisesRegex(G.SvgGlyphError,'revision'):
                G.CustomGlyph.from_png(self.wide.png_bytes,renderer_version=version)

    def test_render_argument_validation(self):
        cases=[dict(size=True),dict(size=0),dict(size=1025),dict(fg=(256,0,0)),
               dict(fg=[0,0,0]),dict(alpha=-1),dict(alpha=True),dict(alpha=1.5),
               dict(foreground_opacity=float('nan')),dict(foreground_opacity=1.1)]
        for kwargs in cases:
            options=dict(size=20,fg=(255,255,255));options.update(kwargs)
            with self.subTest(options=options),self.assertRaises(G.SvgGlyphError):
                self.wide.render(**options)


class ColorGlyphTests(unittest.TestCase):
    FIXED='<svg viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="16" fill="#dd2e44"/></svg>'
    MIXED='<svg viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="16" fill="currentColor"/><circle cx="12" cy="12" r="3" fill="#2f88ff"/></svg>'

    def test_effective_paints_classify_inheritance_current_color_and_neutrals(self):
        cases=[('<svg><circle r="3"/></svg>','color'),
               ('<svg fill="white"><circle r="3"/></svg>','color'),
               ('<svg color="black" fill="currentColor"><circle r="3"/></svg>','color'),
               ('<svg fill="currentColor"><circle r="3"/></svg>','monochrome'),
               ('<svg color="red" fill="currentColor"><circle r="3"/></svg>','color'),
               ('<svg color="red"><g color="inherit" fill="currentColor"><circle r="3"/></g></svg>','color'),
               ('<svg><circle r="3" fill="#555"/></svg>','color'),
               ('<svg><circle r="3" fill="black"/><circle r="1" fill="white"/></svg>','color'),
               ('<svg fill="currentColor"><circle r="3"/><circle r="1" fill="white"/></svg>','mixed'),
               ('<svg stroke="currentColor" fill="none"><circle r="3" fill="red"/></svg>','mixed'),
               ('<svg fill="currentColor"><circle r="3"/><circle r="1" fill="red" opacity="0"/></svg>','monochrome'),
               ('<svg><circle r="3" fill="red" fill-opacity="0" stroke="currentColor"/></svg>','monochrome'),
               ('<svg><line x2="3" y2="3" stroke="currentColor"/></svg>','monochrome')]
        for source,expected in cases:
            with self.subTest(source=source):
                self.assertEqual(G.svg_color_mode(source),expected)

    def test_authored_black_white_and_implicit_black_do_not_invert_with_theme(self):
        for paint,expected in ((' fill="#000"',(0,0,0)),(' fill="#fff"',(255,255,255)),('',(0,0,0))):
            with self.subTest(paint=paint):
                source='<svg viewBox="0 0 24 24"><rect x="2" y="4" width="20" height="16"'+paint+'/></svg>'
                def raster(markup,size):
                    shape=next(n for n in ET.fromstring(markup).iter() if n.tag.endswith('rect'))
                    color=ImageColor.getrgb(shape.get('fill'))
                    image=Image.new('RGBA',(size,size))
                    ImageDraw.Draw(image).rectangle((112,200,911,799),fill=(*color,255))
                    return image
                glyph=G.prepare_glyph(source,rasterizer=raster)
                self.assertEqual(glyph.color_mode,'color')
                black=glyph.render(80,(0,0,0))
                white=glyph.render(80,(255,255,255))
                self.assertEqual(black.tobytes(),white.tobytes())
                self.assertEqual(black.getpixel((40,40)),(*expected,255))
                self.assertEqual(glyph.render(80,(255,255,255),110).getpixel((40,40)),(*expected,110))

    def test_single_solid_color_preserves_rgb_theme_and_full_opacity(self):
        glyph=G.prepare_glyph(self.FIXED,rasterizer=ColorRaster())
        self.assertTrue(glyph.is_colored)
        self.assertEqual(glyph.color_mode,'color')
        with Image.open(io.BytesIO(glyph.png_bytes)) as master:
            self.assertEqual((master.mode,master.size),('RGBA',(1024,1024)))
        dark=glyph.render(80,(255,255,255))
        light=glyph.render(80,(0,0,0),foreground_opacity=0)
        self.assertEqual(dark.tobytes(),light.tobytes())
        self.assertEqual(dark.getpixel((40,40)),(221,46,68,255))
        dim=glyph.render(80,(255,255,255),alpha=110)
        self.assertEqual(dim.getpixel((40,40)),(221,46,68,110))
        restored=G.CustomGlyph.from_png(glyph.png_bytes)
        self.assertEqual(restored.digest,glyph.digest)
        self.assertEqual(restored.render(80,(0,0,0)).tobytes(),light.tobytes())
        self.assertGreater(glyph.memory_bytes,5*1024*1024)

    def test_mixed_paired_masters_preserve_fixed_color_and_adapt_only_current_color(self):
        raster=ColorRaster(mixed=True)
        glyph=G.prepare_glyph(self.MIXED,rasterizer=raster)
        self.assertEqual(len(raster.calls),3)
        self.assertEqual(glyph.color_mode,'mixed')
        self.assertTrue(glyph.is_colored)
        with Image.open(io.BytesIO(glyph.png_bytes)) as master:
            self.assertEqual((master.mode,master.size),('RGBA',(2048,1024)))
        black=glyph.render(80,(0,0,0))
        white=glyph.render(80,(255,255,255))
        self.assertEqual(black.getchannel('A').tobytes(),white.getchannel('A').tobytes())
        self.assertEqual(black.getpixel((40,40)),(47,136,255,255))
        self.assertEqual(white.getpixel((40,40)),(47,136,255,255))
        self.assertEqual(black.getpixel((25,40)),(0,0,0,255))
        self.assertEqual(white.getpixel((25,40)),(255,255,255,255))
        custom=glyph.render(80,(20,100,200),foreground_opacity=0)
        self.assertEqual(custom.getpixel((25,40)),(20,100,200,255))
        self.assertEqual(custom.getpixel((40,40)),(47,136,255,255))
        self.assertEqual(glyph.render(80,(255,255,255),110).getpixel((40,40)),(47,136,255,110))
        restored=G.CustomGlyph.from_png(glyph.png_bytes)
        self.assertEqual(restored.digest,glyph.digest)
        self.assertEqual(restored.render(80,(255,255,255)).tobytes(),white.tobytes())
        self.assertGreater(glyph.memory_bytes,9*1024*1024)

    def test_mixed_native_and_persisted_alpha_disagreement_fails_closed(self):
        with self.assertRaisesRegex(G.SvgGlyphError,'alpha'):
            G.prepare_glyph(self.MIXED,rasterizer=ColorRaster(mixed=True,bad_alpha=True))
        glyph=G.prepare_glyph(self.MIXED,rasterizer=ColorRaster(mixed=True))
        with Image.open(io.BytesIO(glyph.png_bytes)) as master:
            bad=master.copy()
        bad.putpixel((1024+512,512),(255,255,255,0))
        with self.assertRaisesRegex(G.SvgGlyphError,'alpha'):
            G.CustomGlyph.from_png(png(bad))
        with self.assertRaises(G.SvgGlyphError):
            G.CustomGlyph.from_png(png(Image.new('RGBA',(2048,1024))),renderer_version=G.PREVIOUS_RENDERER_VERSION)

    def test_transparent_rgb_is_ignored_and_owned_color_images_do_not_mutate_cache(self):
        glyph=G.prepare_glyph(self.FIXED,rasterizer=ColorRaster())
        with Image.open(io.BytesIO(glyph.png_bytes)) as master:
            hidden=master.copy()
        hidden.putpixel((0,0),(19,47,229,0))
        self.assertEqual(G.CustomGlyph.from_png(png(hidden)).digest,glyph.digest)
        original=glyph.render(80,(255,255,255)).tobytes()
        changed=glyph.render(80,(255,255,255));changed.putalpha(0)
        self.assertEqual(glyph.render(80,(0,0,0)).tobytes(),original)
        self.assertFalse(hasattr(G._sized_color,'cache_info'))
        self.assertLessEqual(len(glyph._cache.images),G.RENDER_CACHE_ITEMS)

    def test_selected_master_reuses_resized_masks_across_frames_themes_and_threads(self):
        glyph=G.prepare_glyph(SVG,rasterizer=FakeRaster())
        original=G._sized_mask
        with mock.patch.object(G,'_sized_mask',wraps=original) as resize:
            def render(index):
                return glyph.render(32,(255,255,255) if index%2 else (0,0,0),110 if index%3 else 255)
            with ThreadPoolExecutor(max_workers=4) as pool:
                images=list(pool.map(render,range(30)))
            self.assertEqual(resize.call_count,1)
            self.assertEqual(len(images),30)
        self.assertEqual(glyph._cache.bytes,32*32)

    def test_object_resize_cache_is_bounded_and_does_not_keep_glyphs_alive(self):
        glyph=G.prepare_glyph(self.MIXED,rasterizer=ColorRaster(mixed=True))
        base=glyph.memory_bytes
        for size in (16,20,24,32,40,48,64,80,128,256,512,1024):
            glyph.render(size,(0,0,0));glyph.render(size,(255,255,255))
            self.assertLessEqual(len(glyph._cache.images),G.RENDER_CACHE_ITEMS)
            self.assertLessEqual(glyph._cache.bytes,G.RENDER_CACHE_BYTES)
        self.assertEqual(glyph.memory_bytes-base,glyph._cache.bytes)
        self.assertFalse(hasattr(G._sized_mask,'cache_info'))
        self.assertFalse(hasattr(G._sized_color,'cache_info'))
        reference=weakref.ref(glyph)
        del glyph
        gc.collect()
        self.assertIsNone(reference(),'A global render cache retained the unused detail master')

    def test_known_v2_monochrome_cache_stays_theme_aware_and_geometry_unchanged(self):
        glyph=G.prepare_glyph(SVG,rasterizer=FakeRaster())
        restored=G.CustomGlyph.from_png(glyph.png_bytes,renderer_version=G.PREVIOUS_RENDERER_VERSION)
        self.assertFalse(restored.is_colored)
        self.assertEqual(restored.color_mode,'monochrome')
        self.assertEqual(restored.png_bytes,glyph.png_bytes)
        self.assertEqual(restored.render(32,(255,255,255)).tobytes(),glyph.render(32,(255,255,255)).tobytes())
        with self.assertRaises(G.SvgGlyphError):
            G.CustomGlyph.from_png(G.prepare_glyph(self.FIXED,rasterizer=ColorRaster()).png_bytes,
                                   renderer_version=G.PREVIOUS_RENDERER_VERSION)

    def test_colored_renderer_must_supply_rgba(self):
        raster=lambda markup,size:FakeRaster()(markup,size).getchannel('A')
        with self.assertRaisesRegex(G.SvgGlyphError,'RGBA'):
            G.prepare_glyph(self.FIXED,rasterizer=raster)


if __name__=='__main__':
    unittest.main()
