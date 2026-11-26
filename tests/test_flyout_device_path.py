"""Real app menu and panel interaction, with window creation and workers faked."""
import types
from unittest import mock

from halo import flyout
from test_flyout import FakeStyle, measure
from test_hide_rename import HideRenameTestCase, dev, make_app


class DeviceFlyoutPathTests(HideRenameTestCase):
    def test_click_through_device_and_alert_submenus_saves_and_closes_all_panels(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        host = flyout.FlyoutHost()
        host._root = types.SimpleNamespace(after=lambda *args: None, after_cancel=lambda *args: None)
        style = FakeStyle()
        style.font = types.SimpleNamespace(measure=measure)
        style.chevron_font = style.check_font = types.SimpleNamespace(measure=lambda _glyph: 10)
        closed = []

        class Panel(flyout._Panel):
            def open(self, x, y):
                self.x, self.y, self.win = x, y, object()

            def update_highlight(self):
                pass

            def destroy(self):
                closed.append(self)
                self.win = None

        def click_text(text):
            panel = host.panels[-1]
            row = next(row for row in panel.rows if row.text == text)
            host._click(panel.x + style.px(12), panel.y + row.y + row.h // 2)

        with mock.patch.object(flyout, "_Panel", Panel):
            root = Panel(host, app.build_tray_menu(owner), style, None, None)
            root.work = (0, 0, 3000, 2000)
            root.open(100, 100)
            host.panels = [root]
            click_text(app.tr("menu.this_device"))
            self.assertEqual(len(host.panels), 2)
            click_text(app.tr("menu.low_battery"))
            self.assertEqual(len(host.panels), 3)
            click_text(app.tr("state.level", level=30))
        self.assertEqual(app.device_low(owner.key), 30)
        self.assertEqual(self.saved[-1]["lows"][owner.key], 30)
        self.assertEqual(host.panels, [])
        self.assertEqual(len(closed), 3)
