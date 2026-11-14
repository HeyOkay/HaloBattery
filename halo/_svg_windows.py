"""Windows software SVG rasterizer using only operating-system DLLs.

ABI source: Microsoft win32metadata generation/WinSDK/RecompiledIdlHeaders:
um/d2d1.h (ID2D1RenderTarget, ID2D1Bitmap), um/d2d1_1.h
(ID2D1DeviceContext, ID2D1Bitmap1, D2D1CreateDevice), um/d2d1_2.h,
um/d2d1_3.h (ID2D1DeviceContext5), um/d3d11.h, shared/dxgi.h.
https://github.com/microsoft/win32metadata/tree/main/generation/WinSDK/RecompiledIdlHeaders
Vtable indices count the SDK declarations, including inherited IUnknown slots.
SHCreateMemStream: https://learn.microsoft.com/windows/win32/api/shlwapi/nf-shlwapi-shcreatememstream
Only call with the bounded, allowlisted SVG produced by svg_glyph.
"""
from __future__ import annotations

import ctypes as C
import sys
import uuid

from PIL import Image

P = C.c_void_p
U = C.c_uint32
F = C.c_float
H = C.c_int32


class _SizeU(C.Structure):
    _fields_ = [('width', U), ('height', U)]


class _SizeF(C.Structure):
    _fields_ = [('width', F), ('height', F)]


class _PixelFormat(C.Structure):
    _fields_ = [('format', U), ('alphaMode', U)]


class _BitmapProperties(C.Structure):
    _fields_ = [('pixelFormat', _PixelFormat), ('dpiX', F), ('dpiY', F),
                ('bitmapOptions', U), ('colorContext', P)]


class _Mapped(C.Structure):
    _fields_ = [('pitch', U), ('bits', P)]


def _method(obj, slot, result, *types):
    table = C.cast(obj, C.POINTER(C.POINTER(P))).contents
    return C.WINFUNCTYPE(result, P, *types)(table[slot])


def _check(hr, operation):
    if hr < 0:
        raise OSError(f'{operation} failed (HRESULT 0x{hr & 0xffffffff:08x})')


def rasterize(svg: str, size: int = 1024, *, preserve_color: bool = False) -> Image.Image:
    """Rasterize sanitized XML to straight-alpha RGBA, without any UI.

    Each invocation owns its device/COM resources; callers need no dispatcher,
    apartment, Settings process, browser, user profile or graphics hardware.
    """
    if sys.platform != 'win32':
        raise OSError('Native SVG rendering requires Windows')
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 2048:
        raise ValueError('SVG raster size is outside 1..2048')
    payload = svg.encode('utf-8')
    if len(payload) > 262144:
        raise ValueError('SVG raster input is too large')
    owned = []

    def pointer():
        value = P()
        owned.append(value)
        return value

    def query(obj, iid):
        result = pointer()
        guid = (C.c_ubyte * 16).from_buffer_copy(uuid.UUID(iid).bytes_le)
        _check(_method(obj, 0, H, P, C.POINTER(P))(obj, C.byref(guid), C.byref(result)), 'QueryInterface')
        return result

    d3d = C.WinDLL('d3d11.dll', winmode=0x800)  # system directory only
    d2d = C.WinDLL('d2d1.dll', winmode=0x800)
    shell = C.WinDLL('shlwapi.dll', winmode=0x800)
    create = d3d.D3D11CreateDevice
    create.argtypes = [P, U, P, U, P, U, U, C.POINTER(P), P, P]
    create.restype = H
    create_d2d = d2d.D2D1CreateDevice
    create_d2d.argtypes = [P, P, C.POINTER(P)]
    create_d2d.restype = H
    stream_fn = shell.SHCreateMemStream
    stream_fn.argtypes = [P, U]
    stream_fn.restype = P
    context = None
    mapped = False
    readback = None
    try:
        device = pointer()
        # D3D_DRIVER_TYPE_WARP=5; BGRA_SUPPORT=0x20; D3D11_SDK_VERSION=7.
        _check(create(None, 5, None, 0x20, None, 0, 7, C.byref(device), None, None), 'D3D11CreateDevice(WARP)')
        dxgi = query(device, '54ec77fa-1377-44e6-8c32-88fd5f44c84c')
        d2device = pointer()
        _check(create_d2d(dxgi, None, C.byref(d2device)), 'D2D1CreateDevice')
        original = pointer()
        _check(_method(d2device, 4, H, U, C.POINTER(P))(d2device, 0, C.byref(original)), 'CreateDeviceContext')
        context = query(original, '7836d248-68cc-4df6-b9e8-de991bf62eb7')
        source = C.create_string_buffer(payload)
        stream = pointer()
        stream.value = stream_fn(source, len(payload))
        if not stream:
            raise OSError('SHCreateMemStream failed')
        document = pointer()
        _check(_method(context, 115, H, P, _SizeF, C.POINTER(P))(
            context, stream, _SizeF(size, size), C.byref(document)), 'CreateSvgDocument')

        def bitmap(options):
            out = pointer()
            # DXGI_FORMAT_B8G8R8A8_UNORM=87; ALPHA_MODE_PREMULTIPLIED=1.
            props = _BitmapProperties(_PixelFormat(87, 1), 96, 96, options, None)
            _check(_method(context, 57, H, _SizeU, P, U, C.POINTER(_BitmapProperties), C.POINTER(P))(
                context, _SizeU(size, size), None, 0, C.byref(props), C.byref(out)), 'CreateBitmap')
            return out

        target = bitmap(1)  # D2D1_BITMAP_OPTIONS_TARGET
        readback = bitmap(6)  # CPU_READ | CANNOT_DRAW
        _method(context, 74, None, P)(context, target)  # SetTarget
        _method(context, 48, None)(context)  # BeginDraw
        clear = (F * 4)(0, 0, 0, 0)
        _method(context, 47, None, P)(context, C.byref(clear))
        _method(context, 116, None, P)(context, document)
        _check(_method(context, 49, H, P, P)(context, None, None), 'EndDraw')
        _method(context, 74, None, P)(context, None)
        _check(_method(readback, 8, H, P, P, P)(readback, None, target, None), 'CopyFromBitmap')
        memory = _Mapped()
        _check(_method(readback, 14, H, U, C.POINTER(_Mapped))(readback, 1, C.byref(memory)), 'Map')
        mapped = True
        if preserve_color:
            rows = b''.join(C.string_at(memory.bits + y * memory.pitch, size * 4)
                            for y in range(size))
            # Direct2D stores premultiplied BGRA. Pillow's RGBa raw decoder and
            # conversion undo that premultiplication in C, including alpha-zero
            # pixels, preserving edge colours without a slow Python pixel loop.
            return Image.frombytes('RGBa', (size, size), rows, 'raw', 'BGRa').convert('RGBA')
        rows = b''.join(C.string_at(memory.bits + y * memory.pitch, size * 4)[3::4]
                        for y in range(size))
        image = Image.new('RGBA', (size, size), (255, 255, 255, 0))
        image.putalpha(Image.frombytes('L', (size, size), rows))
        return image
    finally:
        if mapped:
            _method(readback, 15, H)(readback)  # Unmap
        if context:
            _method(context, 74, None, P)(context, None)
        for obj in reversed(owned):
            if obj:
                _method(obj, 2, U)(obj)  # Release
