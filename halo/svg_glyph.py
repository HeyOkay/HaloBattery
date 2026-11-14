"""Bounded static SVG imports and offline centre-only tray glyph masks.

SVG is allowlisted before the native parser sees it. Root viewport whitespace is
removed using conservative painted geometry, then two measured alpha passes.
The cached master contains theme-aware monochrome ink or original solid SVG
colours. Battery rings and state remain the responsibility of icons.render.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict
import hashlib
import io
import math
import re
import threading
import xml.etree.ElementTree as ET

from PIL import Image, ImageColor

LEGACY_RENDERER_VERSION = 'direct2d-static-centre-v1'
PREVIOUS_RENDERER_VERSION = 'direct2d-static-centre-v2'
RENDERER_VERSION = 'direct2d-static-centre-v3'
LEGACY_RENDERER_VERSIONS = (LEGACY_RENDERER_VERSION, PREVIOUS_RENDERER_VERSION)
MASTER_SIZE = 1024
MAX_SVG_BYTES = 262144
MAX_PNG_BYTES = 2 * 1024 * 1024
MAX_NODES = 1024
MAX_PATH_OPS = 8192
MAX_DEPTH = 24
# Match the built-in mouse/tracker's 11-unit ink span, while retaining the
# keyboard/gamepad's slightly larger radial corner allowance. The former6.6
# clipping boundary was a safety limit, not a comfortable painted-ink budget.
MAX_INK_SPAN = 11 / 16
RADIUS = 5.9 / 16
_LEGACY_RADIUS = 6.6 / 16
_NS = 'http://www.w3.org/2000/svg'
_NUMBER = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
_NUM = re.compile(_NUMBER)
_TRANSFORM = re.compile(r'(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^()]*)\)')
_IDENTITY = (1., 0., 0., 1., 0., 0.)
_PRESENTATION = {'fill', 'stroke', 'color', 'stroke-width', 'stroke-linecap',
                 'stroke-linejoin', 'stroke-miterlimit', 'fill-rule', 'clip-rule',
                 'opacity', 'fill-opacity', 'stroke-opacity', 'stroke-dasharray',
                 'stroke-dashoffset'}
_SHAPES = {'path': {'d'}, 'rect': {'x', 'y', 'width', 'height', 'rx', 'ry'},
           'circle': {'cx', 'cy', 'r'}, 'ellipse': {'cx', 'cy', 'rx', 'ry'},
           'line': {'x1', 'y1', 'x2', 'y2'}, 'polygon': {'points'}, 'polyline': {'points'}}


class SvgGlyphError(ValueError):
    """Artwork is unsafe, unsupported, empty or cannot be rendered."""


def _numbers(value, count=None):
    tokens = []
    at = 0
    for match in _NUM.finditer(value):
        if value[at:match.start()].strip(' ,\t\r\n'):
            raise SvgGlyphError('Invalid SVG number')
        number = float(match.group())
        if not math.isfinite(number) or abs(number) > 1_000_000:
            raise SvgGlyphError('SVG number is outside the supported range')
        tokens.append(number)
        at = match.end()
    if value[at:].strip(' ,\t\r\n') or not tokens or count is not None and len(tokens) != count:
        raise SvgGlyphError('Invalid SVG numeric list')
    return tokens


def _number(value):
    return _numbers(value, 1)[0]


def _mul(a, b):
    a0,a1,a2,a3,a4,a5 = a
    b0,b1,b2,b3,b4,b5 = b
    result = (a0*b0+a2*b1, a1*b0+a3*b1, a0*b2+a2*b3, a1*b2+a3*b3,
              a0*b4+a2*b5+a4, a1*b4+a3*b5+a5)
    if not all(math.isfinite(v) and abs(v) <= 1_000_000 for v in result):
        raise SvgGlyphError('SVG transform is outside the supported range')
    return result


def _transform(value):
    result, at = _IDENTITY, 0
    for match in _TRANSFORM.finditer(value):
        if value[at:match.start()].strip(' ,\t\r\n'):
            raise SvgGlyphError('Unsupported SVG transform')
        kind, body = match.groups()
        n = _numbers(body)
        if kind == 'matrix' and len(n) == 6:
            matrix = tuple(n)
        elif kind == 'translate' and len(n) in (1,2):
            matrix = (1,0,0,1,n[0],n[1] if len(n)==2 else 0)
        elif kind == 'scale' and len(n) in (1,2):
            matrix = (n[0],0,0,n[-1],0,0)
        elif kind == 'rotate' and len(n) in (1,3):
            angle = math.radians(n[0])
            c,s = math.cos(angle),math.sin(angle)
            matrix = (c,s,-s,c,0,0)
            if len(n)==3:
                matrix = _mul(_mul((1,0,0,1,n[1],n[2]),matrix),(1,0,0,1,-n[1],-n[2]))
        elif kind in ('skewX','skewY') and len(n)==1 and abs(n[0]) < 89:
            t = math.tan(math.radians(n[0]))
            matrix = (1,0,t,1,0,0) if kind=='skewX' else (1,t,0,1,0,0)
        else:
            raise SvgGlyphError('Invalid SVG transform arguments')
        result = _mul(result,matrix)
        at = match.end()
    if not at or value[at:].strip(' ,\t\r\n'):
        raise SvgGlyphError('Unsupported SVG transform')
    return result


def _arc_bounds(start, end, rx, ry, angle, large, sweep):
    # SVG 1.1 implementation notes F.6.5: endpoint -> centre conversion.
    # https://www.w3.org/TR/SVG11/implnote.html#ArcConversionEndpointToCenter
    rx,ry = abs(rx),abs(ry)
    if not rx or not ry or start == end:
        return [start,end]
    phi=math.radians(angle % 360)
    c,s=math.cos(phi),math.sin(phi)
    dx,dy=(start[0]-end[0])/2,(start[1]-end[1])/2
    x,y=c*dx+s*dy,-s*dx+c*dy
    ratio=(x/rx)**2+(y/ry)**2
    if ratio>1:
        factor=math.sqrt(ratio); rx*=factor; ry*=factor
    numerator=max(0.,rx*rx*ry*ry-rx*rx*y*y-ry*ry*x*x)
    denominator=rx*rx*y*y+ry*ry*x*x
    factor=math.sqrt(numerator/denominator) if denominator else 0.
    if large==sweep:
        factor=-factor
    u,v=factor*rx*y/ry,-factor*ry*x/rx
    cx=c*u-s*v+(start[0]+end[0])/2
    cy=s*u+c*v+(start[1]+end[1])/2
    ex=math.hypot(rx*c,ry*s); ey=math.hypot(rx*s,ry*c)
    # The entire effective ellipse is a conservative bound for either arc.
    return [(cx-ex,cy-ey),(cx+ex,cy+ey)]


def _path_points(value, budget):
    token_re = re.compile(r'[MmLlHhVvCcSsQqTtAaZz]|'+_NUMBER)
    tokens=[]; at=0
    for match in token_re.finditer(value):
        if value[at:match.start()].strip(' ,\t\r\n'):
            raise SvgGlyphError('Unsupported or invalid SVG path')
        tokens.append(match.group()); at=match.end()
    if value[at:].strip(' ,\t\r\n') or not tokens or tokens[0] not in ('M','m'):
        raise SvgGlyphError('SVG path must start with moveto')
    counts={'M':2,'L':2,'H':1,'V':1,'C':6,'S':4,'Q':4,'T':2,'A':7}
    points=[]; current=(0.,0.); origin=current; control=None; previous=''; command=None; i=0
    while i<len(tokens):
        if tokens[i].isalpha():
            command=tokens[i]; i+=1
            if command.upper()=='Z':
                current=origin; points.append(current); previous='Z'; control=None; command=None
                budget[0]+=1
                if budget[0]>MAX_PATH_OPS:
                    raise SvgGlyphError('SVG has too many path commands')
                continue
        if command is None or command.upper() not in counts:
            raise SvgGlyphError('Invalid SVG path command')
        op=command.upper(); count=counts[op]
        if i+count>len(tokens) or any(t.isalpha() for t in tokens[i:i+count]):
            raise SvgGlyphError('Truncated SVG path')
        n=[_number(v) for v in tokens[i:i+count]]; i+=count
        budget[0]+=1
        if budget[0]>MAX_PATH_OPS:
            raise SvgGlyphError('SVG has too many path commands')
        relative=command.islower()
        def point(x,y):
            return (x+current[0],y+current[1]) if relative else (x,y)
        if op=='H':
            end=(current[0]+n[0] if relative else n[0],current[1])
        elif op=='V':
            end=(current[0],current[1]+n[0] if relative else n[0])
        else:
            end=point(n[-2],n[-1])
        if op=='M':
            origin=end; command='l' if relative else 'L'
        elif op=='C':
            points.extend([point(n[0],n[1]),point(n[2],n[3])]); control=point(n[2],n[3])
        elif op=='S':
            reflected=(2*current[0]-control[0],2*current[1]-control[1]) if previous in ('C','S') else current
            points.extend([reflected,point(n[0],n[1])]); control=point(n[0],n[1])
        elif op=='Q':
            control=point(n[0],n[1]); points.append(control)
        elif op=='T':
            control=(2*current[0]-control[0],2*current[1]-control[1]) if previous in ('Q','T') else current
            points.append(control)
        elif op=='A':
            if n[0]<0 or n[1]<0 or n[3] not in (0,1) or n[4] not in (0,1):
                raise SvgGlyphError('Invalid SVG arc')
            points.extend(_arc_bounds(current,end,*n[:3],n[3],n[4]))
        points.extend([current,end] if op!='M' else [end])
        current=end; previous=op
        if op not in ('C','S','Q','T'):
            control=None
    if command and tokens[-1].isalpha():
        raise SvgGlyphError('Missing SVG path coordinates')
    return points


def _paint(value):
    value=value.strip()
    if value in ('none','transparent'):
        return 'none'
    if value in ('currentColor','inherit'):
        return value
    try:
        color=ImageColor.getrgb(value)
        if len(color)!=3:
            raise ValueError('Alpha paint requires explicit opacity')
    except (ValueError,TypeError) as error:
        raise SvgGlyphError('Unsupported SVG paint') from error
    return '#{:02x}{:02x}{:02x}'.format(*color)


def _sanitize(svg,*,with_color=False):
    if not isinstance(svg,str):
        raise SvgGlyphError('SVG input must be text')
    try:
        length=len(svg.encode('utf-8'))
    except UnicodeEncodeError as error:
        raise SvgGlyphError('SVG input is not valid Unicode') from error
    if length>MAX_SVG_BYTES:
        raise SvgGlyphError('SVG input is too large')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)|<\?(?!xml\s)',svg,re.I):
        raise SvgGlyphError('SVG declarations and processing instructions are forbidden')
    try:
        root=ET.fromstring(svg)
    except (ET.ParseError,ValueError) as error:
        raise SvgGlyphError('Invalid SVG XML') from error
    count=[0]; budget=[0]; all_bounds=[]; paints=set()

    def visit(node, depth, matrix, inherited, opacity):
        count[0]+=1
        if count[0]>MAX_NODES or depth>MAX_DEPTH:
            raise SvgGlyphError('SVG structure is too complex')
        tag=node.tag
        if tag.startswith('{'+_NS+'}'):
            tag=tag[len(_NS)+2:]
        if tag not in ({'svg','g'} if depth==0 else {'g'}) | set(_SHAPES):
            raise SvgGlyphError('Unsupported SVG element')
        if depth==0 and tag!='svg':
            raise SvgGlyphError('Expected an SVG root')
        if (node.text or '').strip() or (node.tail or '').strip():
            raise SvgGlyphError('SVG text content is forbidden')
        allowed=_PRESENTATION | {'transform','id','class'} | _SHAPES.get(tag,set())
        if depth==0:
            allowed |= {'width','height','viewBox','preserveAspectRatio','version'}
        if any(key not in allowed for key in node.attrib):
            raise SvgGlyphError('Unsupported SVG attribute')
        attrs={}; style=dict(inherited)
        for key,value in node.attrib.items():
            if key in ('id','class'):
                if len(value)>256 or not re.fullmatch(r'[A-Za-z0-9_ :.-]*',value):
                    raise SvgGlyphError('Invalid SVG metadata')
                continue
            if key=='transform':
                matrix=_mul(matrix,_transform(value)); attrs[key]=value
            elif key in ('fill','stroke','color'):
                paint=_paint(value)
                if key=='color' and paint=='none':
                    raise SvgGlyphError('SVG color cannot be none')
                if paint!='inherit':
                    if key=='color' and paint=='currentColor':
                        paint=inherited.get('color','currentColor')
                    attrs[key]=paint; style[key]=paint
            elif key in ('opacity','fill-opacity','stroke-opacity'):
                number=_number(value)
                if not 0<=number<=1:
                    raise SvgGlyphError('SVG opacity must be between zero and one')
                attrs[key]=str(number)
                if key=='opacity':
                    opacity*=number
                else:
                    style[key]=number
            elif key in ('fill-rule','clip-rule','stroke-linecap','stroke-linejoin'):
                options={'fill-rule':('nonzero','evenodd'),'clip-rule':('nonzero','evenodd'),
                         'stroke-linecap':('butt','round','square'),'stroke-linejoin':('miter','round','bevel')}
                if value not in options[key]:
                    raise SvgGlyphError('Unsupported SVG stroke or fill rule')
                attrs[key]=value; style[key]=value
            elif key=='stroke-dasharray':
                if value!='none' and (len(_numbers(value))>64 or any(n<0 for n in _numbers(value)) or not any(_numbers(value))):
                    raise SvgGlyphError('Unsupported SVG dash array')
                attrs[key]=value
            elif key in ('d','points'):
                attrs[key]=value
            elif key=='viewBox':
                box=_numbers(value,4)
                if not 0<box[2]<=1_000_000 or not 0<box[3]<=1_000_000:
                    raise SvgGlyphError('Invalid SVG viewBox')
            elif key=='preserveAspectRatio':
                if not re.fullmatch(r'(?:none|x(?:Min|Mid|Max)Y(?:Min|Mid|Max)(?: (?:meet|slice))?)',value):
                    raise SvgGlyphError('Unsupported SVG aspect ratio')
            elif key=='version':
                if value not in ('1.0','1.1','2.0'):
                    raise SvgGlyphError('Unsupported SVG version')
            else:
                number=_number(value)
                if key in ('width','height') and depth==0:
                    if not 1<=number<=4096:
                        raise SvgGlyphError('SVG dimensions must be within 1..4096')
                    continue
                if key in ('width','height','r','rx','ry','stroke-width') and number<0:
                    raise SvgGlyphError('SVG lengths cannot be negative')
                if key=='stroke-width' and number>4096 or key=='stroke-miterlimit' and not 1<=number<=64:
                    raise SvgGlyphError('SVG stroke is outside the supported range')
                attrs[key]=str(number); style[key]=number
        # Resolve currentColor after all attributes, independently of XML order.
        for key in ('fill','stroke'):
            if attrs.get(key)=='currentColor':
                attrs[key]=style.get('color','currentColor')
        clean=ET.Element('g' if depth==0 else tag,attrs)
        if tag in _SHAPES:
            if list(node):
                raise SvgGlyphError('SVG shape cannot contain child elements')
            n=lambda key,default=0.: _number(node.get(key,str(default)))
            if tag=='path':
                points=_path_points(node.get('d',''),budget)
            elif tag in ('polygon','polyline'):
                values=_numbers(node.get('points',''))
                if len(values)%2 or len(values)<4:
                    raise SvgGlyphError('Invalid SVG points')
                budget[0]+=len(values)//2
                points=list(zip(values[::2],values[1::2]))
            elif tag=='line':
                points=[(n('x1'),n('y1')),(n('x2'),n('y2'))]
            elif tag=='rect':
                x,y,w,h=n('x'),n('y'),n('width'),n('height')
                points=[(x,y),(x+w,y+h)] if w and h else []
            else:
                cx,cy=n('cx'),n('cy')
                rx=n('r') if tag=='circle' else n('rx')
                ry=rx if tag=='circle' else n('ry')
                points=[(cx-rx,cy-ry),(cx+rx,cy+ry)] if rx and ry else []
            if budget[0]>MAX_PATH_OPS:
                raise SvgGlyphError('SVG has too many path commands')
            effective={}
            for key,default in (('fill','#000000'),('stroke','none')):
                paint=style.get(key,default)
                if paint=='currentColor':
                    paint=style.get('color','currentColor')
                clean.set(key,paint)
                effective[key]=paint
            fill=tag!='line' and effective['fill']!='none' and style.get('fill-opacity',1)>0
            stroke=effective['stroke']!='none' and style.get('stroke-opacity',1)>0 and style.get('stroke-width',1)>0
            if points and opacity>0 and (fill or stroke):
                if fill:
                    paints.add(effective['fill'])
                if stroke:
                    paints.add(effective['stroke'])
                # Transform a local conservative box, including Bezier controls and
                # effective arc ellipse. Expand for transformed stroke miters/caps.
                x0=min(p[0] for p in points); x1=max(p[0] for p in points)
                y0=min(p[1] for p in points); y1=max(p[1] for p in points)
                radius=style.get('stroke-width',1)/2*max(style.get('stroke-miterlimit',4),math.sqrt(2)) if stroke else 0
                a,b,c,d,e,f=matrix
                corners=[(a*x+c*y+e,b*x+d*y+f) for x in (x0-radius,x1+radius) for y in (y0-radius,y1+radius)]
                if any(not math.isfinite(v) or abs(v)>1_000_000 for p in corners for v in p):
                    raise SvgGlyphError('SVG painted bounds are outside the supported range')
                all_bounds.extend(corners)
        for child in node:
            clean.append(visit(child,depth+1,matrix,style,opacity))
        return clean

    clean=visit(root,0,_IDENTITY,{},1.)
    if not all_bounds:
        raise SvgGlyphError('SVG contains no visible supported artwork')
    bounds=(min(p[0] for p in all_bounds),min(p[1] for p in all_bounds),
            max(p[0] for p in all_bounds),max(p[1] for p in all_bounds))
    theme='currentColor' in paints
    fixed=paints-{'currentColor'}
    # Only symbolic currentColor asks for theme-aware ink. Literal paints and
    # the SVG default black are authored colours, even when their RGB values are
    # black or white; turning those into foreground ink would invert black emoji.
    mode='mixed' if theme and fixed else ('color' if fixed else 'monochrome')
    if mode=='monochrome':
        for node in clean.iter():
            for key in ('fill','stroke','color'):
                if key in node.attrib and node.attrib[key]!='none':
                    node.set(key,'#ffffff')
    wrapper=ET.Element('svg',{'xmlns':_NS})
    wrapper.append(clean)
    return (wrapper,bounds,mode) if with_color else (wrapper,bounds)


def svg_color_mode(svg):
    """Classify validated effective paints without native rendering or network."""
    return _sanitize(svg,with_color=True)[2]


def _viewport(root, bounds, *, size=MASTER_SIZE):
    x0,y0,x1,y1=bounds
    span=max(x1-x0,y1-y0)
    if not math.isfinite(span) or span<=1e-8:
        raise SvgGlyphError('SVG painted bounds are empty')
    extent=span*1.04
    left=(x0+x1-extent)/2; top=(y0+y1-extent)/2
    root.set('viewBox',f'{left:.12g} {top:.12g} {extent:.12g} {extent:.12g}')
    root.set('width',str(size)); root.set('height',str(size))
    return ET.tostring(root,encoding='unicode'),(left,top,extent)


def _alpha(image, *, size=MASTER_SIZE):
    if not isinstance(image,Image.Image) or image.size!=(size,size) or image.mode not in ('L','RGBA'):
        raise SvgGlyphError('SVG renderer returned an invalid image')
    result=image.copy() if image.mode=='L' else image.getchannel('A')
    bbox=result.getbbox()
    if not bbox:
        raise SvgGlyphError('SVG artwork is empty or too small to render')
    if bbox[0]<=0 or bbox[1]<=0 or bbox[2]>=size or bbox[3]>=size:
        raise SvgGlyphError('SVG artwork was clipped during rendering')
    return result,bbox


def _ink_radius(raw, width, height):
    """Exact farthest nonzero pixel-centre distance, scanning row ends in C.

    For a fixed y, squared distance is convex in x: only the first and last
    painted pixel can be farthest. bytes.strip treats alpha 1 as painted too,
    retaining the same boundary check as inspecting every individual pixel.
    """
    radius = 0.
    cx, cy = width / 2, height / 2
    for y in range(height):
        row = raw[y * width:(y + 1) * width]
        leading = row.lstrip(b'\0')
        if not leading:
            continue
        left = width - len(leading)
        right = len(row.rstrip(b'\0')) - 1
        distance = max(abs(left + .5 - cx), abs(right + .5 - cx))
        radius = max(radius, math.hypot(distance, y + .5 - cy))
    return radius


def _normalize(image, *, size=MASTER_SIZE):
    alpha=image.getchannel('A') if image.mode=='RGBA' else image
    bbox=alpha.getbbox()
    ink=image.crop(bbox)
    w,h=ink.size
    # Ink centring is geometric, with no manually selected optical translation.
    ink_alpha=ink.getchannel('A') if ink.mode=='RGBA' else ink
    radius=_ink_radius(ink_alpha.tobytes(),w,h)
    padding=4*size/MASTER_SIZE
    scale=min((RADIUS*size-padding)/max(radius,1),
              (MAX_INK_SPAN*size-padding)/max(w,h))
    width=max(1,round(w*scale)); height=max(1,round(h*scale))
    resized=ink.resize((width,height),Image.Resampling.LANCZOS)
    result=Image.new(image.mode,(size,size))
    result.paste(resized,((size-width)//2,(size-height)//2))
    return result


RENDER_CACHE_ITEMS = 8
RENDER_CACHE_BYTES = 2 * 1024 * 1024


class _RenderCache:
    """A glyph owns its resized images; no global cache can retain its master."""
    def __init__(self):
        self.lock=threading.RLock()
        self.images=OrderedDict()
        self.bytes=0

    def image(self,key,factory):
        with self.lock:
            cached=self.images.get(key)
            if cached is not None:
                self.images.move_to_end(key)
                return cached
            image=factory()
            size=image.width*image.height*len(image.getbands())
            if size<=RENDER_CACHE_BYTES:
                self.images[key]=image
                self.bytes+=size
                while len(self.images)>RENDER_CACHE_ITEMS or self.bytes>RENDER_CACHE_BYTES:
                    _,old=self.images.popitem(last=False)
                    self.bytes-=old.width*old.height*len(old.getbands())
            return image

    def memory_bytes(self):
        with self.lock:
            return self.bytes


@dataclass(frozen=True)
class CustomGlyph:
    """Immutable validated centre-only master; safe for tray and preview caches."""
    png_bytes: bytes
    digest: str
    _alpha_bytes: bytes = field(repr=False,compare=False)
    _rgba_bytes: bytes | None = field(default=None,repr=False,compare=False)
    _light_rgba_bytes: bytes | None = field(default=None,repr=False,compare=False)
    _cache: _RenderCache = field(default_factory=_RenderCache,init=False,repr=False,compare=False)

    @property
    def is_colored(self):
        return self._rgba_bytes is not None

    @property
    def color_mode(self):
        return 'mixed' if self._light_rgba_bytes is not None else ('color' if self.is_colored else 'monochrome')

    @property
    def memory_bytes(self):
        return len(self.png_bytes)+len(self._alpha_bytes)+(len(self._rgba_bytes) if self._rgba_bytes is not None else 0)+(len(self._light_rgba_bytes) if self._light_rgba_bytes is not None else 0)+self._cache.memory_bytes()

    @classmethod
    def from_png(cls,data: bytes,*,renderer_version=None):
        """Validate a current master, or load explicitly identified v1/v2 mono PNGs.

        The catalog verifies the original persisted PNG hash before this call.
        Only the known v1 renderer can use its wider original source boundary;
        it is refitted locally without fetching SVG or discarding the selection.
        Unknown revisions fail closed, including future/obsolete renderer names.
        """
        if renderer_version is None:
            renderer_version=RENDERER_VERSION
        if renderer_version not in (RENDERER_VERSION,PREVIOUS_RENDERER_VERSION,LEGACY_RENDERER_VERSION):
            raise SvgGlyphError('Unsupported cached glyph renderer revision')
        if not isinstance(data,bytes) or len(data)>MAX_PNG_BYTES:
            raise SvgGlyphError('Cached glyph PNG is too large or invalid')
        try:
            with Image.open(io.BytesIO(data)) as image:
                current=renderer_version==RENDERER_VERSION
                mixed=current and image.size==(2*MASTER_SIZE,MASTER_SIZE) and image.mode=='RGBA'
                if (image.format!='PNG' or image.mode not in ('L','RGBA') or getattr(image,'n_frames',1)!=1 or
                        image.size!=(MASTER_SIZE,MASTER_SIZE) and not mixed):
                    raise SvgGlyphError('Cached glyph must be a normalized 1024px PNG or paired RGBA master')
                image.load()
                if not current and image.mode=='RGBA' and any(image.getchannel(channel).getextrema()!=(255,255) for channel in ('R','G','B')):
                    raise SvgGlyphError('Legacy cached glyph must contain white monochrome artwork')
                colored=image.mode=='RGBA' and current
                master=image.crop((0,0,MASTER_SIZE,MASTER_SIZE)) if mixed else image.copy()
                light=image.crop((MASTER_SIZE,0,2*MASTER_SIZE,MASTER_SIZE)) if mixed else None
                if not colored and master.mode=='RGBA':
                    master=master.getchannel('A')
                alpha=master.getchannel('A') if colored else master
        except (OSError,ValueError,Image.DecompressionBombError) as error:
            raise SvgGlyphError('Cached glyph PNG is invalid') from error
        bounds=alpha.getbbox()
        if not bounds or abs((bounds[0]+bounds[2])/2-MASTER_SIZE/2)>2 or abs((bounds[1]+bounds[3])/2-MASTER_SIZE/2)>2:
            raise SvgGlyphError('Cached glyph is empty or not ink-centred')
        raw=alpha.tobytes()
        if light is not None and light.getchannel('A').tobytes()!=raw:
            raise SvgGlyphError('Mixed glyph masters must have identical alpha coverage')
        legacy=renderer_version==LEGACY_RENDERER_VERSION
        source_radius=_LEGACY_RADIUS if legacy else RADIUS
        if _ink_radius(raw,MASTER_SIZE,MASTER_SIZE)>source_radius*MASTER_SIZE+1:
            raise SvgGlyphError('Cached glyph exceeds the centre artwork boundary')
        if not legacy and max(bounds[2]-bounds[0],bounds[3]-bounds[1])>MAX_INK_SPAN*MASTER_SIZE+1:
            raise SvgGlyphError('Cached glyph exceeds the centre artwork span')
        if legacy:
            master=_normalize(alpha)
            raw=master.tobytes()
        if colored:
            # Hidden RGB has no visual meaning. Canonicalize only alpha-zero
            # pixels without premultiplying or rounding semi-transparent colour.
            visible=alpha.point(lambda v:255 if v else 0)
            empty=Image.new('RGBA',(MASTER_SIZE,MASTER_SIZE))
            master=Image.composite(master,empty,visible)
            if light is not None:
                light=Image.composite(light,empty,visible)
        rgba=master.tobytes() if colored else None
        light_raw=light.tobytes() if light is not None else None
        saved=Image.new('RGBA',(2*MASTER_SIZE,MASTER_SIZE)) if light is not None else master
        if light is not None:
            saved.paste(master,(0,0));saved.paste(light,(MASTER_SIZE,0))
        buffer=io.BytesIO();saved.save(buffer,format='PNG')
        mode=b'mixed' if light_raw is not None else (b'color' if colored else b'monochrome')
        digest=hashlib.sha256(RENDERER_VERSION.encode('ascii')+b'\0'+mode+b'\0'+(rgba if colored else raw)+(light_raw or b'')).hexdigest()
        return cls(buffer.getvalue(),digest,raw,rgba,light_raw)

    def render(self,size:int,fg:tuple,alpha:int=255,foreground_opacity:float=230/255):
        if isinstance(size,bool) or not isinstance(size,int) or not 1<=size<=1024:
            raise SvgGlyphError('Glyph size must be within 1..1024')
        if not isinstance(fg,tuple) or len(fg)!=3 or any(isinstance(v,bool) or not isinstance(v,int) or not 0<=v<=255 for v in fg):
            raise SvgGlyphError('Glyph foreground must be an RGB tuple')
        if isinstance(alpha,bool) or not isinstance(alpha,int) or not 0<=alpha<=255:
            raise SvgGlyphError('Glyph alpha must be within 0..255')
        if isinstance(foreground_opacity,bool) or not isinstance(foreground_opacity,(int,float)) or not math.isfinite(foreground_opacity) or not 0<=foreground_opacity<=1:
            raise SvgGlyphError('Glyph foreground opacity must be within 0..1')
        if self.is_colored:
            image=self._cache.image((size,'black'),lambda:_sized_color(self._rgba_bytes,size)).copy()
            if self._light_rgba_bytes is not None and fg!=(0,0,0):
                light=self._cache.image((size,'white'),lambda:_sized_color(self._light_rgba_bytes,size))
                if fg==(255,255,255):
                    image=light.copy()
                else:
                    channels=[Image.blend(image.getchannel(c),light.getchannel(c),v/255)
                              for c,v in zip(('R','G','B'),fg)]
                    image=Image.merge('RGBA',(*channels,image.getchannel('A')))
            image.putalpha(image.getchannel('A').point(lambda v:round(v*alpha/255)))
            return image
        mask=self._cache.image((size,'alpha'),lambda:_sized_mask(self._alpha_bytes,size))
        coverage=mask.point(lambda v:round(v*alpha*foreground_opacity/255))
        image=Image.new('RGBA',(size,size),fg+(0,)); image.putalpha(coverage)
        return image


def _sized_mask(raw,size):
    source=Image.frombytes('L',(MASTER_SIZE,MASTER_SIZE),raw)
    result=source.resize((size,size),Image.Resampling.BOX if size<MASTER_SIZE else Image.Resampling.LANCZOS)
    # Pixel centres beyond this boundary are reserved for the existing ring.
    pixels=result.load()
    result.putdata([pixels[x,y] if math.hypot(x+.5-size/2,y+.5-size/2)<=RADIUS*size else 0
                    for y in range(size) for x in range(size)])
    return result


def _sized_color(raw,size):
    source=Image.frombytes('RGBA',(MASTER_SIZE,MASTER_SIZE),raw)
    image=source.resize((size,size),Image.Resampling.BOX if size<MASTER_SIZE else Image.Resampling.LANCZOS)
    coverage=image.getchannel('A')
    pixels=coverage.load()
    coverage.putdata([pixels[x,y] if math.hypot(x+.5-size/2,y+.5-size/2)<=RADIUS*size else 0
                      for y in range(size) for x in range(size)])
    image.putalpha(coverage)
    return image


def prepare_glyph(svg:str,*,rasterizer=None)->CustomGlyph:
    """Validate and rasterize once; resulting PNG works offline without Settings.

    Tests may inject ``rasterizer(sanitized_svg, physical_size)`` returning a
    straight-alpha RGBA (required for colour) or L image. Production uses Windows
    software Direct2D. Monochrome output is stored as L; colour is stored as RGBA.
    """
    root,bounds,mode=_sanitize(svg,with_color=True)
    colored=mode!='monochrome'
    mixed=mode=='mixed'
    if mixed:
        root.set('color','#000000')
    if rasterizer is None:
        from ._svg_windows import rasterize
        rasterizer=lambda markup,size:rasterize(markup,size,preserve_color=colored)
    try:
        markup,viewport=_viewport(root,bounds)
        rendered=rasterizer(markup,MASTER_SIZE)
        alpha,bbox=_alpha(rendered)
        # Rerender measured ink tightly so padded viewBoxes, transforms and
        # conservative stroke/control-point bounds never waste master resolution.
        left,top,extent=viewport
        pad=4*extent/MASTER_SIZE
        tight=(left+bbox[0]*extent/MASTER_SIZE-pad,top+bbox[1]*extent/MASTER_SIZE-pad,
               left+bbox[2]*extent/MASTER_SIZE+pad,top+bbox[3]*extent/MASTER_SIZE+pad)
        markup,_=_viewport(root,tight)
        rendered=rasterizer(markup,MASTER_SIZE)
        alpha,_=_alpha(rendered)
        if colored and rendered.mode!='RGBA':
            raise SvgGlyphError('Color SVG renderer must return RGBA')
        normalized=_normalize(rendered if colored else alpha)
        if mixed:
            root.set('color','#ffffff')
            markup,_=_viewport(root,tight)
            light=rasterizer(markup,MASTER_SIZE)
            light_alpha,_=_alpha(light)
            if light.mode!='RGBA' or light_alpha.tobytes()!=alpha.tobytes():
                raise SvgGlyphError('Mixed SVG rendering must keep identical theme alpha coverage')
            light_normalized=_normalize(light)
            paired=Image.new('RGBA',(2*MASTER_SIZE,MASTER_SIZE))
            paired.paste(normalized,(0,0));paired.paste(light_normalized,(MASTER_SIZE,0))
            normalized=paired
        buffer=io.BytesIO(); normalized.save(buffer,format='PNG')
        return CustomGlyph.from_png(buffer.getvalue())
    except SvgGlyphError:
        raise
    except Exception as error:
        raise SvgGlyphError('SVG artwork could not be rendered') from error
