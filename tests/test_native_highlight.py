"""Antialiased pixels and fake Win32/GDI lifecycle; no desktop windows or input."""
import ctypes
import importlib.util
from pathlib import Path
import types
import unittest
from unittest import mock

from halo import native_highlight as highlight


class Backend:
    def __init__(self, fail=None):
        self.events, self.fail = [], fail

    def record(self, name, *args):
        self.events.append((name, *args))
        if name == self.fail:
            raise OSError(name)

    def create(self, owner):
        self.record("create", owner)
        return 0x123456789

    def update(self, *args):
        self.record("update", *args)

    def reveal(self, *args):
        self.record("reveal", *args)

    def hide(self, window):
        self.record("hide", window)

    def destroy(self, window):
        self.record("destroy", window)


class NativeHighlightTests(unittest.TestCase):
    def test_import_has_no_dll_side_effects(self):
        spec = importlib.util.spec_from_file_location("highlight_import_probe", Path(highlight.__file__))
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(ctypes, "WinDLL", create=True, side_effect=AssertionError("DLL loaded")):
            spec.loader.exec_module(module)

    def test_125_percent_corners_have_partial_alpha_and_premultiplied_rgb_without_matte(self):
        width, height, radius = 225, 40, 10.0
        pixels = highlight._pixels(width, height, radius, (100, 120, 150))
        alphas = pixels[3::4]
        self.assertEqual(len(pixels), width * height * 4)
        self.assertEqual(alphas[0], 0)
        self.assertEqual(alphas[width * (height // 2) + width // 2], 255)
        self.assertTrue(any(0 < alpha < 255 for alpha in alphas))
        for offset in range(0, len(pixels), 4):
            b, g, r, a = pixels[offset:offset + 4]
            self.assertLessEqual(max(r, g, b), a)
            self.assertEqual((b, g, r), tuple((channel * a + 127) // 255 for channel in (150, 120, 100)))
            if a == 0:
                self.assertEqual((b, g, r), (0, 0, 0))

    def test_cache_is_bounded_to_last_shape_and_alpha_uses_ulw_blend(self):
        backend = Backend()
        window = highlight.NativeHighlight(0x987654321, _backend=backend)
        self.assertEqual(window.hwnd, 0x123456789)
        self.assertEqual(backend.events[0], ("create", 0x987654321))
        window.show((-1900, 20, -1675, 60), 10, "#647896", 0.25)
        first = window._cache[1]
        self.assertEqual(backend.events[-2][-1], 64)
        window.show((-1800, 40, -1575, 80), 10, (100, 120, 150), 0.5)
        self.assertIs(window._cache[1], first)
        self.assertEqual(backend.events[-2][-1], 128)
        window.show((-1800, 40, -1600, 80), 10, (100, 120, 150), 1.0)
        self.assertIsNot(window._cache[1], first)
        self.assertEqual(len(window._cache), 2)
        window.hide()
        self.assertEqual(backend.events[-1], ("hide", window.hwnd))
        window.destroy()
        window.destroy()
        self.assertEqual(window.hwnd, 0)
        self.assertIsNone(window._cache)
        self.assertEqual(sum(e[0] == "destroy" for e in backend.events), 1)

    def test_update_or_reveal_failure_destroys_popup_and_cache(self):
        for fail in ("update", "reveal"):
            with self.subTest(fail=fail):
                backend = Backend(fail)
                window = highlight.NativeHighlight(1, _backend=backend)
                with self.assertRaises(OSError):
                    window.show((0, 0, 200, 40), 8, (10, 20, 30), 0.5)
                self.assertEqual(window.hwnd, 0)
                self.assertIsNone(window._cache)
                self.assertEqual(backend.events[-1][0], "destroy")

    def test_invalid_parameters_do_not_allocate_pixels(self):
        window = highlight.NativeHighlight(1, _backend=Backend())
        for bounds, radius, colour, alpha in (
                ((0, 0, 0, 0), 5, (1, 2, 3), 1), ((0, 0, 100000, 10000), 5, (1, 2, 3), 1),
                ((0, 0, 10, 10), -1, (1, 2, 3), 1), ((0, 0, 10, 10), 5, "magenta", 1),
                ((0, 0, 10, 10), 5, (1, 2, 3), float("nan")),
                ((0, 0, 10, 10), 5, (1, 2, 300), 1)):
            with self.subTest(bounds=bounds, colour=colour), self.assertRaises(ValueError):
                window.show(bounds, radius, colour, alpha)
        self.assertIsNone(window._cache)
        window.destroy()

    def backend(self, fail=None):
        memory = ctypes.create_string_buffer(20 * 10 * 4)
        user = types.SimpleNamespace(**{name: mock.Mock(return_value=1) for name in
            ("CreateWindowExW", "IsWindow", "DestroyWindow", "ShowWindow", "SetWindowPos", "UpdateLayeredWindow")})
        gdi = types.SimpleNamespace(**{name: mock.Mock(return_value=1) for name in
            ("CreateCompatibleDC", "CreateDIBSection", "SelectObject", "DeleteObject", "DeleteDC")})
        gdi.CreateCompatibleDC.return_value = 0 if fail == "dc" else 0x123450000
        selects = []

        def dib(dc, ptr, usage, bits, section, offset):
            info = ctypes.cast(ptr, ctypes.POINTER(highlight._BITMAPINFO)).contents.bmiHeader
            self.assertEqual((info.biWidth, info.biHeight, info.biPlanes, info.biBitCount), (20, -10, 1, 32))
            if fail != "bits":
                ctypes.cast(bits, ctypes.POINTER(ctypes.c_void_p)).contents.value = ctypes.addressof(memory)
            return 0 if fail == "bitmap" else 0x123460000

        def select(dc, value):
            selects.append(value)
            if fail == "select" and len(selects) == 1:
                return 0
            if fail == "restore" and len(selects) == 2:
                raise OSError("restore")
            return 0x123470000

        gdi.CreateDIBSection.side_effect = dib
        gdi.SelectObject.side_effect = select
        user.UpdateLayeredWindow.return_value = 0 if fail == "update" else 1
        kernel = types.SimpleNamespace(GetModuleHandleW=mock.Mock(return_value=0x123480000))
        dlls = {"user32": user, "gdi32": gdi, "kernel32": kernel}
        with mock.patch.object(highlight.sys, "platform", "win32"), \
                mock.patch.object(ctypes, "WinDLL", create=True, side_effect=lambda name, **kw: dlls[name]):
            backend = highlight._Backend()
        return backend, user, gdi, memory

    def test_real_backend_abi_and_ulw_use_only_per_pixel_alpha(self):
        backend, user, gdi, memory = self.backend()
        window = backend.create(0x123490000)
        create = user.CreateWindowExW.call_args.args
        self.assertEqual(create[0] & highlight.EX_STYLE, highlight.EX_STYLE)
        self.assertEqual(create[1], "STATIC")
        self.assertEqual(create[8], 0x123490000)
        self.assertIs(gdi.CreateDIBSection.restype, ctypes.c_void_p)
        pixels = highlight._pixels(20, 10, 3, (100, 120, 150))
        backend.update(window, (-200, -100, -180, -90), pixels, 128)
        self.assertEqual(memory.raw, pixels)
        args = user.UpdateLayeredWindow.call_args.args
        point = ctypes.cast(args[2], ctypes.POINTER(highlight._POINT)).contents
        blend = ctypes.cast(args[7], ctypes.POINTER(highlight._BLENDFUNCTION)).contents
        self.assertEqual((point.x, point.y), (-200, -100))
        self.assertEqual(tuple(bytes(blend)), (0, 0, 128, 1))
        self.assertEqual(args[-1], 2)
        backend.reveal(window, (-200, -100, -180, -90))
        self.assertEqual(user.SetWindowPos.call_args.args[-1], 0x0050)
        self.assertFalse(hasattr(user, "SetLayeredWindowAttributes"))
        gdi.DeleteDC.assert_called_once_with(0x123450000)
        gdi.DeleteObject.assert_called_once_with(0x123460000)

    def test_every_gdi_failure_releases_all_acquired_resources(self):
        for fail in ("dc", "bitmap", "bits", "select", "update", "restore"):
            with self.subTest(fail=fail):
                backend, user, gdi, memory = self.backend(fail)
                with mock.patch.object(ctypes, "WinError", create=True, return_value=OSError("fake Win32 failure")), \
                        self.assertRaises(OSError):
                    backend.update(1, (0, 0, 20, 10), bytes(800), 128)
                self.assertEqual(gdi.DeleteDC.call_count, 0 if fail == "dc" else 1)
                self.assertEqual(gdi.DeleteObject.call_count, 0 if fail in ("dc", "bitmap") else 1)


if __name__ == "__main__":
    unittest.main()
