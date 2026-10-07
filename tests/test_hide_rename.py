"""Tests for "Hide this device" and "Rename..." in halo_battery.pyw. No tray, no hardware.

The tests load the app module, replace the tray icon class with a small fake one,
and call the real App methods.

Run from the repository root:

    python -m unittest discover -s tests
"""
import importlib.util
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# The app writes its log and settings to %APPDATA%\HaloBattery when it loads. Point it
# at a temporary folder, so the tests never touch the real log or settings.
_real_appdata = os.environ.get("APPDATA")
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="halo_battery_test_")
try:
    spec = importlib.util.spec_from_file_location("halo_battery", os.path.join(ROOT, "halo_battery.pyw"))
    hb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hb)
finally:
    if _real_appdata is None:
        os.environ.pop("APPDATA", None)
    else:
        os.environ["APPDATA"] = _real_appdata

from providers.base import DeviceStatus  # noqa: E402

# the tests replace threading.Thread; the race tests need real threads
_RealThread = threading.Thread


class FakeIcon:
    """Stands in for DeviceIcon: records what the app does with it."""

    def __init__(self, app, key):
        self.app, self.key = app, key
        self.status = None
        self.stopped = False
        self.titles = []
        self.icon = types.SimpleNamespace(notify=lambda text, title: app.notes.append(text),
                                          update_menu=lambda: None)

    def update(self, st):
        self.status = st
        self.titles.append(hb.describe(st, self.app.display_name(st)))

    def stop(self):
        self.stopped = True


class FakeTrayIcon:
    """Stands in for pystray.Icon (the "no devices" icon)."""

    def __init__(self, *args, **kw):
        self.stopped = False
        self.visible = False
        self.shows = []           # every change of visible, in order

    def __setattr__(self, name, value):
        if name == "visible" and "shows" in self.__dict__:
            self.shows.append(value)
        super().__setattr__(name, value)

    def run(self, setup=None):
        # pystray calls setup once the icon's window exists
        if setup is not None:
            setup(self)

    def stop(self):
        self.stopped = True

    def update_menu(self):
        pass


def make_app(cfg=None):
    """An App with only the parts that hide / rename / apply use."""
    app = hb.App.__new__(hb.App)
    app.cfg = dict(hb.DEFAULTS, **(cfg or {}))
    app.lock = threading.RLock()
    app.icons, app.missing, app.alerted, app.full_state = {}, {}, {}, {}
    app.low_sound_at = {}
    app.placeholder = None
    app.wake = threading.Event()
    app.light_taskbar = False
    app.update = None
    app.notes = []
    app.key_provider = {}
    app.held = {}
    app.history = hb.history.History()      # in memory: no path, never written
    return app


def dev(key="logitech:C15E09CD", name="G502 LIGHTSPEED", level=76):
    return DeviceStatus(key, name, level, False, True, "logitech", kind="mouse")


class HideRenameTestCase(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(hb, "DeviceIcon", FakeIcon),
            mock.patch.object(hb.pystray, "Icon", FakeTrayIcon),
            mock.patch.object(hb.time, "sleep", lambda s: None),
            mock.patch.object(hb, "save_config", lambda cfg: self.saved.append(dict(cfg))),
            # never depend on what is full screen on the machine running the tests
            mock.patch.object(hb, "fullscreen_app_running", lambda: False),
            # stop() runs in a thread in hide(); run it at once so the test can check it
            mock.patch.object(hb.threading, "Thread",
                              lambda target, args=(), daemon=None: types.SimpleNamespace(
                                  start=lambda: target(*args))),
        ]
        self.saved = []
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class HideTests(HideRenameTestCase):
    def test_hide_removes_icon_and_is_saved(self):
        app = make_app()
        app.apply([dev()])
        ic = app.icons["logitech:C15E09CD"]
        app.hide(ic)
        self.assertTrue(ic.stopped)
        self.assertNotIn("logitech:C15E09CD", app.icons)
        self.assertEqual(self.saved[-1]["hidden"], {"logitech:C15E09CD": "G502 LIGHTSPEED"})

    def test_hidden_device_gets_no_icon_and_no_alert(self):
        app = make_app({"hidden": {"logitech:C15E09CD": "G502"}, "low": 20})
        app.apply([dev(level=5), dev("steelseries:22a1", "Arctis Nova 7", 70)])
        self.assertEqual(list(app.icons), ["steelseries:22a1"])
        self.assertEqual(app.notes, [], "no low battery alert for a hidden device")

    def test_all_hidden_shows_the_no_devices_icon(self):
        app = make_app()
        app.apply([dev()])
        app.hide(app.icons["logitech:C15E09CD"])
        app.apply([dev()])
        self.assertIsInstance(app.placeholder, FakeTrayIcon)
        self.assertEqual(app.build_menu(None).items[0].text, "No devices shown (1 hidden)")

    def test_unhide_brings_it_back(self):
        app = make_app({"hidden": {"logitech:C15E09CD": "G502"}})
        app.unhide("logitech:C15E09CD")
        app.apply([dev()])
        self.assertIn("logitech:C15E09CD", app.icons)
        self.assertEqual(self.saved[-1]["hidden"], {})

    def test_damaged_settings_value_is_ignored(self):
        for bad in (None, 5, "text", ["x"]):
            with self.subTest(value=bad):
                app = make_app({"hidden": bad, "names": bad})
                app.apply([dev()])
                self.assertIn("logitech:C15E09CD", app.icons)
                self.assertEqual(app.display_name(dev()), "G502 LIGHTSPEED")


