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

        class FakePanel:
            def __init__(self, host, menu, style, parent, row):
                self.rows, self.w, self.h, self.hwnd = [1], 250, 300, 1
                self.win = types.SimpleNamespace(focus_force=lambda: None)

            def open(self, x, y):
                seen["y"] = y

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
        self.assertEqual(flyout.layout(rows, FakeStyle(), measure, lambda g: 10)[0], 230)
        rows = flyout.build_rows(Menu(Item("Exit", lambda: None)))
        self.assertEqual(flyout.layout(rows, FakeStyle(1.25), measure, lambda g: 10)[0], 288)

    def test_a_long_text_widens_the_menu(self):
        rows = flyout.build_rows(Menu(Item("x" * 40, lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(w, 4 + 8 + 24 + 280 + 10 + 4)



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

    def test_a_long_header_does_not_widen_the_menu(self):
        long = "85%, charging (last known value, device asleep), about 12 h of use left"
        rows = flyout.build_rows(Menu(self.header("Razer BlackShark V2 Pro 2023 edition", long),
                                      Item("Exit", lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(w, 230)
        room = flyout.header_text_width(w, FakeStyle())
        self.assertEqual(room, 230 - 4 - 4 - 8 - 24 - 10)     # from the items' text column
        for text, _detail in rows[0].lines:
            self.assertLessEqual(measure(text), room)
        titles = [t for t, d in rows[0].lines if not d]
        details = [t for t, d in rows[0].lines if d]
        self.assertEqual(" ".join(titles), "Razer BlackShark V2 Pro 2023 edition")
        self.assertEqual(" ".join(details), long)

    def test_the_state_goes_on_the_line_below_the_name(self):
        rows = flyout.build_rows(Menu(self.header("Viper", "85%"), Item("Exit", lambda: None)))
        flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(rows[0].lines, [("Viper", False), ("85%", True)])
        # 8 above, two lines with a 2 px gap, 8 below, then its separator (4 + 1 + 4)
        self.assertEqual(rows[0].h, 8 + 17 + 2 + 17 + 8 + 9)
        self.assertEqual(flyout.header_line_tops(rows[0], FakeStyle()), [4 + 8, 4 + 8 + 17 + 2])
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
        rows = flyout.build_rows(Menu(self.header("Mouse", "x " * 60), Item("y" * 40, lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        self.assertEqual(w, 4 + 8 + 24 + 280 + 10 + 4)

    def test_the_pencil_leaves_room_next_to_the_title(self):
        long_name = "Razer BlackShark V2 Pro 2023 edition"
        rows = flyout.build_rows(Menu(flyout.HeaderItem(lambda: long_name, lambda: "85%",
                                                        edit=lambda icon: None),
                                      Item("Exit", lambda: None)))
        w, _h = flyout.layout(rows, FakeStyle(), measure, lambda g: 10)
        room = flyout.header_text_width(w, FakeStyle()) - 8 - 28
        titles = [t for t, d in rows[0].lines if not d]
        self.assertTrue(all(measure(t) <= room for t in titles))
        x0, y0, x1, y1 = flyout.edit_box(rows[0], w, FakeStyle())
        self.assertEqual((x1 - x0, y1 - y0), (28, 26))
        self.assertLessEqual(x1, w - 4)                      # inside the panel
        mid = flyout.header_line_tops(rows[0], FakeStyle())[0] + 17 // 2
        self.assertEqual((y0 + y1) // 2, mid)                # level with the title

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
            p.overlay.calls.append(("raise", hwnd))
        p.host = types.SimpleNamespace(_w=types.SimpleNamespace(to_top=to_top))
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
        p.host = types.SimpleNamespace(_w=None)     # not Windows
        p.update_highlight()
        self.assertEqual(p.overlay.calls[-1], ("-alpha", 0.1))



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

if __name__ == "__main__":
    unittest.main()
