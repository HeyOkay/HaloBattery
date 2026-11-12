"""A failed tray icon must not prevent unrelated devices from updating."""
import unittest
from unittest import mock

from test_hide_rename import FakeIcon, HideRenameTestCase, dev, hb, make_app


class UpdateFailureTests(HideRenameTestCase):
    def test_failed_update_keeps_other_devices_alerts_and_missing_cleanup_running(self):
        app = make_app()
        first, second, gone = dev(key="bad"), dev(key="good"), dev(key="gone")
        app.apply([first, second, gone])
        lost = app.icons["gone"]
        bad = app.icons["bad"]
        bad.update = mock.Mock(side_effect=OSError("native swap failed"))
        app.check_alert = mock.Mock()
        app.check_full = mock.Mock()
        app.history.save = mock.Mock()
        with self.assertLogs("halo_battery", "WARNING"):
            app.apply([first, second])
            app.apply([first, second])
        self.assertEqual(bad.update.call_count, 2)
        self.assertEqual(app.icons["good"].status, second)
        self.assertEqual([call.args[1].key for call in app.check_alert.call_args_list], ["good", "good"])
        self.assertEqual(app.check_full.call_count, 2)
        self.assertEqual(app.history.save.call_count, 2)
        self.assertNotIn("gone", app.icons)
        self.assertTrue(lost.stopped)

    def test_failed_creation_is_retried_without_blocking_the_next_device(self):
        app = make_app()
        failing = True
        def create(owner, key):
            if key == "bad" and failing:
                raise OSError("icon creation failed")
            return FakeIcon(owner, key)
        with mock.patch.object(hb, "DeviceIcon", side_effect=create):
            with self.assertLogs("halo_battery", "WARNING"):
                app.apply([dev(key="bad"), dev(key="good")])
            self.assertIn("good", app.icons)
            self.assertNotIn("bad", app.icons)
            failing = False
            app.apply([dev(key="bad"), dev(key="good")])
        self.assertIn("bad", app.icons)

    def test_failed_disconnected_image_retries_even_after_status_became_offline(self):
        app = make_app({"keep_disconnected": True})
        app.apply([dev(key="bad"), dev(key="good")])
        bad = app.icons["bad"]
        attempts = []
        def update(status):
            bad.status = status   # DeviceIcon stores the reading before its native swap
            attempts.append(status)
            if len(attempts) == 1:
                raise OSError("disconnect image failed")
        bad.update = update
        app.apply([])             # first missing poll
        with self.assertLogs("halo_battery", "WARNING"):
            app.apply([])         # failed grey image; other device is still greyed
        self.assertFalse(app.icons["good"].status.online)
        self.assertIn("bad", app.missing)
        self.assertNotIn("good", app.missing)
        app.apply([])
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(not st.online for st in attempts))
        self.assertNotIn("bad", app.missing)


if __name__ == "__main__":
    unittest.main()
