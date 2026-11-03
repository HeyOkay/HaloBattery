"""Regressions for reconciling the app PRs, without hardware."""
import threading
import unittest
from unittest import mock

from halo import flyout, i18n
from test_hide_rename import FakeIcon, HideRenameTestCase, dev, hb, make_app

REAL_THREAD = threading.Thread


class IntegrationTests(HideRenameTestCase):
    def test_all_languages_clarify_device_alert_scope(self):
        for language in i18n.LANGUAGES:
            app = make_app({"language": language, "low": 20})
            app.apply([dev()])
            owner = next(iter(app.icons.values()))
            label = app.tr("menu.device_low")
            item = next(item for item in app.build_menu(owner).items if item.text == label)
            self.assertNotEqual(label, app.tr("preferences.low"))
            self.assertEqual(item.submenu.items[0].text, app.tr("menu.default_low", level=20))
            self.assertEqual(app.low_for(owner.status), 20)

    def test_new_provider_menu_falls_back_to_registry_label(self):
        app = make_app({"language": "fr"})
        with mock.patch.dict(hb.PROVIDER_LABELS, {"future_device": "Future Device"}):
            prefs = next(i for i in app.build_menu(None).items if i.text == app.tr("menu.preferences"))
            providers = next(i for i in prefs.submenu.items if i.text == app.tr("preferences.providers"))
            self.assertIn("Future Device", [i.text for i in providers.submenu.items])

    def test_chinese_poll_units_and_language_specific_fonts(self):
        self.assertEqual(i18n.translate("interval.0", language="zh-CN"), "15 秒")
        expected = {"zh-CN": "Microsoft YaHei UI", "zh-TW": "Microsoft JhengHei UI", "ja": "Yu Gothic UI"}
        for language, font in expected.items():
            self.assertEqual(flyout.ui_fonts(flyout.TEXT_FONTS, language)[0], font)
        self.assertEqual(flyout.ui_fonts(flyout.TEXT_FONTS), flyout.TEXT_FONTS)

    def test_fonts_are_cached_per_language_on_style_thread(self):
        host = flyout.FlyoutHost()
        with mock.patch.object(flyout, "_Style", side_effect=lambda *args: object()) as factory:
            english = host.style(1, False)
            host.language = "zh-CN"
            chinese = host.style(1, False)
            self.assertIsNot(english, chinese)
            self.assertIs(host.style(1, False), chinese)
            host.language = "en"
            self.assertIs(host.style(1, False), english)
            self.assertEqual(factory.call_count, 2)

    def test_shutdown_between_animation_loop_check_and_clear_does_not_hang(self):
        app = make_app()
        app.stop_evt = threading.Event()
        class ShutdownDuringClear(threading.Event):
            def clear(event):
                app.stop_evt.set()
                event.set()  # quit signals before the animation thread clears it
                super().clear()
        app.anim_wake = ShutdownDuringClear()
        thread = REAL_THREAD(target=app.anim_loop, daemon=True)
        thread.start()
        try:
            thread.join(10)
            self.assertFalse(thread.is_alive())
        finally:
            app.anim_wake.set()
            thread.join(10)


if __name__ == "__main__":
    unittest.main()
