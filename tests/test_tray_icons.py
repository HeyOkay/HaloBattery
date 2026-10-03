"""Tests for the tray icon life cycle in halo_battery.pyw (#38, #95). No tray, no hardware.

pystray makes an icon's window in the icon's own thread. Until that window exists,
stop() is ignored and a show is lost. The "no devices" icon used to be stopped and
made again each time a device came or went, which could leave a copy in the tray.
Now it is made once and only shown or hidden, and only after its window exists.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import FakeTrayIcon, HideRenameTestCase, dev, hb, make_app  # noqa: E402
from providers.base import DeviceStatus  # noqa: E402


class PlaceholderTests(HideRenameTestCase):
    def setUp(self):
        super().setUp()
        self.made = []
        real_init = FakeTrayIcon.__init__

        def counting_init(icon, *a, **k):
            real_init(icon, *a, **k)
            self.made.append(icon)
        p = mock.patch.object(FakeTrayIcon, "__init__", counting_init)
        p.start()
        self.addCleanup(p.stop)

    def test_made_once_then_only_shown_or_hidden(self):
        app = make_app()
        app.apply([])                         # no device: the "no devices" icon shows
        app.apply([dev()])                    # a device: it hides
        app.apply([dev()])
        app.icons.clear()                     # the device goes away
        app.apply([])
        app.apply([dev()])
        self.assertEqual(len(self.made), 1, "one icon for the whole session")
        ph = self.made[0]
        self.assertEqual(ph.shows, [True, False, True, False])
        self.assertFalse(ph.stopped, "never stopped while the app runs")
        self.assertFalse(ph.visible)

    def test_no_icon_made_while_devices_are_shown(self):
        app = make_app()
        app.apply([dev()])
        self.assertEqual(self.made, [])
        self.assertIsNone(app.placeholder)

    def test_all_devices_hidden_shows_it(self):
        app = make_app({"hidden": {"logitech:C15E09CD": "G502"}})
        app.apply([dev()])
        self.assertTrue(app.placeholder.visible)


class SlowStartTests(HideRenameTestCase):
    """The icon's window is made later than the app asks to show or hide it."""

    def test_waits_for_the_window_before_showing(self):
        started = {}

        class LateIcon(FakeTrayIcon):
            def run(icon, setup=None):
                started["setup"] = setup          # the window is not there yet

        app = make_app()
        with mock.patch.object(hb.pystray, "Icon", LateIcon), \
                mock.patch.object(hb, "ICON_READY_TIMEOUT", 0.01):
            app.apply([])
            ph = app.placeholder
            self.assertEqual(ph.shows, [], "no show before the window exists")
            started["setup"](ph)                  # now the window exists
            app.apply([])
            self.assertEqual(ph.shows, [True])

    def test_quick_device_after_start_leaves_no_copy(self):
        """#95: a device comes back right after the "no devices" icon was made. The old
        code stopped an icon that was not running yet (ignored by pystray) and forgot it."""
        app = make_app()
        with mock.patch.object(hb.threading, "Thread", threading.Thread):
            class SlowIcon(FakeTrayIcon):
                def run(icon, setup=None):
                    time.sleep(0.2)                   # a busy PC right after a wake
                    setup(icon)
            with mock.patch.object(hb.pystray, "Icon", SlowIcon):
                app.show_placeholder(True)            # waits for the window, then shows
                app.show_placeholder(False)           # the device is back at once
        ph = app.placeholder
        self.assertEqual(ph.shows, [True, False])
        self.assertFalse(ph.visible, "nothing left in the tray")


