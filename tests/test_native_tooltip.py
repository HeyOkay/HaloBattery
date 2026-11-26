"""Native tooltip contracts with fake Win32 functions; no windows are opened."""
import ctypes
import importlib.util
from pathlib import Path
import types
import unittest
from unittest import mock

from halo import native_tooltip as tooltip


class Backend:
    def __init__(self, fail=None, size=(160, 30), font=0):
        self.fail, self.dimensions = fail, size
        self.created_font = font
        self.active = False
        self.events = []

    def record(self, name, *args):
        self.events.append((name, *args))
        if name == self.fail:
            raise OSError(name)

    def create(self, owner):
        self.record("create", owner)
        return 0x123456789

    def theme(self, window, light):
        self.record("theme", window, light)

    def add(self, window, info):
        self.record("add", window, info)

    def font(self, window, owner):
        self.record("font", window, owner)
        return self.created_font

    def delete_font(self, font):
        self.record("delete_font", font)

    def max_width(self, window, width):
        self.record("max_width", window, width)

    def size(self, window, info):
        self.record("size", window, info)
        return self.dimensions if self.active else (18, 16)

    def position(self, window, x, y):
        self.record("position", window, x, y)

    def activate(self, window, info, enabled):
        self.record("activate", window, enabled)
        self.active = enabled

    def cloak(self, window, enabled):
        self.record("cloak", window, enabled)
        if self.fail == ("cloak_on" if enabled else "cloak_off"):
            raise OSError(self.fail)

    def opacity(self, window, alpha):
        self.record("opacity", window, alpha)

    def move(self, window, *bounds):
        self.record("move", window, *bounds)

    def destroy(self, window):
        self.record("destroy", window)