class ProbeLock:
    """An RLock that sets `waiting` when a thread has to wait for it."""

    def __init__(self):
        self._lock = threading.RLock()
        self.waiting = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self._lock.acquire(blocking=False):
            return True
        self.waiting.set()
        return self._lock.acquire(blocking, timeout)

    def release(self):
        self._lock.release()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


class HookDict(dict):
    """A dict that calls `hook` once, the first time `method` is used."""

    def __init__(self, items, method, hook):
        super().__init__(items)
        self.method, self.hook = method, hook

    def _fire(self, name):
        if self.hook is not None and name == self.method:
            hook, self.hook = self.hook, None
            hook()

    def get(self, *args):
        value = super().get(*args)
        self._fire("get")                     # after the read, like a thread switch there
        return value

    def pop(self, *args):
        self._fire("pop")
        return super().pop(*args)

    def __setitem__(self, key, value):
        self._fire("__setitem__")
        super().__setitem__(key, value)


class HideDuringApplyTests(HideRenameTestCase):
    """"Hide this device" runs in a menu thread, apply() in the poll thread. At a set
    point inside apply(), the hook starts hide() in a real second thread and goes on
    when hide() has finished or waits for the app's lock. No timing is involved."""

    KEY = "logitech:C15E09CD"

    def setUp(self):
        super().setUp()
        self.threads, self.errors = [], []
        self.app = make_app()
        self.app.lock = ProbeLock()
        self.app.apply([dev()])
        self.ic = self.app.icons[self.KEY]

    def hide_in_menu_thread(self):
        settled = self.app.lock.waiting

        def run():
            try:
                self.app.hide(self.ic)
            except Exception as e:
                self.errors.append(e)
            finally:
                settled.set()
        t = _RealThread(target=run, daemon=True)
        self.threads.append(t)
        t.start()
        self.assertTrue(settled.wait(10), "hide() neither finished nor waited for the lock")

    def finish(self):
        for t in self.threads:
            t.join(10)
            self.assertFalse(t.is_alive())
        self.assertEqual(self.errors, [])

    def test_hide_while_apply_removes_a_missing_device(self):
        app = self.app
        app.apply([])                         # first miss: the icon stays
        self.assertEqual(app.missing, {self.KEY: 1})
        # second miss: hide() runs after apply() has listed the icons
        app.missing = HookDict(app.missing, "__setitem__", self.hide_in_menu_thread)
        app.apply([])
        self.finish()
        self.assertNotIn(self.KEY, app.icons)
        self.assertTrue(self.ic.stopped)
        self.assertTrue(app.placeholder.visible, "the \"no devices\" icon is shown")

    def test_hide_while_apply_does_not_make_a_new_icon(self):
        app = self.app
        made = []

        def counting(app_, key):
            made.append(key)
            return FakeIcon(app_, key)
        with mock.patch.object(hb, "DeviceIcon", counting):
            # hide() runs after apply() has checked the "hidden" list
            app.missing = HookDict(app.missing, "pop", self.hide_in_menu_thread)
            app.apply([dev()])
            self.finish()
            app.apply([dev()])
        self.assertEqual(made, [], "no new icon for the device that was just hidden")
        self.assertNotIn(self.KEY, app.icons)
        self.assertTrue(self.ic.stopped)

    def test_hide_while_apply_does_not_update_a_stopped_icon(self):
        # a stopped pystray icon that is shown again can stay in the tray as a copy
        # (the icon is found and updated in one hold of the lock, so hide() waits)
        app, at_update = self.app, []
        update = self.ic.update
        self.ic.update = lambda st: (at_update.append((self.ic.stopped, app.lock._lock._is_owned())),
                                     update(st))
        # hide() runs after apply() has found the device's icon
        app.icons = HookDict(app.icons, "get", self.hide_in_menu_thread)
        app.apply([dev()])
        self.finish()
        self.assertEqual(at_update, [(False, True)], "updated while stopped, or without the lock")
        self.assertTrue(self.ic.stopped)
        self.assertNotIn(self.KEY, app.icons)

    def test_gone_icon_is_stopped_without_the_lock(self):
        # pystray's stop() waits for the icon's thread, and a menu callback in that
        # thread can wait for the lock: apply() must not hold it while it stops an icon
        app, held = self.app, []
        self.ic.stop = lambda: held.append(app.lock._lock._is_owned())
        app.apply([])
        app.apply([])
        self.assertEqual(held, [False])
        self.assertNotIn(self.KEY, app.icons)


