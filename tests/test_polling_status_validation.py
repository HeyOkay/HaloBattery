"""Native provider-to-app rejection must also remove invalid derived percentages."""
from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from halo import i18n
from halo.polling import PollingRuntime
from providers.base import DeviceStatus, Provider
from test_hide_rename import FakeIcon, hb, make_app


class FakeProvider(Provider):
    name = "validation"

    def __init__(self, values):
        self.values = values
        self.entered = threading.Event()

    def poll(self):
        self.entered.set()
        return list(self.values)


class NativeStatusValidationTests(unittest.TestCase):
    def setUp(self):
        context = patch.object(hb, "DeviceIcon", FakeIcon)
        context.start()
        self.addCleanup(context.stop)

    def pipeline(self, values):
        provider = FakeProvider(values)
        runtime = PollingRuntime([provider], lambda full: None)
        self.addCleanup(lambda: self.assertTrue(runtime.close(.5)))
        runtime.configure(set())
        app = make_app({"bluetooth": False})
        app._polling_runtime = runtime
        app.hid_results, app.bt_cache = [], []
        app._bt_revision = app._polling_bt_revision = 0
        app.show_placeholder, app.notify = Mock(), Mock()
        runtime.request_poll()
        self.assertTrue(provider.entered.wait(1))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            runtime.pump()
            stats = runtime.stats()
            if not stats["running"] and not stats["queued"]:
                break
            time.sleep(.001)
        else:
            self.fail("fake provider did not complete")
        results = app.consume_polling()
        return app, results

    def test_invalid_derived_percentage_cannot_cross_native_app_boundary_in_any_locale(self):
        values = (255, -1, -0.5, 100.5, True, False, "50", float("nan"), float("inf"), -float("inf"))
        originals = [DeviceStatus(
            f"bad:{index}", "Fake device", level, charging=True, online=True,
            source="validation", kind="headset", approx=f"INVALID_DERIVED_PERCENT {level}%",
            ui_message="state.approx", ui_params={"level": level},
        ) for index, level in enumerate(values)]
        app, results = self.pipeline(originals)
        self.assertEqual(len(results), len(originals))
        for original, current in zip(originals, results):
            self.assertIsNot(original, current)
            self.assertIsNone(current.level)
            self.assertEqual((current.approx, current.ui_message, current.ui_params), ("", "", {}))
            self.assertEqual((current.key, current.name, current.charging, current.online, current.kind),
                             (original.key, original.name, True, True, "headset"))
            self.assertIs(original.level, values[int(original.key.split(":")[1])])
            self.assertTrue(original.approx.startswith("INVALID_DERIVED_PERCENT"))
            self.assertEqual(original.ui_message, "state.approx")
            self.assertIs(original.ui_params["level"], original.level)
            expected = replace(original, level=None, approx="", ui_message="", ui_params={})
            for language in i18n.LANGUAGES:
                with self.subTest(key=original.key, language=language):
                    self.assertEqual(hb.describe(current, language=language),
                                     hb.describe(expected, language=language))
                    self.assertNotIn("INVALID_DERIVED_PERCENT", hb.describe(current, language=language))
        # A previously charging device must not produce a false full alert,
        # and none of these rejected measurements may enter the history.
        app.full_state.update({status.key: "charging" for status in results})
        app.apply_polling(results)
        self.assertEqual(app.history.devices, {})
        self.assertEqual(app.alerted, {})
        app.notify.assert_not_called()
        self.assertTrue(all(icon.status.level is None for icon in app.icons.values()))

    def test_valid_unknown_coarse_zero_and_full_metadata_are_preserved_without_mutation(self):
        originals = [
            DeviceStatus("unknown", "Unknown", None, source="validation",
                         approx="connected, battery level not reported yet",
                         ui_message="state.pending", ui_params={}),
            DeviceStatus("coarse", "Coarse", None, source="validation",
                         approx="about 55% (medium)", ui_message="state.approx_grade",
                         ui_params={"level": 55, "grade": "medium"}),
            DeviceStatus("zero", "Zero", 0, source="validation", approx="about 0%",
                         ui_message="state.approx", ui_params={"level": 0}),
            DeviceStatus("full", "Full", 100, source="validation", approx="about 100%",
                         ui_message="state.approx", ui_params={"level": 100}),
        ]
        app, results = self.pipeline(originals)
        for original, current in zip(originals, results):
            self.assertEqual(current, original)
            self.assertIsNot(current, original)
            self.assertIsNot(current.ui_params, original.ui_params)
            for language in i18n.LANGUAGES:
                with self.subTest(key=original.key, language=language):
                    self.assertEqual(hb.describe(current, language=language),
                                     hb.describe(original, language=language))
        # Valid unknown/coarse states retain their presentation and do not
        # become precise history samples or arm low/full notifications.
        app.apply_polling(results[:2])
        self.assertEqual(app.history.devices, {})
        app.notify.assert_not_called()


    def test_valid_fractions_are_rounded_before_native_publication_without_mutating_provider(self):
        pairs = ((50.4, 50), (50.6, 51), (0.2, 0), (99.7, 100), (100.0, 100),
                 (50.5, 50), (1.5, 2))
        originals = [DeviceStatus(f"fraction:{index}", "Fractional reading", level,
                                  charging=True, source="validation", kind="mouse",
                                  ui_params={"origin": "provider"})
                     for index, (level, _) in enumerate(pairs)]
        app, results = self.pipeline(originals)
        self.assertEqual(len(results), len(pairs))
        for original, current, (level, expected) in zip(originals, results, pairs):
            with self.subTest(level=level):
                self.assertEqual(current.level, expected)
                self.assertIs(type(current.level), int)
                self.assertEqual((current.key, current.name, current.charging, current.online),
                                 (original.key, original.name, True, True))
                self.assertEqual(current.ui_params, original.ui_params)
                self.assertIsNot(current.ui_params, original.ui_params)
                self.assertEqual(original.level, level)
                self.assertIs(type(original.level), float)
        app.apply_polling(results)
        self.assertTrue(all(type(icon.status.level) is int for icon in app.icons.values()))


if __name__ == "__main__":
    unittest.main()
