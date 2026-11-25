"""Tests for the tray menu in flyout.py: colours, where the menu and its submenus
open, the - / + counter items and the size of a menu. No window is opened.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from halo import flyout  # noqa: E402
from pystray import Menu, MenuItem as Item  # noqa: E402

WORK = (0, 0, 1920, 1032)         # a 1080p screen above a 48 px taskbar


class ColourTests(unittest.TestCase):
    def test_argb(self):
        self.assertEqual(flyout.parse_argb("#99202020"), (0x99, 0x20, 0x20, 0x20))
        self.assertEqual(flyout.parse_argb("#F2F2F2"), (0xFF, 0xF2, 0xF2, 0xF2))

    def test_gradient_colour_is_abgr(self):
        self.assertEqual(flyout.abgr("#99112233"), 0x99332211)

    def test_translucent_colours_are_laid_over_the_tint(self):
        dark = flyout.colours(False)
        self.assertEqual(dark["text"], "#f2f2f2")
        self.assertEqual(dark["muted"], "#9e9e9e")        # 60 % of #F2F2F2 over #202020
        light = flyout.colours(True)
        self.assertEqual(light["text"], "#1a1a1a")
        self.assertEqual(dark["faint"], "#5f5f5f")        # 30 %: the pencil, dimmer than muted

    def test_transparent_key_is_next_to_the_tint_and_not_a_used_colour(self):
        for light in (False, True):
            c = flyout.colours(light)
            self.assertNotIn(c["key"], (c["text"], c["muted"], c["faint"], c["separator"], c["border"]))
            tint = flyout.parse_argb(flyout.PALETTE[light]["tint"])[1:]
            key = flyout.parse_argb(c["key"])[1:]
            self.assertLessEqual(max(abs(a - b) for a, b in zip(tint, key)), 1)

    def test_hover_keeps_its_opacity(self):
        self.assertAlmostEqual(flyout.colours(False)["hover_alpha"], 0x1A / 255)
        self.assertEqual(flyout.colours(False)["hover"], "#ffffff")
        self.assertEqual(flyout.colours(True)["hover"], "#000000")


class PlacementTests(unittest.TestCase):
    # taskbars on a 1920x1080 screen, 48 px thick
    BOTTOM = (0, 1032, 1920, 1080)

    def test_bottom_taskbar_centred_on_the_click_with_a_gap(self):
        self.assertEqual(flyout.place_menu(1500, 1050, 250, 300, WORK, self.BOTTOM), (1375, 724))

    def test_top_taskbar(self):
        work = (0, 48, 1920, 1080)
        self.assertEqual(flyout.place_menu(1500, 20, 250, 300, work, (0, 0, 1920, 48)), (1375, 56))

    def test_right_taskbar_centred_vertically(self):
        work = (0, 0, 1872, 1080)
        self.assertEqual(flyout.place_menu(1890, 500, 250, 300, work, (1872, 0, 1920, 1080)),
                         (1614, 350))

    def test_left_taskbar(self):
        work = (48, 0, 1920, 1080)
        self.assertEqual(flyout.place_menu(20, 500, 250, 300, work, (0, 0, 48, 1080)), (56, 350))

    def test_kept_off_the_screen_edges(self):
        # a click at the far right: the menu stops 8 px from the edge
        self.assertEqual(flyout.place_menu(1910, 1050, 250, 300, WORK, self.BOTTOM), (1662, 724))
        # a vertical taskbar and a click near the bottom
        work = (0, 0, 1872, 1080)
        self.assertEqual(flyout.place_menu(1890, 1070, 250, 300, work, (1872, 0, 1920, 1080)),
                         (1614, 780))

    def test_gap_scales(self):
        self.assertEqual(flyout.place_menu(1500, 1050, 250, 300, WORK, self.BOTTOM, 1.5), (1375, 720))

    def test_without_a_taskbar_centred_above_the_cursor(self):
        self.assertEqual(flyout.place_menu(1000, 800, 250, 300, WORK), (875, 492))

    def test_submenu_to_the_right_with_overlap_and_raised(self):
        self.assertEqual(flyout.place_submenu(1000, 1250, 800, 200, 150, WORK), (1248, 795))

    def test_submenu_to_the_left_without_room(self):
        self.assertEqual(flyout.place_submenu(1600, 1850, 800, 200, 150, WORK), (1402, 795))

    def test_submenu_scaled(self):
        # 150 %: overlap 3 px, 8 px higher (5 * 1.5 rounded)
        self.assertEqual(flyout.place_submenu(1000, 1250, 800, 200, 150, WORK, 1.5), (1247, 792))

    def test_submenu_stays_on_the_screen(self):
        x, y = flyout.place_submenu(1000, 1250, 1000, 200, 150, WORK)
        self.assertEqual(y, WORK[3] - 150)


class TaskbarAreaTests(unittest.TestCase):
    """The menu and its submenus stay off the taskbar even when the work area includes
    it: an auto-hide taskbar, or the taskbar over a full screen game (reported with
    Dota 2: the bottom rows of the menu were behind the taskbar)."""
    SCREEN = (0, 0, 1920, 1080)

    def test_bottom_taskbar_is_cut_off(self):
        self.assertEqual(flyout.usable_area(self.SCREEN, (0, 1032, 1920, 1080)), (0, 0, 1920, 1032))

    def test_top_left_and_right_taskbars(self):
        self.assertEqual(flyout.usable_area(self.SCREEN, (0, 0, 1920, 48)), (0, 48, 1920, 1080))
        self.assertEqual(flyout.usable_area(self.SCREEN, (0, 0, 48, 1080)), (48, 0, 1920, 1080))
        self.assertEqual(flyout.usable_area(self.SCREEN, (1872, 0, 1920, 1080)), (0, 0, 1872, 1080))

    def test_a_work_area_without_the_taskbar_is_unchanged(self):
        # the usual case: Windows already left the taskbar out
        self.assertEqual(flyout.usable_area(WORK, (0, 1032, 1920, 1080)), WORK)
        self.assertEqual(flyout.usable_area(WORK, None), WORK)

    def test_a_hidden_auto_hide_taskbar_leaves_its_visible_edge_out(self):
        # hidden, it keeps a 2 px edge on the screen
        self.assertEqual(flyout.usable_area(self.SCREEN, (0, 1078, 1920, 1126)), (0, 0, 1920, 1078))

    def test_a_taskbar_on_another_monitor_is_ignored(self):
        self.assertEqual(flyout.usable_area(self.SCREEN, (1920, 1032, 3840, 1080)), self.SCREEN)

    def test_a_long_submenu_opens_above_the_taskbar(self):
        # Preferences near the bottom of a full screen game: 500 px tall
        area = flyout.usable_area(self.SCREEN, (0, 1032, 1920, 1080))
        x, y = flyout.place_submenu(1000, 1250, 900, 250, 500, area)
        self.assertLessEqual(y + 500, 1032)

    def test_the_menu_passes_the_area_without_the_taskbar_to_its_submenus(self):
        import types
        seen = {}

        class FakeWin32:
            def monitor(self, x, y):
                return (0, 0, 1920, 1080), 1.0          # the work area includes the taskbar

            def taskbar(self, x, y):
                return (0, 1032, 1920, 1080)

            def buttons_down(self):
                return False

            def activate(self, hwnd):
                pass

            def stack_windows(self, windows):
                pass

        class FakePanel:
            def __init__(self, host, menu, style, parent, row):
                self.rows, self.w, self.h, self.hwnd = [1], 250, 300, 1
                self._hwnds = [1]
                self.win = types.SimpleNamespace(focus_force=lambda: None)

            def open(self, x, y):
                seen["y"] = y

            def fit(self, work):
                pass

            def destroy(self):
                pass

        host = flyout.FlyoutHost(FakeWin32())
        host._root = types.SimpleNamespace(after=lambda *a: None, after_cancel=lambda *a: None)
        host.style = lambda scale, light: types.SimpleNamespace(scale=scale)
        saved = flyout._Panel
        flyout._Panel = FakePanel
        try:
            host._show(None, None, (1500, 1050))
        finally:
            flyout._Panel = saved
        self.assertEqual(host.panels[0].work, (0, 0, 1920, 1032))
        self.assertLessEqual(seen["y"] + 300, 1032)


class CounterTests(unittest.TestCase):
    def make(self, value=20):
        cfg = {"low": value}
        item = flyout.CounterItem("Low battery alert", [(0, "Off"), (10, "10%"), (20, "20%"), (30, "30%")],
                                  lambda: cfg["low"], lambda v: cfg.__setitem__("low", v))
        return item, cfg

    def test_steps_through_the_choices(self):
        item, cfg = self.make(20)
        self.assertEqual(item.label(), "20%")
        self.assertTrue(item.step(+1))
        self.assertEqual(cfg["low"], 30)
        self.assertFalse(item.step(+1))              # at the end
        self.assertEqual(cfg["low"], 30)
        item.step(-1), item.step(-1), item.step(-1)
        self.assertEqual(item.label(), "Off")
        self.assertFalse(item.can_step(-1))

    def test_a_value_between_the_choices_counts_as_the_nearest(self):
        item, _cfg = self.make(12)
        self.assertEqual(item.label(), "10%")

    def test_classic_menu_sees_a_radio_submenu(self):
        item, cfg = self.make(10)
        sub = item.submenu
        self.assertEqual([i.text for i in sub.items], ["Off", "10%", "20%", "30%"])
        self.assertEqual([i.checked for i in sub.items], [False, True, False, False])
        sub.items[3](None)
        self.assertEqual(cfg["low"], 30)


class FakeStyle:
    """_Style without tkinter: 7 px per character, 17 px line."""

    def __init__(self, scale=1.0):
        self.scale = scale
        self.px = lambda v: max(1, round(v * scale))
        self.line = round(17 * scale)
        self.title_line = round(17 * scale)
        self.detail_line = round(14 * scale)
        self.title_font = types.SimpleNamespace(measure=lambda text: round(8 * len(text) * scale))
        self.detail_font = types.SimpleNamespace(measure=lambda text: round(6 * len(text) * scale))
        self.icon_family = "Segoe Fluent Icons"

    def glyph(self, g):
        return g


def measure(text):
    return 7 * len(text)


class LayoutTests(unittest.TestCase):
    def menu(self):
        cfg = {"low": 20}
        return Menu(
            Item("Header", None, enabled=False),
            Menu.SEPARATOR,
            Item("Refresh now", lambda: None),
            Item("Hidden", lambda: None, visible=False),
            Item("Preferences", Menu(Item("A", lambda: None))),
            flyout.CounterItem("Low battery alert", [(0, "Off"), (20, "20%")],
                               lambda: cfg["low"], lambda v: None),
            Item("Checked", lambda: None, checked=lambda i: True),
        )

    def test_rows_follow_the_visible_items(self):
        rows = flyout.build_rows(self.menu())
        self.assertEqual([r.kind for r in rows], ["item", "sep", "item", "item", "counter", "item"])
        self.assertFalse(rows[0].selectable)
        self.assertFalse(rows[1].selectable)
        self.assertIsNotNone(rows[3].submenu)
        self.assertTrue(rows[5].checked)

    def test_sizes_at_100_percent(self):
        rows = flyout.build_rows(self.menu())
        w, h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        # the widest row is the counter: text, 16 px gap, - (28) value (34) + (28)
        self.assertEqual(w, 4 + 8 + 24 + 7 * 17 + 16 + 28 + 34 + 28 + 10 + 4)
        item_h = 1 + 6 + 17 + 6 + 1
        sep_h = 4 + 1 + 4
        self.assertEqual(rows[0].y, 4)
        self.assertEqual(rows[0].h, item_h)
        self.assertEqual(rows[1].h, sep_h)
        self.assertEqual(rows[4].h, item_h)                        # the 26 px buttons fit
        self.assertEqual(h, 4 + 5 * item_h + sep_h + 4)

    def test_everything_scales(self):
        rows = flyout.build_rows(self.menu())
        w1, h1 = flyout.layout(rows, FakeStyle(1.0), measure, lambda g: 10)
        rows = flyout.build_rows(self.menu())
        w2, h2 = flyout.layout(rows, FakeStyle(1.5), lambda t: round(measure(t) * 1.5), lambda g: 15)
        self.assertAlmostEqual(w2 / w1, 1.5, delta=0.01)
        self.assertAlmostEqual(h2 / h1, 1.5, delta=0.05)

    def test_minimum_width(self):
        rows = flyout.build_rows(Menu(Item("Exit", lambda: None)))
        self.assertEqual(flyout.layout(rows, FakeStyle(), measure, lambda g: 10)[0], 180)
        rows = flyout.build_rows(Menu(Item("Exit", lambda: None)))
        self.assertEqual(flyout.layout(rows, FakeStyle(1.25), measure, lambda g: 10)[0], 225)

    def test_a_long_text_widens_the_menu(self):
        rows = flyout.build_rows(Menu(Item("x" * 40, lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(w, 4 + 8 + 6 + 280 + 10 + 4)

    def test_only_checkable_menus_reserve_the_full_check_gutter(self):
        for checked in (False, True):
            rows = flyout.build_rows(Menu(Item("Choice", lambda: None,
                                               checked=lambda _item: checked)))
            self.assertEqual(flyout.check_column(rows), flyout.CHECK_COLUMN)
        plain = flyout.build_rows(Menu(Item("Settings", lambda: None)))
        self.assertEqual(flyout.check_column(plain), flyout.PLAIN_COLUMN)



class HeaderTests(unittest.TestCase):
    """The device's name and state at the top of the menu: wrapped, never wider."""

    def header(self, title, detail):
        return flyout.HeaderItem(lambda: title, lambda: detail)

    def test_classic_menu_sees_one_disabled_item(self):
        item = self.header("Viper Ultimate", "85%, charging")
        self.assertEqual(item.text, "Viper Ultimate: 85%, charging")
        self.assertFalse(item.enabled)
        self.assertEqual(self.header("No devices found", "").text, "No devices found")

    def test_the_texts_are_read_each_time(self):
        level = [50]
        item = flyout.HeaderItem(lambda: "Mouse", lambda: f"{level[0]}%")
        level[0] = 40
        self.assertEqual(item.text, "Mouse: 40%")

    def test_a_long_header_widens_the_menu_only_to_the_header_cap(self):
        long = "85%, charging (last known value, device asleep), about 12 h of use left"
        rows = flyout.build_rows(Menu(self.header("Razer BlackShark V2 Pro 2023 edition", long),
                                      Item("Exit", lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(w, 320)
        room = flyout.header_text_width(w, FakeStyle())
        self.assertEqual(room, 320 - 4 - 4 - 8 - 6 - 10)
        for text, detail in rows[0].lines:
            font = FakeStyle().detail_font if detail else FakeStyle().title_font
            self.assertLessEqual(font.measure(text), room)
        titles = [t for t, d in rows[0].lines if not d]
        details = [t for t, d in rows[0].lines if d]
        self.assertEqual(" ".join(titles), "Razer BlackShark V2 Pro 2023 edition")
        self.assertEqual(" ".join(details), long)

    def test_the_state_goes_on_the_line_below_the_name(self):
        rows = flyout.build_rows(Menu(self.header("Viper", "85%"), Item("Exit", lambda: None)))
        flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(rows[0].lines, [("Viper", False), ("85%", True)])
        # 8 above, two lines with a 2 px gap, 8 below, then its separator (4 + 1 + 4)
        self.assertEqual(rows[0].h, 8 + 17 + 8 + 14 + 8 + 9)
        self.assertEqual(flyout.header_line_tops(rows[0], FakeStyle()), [4 + 8, 4 + 8 + 17 + 8])
        self.assertEqual(rows[1].y, 4 + rows[0].h)
        self.assertFalse(rows[0].selectable)

    def test_a_separator_after_the_header_is_not_doubled(self):
        rows = flyout.build_rows(Menu(self.header("No devices found", ""), Menu.SEPARATOR,
                                      Item("Exit", lambda: None)))
        self.assertEqual([r.kind for r in rows], ["header", "item"])

    def test_without_a_detail_it_is_one_line(self):
        rows = flyout.build_rows(Menu(self.header("No devices found", "")))
        flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(rows[0].lines, [("No devices found", False)])
        self.assertEqual(rows[0].h, 8 + 17 + 8 + 9)

    def test_other_items_still_set_the_width(self):
        rows = flyout.build_rows(Menu(self.header("Mouse", "50%"), Item("y" * 40, lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(w, 4 + 8 + 6 + 280 + 10 + 4)

    def test_the_pencil_leaves_room_next_to_the_title(self):
        long_name = "Razer BlackShark V2 Pro 2023 edition"
        rows = flyout.build_rows(Menu(flyout.HeaderItem(lambda: long_name, lambda: "85%",
                                                        edit=lambda icon: None),
                                      Item("Exit", lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        room = flyout.header_text_width(w, FakeStyle()) - 8 - 28
        titles = [t for t, d in rows[0].lines if not d]
        self.assertTrue(all(FakeStyle().title_font.measure(t) <= room for t in titles))
        x0, y0, x1, y1 = flyout.edit_box(rows[0], w, FakeStyle())
        self.assertEqual((x1 - x0, y1 - y0), (28, 26))
        self.assertLessEqual(x1, w - 4)                      # inside the panel
        mid = flyout.header_line_tops(rows[0], FakeStyle())[0] + 17 // 2
        self.assertEqual((y0 + y1) // 2, mid)                # level with the title

    def test_scaled_long_headers_fit_their_fonts_and_do_not_overlap_the_next_row(self):
        for scale in (1, 1.25, 1.75, 2):
            with self.subTest(scale=scale):
                style = FakeStyle(scale)
                rows = flyout.build_rows(Menu(self.header(
                    "SteelSeries Arctis Nova 7 Wireless ヘッドセット",
                    "69%, about 33 h of use left; останнє відоме значення"),
                    Item("Settings", lambda: None)))
                width, _height = flyout.layout(rows, style,
                    lambda t: round(measure(t) * scale), lambda _g: round(10 * scale))
                room = flyout.header_text_width(width, style)
                tops = flyout.header_line_tops(rows[0], style)
                for (text, detail), top in zip(rows[0].lines, tops):
                    font = style.detail_font if detail else style.title_font
                    line = style.detail_line if detail else style.title_line
                    self.assertLessEqual(font.measure(text), room)
                    self.assertLessEqual(top + line, rows[1].y - style.px(flyout.HEADER_PAD_B))
                for index in range(len(tops) - 1):
                    detail = rows[0].lines[index][1]
                    line = style.detail_line if detail else style.title_line
                    self.assertLessEqual(tops[index] + line, tops[index + 1])

    def test_only_a_header_with_a_pencil_can_be_chosen(self):
        with_edit = flyout.build_rows(Menu(flyout.HeaderItem(lambda: "M", edit=lambda i: None)))
        without = flyout.build_rows(Menu(self.header("M", "")))
        self.assertTrue(with_edit[0].selectable)
        self.assertFalse(without[0].selectable)

    def test_classic_only_items_are_left_out_of_the_flyout(self):
        menu = Menu(self.header("Mouse", "50%"),
                    flyout.classic_only(Item("Rename…", lambda: None)),
                    Item("Icon", lambda: None))
        self.assertEqual([r.text for r in flyout.build_rows(menu)], ["Mouse: 50%", "Icon"])
        self.assertIn("Rename…", [i.text for i in menu.items])   # the classic menu keeps it

    def test_wrap(self):
        self.assertEqual(flyout.wrap("aa bb cc", 35, measure), ["aa bb", "cc"])
        self.assertEqual(flyout.wrap("abcdefghij", 28, measure), ["abcd", "efgh", "ij"])
        self.assertEqual(flyout.wrap("", 28, measure), [""])
        self.assertEqual(flyout.wrap("a", 1, measure), ["a"])         # never an endless loop

class FakeWindow:
    """Records what the highlight code does to a tkinter window."""

    def __init__(self):
        self.calls = []

    def attributes(self, name, value):
        self.calls.append((name, value))

    def geometry(self, geo):
        self.calls.append(("geometry", geo))

    def update_idletasks(self):
        self.calls.append(("idle",))

    def configure(self, **kw):
        pass

    def delete(self, *a):
        pass

    def create_rectangle(self, *a, **kw):
        pass

    def create_oval(self, *a, **kw):
        pass


class HighlightTests(unittest.TestCase):
    """The highlight is a window of its own. Opening the menu brings the panel to the
    front (it takes the focus), and the panel's acrylic then hides a highlight that is
    behind it, so the highlight is raised each time it is shown."""

    def panel(self, box):
        p = object.__new__(flyout._Panel)
        p.overlay, p.ocanvas = FakeWindow(), FakeWindow()
        self.raised = []

        def to_top(hwnd):
            self.raised.append(hwnd)
            if p.overlay is not None:
                p.overlay.calls.append(("raise", hwnd))
        p.host = flyout.FlyoutHost(types.SimpleNamespace(to_top=to_top))
        p.host.panels = [p]
        p._hwnds = [0x1234]
        p._overlay_hwnd = 0x1234
        p.style = FakeStyle()
        p.style.colours = {"hover": "#ffffff", "hover_alpha": 0.1}
        p.x, p.y, p._overlay_geo = 100, 200, None
        p.highlight_box = lambda: box
        p._fade_running = lambda: False
        return p

    def test_a_shown_highlight_is_raised_over_the_panel(self):
        p = self.panel((4, 5, 200, 34))
        p.update_highlight()
        self.assertEqual(self.raised, [0x1234])
        self.assertEqual(p.overlay.calls[-1], ("-alpha", 0.1))
        # moved first, and the move applied before the raise: raising a window whose
        # move Tk has not made yet keeps it in the panel's corner
        self.assertEqual(p.overlay.calls, [("geometry", "196x29+104+205"), ("idle",),
                                           ("raise", 0x1234), ("-alpha", 0.1)])

    def test_the_same_box_again_is_raised_again(self):
        p = self.panel((4, 5, 200, 34))
        p.update_highlight()
        p.update_highlight()                       # e.g. after a click brought the panel up
        self.assertEqual(self.raised, [0x1234, 0x1234])

    def test_a_hidden_highlight_is_not_raised(self):
        p = self.panel(None)
        p.update_highlight()
        self.assertEqual(p.overlay.calls, [("-alpha", 0.0)])
        self.assertEqual(self.raised, [])

    def test_no_win32_helper_is_not_an_error(self):
        p = self.panel((4, 5, 200, 34))
        p.host = flyout.FlyoutHost()     # not Windows
        p.update_highlight()
        self.assertEqual(p.overlay.calls[-1], ("-alpha", 0.1))

    def test_native_per_pixel_highlight_changes_without_reordering_the_panel_stack(self):
        from unittest import mock
        p = self.panel((4, 5, 200, 34))
        p.overlay = None
        p._native_highlight = mock.Mock(hwnd=0x1234)
        p.update_highlight()
        p._native_highlight.show.assert_called_once_with((104, 205, 300, 234), 4, "#ffffff", 0.1)
        self.assertEqual(self.raised, [])
        p.highlight_box = lambda: None
        p.update_highlight()
        p._native_highlight.hide.assert_called_once_with()
        self.assertEqual(self.raised, [])

    def test_window_stack_is_submitted_as_one_batch(self):
        from unittest import mock
        win32 = object.__new__(flyout._Win32)
        win32.user32 = mock.Mock()
        win32.user32.BeginDeferWindowPos.return_value = 100
        win32.user32.DeferWindowPos.side_effect = [101, 102, 103]
        win32.stack_windows([11, 12, 13])
        self.assertEqual(win32.user32.DeferWindowPos.call_args_list,
                         [mock.call(100, 11, -1, 0, 0, 0, 0, 0x0213),
                          mock.call(101, 12, -1, 0, 0, 0, 0, 0x0213),
                          mock.call(102, 13, -1, 0, 0, 0, 0, 0x0213)])
        win32.user32.EndDeferWindowPos.assert_called_once_with(103)
        win32.user32.SetWindowPos.assert_not_called()

    def test_failed_batch_places_ancestors_below_descendants_instead_of_raising_them(self):
        from unittest import mock
        for fail in ("begin", "defer", "end"):
            with self.subTest(fail=fail):
                win32 = object.__new__(flyout._Win32)
                win32.user32 = mock.Mock()
                win32.user32.BeginDeferWindowPos.return_value = 0 if fail == "begin" else 100
                win32.user32.DeferWindowPos.return_value = 0 if fail == "defer" else 101
                win32.user32.EndDeferWindowPos.return_value = 0
                win32.stack_windows([11, 12, 13])
                self.assertEqual(win32.user32.SetWindowPos.call_args_list,
                                 [mock.call(13, -1, 0, 0, 0, 0, 0x0213),
                                  mock.call(12, 13, 0, 0, 0, 0, 0x0213),
                                  mock.call(11, 12, 0, 0, 0, 0, 0x0213)])

    def test_restack_does_not_change_owner_z_order_geometry_or_focus(self):
        from unittest import mock
        win32 = object.__new__(flyout._Win32)
        win32.user32 = mock.Mock()
        win32.to_top(123)
        win32.user32.SetWindowPos.assert_called_once_with(123, -1, 0, 0, 0, 0, 0x0213)



class SubmenuStackingTests(unittest.TestCase):
    def panels(self):
        order = [11, 12, 13, 21, 22, 23, 31, 32, 33]
        calls = []

        def top(hwnd):
            calls.append(hwnd)
            order.remove(hwnd)
            order.append(hwnd)  # model HWND_TOPMOST within its window band

        host = flyout.FlyoutHost(types.SimpleNamespace(to_top=top))
        host._root = types.SimpleNamespace(after=lambda *args: None, after_cancel=lambda *_args: None)
        percent = Menu(Item("15%", lambda: None, checked=lambda _item: False))
        menus = [Menu(Item("This device", percent)), Menu(Item("Low battery", percent)), percent]
        panels = []
        for depth, (menu, x, width) in enumerate(zip(menus, (100, 280, 150), (180, 130, 130))):
            p = object.__new__(flyout._Panel)
            p.host, p.menu, p.rows, p.style = host, menu, flyout.build_rows(menu), FakeStyle()
            p.style.colours = {"hover": "#ffffff", "hover_alpha": 0.1}
            p.w, p.h = width, 100
            flyout.layout(p.rows, p.style, measure, lambda _g: 10)
            p.x, p.y = x, 200
            p.win, p.overlay, p.ocanvas = FakeWindow(), FakeWindow(), FakeWindow()
            p._hwnds = list(range((depth + 1) * 10 + 1, (depth + 1) * 10 + 4))
            p._overlay_hwnd = p._hwnds[-1]
            p._overlay_geo = None
            p.editor = p._hint = p._hint_timer = None
            p.hover, p.part = (0, None) if depth < 2 else (None, None)
            p.child_row = 0 if depth < 2 else None
            p._fade_running = lambda: False
            panels.append(p)
        host.panels = panels
        return host, panels, order, calls

    def test_left_opening_grandchild_stays_above_retained_ancestor_highlights(self):
        host, panels, order, calls = self.panels()
        root, child, grandchild = panels
        # Percent submenu opens left over the main menu. Clearing root.hover
        # retains its selected child-row highlight underneath that submenu.
        point = (170, 215)
        self.assertTrue(root.contains(*point))
        self.assertTrue(grandchild.contains(*point))
        host._track(*point)
        self.assertIsNone(root.hover)
        self.assertEqual(grandchild.hover, 0)
        self.assertIsNotNone(root.highlight_box())
        self.assertLess(order.index(root._overlay_hwnd), order.index(child._hwnds[0]))
        self.assertLess(order.index(child._overlay_hwnd), order.index(grandchild._hwnds[0]))
        self.assertEqual(order, [11, 12, 13, 21, 22, 23, 31, 32, 33])
        self.assertEqual(calls[-9:], order)
        # Later keyboard/hover redraws cannot raise an ancestor over any of the
        # descendant's catcher, panel surface or highlight windows either.
        root.update_highlight()
        child.update_highlight()
        self.assertEqual(order, [11, 12, 13, 21, 22, 23, 31, 32, 33])
        host._track(175, 216)
        self.assertEqual(order, [11, 12, 13, 21, 22, 23, 31, 32, 33])

    def test_fade_completion_preserves_descendant_depth_order(self):
        _host, panels, order, _calls = self.panels()
        root = panels[0]
        root._fade_running = lambda: True
        root.update_highlight()
        self.assertEqual(root.overlay.calls, [("-alpha", 0.0)])
        root._fade_running = lambda: False
        root.update_highlight()
        self.assertEqual(order, [11, 12, 13, 21, 22, 23, 31, 32, 33])
        self.assertEqual(root.overlay.calls[-1], ("-alpha", 0.1))

    def timers(self, host):
        pending, cancelled = {}, []
        def after(delay, fn, *args):
            key = len(cancelled) + len(pending) + 1
            pending[key] = (delay, fn, args)
            return key
        def cancel(key):
            cancelled.append(key)
            pending.pop(key, None)
        host._root = types.SimpleNamespace(after=after, after_cancel=cancel)
        return pending, cancelled

    def test_leaving_all_panels_closes_children_after_delay_without_postponing_on_motion(self):
        host, panels, _order, _calls = self.panels()
        pending, _cancelled = self.timers(host)
        for panel in panels:
            panel.destroy = lambda: None
        host._track(1000, 1000)
        timer = host._timer
        host._track(1001, 1001)
        self.assertEqual(host._timer, timer)
        delay, fn, args = pending.pop(timer)
        self.assertEqual(delay, 200)
        fn(*args)
        self.assertEqual(host.panels, [panels[0]])

    def test_return_to_submenu_or_opening_row_cancels_delayed_close(self):
        for point in ((170, 215), (115, 215)):
            host, _panels, _order, _calls = self.panels()
            _pending, cancelled = self.timers(host)
            host._track(1000, 1000)  # a short diagonal crossing outside the panels
            timer = host._timer
            host._track(*point)
            self.assertIn(timer, cancelled)
            self.assertEqual(len(host.panels), 3)

    def test_return_to_main_opener_closes_only_grandchild_after_grace_period(self):
        host, panels, _order, _calls = self.panels()
        pending, _cancelled = self.timers(host)
        for panel in panels:
            panel.destroy = lambda: None
        host._last_pt = None
        host._track(115, 215)
        timer = host._timer
        self.assertIsNotNone(timer)
        self.assertEqual(len(host.panels), 3)
        host._track(116, 216)  # moving inside the opener cannot postpone closure
        self.assertEqual(host._timer, timer)
        delay, fn, args = pending.pop(timer)
        self.assertEqual(delay, 200)
        fn(*args)
        self.assertEqual(host.panels, panels[:2])
        self.assertEqual(panels[0].child_row, 0)
        self.assertIsNone(panels[1].child_row)

    def test_motion_within_opening_row_does_not_repaint_or_raise_windows(self):
        from unittest import mock
        host, panels, _order, calls = self.panels()
        self.timers(host)
        root = panels[0]
        root.update_highlight = mock.Mock()
        host._track(115, 215)
        calls.clear()  # the first crossing clears the child's previous hover
        host._track(116, 216)
        root.update_highlight.assert_not_called()
        self.assertEqual(calls, [])

    def test_reenter_child_cancels_grandchild_close_and_outside_changes_close_target(self):
        host, panels, _order, _calls = self.panels()
        pending, cancelled = self.timers(host)
        host._track(115, 215)
        first = host._timer
        host._track(285, 215)
        self.assertIn(first, cancelled)
        self.assertIsNone(host._timer)
        host._track(115, 215)
        first = host._timer
        host._track(1000, 1000)
        self.assertIn(first, cancelled)
        _delay, _fn, args = pending[host._timer]
        self.assertEqual(args[1], (panels[0],))

    def test_keyboard_navigation_cancels_pointer_close_before_selecting_grandchild(self):
        host, panels, _order, _calls = self.panels()
        pending, cancelled = self.timers(host)
        host._track(115, 215)
        timer = host._timer
        self.assertIsNotNone(timer)
        host._key(types.SimpleNamespace(keysym="Down"))
        self.assertIn(timer, cancelled)
        self.assertEqual(pending, {})
        self.assertEqual(host.panels, panels)
        self.assertEqual((panels[-1].hover, panels[-1].part), (0, "keyboard"))

    def test_parent_blank_padding_schedules_descendant_close(self):
        host, panels, _order, _calls = self.panels()
        pending, _cancelled = self.timers(host)
        host._track(101, 205)  # inside main menu, outside its selectable item inset
        self.assertEqual(host._timer_panel, panels[0])
        self.assertEqual(pending[host._timer][0], 200)

    def test_hint_window_stays_above_all_depths_after_show_and_hide_restack(self):
        host, panels, order, _calls = self.panels()
        order.append(99)
        panels[0]._hint = types.SimpleNamespace(hwnd=99)
        panels[0].update_highlight()
        self.assertEqual(order, [11, 12, 13, 21, 22, 23, 31, 32, 33, 99])
        panels[0]._hint = None
        order.remove(99)
        host._restack()
        self.assertEqual(order, [11, 12, 13, 21, 22, 23, 31, 32, 33])


class PencilTests(unittest.TestCase):
    """The pencil at the right of the device's name: hover, keyboard and click."""

    def panel(self, edit):
        rows = flyout.build_rows(Menu(flyout.HeaderItem(lambda: "Mouse", lambda: "50%", edit=edit),
                                      Item("Icon", lambda: None)))
        p = object.__new__(flyout._Panel)
        p.rows, p.style = rows, FakeStyle()
        p.w, p.h = flyout.layout(rows, p.style, measure, lambda g: 10)
        p.x, p.y, p.hover, p.part, p.child_row = 100, 200, None, None, None
        p.win = object()
        p.editor = p._hint_timer = p._hint = None
        self.draws = []
        p.draw = lambda: self.draws.append((p.hover, p.part))
        p.update_highlight = lambda: None
        return p

    def test_only_the_pencil_is_under_the_mouse(self):
        p = self.panel(lambda icon: None)
        x0, y0, x1, y1 = flyout.edit_box(p.rows[0], p.w, p.style)
        self.assertEqual(p.hit(p.x + (x0 + x1) // 2, p.y + (y0 + y1) // 2), (0, "edit"))
        self.assertEqual(p.hit(p.x + 40, p.y + (y0 + y1) // 2), (None, None))   # the name
        self.assertEqual(p.hit(p.x + (x0 + x1) // 2, p.y + y1 + 20), (None, None))

    def test_the_highlight_covers_the_pencil(self):
        p = self.panel(lambda icon: None)
        p.hover, p.part = 0, "edit"
        self.assertEqual(p.highlight_box(), flyout.edit_box(p.rows[0], p.w, p.style))

    def test_the_pencil_brightens_under_the_mouse(self):
        p = self.panel(lambda icon: None)
        p.set_hover(0, "edit")
        p.set_hover(0, "edit")
        p.set_hover(1, None)
        self.assertEqual(self.draws, [(0, "edit"), (1, None)])        # redrawn on a change only

    def test_the_keyboard_reaches_the_pencil(self):
        p = self.panel(lambda icon: None)
        p.move_hover(+1)
        self.assertEqual((p.hover, p.part), (0, "keyboard"))

    def test_a_click_closes_the_menu_and_edits(self):
        done = []
        p = self.panel(lambda icon: done.append(icon))
        host = object.__new__(flyout.FlyoutHost)
        host._icon, host.closed = "icon", False
        host.close = lambda: setattr(host, "closed", True)
        host._activate(p, 0, "edit")
        for _ in range(100):
            if done:
                break
            import time
            time.sleep(0.01)
        self.assertTrue(host.closed)
        self.assertEqual(done, ["icon"])

class InlineRenameTests(unittest.TestCase):
    class Entry:
        def __init__(self, *args, **kwargs):
            self.text, self.bindings, self.destroyed, self.focused = "", {}, False, False

        def place(self, **kwargs):
            self.geometry = kwargs

        def insert(self, index, text):
            self.text = text

        def get(self):
            return self.text

        def selection_range(self, *args):
            pass

        def bind(self, key, callback):
            self.bindings[key] = callback

        def lift(self):
            pass

        def focus_set(self):
            self.focused = True

        def destroy(self):
            self.destroyed = True

    class Field(Entry):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.ovals = []
            self._w = ".field"
            self.raises = []
            self.tk = types.SimpleNamespace(call=lambda *args: self.raises.append(args))

        def lift(self):
            raise AssertionError("Canvas.lift raises tags, not the field widget")

        def create_rectangle(self, *args, **kwargs):
            pass

        def create_oval(self, *args, **kwargs):
            self.ovals.append(args)

    def panel(self, factory=None):
        p = flyout._Panel.__new__(flyout._Panel)
        self.commits = []
        self.name = "Work mouse"

        def commit(text):
            self.commits.append(text)
            self.name = text
            return True

        header = flyout.HeaderItem(lambda: self.name, lambda: "85%, about 12 h left",
                                  begin_rename=factory or (lambda: (self.name, commit)),
                                  rename_hint="Double-click to rename")
        p.menu = Menu(header, Item("Settings", lambda: None))
        p.rows, p.style = flyout.build_rows(p.menu), FakeStyle()
        p.style.font = types.SimpleNamespace(measure=measure)
        p.style.colours = flyout.colours(False)
        p.style.light = False
        p.hwnd = 123
        p.x, p.y, p.hover, p.part, p.child_row = 100, 200, None, None, None
        p.work = (0, 0, 800, 600)
        p.editor = p._edit_frame = p._hint_timer = p._hint = p._edit_commit = p._edit_row = None
        p._native_highlight = p._hint_fade_timer = None
        p.catcher = p.overlay = p.canvas = None
        p.parent = None
        p.win = types.SimpleNamespace(focus_set=lambda: None, geometry=lambda geo: None)
        p.host = flyout.FlyoutHost()
        p.host._tk = types.SimpleNamespace(Entry=self.Entry, Canvas=self.Field)
        p.host._root = types.SimpleNamespace(after=lambda *args: "hint", after_cancel=lambda _id: None)
        p.host.panels = [p]
        p.draw = lambda: None
        p.update_highlight = lambda: None
        p.fit(p.work)
        return p

    def test_only_the_name_is_clickable_not_the_status_or_padding(self):
        p = self.panel()
        x0, y0, x1, y1 = p.name_box(p.rows[0])
        self.assertEqual(p.hit(p.x + x0 + 1, p.y + y0 + 1), (0, "name"))
        self.assertEqual(p.hit(p.x + x1 + 1, p.y + y0 + 1), (None, None))
        self.assertEqual(p.hit(p.x + x0 + 1, p.y + y1 + p.style.px(3)), (None, None))

    def test_single_click_keeps_menu_double_click_begins_inline(self):
        p = self.panel()
        x0, y0, _x1, _y1 = p.name_box(p.rows[0])
        x, y = p.x + x0 + 1, p.y + y0 + 1
        p.host._click(x, y)
        self.assertIsNone(p.editor)
        self.assertEqual(p.host.panels, [p])
        self.assertEqual(p.host._double_click(types.SimpleNamespace(x_root=x, y_root=y)), "break")
        self.assertEqual(p.editor.text, "Work mouse")
        self.assertTrue(p.editor.focused)

    def test_enter_saves_and_refreshes_header_without_closing(self):
        p = self.panel()
        p.begin_edit(0)
        entry = p.editor
        entry.text = "Desk mouse"
        self.assertEqual(entry.bindings["<Return>"](), "break")
        self.assertEqual(self.commits, ["Desk mouse"])
        self.assertTrue(entry.destroyed)
        self.assertIsNone(p.editor)
        self.assertEqual(p.rows[0].title, "Desk mouse")
        self.assertEqual(p.host.panels, [p])

    def test_escape_cancels_without_calling_commit(self):
        p = self.panel()
        p.begin_edit(0)
        entry = p.editor
        entry.text = "Discard"
        self.assertEqual(entry.bindings["<Escape>"](), "break")
        self.assertEqual(self.commits, [])
        self.assertTrue(entry.destroyed)

    def test_editor_does_not_cover_the_detail_and_destroy_cancels_without_saving(self):
        p = self.panel()
        p.begin_edit(0)
        entry = p.editor
        detail_top = next(top for (_text, detail), top in
                          zip(p.rows[0].lines, flyout.header_line_tops(p.rows[0], p.style)) if detail)
        field = p._edit_frame
        self.assertLessEqual(field.geometry["y"] + field.geometry["height"], detail_top)
        self.assertGreater(len(field.ovals), 0)  # rounded Canvas field, inset borderless Entry
        self.assertEqual(field.raises, [("raise", ".field")])
        p.win.destroy = lambda: None
        p.destroy()
        self.assertTrue(entry.destroyed)
        self.assertTrue(field.destroyed)
        self.assertEqual(self.commits, [])

    def test_pointer_motion_during_edit_does_not_switch_submenus(self):
        p = self.panel()
        p.begin_edit(0)
        p.host._panel_at = lambda *_args: self.fail("editing should suspend hover navigation")
        p.host._track(1000, 2000)
        self.assertIsNotNone(p.editor)

    def test_rejected_or_stale_commit_keeps_the_editor_and_text(self):
        p = self.panel(lambda: ("Work mouse", lambda _text: False))
        p.begin_edit(0)
        entry = p.editor
        entry.text = "Keep this text"
        self.assertEqual(p.commit_edit(), "break")
        self.assertIs(p.editor, entry)
        self.assertFalse(entry.destroyed)
        self.assertEqual(entry.text, "Keep this text")

    def test_unavailable_factory_does_not_open_editor(self):
        p = self.panel(lambda: None)
        p.begin_edit(0)
        self.assertIsNone(p.editor)

    def test_typing_space_arrows_and_escape_cannot_activate_host_actions(self):
        p = self.panel()
        p.begin_edit(0)
        p.host._activate = lambda *args: self.fail("host activated while editing")
        p.host.close = lambda: self.fail("host closed while editing")
        for key in ("space", "Right", "Left", "Down", "Up", "Return", "Escape"):
            p.host._key(types.SimpleNamespace(keysym=key))
        self.assertIsNotNone(p.editor)

    def test_keyboard_f2_and_enter_start_the_editor(self):
        for key in ("F2", "Return"):
            p = self.panel()
            p.hover, p.part = 0, "keyboard"
            p.host._key(types.SimpleNamespace(keysym=key))
            self.assertIsNotNone(p.editor)

    def test_hover_hint_is_cancelled_before_editing_and_on_leave(self):
        p = self.panel()
        cancelled = []
        p.host._root.after_cancel = cancelled.append
        p.set_hover(0, "name")
        self.assertEqual(p._hint_timer, "hint")
        p.set_hover(None, None)
        self.assertEqual(cancelled, ["hint"])
        p.set_hover(0, "name")
        p.begin_edit(0)
        self.assertIsNone(p._hint_timer)
        self.assertEqual(cancelled, ["hint", "hint"])

    def test_width_fits_work_area_and_plain_short_submenus_are_compact(self):
        p = self.panel()
        p.fit((0, 0, 210, 600))
        self.assertLessEqual(p.w, 210)
        rows = flyout.build_rows(Menu(Item("Hide", lambda: None)))
        self.assertEqual(flyout.layout(rows, FakeStyle(), measure, lambda _g: 10)[0], 180)

    def test_opaque_editor_surface_has_readable_contrast_in_both_themes(self):
        for light in (False, True):
            colours = flyout.colours(light)
            def brightness(hex_colour):
                rgb = [int(hex_colour[i:i + 2], 16) for i in (1, 3, 5)]
                return sum(rgb) / 3
            self.assertGreater(abs(brightness(colours["text"]) -
                                   brightness(colours["surface"])), 100)
            self.assertNotEqual(colours["surface"], colours["hover"])

    def test_name_highlight_has_equal_padding_and_stays_above_detail_at_each_dpi(self):
        for scale in (1, 1.25, 1.75, 2):
            p = self.panel()
            p.style = FakeStyle(scale)
            p.w, p.h = flyout.layout(p.rows, p.style, measure, lambda _g: 10)
            row, px = p.rows[0], p.style.px
            left, top, right, bottom = p.name_box(row)
            titles = [(text, y) for (text, detail), y in
                      zip(row.lines, flyout.header_line_tops(row, p.style)) if not detail]
            text_left = px(flyout.ITEM_MARGIN_X) + px(flyout.ITEM_PAD_L) + px(flyout.PLAIN_COLUMN)
            text_right = text_left + max(p.style.title_font.measure(t) for t, _y in titles)
            self.assertEqual(text_left - left, px(4))
            self.assertEqual(top, titles[0][1] - px(4))
            self.assertEqual(right - text_right, px(4))
            self.assertEqual(bottom - titles[-1][1] - p.style.title_line, px(4))
            detail_top = next(y for (_t, d), y in
                              zip(row.lines, flyout.header_line_tops(row, p.style)) if d)
            self.assertLess(bottom, detail_top)

    def test_windows_hint_uses_system_tooltip_above_name_without_tk_popup(self):
        from unittest import mock
        p = self.panel()
        p.hover, p.part = 0, "name"
        p._toplevel = lambda *_args: self.fail("Windows tooltip must be native")
        with mock.patch.object(flyout.sys, "platform", "win32"), \
                mock.patch("halo.native_tooltip.NativeTooltip") as native:
            p._show_hint(0)
            native.assert_called_once_with(123, "Double-click to rename", False)
            x0, y0, x1, y1 = p.name_box(p.rows[0])
            native.return_value.show_above.assert_called_once_with(
                (p.x + x0, p.y + y0, p.x + x1, p.y + y1), p.work, p.style.px(6), opacity=0.0)
            p._hide_hint()
            native.return_value.destroy.assert_called_once_with()

    def test_native_tooltip_failure_omits_hint_instead_of_emulating_windows(self):
        from unittest import mock
        p = self.panel()
        p.hover, p.part = 0, "name"
        p._toplevel = lambda *_args: self.fail("no custom Windows fallback")
        with mock.patch.object(flyout.sys, "platform", "win32"), \
                mock.patch("halo.native_tooltip.NativeTooltip", side_effect=OSError("unavailable")), \
                mock.patch.object(flyout, "log"):
            p._show_hint(0)
        self.assertIsNone(p._hint)

    def test_hint_fades_in_and_pending_frame_is_cancelled_on_leave(self):
        from unittest import mock
        p = self.panel()
        p._hint = mock.Mock()
        p._hint_fade_start = 100.0
        with mock.patch.object(flyout.time, "perf_counter", return_value=100.06):
            p._fade_hint()
        self.assertAlmostEqual(p._hint.set_opacity.call_args.args[0], .5)
        self.assertEqual(p._hint_fade_timer, "hint")
        hint = p._hint
        cancelled = []
        p.host._root.after_cancel = cancelled.append
        p._hide_hint()
        self.assertEqual(cancelled, ["hint"])
        self.assertIsNone(p._hint_fade_timer)
        hint.destroy.assert_called_once_with()
        p._fade_hint()  # a callback already dequeued before cancellation stays harmless
        self.assertIsNone(p._hint_fade_timer)


class TkInlineRenameSmokeTests(unittest.TestCase):
    def test_real_withdrawn_tk_field_creates_inset_editor_and_disposes_it(self):
        import subprocess
        # Tk/Font objects can form cycles. Keep their finalizers and interpreter
        # on one dedicated main thread, never a later unrelated worker's GC.
        script = """
import sys
import unittest
sys.path.insert(0, 'tests')
from test_flyout import TkInlineRenameSmokeTests
try:
    TkInlineRenameSmokeTests()._check_real_widgets()
except unittest.SkipTest as exc:
    print(str(exc))
    sys.exit(77)
"""
        result = subprocess.run([sys.executable, "-c", script],
                                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        if result.returncode == 77 and sys.platform != "win32":
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _check_real_widgets(self):
        # Native widget API smoke test: Canvas.lift is a tag operation, unlike
        # Entry.lift. Test real Tk so a permissive fake cannot hide that error.
        try:
            import tkinter as tk
            from tkinter import font as tk_font
        except ImportError:
            if flyout.sys.platform == "win32":
                raise
            self.skipTest("Tk is not installed")
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            if flyout.sys.platform == "win32":
                raise
            self.skipTest(f"Tk display is unavailable: {exc}")
        root.withdraw()
        try:
            for scale in (1, 1.25, 1.75, 2):
                with self.subTest(scale=scale):
                    host = flyout.FlyoutHost()
                    host._root, host._tk = root, tk
                    style = flyout._Style(tk_font, root, set(tk_font.families(root)), scale, False)
                    calls = []
                    menu = Menu(flyout.HeaderItem(lambda: "Mouse", lambda: "50%",
                        begin_rename=lambda: ("Mouse", lambda name: calls.append(name) or True)),
                        Item("Settings", lambda: None))
                    panel = flyout._Panel(host, menu, style, None, None)
                    host.panels = [panel]
                    panel.work = (0, 0, 1000, 1000)
                    panel.win = tk.Toplevel(root)
                    panel.win.withdraw()  # no visible app window or OS input
                    panel.canvas = tk.Canvas(panel.win)
                    panel.canvas.pack()
                    try:
                        panel.begin_edit(0)
                        root.update_idletasks()
                        entry, field = panel.editor, panel._edit_frame
                        self.assertIsInstance(entry, tk.Entry)
                        self.assertIsInstance(field, tk.Canvas)
                        self.assertEqual(entry.master, field)
                        self.assertEqual(entry.cget("highlightthickness"), 0)
                        self.assertEqual(entry.winfo_x(), style.px(flyout.NAME_PAD))
                        self.assertEqual(entry.winfo_y(), style.px(flyout.NAME_PAD))
                        self.assertGreater(entry.winfo_width(), 1)
                        self.assertEqual(entry.get(), "Mouse")
                        panel.cancel_edit()
                        self.assertEqual(entry.winfo_exists(), 0)
                        self.assertEqual(field.winfo_exists(), 0)
                        self.assertEqual(calls, [])
                    finally:
                        panel.destroy()
        finally:
            root.destroy()


class DeviceSubmenuWidthTests(unittest.TestCase):
    def test_both_real_device_submenus_fit_all_locales_and_dpi_without_root_minimum(self):
        from test_hide_rename import dev, hb, make_app
        for language in hb.i18n.LANGUAGES:
            app = make_app({"language": language})
            status = dev()
            owner = types.SimpleNamespace(status=status)
            app.icons[status.key] = owner
            menu = app.build_tray_menu(owner)
            device = next(item.submenu for item in menu.items
                          if item.text == app.tr("menu.this_device"))
            percentages = next(item.submenu for item in device.items if item.submenu is not None)
            for scale in (1, 1.25, 1.75, 2):
                style = FakeStyle(scale)
                style.font = types.SimpleNamespace(measure=lambda text: round(7 * len(text) * scale))
                style.chevron_font = style.check_font = types.SimpleNamespace(measure=lambda _glyph: round(10 * scale))
                for child_menu in (device, percentages):
                    with self.subTest(language=language, scale=scale, percentages=child_menu is percentages):
                        panel = flyout._Panel(None, child_menu, style, object(), 0)
                        natural = []
                        for row in panel.rows:
                            width = style.font.measure(row.text)
                            if row.submenu is not None:
                                width += style.px(flyout.CHEVRON_GAP) + style.chevron_font.measure("")
                            natural.append(width)
                        column = flyout.check_column(panel.rows)
                        padding = (style.px(flyout.ITEM_MARGIN_X) * 2 + style.px(flyout.ITEM_PAD_L)
                                   + style.px(column) + style.px(flyout.ITEM_PAD_R))
                        self.assertEqual(panel.w, max(style.px(96), padding + max(natural)))
                        panel.fit((0, 0, 2000, 2000))
                        self.assertEqual(panel.w, max(style.px(96), padding + max(natural)))
                        self.assertEqual(column, flyout.CHECK_COLUMN if child_menu is percentages else flyout.PLAIN_COLUMN)
                        if child_menu is percentages:
                            self.assertGreaterEqual(style.px(column), style.check_font.measure(""))
                root_rows = flyout.build_rows(Menu(Item("Exit", lambda: None)))
                width, _height = flyout.layout(root_rows, style, style.font.measure, lambda _g: 10)
                self.assertEqual(width, style.px(180))


if __name__ == "__main__":
    unittest.main()