class RenameTests(HideRenameTestCase):
    def rename_to(self, app, ic, answer):
        with mock.patch.object(hb, "ask_name", lambda current, **kwargs: answer):
            app._rename(ic)

    def test_rename_changes_tooltip_and_alert(self):
        app = make_app({"low": 20})
        app.apply([dev()])
        ic = app.icons["logitech:C15E09CD"]
        self.rename_to(app, ic, "Work mouse")
        self.assertEqual(ic.titles[-1], "Work mouse: 76%")
        self.assertEqual(self.saved[-1]["names"], {"logitech:C15E09CD": "Work mouse"})
        app.apply([dev(level=5)])
        self.assertEqual(app.notes, ["Work mouse: 5% left. Time to charge."])

    def test_cancel_changes_nothing(self):
        app = make_app()
        app.apply([dev()])
        self.rename_to(app, app.icons["logitech:C15E09CD"], None)
        self.assertEqual(self.saved, [])

    def test_own_name_again_removes_the_rename(self):
        app = make_app({"names": {"logitech:C15E09CD": "Work mouse"}})
        app.apply([dev()])
        self.rename_to(app, app.icons["logitech:C15E09CD"], "G502 LIGHTSPEED")
        self.assertEqual(self.saved[-1]["names"], {})

    def test_reset_name(self):
        app = make_app({"names": {"logitech:C15E09CD": "Work mouse"}})
        app.apply([dev()])
        ic = app.icons["logitech:C15E09CD"]
        app.reset_name(ic)
        self.assertEqual(ic.titles[-1], "G502 LIGHTSPEED: 76%")

    def test_rename_keeps_the_badge(self):
        """The pictogram comes from the device's own name ("headset" words), not the new name."""
        st = DeviceStatus("bt:AA", "WH-1000XM4 headphones", 90, False, True, "bluetooth")
        self.assertEqual(hb.badge_for(st), "headset")   # the icon code gets the unchanged status


class MenuTests(HideRenameTestCase):
    @staticmethod
    def texts(menu):
        return [item.text for item in menu.items if item.visible]

    def test_device_menu_has_rename_and_hide(self):
        app = make_app()
        app.apply([dev()])
        texts = self.texts(app.build_menu(app.icons["logitech:C15E09CD"]))
        self.assertIn("Rename…", texts)
        self.assertIn("Hide this device", texts)
        self.assertNotIn("Reset name", texts)
        self.assertNotIn("Hidden devices", texts)

    def test_hidden_devices_submenu(self):
        app = make_app({"hidden": {"a": "Xbox Controller", "b": "8BitDo"}})
        menu = app.build_menu(None)
        self.assertIn("Hidden devices", self.texts(menu))
        sub = next(i for i in menu.items if i.text == "Hidden devices").submenu
        self.assertEqual([i.text for i in sub.items], ["Show 8BitDo", "Show Xbox Controller"])
        self.assertEqual(menu.items[0].text, "No devices shown (2 hidden)")
        self.assertNotIn("Rename…", self.texts(menu), "the no-devices icon has no device items")

    def test_clicking_show_brings_the_device_back(self):
        app = make_app({"hidden": {"logitech:C15E09CD": "G502"}})
        menu = app.build_menu(None)
        sub = next(i for i in menu.items if i.text == "Hidden devices").submenu
        [show] = list(sub.items)
        show(FakeTrayIcon())                         # what pystray does on a click
        self.assertEqual(app.cfg["hidden"], {})
        app.apply([dev()])
        self.assertIn("logitech:C15E09CD", app.icons)

    def test_clicking_hide_and_rename_in_the_device_menu(self):
        app = make_app()
        app.apply([dev()])
        ic = app.icons["logitech:C15E09CD"]
        items = {i.text: i for i in app.build_menu(ic).items if i.visible}
        with mock.patch.object(hb, "ask_name", lambda current, **kwargs: "Work mouse"):
            items["Rename…"](FakeTrayIcon())
        self.assertEqual(app.cfg["names"], {"logitech:C15E09CD": "Work mouse"})
        items["Hide this device"](FakeTrayIcon())
        self.assertEqual(app.cfg["hidden"], {"logitech:C15E09CD": "Work mouse"})


