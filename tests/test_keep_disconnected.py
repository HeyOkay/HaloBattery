"""Tests for Preferences > "Keep disconnected devices until restart" (#151). No tray, no hardware.

Run from the repository root:

    python -m unittest discover -s tests
"""
import unittest

from test_hide_rename import HideRenameTestCase, dev, hb, make_app

KEY = "logitech:C15E09CD"


class KeepDisconnectedTests(HideRenameTestCase):
    def test_default_is_off(self):
        self.assertFalse(hb.DEFAULTS["keep_disconnected"])

    def test_removed_when_off(self):
        app = make_app()
        app.apply([dev()])
        ic = app.icons[KEY]
        app.apply([])
        app.apply([])
        self.assertNotIn(KEY, app.icons)
        self.assertTrue(ic.stopped)

    def test_kept_grey_with_last_level_when_on(self):
        app = make_app({"keep_disconnected": True})
        app.apply([dev(level=64)])
        ic = app.icons[KEY]
        for _ in range(4):
            app.apply([])
        self.assertIs(app.icons[KEY], ic)
        self.assertFalse(ic.stopped)
        self.assertFalse(ic.status.online)
        self.assertFalse(ic.status.charging)
        self.assertEqual(ic.status.level, 64)
        self.assertIn("last known", ic.titles[-1])

    def test_kept_icon_does_not_keep_the_fast_recheck_running(self):
        app = make_app({"keep_disconnected": True})
        app.apply([dev()])
        app.apply([])
        app.apply([])
        for _ in range(4):
            app.apply([])
            self.assertFalse(any(app.missing.values()))

    def test_charging_flag_is_cleared(self):
        app = make_app({"keep_disconnected": True})
        app.apply([hb.replace(dev(), charging=True)])
        for _ in range(3):
            app.apply([])
        self.assertFalse(app.icons[KEY].status.charging)

    def test_back_online_updates_the_kept_icon(self):
        app = make_app({"keep_disconnected": True})
        app.apply([dev(level=64)])
        ic = app.icons[KEY]
        for _ in range(3):
            app.apply([])
        app.apply([dev(level=60)])
        self.assertIs(app.icons[KEY], ic)
        self.assertTrue(ic.status.online)
        self.assertEqual(ic.status.level, 60)
        self.assertNotIn(KEY, app.missing)

    def test_no_alert_for_a_kept_icon(self):
        app = make_app({"keep_disconnected": True, "low": 20})
        app.apply([dev(level=50)])
        for _ in range(4):
            app.apply([])
        app.alerted[KEY] = False
        app.check_alert(app.icons[KEY], app.icons[KEY].status)
        app.check_full(app.icons[KEY], app.icons[KEY].status)
        self.assertEqual(app.notes, [])

    def test_a_low_kept_icon_is_silent_after_the_first_alert(self):
        app = make_app({"keep_disconnected": True, "low": 20})
        app.apply([dev(level=10)])
        self.assertEqual(len(app.notes), 1)
        for _ in range(4):
            app.apply([])
        self.assertEqual(len(app.notes), 1)

    def test_turning_the_option_off_removes_kept_icons(self):
        app = make_app({"keep_disconnected": True})
        app.apply([dev()])
        ic = app.icons[KEY]
        for _ in range(3):
            app.apply([])
        self.assertIn(KEY, app.icons)
        app.cfg["keep_disconnected"] = False
        app.apply([])
        app.apply([])
        self.assertNotIn(KEY, app.icons)
        self.assertTrue(ic.stopped)

    def test_hide_still_removes_a_kept_icon(self):
        app = make_app({"keep_disconnected": True})
        app.apply([dev()])
        for _ in range(3):
            app.apply([])
        ic = app.icons[KEY]
        app.hide(ic)
        self.assertNotIn(KEY, app.icons)
        app.apply([])
        self.assertNotIn(KEY, app.icons)


if __name__ == "__main__":
    unittest.main()