class NativeTooltipTests(unittest.TestCase):
    def test_import_does_not_load_windows_dlls(self):
        spec = importlib.util.spec_from_file_location("tooltip_import_probe", Path(tooltip.__file__))
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(ctypes, "WinDLL", create=True, side_effect=AssertionError("DLL loaded")):
            spec.loader.exec_module(module)

    def test_unicode_buffer_and_pointer_sized_owner_survive_show(self):
        backend = Backend()
        owner = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else 123
        tip = tooltip.NativeTooltip(owner, "Переименовать 鼠 🐁 & name", False, _backend=backend)
        self.assertEqual(tip._tool.hwnd, owner)
        self.assertEqual(tip._tool.lpszText, "Переименовать 鼠 🐁 & name")
        self.assertEqual(tip._text.value, tip._tool.lpszText)
        self.assertEqual(tip._tool.cbSize, 72 if ctypes.sizeof(ctypes.c_void_p) == 8 else 48)
        self.assertEqual(tip._tool.uFlags, 0x1A0)
        tip.show_above((200, 100, 400, 140), (0, 0, 800, 600))
        self.assertEqual(backend.events[-3], ("move", tip._window, 220, 64, 160, 30))
        self.assertEqual(backend.events[-1], ("cloak", tip._window, False))
        self.assertEqual(tip._text.value, "Переименовать 鼠 🐁 & name")
        tip.destroy()

    def test_negative_monitor_coordinates_and_work_area_clamping(self):
        cases = [
            ((-1900, -100, -1700, -60), (-1920, -200, 0, 880), (160, 30), (-1880, -136, 160, 30)),
            ((-1930, -198, -1900, -170), (-1920, -200, 0, 880), (160, 30), (-1920, -200, 160, 30)),
            ((750, 750, 900, 780), (0, 0, 800, 600), (160, 30), (640, 570, 160, 30)),
            ((10, 10, 20, 20), (-20, -20, 80, 40), (200, 100), (-20, -20, 100, 60)),
        ]
        for anchor, work, size, expected in cases:
            with self.subTest(anchor=anchor):
                backend = Backend(size=size)
                tip = tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
                tip.show_above(anchor, work)
                self.assertEqual(backend.events[-3][2:], expected)
                tip.destroy()

    def test_rect_objects_and_custom_gap(self):
        anchor = tooltip._RECT(10, 100, 90, 120)
        work = tooltip._RECT(0, 0, 1000, 1000)
        self.assertEqual(tooltip._placement(anchor, work, (40, 20), 12), (30, 68, 40, 20))

    def test_constructor_failures_destroy_only_created_window(self):
        for fail, destroyed in (("create", 0), ("font", 1), ("add", 1)):
            with self.subTest(fail=fail):
                backend = Backend(fail=fail)
                with self.assertRaises(OSError):
                    tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
                self.assertEqual(sum(e[0] == "destroy" for e in backend.events), destroyed)

    def test_missing_theme_is_optional(self):
        backend = Backend(fail="theme")
        tip = tooltip.NativeTooltip(1, "Hint", False, _backend=backend)
        self.assertEqual(backend.events[-1][0], "add")
        tip.destroy()

    def test_show_failures_and_zero_size_destroy_without_retry(self):
        for fail in ("max_width", "cloak_on", "size", "position", "activate", "move", "opacity", "cloak_off", "zero_size"):
            with self.subTest(fail=fail):
                backend = Backend(fail=fail, size=(0, 0) if fail == "zero_size" else (160, 30))
                tip = tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
                with self.assertRaises(OSError):
                    tip.show_above((0, 40, 100, 60), (0, 0, 800, 600))
                tip.destroy()
                tip.show_above((0, 40, 100, 60), (0, 0, 800, 600))
                self.assertEqual(sum(e[0] == "destroy" for e in backend.events), 1)

    def test_first_activation_is_cloaked_and_measured_before_revealing_full_text(self):
        backend = Backend(size=(262, 31))
        tip = tooltip.NativeTooltip(1, "Переименовать 鼠 🐁 & name", True, _backend=backend)
        self.assertEqual(backend.size(tip._window, tip._tool), (18, 16))
        start = len(backend.events)
        tip.show_above((200, 100, 400, 140), (0, 0, 800, 600))
        self.assertEqual([e[0] for e in backend.events[start:]],
                         ["max_width", "cloak", "position", "activate", "size", "position", "move", "opacity", "cloak"])
        self.assertEqual(backend.events[start + 1], ("cloak", tip._window, True))
        self.assertEqual(backend.events[-3], ("move", tip._window, 169, 63, 262, 31))
        self.assertEqual(backend.events[-1], ("cloak", tip._window, False))
        tip.destroy()

    def test_native_cloak_uses_bool_hresult_and_rejects_failure(self):
        backend = tooltip._Backend.__new__(tooltip._Backend)
        values = []

        def cloak(window, attribute, pointer, size):
            values.append((window, attribute, ctypes.cast(pointer, ctypes.POINTER(ctypes.c_int)).contents.value, size))
            return 0

        dwm = types.SimpleNamespace(DwmSetWindowAttribute=mock.Mock(side_effect=cloak))
        with mock.patch.object(ctypes, "WinDLL", create=True, return_value=dwm):
            backend.cloak(123, True)
            backend.cloak(123, False)
            dwm.DwmSetWindowAttribute.side_effect = None
            dwm.DwmSetWindowAttribute.return_value = -1
            with self.assertRaises(OSError):
                backend.cloak(123, True)
        self.assertEqual(values, [(123, 13, 1, 4), (123, 13, 0, 4)])
        self.assertIs(dwm.DwmSetWindowAttribute.restype, ctypes.c_int32)

    def test_repeated_show_reuses_window_and_destroy_is_idempotent(self):
        backend = Backend()
        tip = tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
        for _ in range(3):
            tip.show_above((0, 40, 100, 60), (0, 0, 800, 600))
        tip.destroy()
        tip.destroy()
        self.assertEqual(sum(e[0] == "create" for e in backend.events), 1)
        self.assertEqual(sum(e[0] == "add" for e in backend.events), 1)
        self.assertEqual(sum(e[0] == "destroy" for e in backend.events), 1)

    def test_invalid_inputs_never_load_native_backend(self):
        with mock.patch.object(tooltip, "_Backend", side_effect=AssertionError("DLL loaded")):
            for owner, text in ((0, "Hint"), (True, "Hint"), (-1, "Hint"), (1, ""), (1, "a\0b"), (1, None)):
                with self.subTest(owner=owner, text=text), self.assertRaises(ValueError):
                    tooltip.NativeTooltip(owner, text, True)

    def test_invalid_rect_destroys_window(self):
        backend = Backend()
        tip = tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
        with self.assertRaises(ValueError):
            tip.show_above((0, 0, 0, 0), (0, 0, 800, 600))
        self.assertEqual(backend.events[-1][0], "destroy")

    def test_windows_calls_preserve_abi_and_do_not_activate(self):
        user = types.SimpleNamespace(**{name: mock.Mock(return_value=1) for name in
            ("CreateWindowExW", "SendMessageW", "SetWindowPos", "DestroyWindow", "IsWindow", "GetWindowRect")})
        common = types.SimpleNamespace(InitCommonControlsEx=mock.Mock(return_value=1))
        kernel = types.SimpleNamespace(GetModuleHandleW=mock.Mock(return_value=99))
        def owner_rect(hwnd, pointer):
            value = ctypes.cast(pointer, ctypes.POINTER(tooltip._RECT)).contents
            value.left, value.top, value.right, value.bottom = -1600, 100, -1300, 600
            return 1
        user.GetWindowRect.side_effect = owner_rect
        dlls = {"user32": user, "comctl32": common, "kernel32": kernel}
        with mock.patch.object(tooltip.sys, "platform", "win32"), \
                mock.patch.object(ctypes, "WinDLL", create=True, side_effect=lambda name, **kw: dlls[name]):
            backend = tooltip._Backend()
        self.assertIs(user.SendMessageW.restype, ctypes.c_ssize_t)
        self.assertEqual(user.SendMessageW.argtypes[-2:], [ctypes.c_size_t, ctypes.c_ssize_t])
        self.assertIs(user.CreateWindowExW.restype, ctypes.c_void_p)
        backend.create(123)
        args = user.CreateWindowExW.call_args.args
        self.assertEqual(args[0] & 0x08000088, 0x08000088)
        self.assertEqual(args[1], "tooltips_class32")
        self.assertEqual(args[4:6], (-1600, 100))
        self.assertEqual(args[8], 123)
        backend.position(1, -1900, -136)
        packed = user.SendMessageW.call_args.args[-1]
        self.assertEqual(ctypes.c_int16(packed & 0xFFFF).value, -1900)
        self.assertEqual(ctypes.c_int16(packed >> 16).value, -136)
        backend.move(1, -40000, -300, 160, 30)
        self.assertEqual(user.SetWindowPos.call_args.args[2:6], (-40000, -300, 160, 30))
        self.assertEqual(user.SetWindowPos.call_args.args[-1], 0x0010)

    def test_owned_system_font_is_deleted_after_window_on_failure_and_normal_close(self):
        for fail in (None, "add", "move", "destroy"):
            with self.subTest(fail=fail):
                backend = Backend(fail=fail, font=123456)
                if fail == "add":
                    with self.assertRaises(OSError):
                        tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
                else:
                    tip = tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
                    if fail == "move":
                        with self.assertRaises(OSError):
                            tip.show_above((0, 40, 100, 60), (0, 0, 800, 600))
                    tip.destroy()
                    tip.destroy()
                self.assertEqual(backend.events[-2:], [("destroy", 0x123456789), ("delete_font", 123456)])

    def test_system_status_font_uses_owner_dpi_without_hardcoded_face_or_size(self):
        if tooltip.sys.platform == "win32":
            self.assertEqual(ctypes.sizeof(tooltip._LOGFONT), 92)
            self.assertEqual(ctypes.sizeof(tooltip._NONCLIENTMETRICS), 504)
        for dpi in (120, 144, 192, 240, 288):
            for matches in (True, False):
                with self.subTest(dpi=dpi, existing_font_matches=matches):
                    requested = []

                    def metrics(action, size, ptr, flags, actual_dpi):
                        self.assertEqual((action, size, flags, actual_dpi),
                                         (0x29, ctypes.sizeof(tooltip._NONCLIENTMETRICS), 0, dpi))
                        value = ctypes.cast(ptr, ctypes.POINTER(tooltip._NONCLIENTMETRICS)).contents
                        value.lfStatusFont.lfHeight = -(dpi // 8)
                        value.lfStatusFont.lfFaceName = "Configured system font"
                        return 1

                    def existing(handle, size, ptr):
                        value = ctypes.cast(ptr, ctypes.POINTER(tooltip._LOGFONT)).contents
                        value.lfHeight = -(dpi // 8) if matches else -12
                        value.lfFaceName = "Configured system font"
                        return size

                    def create(ptr):
                        value = ctypes.cast(ptr, ctypes.POINTER(tooltip._LOGFONT)).contents
                        requested.append((value.lfHeight, value.lfFaceName))
                        return 123456

                    user = types.SimpleNamespace(GetDpiForWindow=mock.Mock(return_value=dpi),
                        SystemParametersInfoForDpi=mock.Mock(side_effect=metrics),
                        SendMessageW=mock.Mock(return_value=500))
                    gdi = types.SimpleNamespace(GetObjectW=mock.Mock(side_effect=existing),
                        CreateFontIndirectW=mock.Mock(side_effect=create))
                    backend = tooltip._Backend.__new__(tooltip._Backend)
                    backend.user = user
                    with mock.patch.object(ctypes, "WinDLL", create=True, return_value=gdi):
                        result = backend.font(1, 2)
                    user.GetDpiForWindow.assert_called_once_with(2)
                    self.assertEqual(result, 0 if matches else 123456)
                    self.assertEqual(requested, [] if matches else [(-(dpi // 8), "Configured system font")])

    def test_older_windows_keeps_native_font_without_loading_gdi(self):
        backend = tooltip._Backend.__new__(tooltip._Backend)
        backend.user = types.SimpleNamespace()
        with mock.patch.object(ctypes, "WinDLL", create=True, side_effect=AssertionError("DLL loaded")):
            self.assertEqual(backend.font(1, 2), 0)

    def test_opacity_is_applied_while_cloaked_then_can_fade_without_repositioning(self):
        backend = Backend()
        tip = tooltip.NativeTooltip(1, "Hint", True, _backend=backend)
        self.assertEqual(tip.hwnd, 0x123456789)
        tip.show_above((0, 40, 100, 60), (0, 0, 800, 600), opacity=0.0)
        self.assertEqual(backend.events[-2:], [("opacity", tip.hwnd, 0), ("cloak", tip.hwnd, False)])
        start = len(backend.events)
        tip.set_opacity(0.5)
        tip.set_opacity(1.0)
        self.assertEqual(backend.events[start:], [("opacity", tip.hwnd, 128), ("opacity", tip.hwnd, 255)])
        for invalid in (-1, 2, float("nan"), float("inf"), True, "0.5"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                tip.set_opacity(invalid)
        tip.destroy()
        self.assertEqual(tip.hwnd, 0)
        tip.set_opacity(0.5)

    def test_opacity_uses_pointer_sized_style_and_alpha_without_colour_key(self):
        user = types.SimpleNamespace(GetWindowLongPtrW=mock.Mock(return_value=tooltip.EX_STYLE),
            SetWindowLongPtrW=mock.Mock(return_value=tooltip.EX_STYLE),
            SetLayeredWindowAttributes=mock.Mock(return_value=1))
        backend = tooltip._Backend.__new__(tooltip._Backend)
        backend.user = user
        with mock.patch.object(ctypes, "set_last_error", create=True), \
                mock.patch.object(ctypes, "get_last_error", create=True, return_value=0):
            backend.opacity(0x123456789, 128)
        self.assertIs(user.GetWindowLongPtrW.restype, ctypes.c_ssize_t)
        self.assertIs(user.SetWindowLongPtrW.argtypes[-1], ctypes.c_ssize_t)
        user.SetWindowLongPtrW.assert_called_once_with(0x123456789, -20, tooltip.EX_STYLE | 0x80000)
        user.SetLayeredWindowAttributes.assert_called_once_with(0x123456789, 0, 128, 2)


if __name__ == "__main__":
    unittest.main()
