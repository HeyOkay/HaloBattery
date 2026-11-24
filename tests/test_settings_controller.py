"""Settings use cached statuses and the exact tray renderer, without hardware."""
import base64
import io
import os
import sys
import threading
import types
import unittest
from dataclasses import replace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import HideRenameTestCase, dev, hb, make_app
from halo.settings_model import SettingsController
REAL_DEVICE_ICON = hb.DeviceIcon


class SettingsControllerTests(HideRenameTestCase):
    def setup_controller(self, cfg=None, status=None):
        app = make_app(cfg)
        app.bt_wake, app.update_wake, app.theme_evt = (threading.Event() for _ in range(3))
        app.bt_cache, app.hid_results = [], []
        app.anim_tick = 3
        app.apply([status or dev()])
        self.controller = SettingsController(app)
        return app, self.controller

    def test_whats_new_reuses_the_existing_application_window(self):
        app, controller = self.setup_controller()
        with mock.patch.object(app, "show_whats_new") as show:
            controller.action("show_whats_new")
        show.assert_called_once_with()

    def test_about_links_open_the_website_discord_and_project(self):
        app, controller = self.setup_controller()
        with mock.patch.object(app, "open_url") as open_url:
            for name in ("open_website", "open_discord", "open_project", "open_issue"):
                controller.action(name)
        project = "https://github.com/" + controller.context["repo"]
        self.assertEqual([c.args for c in open_url.call_args_list],
                         [("https://halobattery.app",), ("https://discord.gg/K796KR9xtk",),
                          (project,), (project + "/issues/new/choose",)])

    def test_snapshot_is_detached_and_hidden_devices_remain_editable(self):
        app, controller = self.setup_controller()
        key = dev().key
        with mock.patch.object(hb, "autostart_enabled", return_value=False):
            controller.context["autostart_enabled"] = hb.autostart_enabled
            before = controller.snapshot()
            before["cfg"]["interval"] = 999
            self.assertEqual(app.cfg["interval"], 60)
            controller.set_device(key, "hidden", True)
            hidden = controller.snapshot()["devices"][0]
        self.assertTrue(hidden["hidden"])
        controller.set_device(key, "name", "Work mouse")
        self.assertEqual(app.cfg["hidden"][key], "Work mouse")
        controller.set_device(key, "reset_name")
        self.assertNotIn(key, app.cfg["names"])
        controller.set_device(key, "hidden", False)
        self.assertNotIn(key, app.cfg["hidden"])

    def test_validate_globals_and_preserve_side_effects(self):
        app, controller = self.setup_controller()
        for key, value in (("low", True), ("low", -1), ("interval", 0), ("bluetooth", "yes"),
                           ("language", "invalid"), ("icon_theme", "purple"), ("disabled_providers", [])):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                controller.set_global(key, value)
        app.bt_cache = [dev()]
        controller.set_global("bluetooth", False)
        self.assertEqual(app.bt_cache, [])
        self.assertTrue(app.bt_wake.is_set())
        controller.set_global("update_check", True)
        self.assertEqual(app.cfg["update_last"], 0)
        self.assertTrue(app.update_wake.is_set())
        app.update = types.SimpleNamespace(version="99", url="https://example.com")
        controller.set_global("update_check", False)
        self.assertIsNone(app.update)
        remove = mock.Mock()
        controller.context["remove_status_file"] = remove
        controller.set_global("status_file", False)
        remove.assert_called_once_with()

    def test_device_threshold_types_and_inheritance(self):
        app, controller = self.setup_controller()
        key = dev().key
        for value in (True, -1, 101, "20", 2.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                controller.set_device(key, "low_override", value)
        for value in (0, 100, None):
            app.alerted[key], app.low_sound_at[key] = True, 123
            controller.set_device(key, "low_override", value)
            self.assertEqual(app.device_low(key), value)
            self.assertNotIn(key, app.alerted)
            self.assertNotIn(key, app.low_sound_at)

    def test_icon_content_is_one_save_and_redraw_and_preserves_percentage_fallback(self):
        app, controller = self.setup_controller()
        for mode, percent, badges in (("percent", True, True), ("ring", False, False), ("pictogram", False, True)):
            with self.subTest(mode=mode), mock.patch.object(controller, "_save") as save, mock.patch.object(controller, "_redraw") as redraw:
                controller.set_icon_content(mode)
                self.assertEqual(app.cfg["percent_in_icon"], percent)
                self.assertEqual(app.cfg["badges"], badges)
                save.assert_called_once()
                redraw.assert_called_once()
        for mode in (None, True, [], "invalid"):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                controller.set_icon_content(mode)

    def test_provider_set_is_idempotent_and_rejects_unknown_names(self):
        app, controller = self.setup_controller()
        with mock.patch.object(app, "toggle_provider", wraps=app.toggle_provider) as toggle:
            controller.set_provider("razer", True)
            toggle.assert_not_called()
            controller.set_provider("razer", False)
            controller.set_provider("razer", False)
            self.assertEqual(toggle.call_count, 1)
        with self.assertRaises(ValueError):
            controller.set_provider("invented", False)

    def test_preview_matches_shared_tray_state_at_native_eighty_pixels(self):
        cases = [dev(level=None), dev(level=0), dev(level=100),
                 replace(dev(level=15), approx="rough", charging=True),
                 replace(dev(level=None), charging=True),
                 replace(dev(), online=False, polling_paused=True)]
        for st in cases:
            with self.subTest(status=st):
                app, controller = self.setup_controller({"percent_in_icon": True, "filled_icons": True}, st)
                state = app.icon_render_state(st, size=80)
                owner = REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON)
                owner._images = {}
                expected = owner._art(state)
                if isinstance(expected, list):
                    expected = expected[app.anim_tick % len(expected)]
                actual = controller.preview(st.key)
                self.assertEqual(actual.size, (80, 80))
                self.assertEqual(actual.tobytes(), expected.tobytes())
        with self.assertRaises(ValueError):
            controller.preview(st.key, size=32)

    def test_preview_uses_controller_overrides_and_explicit_animation_frame(self):
        st = replace(dev(), kind="gamepad", charging=True)
        app, controller = self.setup_controller({"controller_brands": {st.key: "xbox"},
                                                "controller_colors": {st.key: True}, "lows": {st.key: 100}}, st)
        owner=REAL_DEVICE_ICON.__new__(REAL_DEVICE_ICON)
        owner._images={}
        expected=owner._art(app.icon_render_state(st,size=80))
        with mock.patch.object(hb.icons,"charging_frames",side_effect=AssertionError("eager preview cycle")):
            for index in (0,1,25,hb.icons.BREATH_FRAMES-1):
                self.assertEqual(controller.preview(st.key,frame=index).tobytes(),expected[index].tobytes())

    def test_preview_transition_matches_canonical_and_static_layers(self):
        from PIL import Image
        for style in ("thin", "classic"):
            for level, charging, online, paused in ((0, False, True, False), (1, False, True, False),
                    (100, False, True, False), (15, True, True, False),
                    (None, True, True, False), (None, False, True, False),
                    (42, False, False, False), (42, False, False, True)):
                for percent in (False, True):
                    with self.subTest(style=style, level=level, charging=charging, online=online, percent=percent):
                        st = replace(dev(level=level), charging=charging, online=online, polling_paused=paused)
                        app, controller = self.setup_controller({"ring_style": style, "percent_in_icon": percent}, st)
                        image, packet = controller.preview_transition(st.key)
                        self.assertEqual(image.tobytes(), controller.preview(st.key).tobytes())
                        state = app.icon_render_state(st, size=80)
                        lvl, charge, active, low, light, badge, animate, text, pause = state[:9]
                        phase = app.anim_tick / hb.icons.BREATH_FRAMES if animate else 0.
                        pulse = hb.icons.breath_level(phase) if animate else 1.
                        expected = hb.icons.render(lvl, charge, active, low, light, badge, pulse=pulse,
                            text=text, force_active=pause if not animate else False, size=80,
                            brand=state[10], brand_color=state[11], filled=state[12],
                            animation_phase=phase if animate and lvl is None else 0.,
                            ring_style=style, include_arc=False)
                        background = Image.open(io.BytesIO(base64.b64decode(packet["background"])))
                        self.assertEqual(background.tobytes(), expected.tobytes())
                        eligible = active and level is not None
                        self.assertEqual(packet["eligible"], eligible)
                        fraction = (max(level, 2) / 100 if level else 0.) if eligible else 0.
                        self.assertEqual(packet["level"], fraction)
                        fg = (0, 0, 0) if light else (255, 255, 255)
                        color = hb.icons.arc_color(level, charging, low, fg)
                        opacity = hb.icons.NEUTRAL_OPACITY if color == fg else 255
                        self.assertEqual(packet["color"], [*color, int(opacity * pulse)])
                        width = 4. if style == "thin" else 6.5
                        self.assertEqual(packet["width"], width * 80 / 64)
                        self.assertEqual(packet["radius"], (31.5 - width / 2) * 80 / 64)

    def test_retained_phone_uses_its_visible_ring_for_selection_transition(self):
        from providers.base import DeviceStatus
        phone = DeviceStatus("phone_link:fixture", "S24+", 65, False, False, "phone_link",
            kind="phone", charging_known=False, ui_message="state.phone_link_last_reported",
            ui_params={"level": 65})
        app, controller = self.setup_controller(status=phone)
        self.assertFalse(phone.online)
        self.assertTrue(app.icon_render_state(phone, size=80)[2])
        image, packet = controller.preview_transition(phone.key)
        self.assertTrue(packet["eligible"])
        self.assertEqual(packet["level"], .65)
        self.assertEqual(image.tobytes(), controller.preview(phone.key).tobytes())
        # A retained normal offline device has no visible charge arc.
        offline = replace(phone, key="fixture:offline", source="fixture", ui_message="")
        app.apply([offline])
        self.assertFalse(controller.preview_transition(offline.key)[1]["eligible"])

    def test_transition_custom_art_and_background_cache_follow_preview_state(self):
        from PIL import Image
        app, controller = self.setup_controller()
        glyph = mock.Mock()
        glyph.render.return_value = Image.new("RGBA", (80, 80), (50, 60, 70, 100))
        app._custom_assets = {"custom": types.SimpleNamespace(glyph=glyph)}
        state = (*app.icon_render_state(dev(), size=80)[:13], "custom", "classic")
        with mock.patch.object(app, "icon_render_state", return_value=state):
            image, first = controller.preview_transition(dev().key)
            self.assertEqual(image.tobytes(), controller.preview(dev().key).tobytes())
            self.assertEqual(first, controller.preview_transition(dev().key)[1])
            self.assertEqual(glyph.render.call_count, 2)
            expected = hb.icons.render(*state[:6], size=80, brand=state[10],
                brand_color=state[11], filled=state[12], custom_glyph=glyph,
                ring_style="classic", include_arc=False)
            background = Image.open(io.BytesIO(base64.b64decode(first["background"])))
            self.assertEqual(background.tobytes(), expected.tobytes())
        app.icons.clear()
        image, empty = controller.preview_transition()
        self.assertFalse(empty["eligible"])
        self.assertNotEqual(first["background"], empty["background"])
        self.assertEqual(image.tobytes(), controller.preview().tobytes())
        with self.assertRaises(ValueError):
            controller.preview_transition(size=True)

    def test_no_status_preview_and_disappearance_are_safe(self):
        app, controller = self.setup_controller()
        app.icons.clear()
        self.assertEqual(controller.preview().size, (80, 80))
        controller.set_device("gone", "low_override", 20)
        self.assertEqual(app.cfg.get("lows", {}), {})