class DeviceIconStartTests(unittest.TestCase):
    """The real DeviceIcon shows itself only after its window exists (#61, item 1)."""

    def test_show_waits_for_the_window(self):
        order = []

        class SlowIcon:
            def __init__(icon, *a, **k):
                icon.title, icon.icon = "", None
                icon._visible = False

            @property
            def visible(icon):
                return icon._visible

            @visible.setter
            def visible(icon, value):
                order.append(("visible", value))
                icon._visible = value

            def run(icon, setup=None):
                time.sleep(0.2)
                order.append(("window", True))
                setup(icon)

            def update_menu(icon):
                pass

        app = make_app()
        with mock.patch.object(hb.pystray, "Icon", SlowIcon):
            ic = hb.DeviceIcon(app, "logitech:C15E09CD")
            ic.update(dev())
        self.assertEqual(order, [("window", True), ("visible", True)])


class RenderOnceTests(unittest.TestCase):
    """A new state draws only the colour in use; the other colour is drawn once, when
    the bar colour flips to it (MyDockFinder or a theme change)."""

    def setUp(self):
        self.calls = []
        self.texts = []

        def fake_render(*a, **k):
            self.calls.append(a[4] if len(a) > 4 else k.get("light_taskbar"))
            self.texts.append(k.get("text", ""))
            return object()

        p = mock.patch.object(hb.icons, "render", fake_render)
        p.start()
        self.addCleanup(p.stop)
        self.app = make_app()
        self.app.anim_tick = 0
        ic = hb.DeviceIcon.__new__(hb.DeviceIcon)
        ic.app = self.app
        ic.key, ic.status, ic.frames, ic._state, ic._images = "logitech:C15E09CD", None, None, None, {}
        ic.icon = types.SimpleNamespace(title="", icon=None, visible=True, update_menu=lambda: None)
        self.ic = ic

    @staticmethod
    def charging(level):
        return DeviceStatus("logitech:C15E09CD", "G502 LIGHTSPEED", level, True, True, "logitech",
                            kind="mouse")

    def test_a_charging_level_change_draws_one_colour(self):
        self.ic.update(self.charging(50))
        self.assertEqual(self.calls, [False] * hb.icons.BREATH_FRAMES)
        self.calls.clear()
        self.ic.update(self.charging(51))
        self.assertEqual(self.calls, [False] * hb.icons.BREATH_FRAMES)

    def test_a_still_icon_draws_one_colour(self):
        self.ic.update(dev())
        self.assertEqual(self.calls, [False])

    def test_the_other_colour_is_drawn_once_on_the_first_flip(self):
        self.ic.update(self.charging(50))
        dark = self.ic.frames
        self.calls.clear()
        self.app.light_taskbar = True
        self.ic.update(self.charging(50))
        self.assertEqual(self.calls, [True] * hb.icons.BREATH_FRAMES)
        light = self.ic.frames
        self.assertIsNot(light, dark)
        self.calls.clear()
        for flip in (False, True, False):     # MyDockFinder flipping back and forth
            self.app.light_taskbar = flip
            self.ic.update(self.charging(50))
            self.assertIs(self.ic.frames, light if flip else dark)
        self.assertEqual(self.calls, [])

    def test_an_unchanged_state_draws_nothing(self):
        self.ic.update(self.charging(50))
        self.calls.clear()
        self.ic._state = None                 # even past the state check in _update
        self.ic.update(self.charging(50))
        self.ic.update(self.charging(50))
        self.assertEqual(self.calls, [])

    def test_the_percentage_in_the_icon_is_drawn_in_both_colours(self):
        self.app.cfg["percent_in_icon"] = True
        self.ic.update(dev())
        self.app.light_taskbar = True
        self.ic.update(dev())
        self.assertEqual(self.calls, [False, True])
        self.assertEqual(self.texts, [str(dev().level)] * 2)
        self.app.light_taskbar = False
        self.ic.update(dev())
        self.assertEqual(len(self.calls), 2)   # the other colour was kept

    def test_the_cache_keeps_one_state(self):
        self.ic.update(self.charging(50))
        self.app.light_taskbar = True
        self.ic.update(self.charging(50))
        self.ic.update(self.charging(51))
        self.assertEqual(len(self.ic._images), 1)
        self.app.light_taskbar = False
        self.ic.update(self.charging(51))
        self.assertEqual(len(self.ic._images), 2)


if __name__ == "__main__":
    unittest.main()
