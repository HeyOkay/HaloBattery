"""Default native-transport app glue: caches must not replay observations or disappearance."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest import mock

from test_hide_rename import HideRenameTestCase, hb, make_app, dev


class NativeTransportDefaultTests(unittest.TestCase):
    def test_windows_transport_is_default_without_any_marker(self):
        with mock.patch.object(hb.sys, "platform", "win32"):
            self.assertTrue(hb.native_transport_enabled([]))
            self.assertFalse(hb.native_transport_enabled(["--legacy-native-io"]))
        with mock.patch.object(hb.sys, "platform", "linux"):
            self.assertFalse(hb.native_transport_enabled([]))

    def test_controlled_shutdown_requires_explicit_verification_flag(self):
        self.assertIsNone(hb.verification_stop_file(["--test-stop-file", "unexpected"]))
        self.assertIsNotNone(hb.verification_stop_file(["--verify-native-io", "--test-stop-file", "stop"]))

    def test_normal_start_installs_transport_before_creating_polling_workers(self):
        app = make_app()
        app.providers = []
        events = []
        transport = mock.Mock()
        transport.install.side_effect = lambda *args: events.append("install")
        factory = mock.Mock(side_effect=lambda *args, **kwargs: events.append("providers") or mock.Mock())
        with mock.patch.object(hb.sys, "platform", "win32"), \
                mock.patch.object(hb.sys, "argv", ["HaloBattery.exe"]), \
                mock.patch.dict(hb.sys.modules, {
                    "halo.hid_transport": SimpleNamespace(TransportRuntime=lambda: transport),
                    "halo.polling": SimpleNamespace(PollingRuntime=factory)}):
            app.start_native_transport()
        self.assertEqual(events, ["install", "providers"])
        self.assertIs(app._native_transport, transport)
        self.assertIsNotNone(app._polling_runtime)


class ExperimentalAppTests(HideRenameTestCase):
    def app(self, **cfg):
        app = make_app(dict(bluetooth=False, **cfg))
        app.hid_results, app.bt_cache = [], []
        app._polling_fresh, app._polling_completed = set(), set()
        app._polling_raw_keys = set()
        return app

    def test_cached_results_do_not_replay_history_or_alerts(self):
        app = self.app()
        reading = dev(level=5)
        app.key_provider[reading.key] = "logitech"
        with mock.patch.object(app.history, "record") as record, \
                mock.patch.object(app, "check_alert") as alert, \
                mock.patch.object(app, "check_full") as full:
            app.apply([reading], fresh_keys={reading.key})
            app.apply([reading], fresh_keys=set())
            app.apply([reading], fresh_keys=set())
            self.assertEqual(record.call_count, 1)
            self.assertEqual(alert.call_count, 1)
            self.assertEqual(full.call_count, 1)

    def test_stale_result_stops_estimation_and_cannot_alert(self):
        app = self.app()
        reading = replace(dev(level=5), polling_stale=True, online=False)
        with mock.patch.object(app.history, "record") as record, \
                mock.patch.object(app, "check_alert") as alert:
            app.apply([reading], fresh_keys={reading.key})
            self.assertFalse(record.call_args.args[3])
            alert.assert_not_called()

    def test_unrelated_completion_does_not_remove_another_providers_icon(self):
        app = self.app()
        reading = dev()
        app.key_provider[reading.key] = "logitech"
        app.apply([reading])
        app._polling_completed = {"razer"}
        app.apply_polling([])
        self.assertEqual(app.missing, {})
        self.assertIn(reading.key, app.icons)
        app._polling_completed = {"logitech"}
        app.apply_polling([])
        self.assertEqual(app.missing[reading.key], 1)
        app._polling_completed = set()
        app.apply_polling([])
        self.assertEqual(app.missing[reading.key], 1)
        app._polling_completed = {"logitech"}
        app.apply_polling([])
        self.assertNotIn(reading.key, app.icons)

    def test_disabled_provider_cannot_publish_a_late_cached_observation(self):
        app = self.app(disabled_providers=["logitech"])
        reading = dev()
        app.key_provider[reading.key] = "logitech"
        app.apply([reading], fresh_keys={reading.key})
        self.assertNotIn(reading.key, app.icons)

    def test_bluetooth_revision_counts_only_once(self):
        app = self.app()
        app.cfg["bluetooth"] = True
        reading = replace(dev(key="bt:1"), source="bluetooth")
        app.bt_cache = [reading]
        app._bt_revision, app._polling_bt_revision = 1, 0
        app.merge_bluetooth = lambda values: values + app.bt_cache
        app._polling_runtime = SimpleNamespace(consume=lambda: SimpleNamespace(
            hid=[], owners={}, fresh_keys=set(), completed=set()))
        app.consume_polling()
        self.assertIn(reading.key, app._polling_fresh)
        self.assertEqual(app._polling_completed, {"bluetooth"})
        app.consume_polling()
        self.assertFalse(app._polling_fresh)
        self.assertFalse(app._polling_completed)


class ExperimentalStartupTests(unittest.TestCase):
    def test_controlled_observation_does_not_repoint_user_autostart(self):
        with mock.patch.object(hb.sys, "argv", ["HaloBattery.exe", "--verify-native-io", "--test-stop-file", "stop"]), \
                mock.patch.object(hb.flyout, "enable_dpi_awareness"), \
                mock.patch.object(hb.updates, "parse_finish_args", return_value=None), \
                mock.patch.object(hb, "single_instance", return_value=True), \
                mock.patch.object(hb, "set_app_id"), \
                mock.patch.object(hb, "migrate_legacy_config") as config, \
                mock.patch.object(hb, "migrate_legacy_autostart") as migrate, \
                mock.patch.object(hb, "refresh_autostart") as startup, \
                mock.patch.object(hb, "sync_installed_version") as version, \
                mock.patch.object(hb, "App") as factory:
            hb.main()
            config.assert_not_called()
            migrate.assert_not_called()
            startup.assert_not_called()
            version.assert_not_called()
            factory.return_value.run.assert_called_once()