class CompactTrayTests(HideRenameTestCase):
    def test_real_menu_contains_only_quick_actions_and_no_configuration_submenus(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        app.open_settings = mock.Mock()
        menu = app.build_tray_menu(owner)
        visible = [i for i in menu.items if i.visible]
        self.assertEqual(len(visible), 6)
        # the only submenu holds the device's quick actions, nothing from Settings
        self.assertEqual([i.text for i in visible if i.submenu], [app.tr("menu.this_device")])
        self.assertEqual([i.text for i in visible[1].submenu.items],
                         [app.tr("menu.hide"), app.tr("preferences.low")])
        visible[2](None)
        app.open_settings.assert_called_once_with(owner.key, page="devices")
        app.wake.clear()
        visible[3](None)
        self.assertTrue(app.wake.is_set())

    def test_this_device_hides_the_device(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        key = owner.status.key
        this_device = app.build_tray_menu(owner).items[1].submenu
        this_device.items[0](None)
        self.assertIn(key, app.cfg["hidden"])
        self.assertNotIn(key, app.icons)

    def test_this_device_sets_and_resets_its_own_low_alert(self):
        app = make_app({"low": 20})
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        key = owner.status.key
        lows = app.build_tray_menu(owner).items[1].submenu.items[1].submenu
        texts = [i.text for i in lows.items]
        self.assertEqual(texts[0], app.tr("menu.default_low", level=20))
        self.assertEqual(texts[1], app.tr("choice.off"))
        self.assertTrue(lows.items[0].checked)                 # follows Preferences at first
        lows.items[texts.index(app.tr("state.level", level=30))](None)
        self.assertEqual(app.device_low(key), 30)
        self.assertTrue(lows.items[texts.index(app.tr("state.level", level=30))].checked)
        self.assertFalse(lows.items[0].checked)
        lows.items[1](None)                                     # Off is 0, not the default
        self.assertEqual(app.device_low(key), 0)
        lows.items[0](None)
        self.assertIsNone(app.device_low(key))

    def test_this_device_is_hidden_while_the_icon_has_no_status(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        menu = app.build_tray_menu(owner)
        owner.status = None
        self.assertFalse(menu.items[1].visible)
        menu.items[1].submenu.items[0](None)                    # stale click: nothing happens
        self.assertEqual(app.cfg.get("hidden", {}), {})

    def test_placeholder_settings_restores_the_previous_page(self):
        app = make_app()
        app.open_settings = mock.Mock()
        app.build_tray_menu(None).items[1](None)
        app.open_settings.assert_called_once_with()

    def test_compact_labels_strip_only_trailing_ellipses(self):
        app = make_app()
        for label, expected in (("Settings…", "Settings"), ("Settings...", "Settings"),
                                ("Settings...… ", "Settings"),
                                ("Choose... a device…", "Choose... a device")):
            with self.subTest(label=label), mock.patch.object(app, "tr", return_value=label):
                self.assertEqual(app.build_tray_menu(None).items[1].text, expected)

    def test_compact_labels_have_no_trailing_ellipsis_in_every_language_and_update_state(self):
        for language in hb.i18n.LANGUAGES:
            app = make_app({"language": language})
            app.apply([dev(name="Work... mouse…")])
            device_owner = next(iter(app.icons.values()))
            for owner in (device_owner, None):
                for state in (None, "download", "install", "progress"):
                    with self.subTest(language=language, device=owner is not None, state=state):
                        app.update = None if state is None else types.SimpleNamespace(version="2.0.0")
                        app.install_ok = state == "install"
                        app.update_progress = 42 if state == "progress" else None
                        visible = [item for item in app.build_tray_menu(owner).items if item.visible]
                        device = 1 if owner is not None else 0     # "This device"
                        self.assertEqual(len(visible), (5 if state is None else 6) + device)
                        for item in visible[1:]:
                            self.assertFalse(item.text.endswith(("...", "…")))
                        if state == "progress":
                            self.assertEqual(visible[3 + device].text, hb.update_item_text(
                                "2.0.0", False, 42, language=language))
                        if owner is not None:
                            self.assertIn("Work... mouse…", visible[0].text)

    @unittest.skipUnless(sys.platform == "win32", "Windows tray notifications")
    def test_double_click_opens_once_and_single_click_does_not_refresh(self):
        from pystray._util import win32
        original, callback = mock.Mock(), mock.Mock()
        icon = types.SimpleNamespace(_message_handlers={win32.WM_NOTIFY: original})
        hb.connect_settings_click(icon, callback)
        notify = icon._message_handlers[win32.WM_NOTIFY]
        with mock.patch.object(hb.time, "monotonic", side_effect=[1, 1.1, 2]):
            notify(0, hb.WM_LBUTTONUP)
            callback.assert_not_called()
            notify(0, hb.WM_LBUTTONUP)
            callback.assert_called_once_with()
            notify(0, hb.WM_LBUTTONUP)
        notify(0, hb.WM_RBUTTONUP)
        original.assert_called_once_with(0, hb.WM_RBUTTONUP)
        callback.reset_mock()
        notify(0, hb.WM_LBUTTONDBLCLK)
        notify(0, hb.WM_LBUTTONUP)
        callback.assert_called_once_with()

    def test_settings_dispatch_uses_one_native_companion_without_tk_host(self):
        app = make_app()
        app.settings = object()
        from halo import settings_host
        with mock.patch.object(settings_host, "SettingsHost") as manager:
            app.open_settings("mouse:1", page="devices")
            app.open_settings(page="general")
        manager.assert_called_once_with(
            app.settings, on_failure=app._settings_unavailable, on_ready=app._settings_available)
        self.assertEqual(manager.return_value.open.call_args_list,
                         [mock.call("mouse:1", page="devices"), mock.call(None, page="general")])


class SettingsFailureFallbackTests(HideRenameTestCase):
    def setup_app(self, language="en"):
        app = make_app({"language": language})
        app.stop_evt = threading.Event()
        app.apply([dev()])
        return app

    def preferences(self, menu, app):
        return next(item for item in menu.items if item.text == app.tr("menu.preferences"))

    def test_companion_launch_failures_show_error_and_leave_preferences_usable(self):
        from halo.settings_host import SettingsHost
        errors = (FileNotFoundError("missing companion"), PermissionError("blocked companion"),
                  OSError(193, "incompatible executable"), OSError(216, "incompatible architecture"),
                  OSError(1150, "unsupported Windows version"))
        for error in errors:
            with self.subTest(error=error):
                app = self.setup_app()
                menu = app.build_tray_menu(None)
                manager = SettingsHost(object(), popen=mock.Mock(side_effect=error),
                                       on_failure=app._settings_unavailable, on_ready=app._settings_available)
                # Launch arguments are unrelated to this simulated OS failure.
                with mock.patch.object(manager, "_launch_args", return_value=["settings.exe"]), \
                        mock.patch.object(manager, "_thread"), \
                        mock.patch.object(hb, "show_settings_error") as show:
                    manager.open()
                    with self.assertLogs("halo_battery", level="ERROR"):
                        manager._launch(manager._session)
                    manager._dispatch_notifications()
                show.assert_called_once_with(app.tr("settings.open_failed", preferences=app.tr("menu.preferences")))
                prefs = self.preferences(menu, app)
                self.assertTrue(prefs.visible)
                sound = next(item for item in prefs.submenu.items if item.text == app.tr("preferences.low_sound"))
                before = app.cfg["low_sound"]
                sound(None)
                self.assertEqual(app.cfg["low_sound"], not before)
                self.assertIsNone(manager._session)

    def test_existing_device_and_placeholder_menus_gain_preferences_then_recover(self):
        app = self.setup_app()
        owner = next(iter(app.icons.values()))
        menus = [app.build_tray_menu(owner), app.build_tray_menu(None)]
        with mock.patch.object(hb, "show_settings_error") as show, mock.patch.object(app, "refresh_menus") as refresh:
            for menu in menus:
                self.assertFalse(self.preferences(menu, app).visible)
            app._settings_unavailable("launch")
            refresh.assert_called_once_with()
            show.assert_called_once_with(app.tr("settings.open_failed", preferences=app.tr("menu.preferences")))
            for menu in menus:
                prefs = self.preferences(menu, app)
                self.assertTrue(prefs.visible)
                sound = next(item for item in prefs.submenu.items if item.text == app.tr("preferences.low_sound"))
                before = app.cfg["low_sound"]
                sound(None)
                self.assertEqual(app.cfg["low_sound"], not before)
            # Merely opening Settings again keeps the fallback until visible is acknowledged.
            app.settings = object()
            with mock.patch("halo.settings_host.SettingsHost"):
                app.open_settings()
            self.assertTrue(self.preferences(menus[0], app).visible)
            app._settings_available()
            self.assertEqual(refresh.call_count, 2)
            app._settings_available()
            self.assertEqual(refresh.call_count, 2)
            for menu in menus:
                self.assertFalse(self.preferences(menu, app).visible)

    def test_rapid_retry_does_not_stack_error_dialogs(self):
        app = self.setup_app()
        with mock.patch.object(hb, "show_settings_error", side_effect=lambda text: app._settings_unavailable("connection")) as show:
            app._settings_unavailable("launch")
        show.assert_called_once()
        self.assertFalse(app._settings_error_pending)
        with mock.patch.object(hb, "show_settings_error") as retry:
            app._settings_unavailable("startup")
        retry.assert_called_once()

    def test_queued_dialog_is_suppressed_after_recovery_or_shutdown(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown):
                app = self.setup_app()
                pending = []
                with mock.patch.object(hb.threading, "Thread", side_effect=lambda target, **kw:
                                       types.SimpleNamespace(start=lambda: pending.append(target))), \
                        mock.patch.object(hb, "show_settings_error") as show:
                    app._settings_unavailable("launch")
                    self.assertEqual(len(pending), 1)
                    if shutdown:
                        app.stop_evt.set()
                    else:
                        app._settings_available()
                    pending[0]()
                show.assert_not_called()
                self.assertFalse(app._settings_error_pending)

    def test_shutdown_does_not_add_fallback_or_schedule_dialog(self):
        app = self.setup_app()
        app.stop_evt.set()
        with mock.patch.object(hb.threading, "Thread") as thread:
            app._settings_unavailable("connection")
        thread.assert_not_called()
        self.assertFalse(getattr(app, "_settings_fallback", False))

    def test_message_and_fallback_are_localized_in_all_catalogs(self):
        for language in hb.i18n.LANGUAGES:
            with self.subTest(language=language):
                app = self.setup_app(language)
                with mock.patch.object(hb, "show_settings_error") as show:
                    app._settings_unavailable("launch")
                text = show.call_args.args[0]
                self.assertIn(app.tr("menu.preferences"), text)
                self.assertNotIn("{", text)
                self.assertNotIn("settings.open_failed", text)
                menu = app.build_tray_menu(None)
                self.assertTrue(self.preferences(menu, app).visible)

    def test_dialog_error_releases_the_retry_guard(self):
        app = self.setup_app()
        app._settings_fallback = app._settings_error_pending = True
        with mock.patch.object(hb, "show_settings_error", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                app._show_settings_error()
        self.assertFalse(app._settings_error_pending)