class MenuLayoutTests(HideRenameTestCase):
    def test_main_menu_is_short(self):
        app = make_app()
        app.apply([dev()])
        texts = [i.text for i in app.build_menu(app.icons["logitech:C15E09CD"]).items
                 if i.visible and i is not hb.Menu.SEPARATOR]
        self.assertEqual(texts, ["G502 LIGHTSPEED: 76%", "Rename…", "Icon", "Low battery alert at",
                                 "Hide this device",
                                 "Refresh now", "Preferences", "Diagnostics…",
                                 f"Exit (v{hb.VERSION})"])

    def test_hidden_devices_sits_with_preferences(self):
        app = make_app({"hidden": {"a": "Xbox Controller"}})
        app.apply([dev()])
        texts = [i.text for i in app.build_menu(app.icons["logitech:C15E09CD"]).items
                 if i.visible and i is not hb.Menu.SEPARATOR]
        self.assertEqual(texts, ["G502 LIGHTSPEED: 76%", "Rename…", "Icon", "Low battery alert at",
                                 "Hide this device",
                                 "Refresh now", "Preferences", "Hidden devices",
                                 "Diagnostics…", f"Exit (v{hb.VERSION})"])

    def test_preferences_holds_the_settings(self):
        app = make_app()
        menu = app.build_menu(None)
        prefs = next(i for i in menu.items if i.text == "Preferences").submenu
        texts = [i.text for i in prefs.items if i is not hb.Menu.SEPARATOR]
        self.assertEqual(texts, ["Language / Langue", "Poll interval", "Low battery alert", "Alert when fully charged",
                                 "Estimated time left", "Quiet while gaming",
                                 "Sound with the low battery alert",
                                 "Windows Bluetooth devices", "PlayStation full mode (Bluetooth)",
                                 "Device types",
                                 "Device pictogram", "Percentage in the icon", "Charging animation",
                                 "Icon colour", "Status file for other apps",
                                 "Start with Windows", "Check for updates"])


class MenuRefreshTests(unittest.TestCase):
    """pystray builds the Windows menu once. The real DeviceIcon must rebuild it when
    the text changes, or the header keeps "No devices found"."""

    def make_icon(self):
        ic = hb.DeviceIcon.__new__(hb.DeviceIcon)
        ic.app = make_app()
        ic.key, ic.status, ic.frames, ic._state, ic._images = "logitech:C15E09CD", None, None, None, {}
        ic.icon = types.SimpleNamespace(title=hb.APP_TITLE, icon=None, visible=True, rebuilt=0)
        ic.icon.update_menu = lambda: setattr(ic.icon, "rebuilt", ic.icon.rebuilt + 1)
        return ic

    def test_menu_is_rebuilt_when_the_level_changes(self):
        ic = self.make_icon()
        ic.update(dev(level=76))
        self.assertEqual(ic.icon.rebuilt, 1)
        ic.update(dev(level=76))               # nothing changed: no rebuild
        self.assertEqual(ic.icon.rebuilt, 1)
        ic.update(dev(level=75))
        self.assertEqual(ic.icon.rebuilt, 2)


class AskNameTests(unittest.TestCase):
    def run_box(self, stdout):
        calls = []

        def fake_run(args, **kw):
            calls.append((args, kw))
            return types.SimpleNamespace(stdout=stdout)
        with mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.object(hb.subprocess, "run", fake_run):
            return hb.ask_name('My "quoted" name; exit'), calls

    def test_name_goes_in_the_environment_not_the_command(self):
        _, [(args, kw)] = self.run_box(b"New\r\n")
        self.assertNotIn("quoted", " ".join(args))
        self.assertEqual(kw["env"]["HALO_BATTERY_NAME"], 'My "quoted" name; exit')

    def test_result(self):
        self.assertEqual(self.run_box("Mäuse\r\n".encode())[0], "Mäuse")
        self.assertIsNone(self.run_box(b"\r\n")[0])             # cancel / empty
        self.assertEqual(len(self.run_box(b"x" * 100)[0]), 60)   # capped


if __name__ == "__main__":
    unittest.main()
