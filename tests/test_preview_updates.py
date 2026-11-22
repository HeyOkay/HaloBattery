"""Preview -> stable transitions, using fake releases and no network or hardware."""
import os
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import hb, make_app
from halo import updates


class PreviewVersionTests(unittest.TestCase):
    def test_only_same_or_higher_stable_release_replaces_preview(self):
        for latest, current, preview, expected in (
                ("1.18.0", "1.18.0", True, True),
                ("v1.18.0", "1.18", True, True),
                ("1.18.0", "1.18.0", False, False),
                ("1.17.0", "1.18.0", True, False),
                ("1.19.0", "1.18.0", True, True),
                ("1.19.0", "1.18.0", False, True),
                ("1.18.0-rc1", "1.18.0", True, False),
                ("1.18.0", None, True, False),
                (None, "1.18.0", True, False),
                ("1.18.0", "1.18.0", "true", False)):
            with self.subTest(latest=latest, current=current, preview=preview):
                self.assertIs(updates.is_update_available(
                    latest, current, current_prerelease=preview), expected)
        self.assertFalse(updates.is_newer("1.18.0", "1.18.0"))
        with self.assertRaises(ValueError):
            updates.parse_release({"tag_name": "pre-1.18.0-abcdef0-1-1"})


class PreviewAppTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(hb, "BUILD_CHANNEL", "pre-release").start()
        mock.patch.object(hb, "save_config").start()

    def app(self, **cfg):
        app = make_app(cfg)
        app.show_whats_new = mock.Mock()
        app.notify_update = mock.Mock()
        app.notify_any = mock.Mock()
        app.refresh_menus = mock.Mock()
        app.open_update = mock.Mock()
        app.quit = mock.Mock()
        app.update_lock = threading.Lock()
        app.install_ok = True
        return app

    def test_stable_offer_for_same_preview_version_and_no_downgrade(self):
        app = self.app()
        same = updates.Release(hb.VERSION, "https://example.invalid/stable")
        with mock.patch.object(updates, "fetch_latest", return_value=same):
            app.check_update(manual=True)
        self.assertIs(app.update, same)
        app.notify_any.assert_called_once()
        with mock.patch.object(updates, "fetch_latest", return_value=updates.Release("1.17.0", "old")):
            app.check_update(manual=True)
        self.assertIsNone(app.update)

    def test_cached_same_version_offer_is_restored(self):
        app = self.app(update_repo=updates.REPO, update_latest=hb.VERSION)
        app.after_update = mock.Mock()
        app.stop_evt = mock.Mock()
        app.stop_evt.wait.return_value = True  # stop before any network/background work
        app.update_loop()
        self.assertEqual(app.update.version, hb.VERSION)

    def test_cached_offer_installs_equal_stable_version(self):
        app = self.app()
        app.update = updates.Release(hb.VERSION, "https://example.invalid/stable")
        rel = updates.Release(hb.VERSION, "https://example.invalid/stable", zip_url="https://example.invalid/a.zip")
        with mock.patch.object(updates, "fetch_latest", return_value=rel), \
                mock.patch.object(updates, "staging_dir", return_value="fake-stage"), \
                mock.patch.object(updates, "download", return_value="fake-app") as download, \
                mock.patch.object(updates, "prepare_runner", return_value="fake-runner"), \
                mock.patch.object(updates, "current_process_creation_time", return_value=123), \
                mock.patch.object(updates, "start_detached"):
            app.install_update()
        download.assert_called_once()
        app.quit.assert_called_once()
        self.assertEqual(app.cfg["update_target"], hb.VERSION)

    def test_same_version_rollback_is_reported_once(self):
        app = self.app(last_version=hb.VERSION, last_channel="pre-release",
                       update_from=hb.VERSION, update_target=hb.VERSION)
        app.placeholder = SimpleNamespace(visible=True)
        app.after_update()
        app.notify_update.assert_called_once_with(
            "notification.update_not_completed", target=hb.VERSION, version=hb.VERSION)
        app.show_whats_new.assert_not_called()
        app.after_update()
        self.assertEqual(app.notify_update.call_count, 1)

    def test_same_version_stable_completion_shows_notes_once(self):
        app = self.app(last_version=hb.VERSION, last_channel="pre-release",
                       update_from=hb.VERSION, update_target=hb.VERSION)
        with mock.patch.object(hb, "BUILD_CHANNEL", "stable"):
            app.after_update()
            app.show_whats_new.assert_called_once_with(hb.VERSION)
            self.assertEqual(app.cfg["last_channel"], "stable")
            app.after_update()
        self.assertEqual(app.show_whats_new.call_count, 1)
        app.notify_update.assert_not_called()

    def test_same_version_manual_stable_install_and_preview_restarts(self):
        for old_channel, current, shown in (("pre-release", "stable", True),
                                            ("stable", "stable", False),
                                            ("pre-release", "pre-release", False),
                                            ("stable", "pre-release", False),
                                            ([], "stable", False)):
            with self.subTest(old=old_channel, current=current):
                app = self.app(last_version=hb.VERSION, last_channel=old_channel)
                with mock.patch.object(hb, "BUILD_CHANNEL", current):
                    app.after_update()
                self.assertEqual(app.show_whats_new.called, shown)

    def test_preview_notes_are_local_and_replace_cached_stable_notes(self):
        app = self.app(whats_new_notes={"version": hb.VERSION, "notes": "stale stable notes"})
        app.ui_host = object()
        with mock.patch.object(hb.build_info, "preview_notes", return_value="### Changed\n- Preview notes"), \
                mock.patch.object(updates, "fetch_release", side_effect=AssertionError("must stay offline")), \
                mock.patch.object(hb.whatsnew, "show") as show:
            hb.App.show_whats_new(app)
        self.assertEqual(show.call_args.args[2], "### Changed\n- Preview notes")
        self.assertTrue(show.call_args.args[3].endswith("CHANGELOG.md#unreleased"))


if __name__ == "__main__":
    unittest.main()
