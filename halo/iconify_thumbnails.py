"""Small, verified SVG glyphs for browsing; full selected assets stay separate.

The catalog verifies source and attribution before invoking prepare_preview.
No 1024px master, PNG round-trip, cache file or selected asset is created here.
Safety, painted bounds, optical fit, colours and mixed currentColor semantics
reuse the same helpers as the full tray asset renderer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math

from PIL import Image

from . import svg_glyph as _svg

PREVIEW_VERSION = 'iconify-small-preview-v1'
RASTER_SIZES = frozenset((128,256,512))


@dataclass(frozen=True)
class PreviewGlyph:
    raster_size: int
    digest: str
    _alpha_bytes: bytes = field(repr=False,compare=False)
    _rgba_bytes: bytes | None = field(default=None,repr=False,compare=False)
    _light_rgba_bytes: bytes | None = field(default=None,repr=False,compare=False)

    @property
    def color_mode(self):
        return 'mixed' if self._light_rgba_bytes is not None else ('color' if self._rgba_bytes is not None else 'monochrome')

    @property
    def is_colored(self):
        return self._rgba_bytes is not None

    @property
    def memory_bytes(self):
        return len(self._alpha_bytes)+(len(self._rgba_bytes) if self._rgba_bytes is not None else 0)+(len(self._light_rgba_bytes) if self._light_rgba_bytes is not None else 0)+len(self.digest)

    def render(self,size,fg,alpha=255,foreground_opacity=230/255):
        if isinstance(size,bool) or not isinstance(size,int) or not 1<=size<=1024:
            raise _svg.SvgGlyphError('Glyph size must be within 1..1024')
        if not isinstance(fg,tuple) or len(fg)!=3 or any(isinstance(v,bool) or not isinstance(v,int) or not 0<=v<=255 for v in fg):
            raise _svg.SvgGlyphError('Glyph foreground must be an RGB tuple')
        if isinstance(alpha,bool) or not isinstance(alpha,int) or not 0<=alpha<=255:
            raise _svg.SvgGlyphError('Glyph alpha must be within 0..255')
        if isinstance(foreground_opacity,bool) or not isinstance(foreground_opacity,(int,float)) or not math.isfinite(foreground_opacity) or not 0<=foreground_opacity<=1:
            raise _svg.SvgGlyphError('Glyph foreground opacity must be within 0..1')
        if not self.is_colored:
            mask=_sized(self._alpha_bytes,self.raster_size,size,'L')
            coverage=mask.point(lambda v:round(v*alpha*foreground_opacity/255))
            image=Image.new('RGBA',(size,size),fg+(0,));image.putalpha(coverage)
            return image
        image=_sized(self._rgba_bytes,self.raster_size,size,'RGBA').copy()
        if self._light_rgba_bytes is not None and fg!=(0,0,0):
            light=_sized(self._light_rgba_bytes,self.raster_size,size,'RGBA')
            if fg==(255,255,255):
                image=light.copy()
            else:
                channels=[Image.blend(image.getchannel(c),light.getchannel(c),v/255)
                          for c,v in zip(('R','G','B'),fg)]
                image=Image.merge('RGBA',(*channels,image.getchannel('A')))
        image.putalpha(image.getchannel('A').point(lambda v:round(v*alpha/255)))
        return image


def _sized(raw,raster_size,size,mode):
    image=Image.frombytes(mode,(raster_size,raster_size),raw)
    image=image.resize((size,size),Image.Resampling.BOX if size<raster_size else Image.Resampling.LANCZOS)
    coverage=image.getchannel('A') if mode=='RGBA' else image
    pixels=coverage.load()
    coverage.putdata([pixels[x,y] if math.hypot(x+.5-size/2,y+.5-size/2)<=_svg.RADIUS*size else 0
                      for y in range(size) for x in range(size)])
    if mode=='RGBA':
        image.putalpha(coverage)
    return image


def prepare_preview(svg,*,raster_size=128,rasterizer=None):
    """Render only a 128/256/512px browse glyph; never create a full asset PNG.

    A test rasterizer takes (sanitized_xml, raster_size), returning L or RGBA.
    Colour and mixed SVG require RGBA, just like the full asset renderer.
    """
    if type(raster_size) is not int or raster_size not in RASTER_SIZES:
        raise _svg.SvgGlyphError('Preview raster size must be 128, 256 or 512')
    root,bounds,mode=_svg._sanitize(svg,with_color=True)
    colored=mode!='monochrome';mixed=mode=='mixed'
    if mixed:
        root.set('color','#000000')
    if rasterizer is None:
        from ._svg_windows import rasterize
        rasterizer=lambda markup,size:rasterize(markup,size,preserve_color=colored)
    try:
        markup,viewport=_svg._viewport(root,bounds,size=raster_size)
        image=rasterizer(markup,raster_size)
        alpha,bbox=_svg._alpha(image,size=raster_size)
        left,top,extent=viewport
        pad=4*extent/raster_size
        tight=(left+bbox[0]*extent/raster_size-pad,top+bbox[1]*extent/raster_size-pad,
               left+bbox[2]*extent/raster_size+pad,top+bbox[3]*extent/raster_size+pad)
        markup,_=_svg._viewport(root,tight,size=raster_size)
        image=rasterizer(markup,raster_size)
        alpha,_=_svg._alpha(image,size=raster_size)
        if colored and image.mode!='RGBA':
            raise _svg.SvgGlyphError('Color SVG renderer must return RGBA')
        master=_svg._normalize(image if colored else alpha,size=raster_size)
        light=None
        if mixed:
            root.set('color','#ffffff')
            markup,_=_svg._viewport(root,tight,size=raster_size)
            light_image=rasterizer(markup,raster_size)
            light_alpha,_=_svg._alpha(light_image,size=raster_size)
            if light_image.mode!='RGBA' or light_alpha.tobytes()!=alpha.tobytes():
                raise _svg.SvgGlyphError('Mixed SVG rendering must keep identical theme alpha coverage')
            light=_svg._normalize(light_image,size=raster_size)
        coverage=master.getchannel('A') if colored else master
        raw=coverage.tobytes()
        rgba=master.tobytes() if colored else None
        light_raw=light.tobytes() if light is not None else None
        identity='\0'.join((PREVIEW_VERSION,_svg.RENDERER_VERSION,str(raster_size),mode)).encode('ascii')
        digest=hashlib.sha256(identity+b'\0'+(rgba if colored else raw)+(light_raw or b'')).hexdigest()
        return PreviewGlyph(raster_size,digest,raw,rgba,light_raw)
    except _svg.SvgGlyphError:
        raise
    except Exception as error:
        raise _svg.SvgGlyphError('SVG preview could not be rendered') from error
