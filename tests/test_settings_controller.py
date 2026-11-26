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
    def test_real_menu_keeps_quick_actions_and_one_essential_device_submenu(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        app.open_settings = mock.Mock()
        menu = app.build_tray_menu(owner)
        visible = [i for i in menu.items if i.visible]
        self.assertEqual(len(visible), 6)
        self.assertEqual([i.text for i in visible[1:3]],
                         [app.tr("menu.this_device"), app.tr("settings.open_app").rstrip(".…")])
        submenus = [i for i in visible if i.submenu]
        self.assertEqual([i.text for i in submenus], [app.tr("menu.this_device")])
        self.assertEqual([i.text for i in submenus[0].submenu.items],
                         [app.tr("menu.hide"), app.tr("menu.low_battery"),
                          app.tr("settings.open_device").rstrip(".…")])
        next(i for i in visible if i.default)(None)
        app.open_settings.assert_called_once_with(page="general")
        app.wake.clear()
        next(i for i in visible if i.text == app.tr("menu.refresh"))(None)
        self.assertTrue(app.wake.is_set())

    def test_placeholder_settings_opens_general_page(self):
        app = make_app()
        app.open_settings = mock.Mock()
        next(i for i in app.build_tray_menu(None).items if i.default)(None)
        app.open_settings.assert_called_once_with(page="general")

    def test_compact_labels_strip_only_trailing_ellipses(self):
        app = make_app()
        for label, expected in (("Settings…", "Settings"), ("Settings...", "Settings"),
                                ("Settings...… ", "Settings"),
                                ("Choose... a device…", "Choose... a device")):
            with self.subTest(label=label), mock.patch.object(app, "tr", return_value=label):
                self.assertEqual(next(i for i in app.build_tray_menu(None).items if i.default).text, expected)

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
                        self.assertEqual(len(visible), 5 + (owner is not None) + (state is not None))
                        for item in visible[1:]:
                            self.assertFalse(item.text.endswith(("...", "…")))
                        if state == "progress":
                            update_item = next(item for item in visible
                                               if not isinstance(item, hb.flyout.HeaderItem) and "%" in item.text)
                            self.assertEqual(update_item.text, hb.update_item_text(
                                "2.0.0", False, 42, language=language))
                        if owner is not None:
                            self.assertIn("Work... mouse…", visible[0].text)

    def device_menu(self, app, menu):
        return next(i for i in menu.items if i.text == app.tr("menu.this_device"))

    def test_classic_and_compact_thresholds_match_preferences_in_every_locale(self):
        for language in hb.i18n.LANGUAGES:
            with self.subTest(language=language):
                app = make_app({"language": language, "low": 20})
                app.apply([dev()])
                owner = next(iter(app.icons.values()))
                compact = self.device_menu(app, app.build_tray_menu(owner)).submenu.items[1].submenu
                classic = next(i.submenu for i in app.build_menu(owner).items
                               if i.text == app.tr("menu.device_low"))
                preferences = app.build_menu(owner, preferences_only=True)
                counter = next(i for i in preferences.items if i.text == app.tr("preferences.low"))
                expected = hb.low_choices(app.tr)
                self.assertEqual(list(counter.choices), expected)
                self.assertEqual([i.text for i in compact.items], [i.text for i in classic.items])
                self.assertEqual([i.text for i in classic.items[1:]], [text for _v, text in expected])
                for menu in (compact, classic):
                    for index, value in enumerate((None, 0, 5, 10, 15, 20, 25, 30)):
                        menu.items[index](None)
                        self.assertEqual(app.device_low(owner.status.key), value)
                        self.assertEqual([bool(i.checked) for i in menu.items],
                                         [j == index for j in range(8)])
                    menu.items[0](None)
                    app.cfg["low"] = 0
                    self.assertEqual(menu.items[0].text, app.tr("menu.default_off"))

    def test_both_threshold_menus_reject_retired_owner_but_follow_current_identity(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        compact = self.device_menu(app, app.build_tray_menu(owner)).submenu.items[1].submenu
        classic = next(i.submenu for i in app.build_menu(owner).items
                       if i.text == app.tr("menu.device_low"))
        previous = owner.status.key
        owner.status = replace(owner.status, key="new:identity")
        app.icons[owner.status.key] = app.icons.pop(previous)
        for menu in (compact, classic):
            menu.items[4](None)
            self.assertEqual(app.device_low("new:identity"), 15)
            self.assertNotIn(previous, app.cfg["lows"])
        app.icons[owner.status.key] = object()
        app.set_device_low = mock.Mock()
        for menu in (compact, classic):
            for item in menu.items:
                item(None)
                self.assertFalse(item.checked)
        app.set_device_low.assert_not_called()

    def test_device_callbacks_follow_current_status_key_without_header_action(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        menu = app.build_tray_menu(owner)
        device = self.device_menu(app, menu)
        app.open_settings = mock.Mock()
        old_key = owner.status.key
        owner.status = replace(owner.status, key="new:identity", name="Current mouse")
        app.icons[owner.status.key] = app.icons.pop(old_key)
        self.assertNotEqual(owner.key, owner.status.key)
        self.assertIsNone(menu.items[0].edit)
        self.assertIn("Current mouse", menu.items[0].text)
        device.submenu.items[2](None)
        app.open_settings.assert_called_once_with("new:identity", page="devices")
        choices = device.submenu.items[1].submenu.items
        choices[4](None)  # 15%, using the existing persisted per-device override
        self.assertEqual(app.device_low("new:identity"), 15)
        self.assertTrue(choices[4].checked)
        device.submenu.items[0](None)
        self.assertNotIn("new:identity", app.icons)
        self.assertEqual(app.cfg["hidden"], {"new:identity": "Current mouse"})
        self.assertFalse(device.visible)

    def test_low_choices_preserve_default_off_and_each_threshold(self):
        app = make_app({"low": 20})
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        menu = app.build_tray_menu(owner)
        choices = self.device_menu(app, menu).submenu.items[1].submenu.items
        values = (None, 0, 5, 10, 15, 20, 25, 30)
        self.assertEqual(len(choices), len(values))
        for index, value in enumerate(values):
            with self.subTest(value=value):
                choices[index](None)
                self.assertEqual(app.device_low(owner.status.key), value)
                self.assertEqual([bool(i.checked) for i in choices],
                                 [i == index for i in range(len(choices))])
                self.assertTrue(all(i.radio for i in choices))
        choices[0](None)
        self.assertNotIn(owner.status.key, app.cfg["lows"])
        self.assertEqual(choices[0].text, app.tr("menu.default_low", level=20))
        app.cfg["low"] = 0
        self.assertEqual(choices[0].text, app.tr("menu.default_off"))

    def test_missing_or_replaced_owner_has_no_device_actions(self):
        for state in ("no-status", "removed", "replaced"):
            with self.subTest(state=state):
                app = make_app()
                app.apply([dev()])
                owner = next(iter(app.icons.values()))
                menu = app.build_tray_menu(owner)
                device = self.device_menu(app, menu)
                app.rename, app.hide = mock.Mock(), mock.Mock()
                app.set_device_low, app.open_settings = mock.Mock(), mock.Mock()
                if state == "no-status":
                    owner.status = None
                elif state == "removed":
                    app.icons.clear()
                else:
                    app.icons[owner.status.key] = object()
                self.assertFalse(device.visible)
                self.assertIsNone(menu.items[0].edit)
                device.submenu.items[0](None)
                device.submenu.items[2](None)
                for item in device.submenu.items[1].submenu.items:
                    self.assertFalse(item.checked)
                    item(None)
                for callback in (app.rename, app.hide, app.set_device_low, app.open_settings):
                    callback.assert_not_called()
                self.assertIsNone(app.build_tray_menu(owner).items[0].edit)
        app = make_app()
        menu = app.build_tray_menu(None)
        self.assertFalse(self.device_menu(app, menu).visible)
        self.assertIsNone(menu.items[0].edit)

    def test_device_submenu_labels_are_present_and_compact_in_every_locale(self):
        for language, details in hb.i18n.LANGUAGES.items():
            with self.subTest(language=language):
                for key in ("menu.this_device", "menu.low_battery", "menu.rename_hint"):
                    self.assertIn(key, details.catalog)
                    self.assertTrue(details.catalog[key].strip())
                app = make_app({"language": language})
                app.apply([dev()])
                owner = next(iter(app.icons.values()))
                device = self.device_menu(app, app.build_tray_menu(owner))
                self.assertTrue(device.visible)
                for item in device.submenu.items:
                    self.assertFalse(item.text.endswith(("...", "…")))
                    if item.submenu:
                        self.assertTrue(all(not i.text.endswith(("...", "…")) for i in item.submenu.items))

    def test_inline_rename_factory_is_read_only_and_commits_unicode_name(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        header = app.build_tray_menu(owner).items[0]
        app.refresh_menus = mock.Mock()
        with mock.patch.object(hb, "ask_name") as modal:
            current, commit = header.begin_rename()
            self.assertEqual(current, owner.status.name)
            self.assertEqual(header.rename_hint, app.tr("menu.rename_hint"))
            self.assertIsNone(header.edit)
            self.assertEqual(self.saved, [])
            self.assertNotIn(owner.status.key, app.cfg.get("names", {}))
            self.assertTrue(commit("  Рабочая мышь 🐁  "))
            modal.assert_not_called()
        self.assertEqual(app.cfg["names"][owner.status.key], "Рабочая мышь 🐁")
        self.assertIn("Рабочая мышь 🐁", owner.titles[-1])
        app.refresh_menus.assert_called_once_with()
        self.assertEqual(len(self.saved), 1)

    def test_inline_rename_limits_name_and_restores_default_with_hidden_sync(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        key = owner.status.key
        app.cfg["names"] = {"other": "Other"}
        app.cfg["hidden"] = {key: "Old", "other": "Other"}
        header = app.build_tray_menu(owner).items[0]
        _, commit = header.begin_rename()
        self.assertTrue(commit(" " + "鼠" * 100 + " "))
        self.assertEqual(app.cfg["names"][key], "鼠" * 60)
        self.assertEqual(app.cfg["hidden"][key], "鼠" * 60)
        _, restore = header.begin_rename()
        self.assertTrue(restore(owner.status.name))
        self.assertEqual(app.cfg["names"], {"other": "Other"})
        self.assertEqual(app.cfg["hidden"], {key: owner.status.name, "other": "Other"})

    def test_inline_rename_rejects_empty_control_and_non_text_without_writes(self):
        app = make_app()
        app.apply([dev()])
        owner = next(iter(app.icons.values()))
        _, commit = app.build_tray_menu(owner).items[0].begin_rename()
        for bad in (None, False, 15, [], "", "  ", "Work\nmouse", "\tWork", "Work\r",
                    "Mouse\0", "Mouse\x7f", "Mouse\x85", "Mouse\u2028", "Mouse\u2029"):
            with self.subTest(value=bad):
                self.assertFalse(commit(bad))
        self.assertEqual(self.saved, [])
        self.assertNotIn(owner.status.key, app.cfg.get("names", {}))
        self.assertTrue(commit(owner.status.name))  # unchanged is a successful no-op
        self.assertEqual(self.saved, [])

    def test_inline_rename_commit_rejects_owner_or_key_changed_during_edit(self):
        for state in ("no-status", "removed", "replaced", "new-key"):
            with self.subTest(state=state):
                app = make_app()
                app.apply([dev()])
                owner = next(iter(app.icons.values()))
                old_key = owner.status.key
                header = app.build_tray_menu(owner).items[0]
                _, commit = header.begin_rename()
                if state == "no-status":
                    owner.status = None
                elif state == "removed":
                    app.icons.clear()
                elif state == "replaced":
                    app.icons[old_key] = object()
                else:
                    owner.status = replace(owner.status, key="new:identity")
                    app.icons[owner.status.key] = app.icons.pop(old_key)
                self.assertFalse(commit("Wrong device"))
                self.assertEqual(app.cfg.get("names", {}), {})
                self.assertEqual(self.saved, [])
                if state != "new-key":
                    self.assertIsNone(header.begin_rename())

    def test_inline_rename_factory_resolves_first_status_after_menu_creation(self):
        app = make_app()
        owner = types.SimpleNamespace(status=None)
        header = app.build_tray_menu(owner).items[0]
        self.assertIsNone(header.begin_rename())
        owner.status = dev()
        app.icons[owner.status.key] = owner
        self.assertEqual(header.begin_rename()[0], owner.status.name)
        self.assertIsNone(app.build_tray_menu(None).items[0].begin_rename)

    def test_inline_rename_repairs_wrong_type_name_and_hidden_maps(self):
        for bad in (None, 17, "invalid", []):
            with self.subTest(value=bad):
                app = make_app()
                app.apply([dev()])
                owner = next(iter(app.icons.values()))
                _, commit = app.build_tray_menu(owner).items[0].begin_rename()
                app.cfg["names"], app.cfg["hidden"] = bad, bad
                self.assertTrue(commit("Work mouse"))
                self.assertEqual(app.cfg["names"], {owner.status.key: "Work mouse"})
                self.assertEqual(app.cfg["hidden"], {})
                self.assertEqual(self.saved[-1]["names"], app.cfg["names"])

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
