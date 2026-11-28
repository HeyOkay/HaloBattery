"""The full and asleep power states, without a tray or hardware.

`full` is a state of its own: a charging reading at 100 % (or a provider's own
charged flag) stops the breathing animation and says "fully charged", while a
reading the receiver kept for a device that stopped answering is asleep and
never charging.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers

from providers.base import DeviceStatus  # noqa: E402
from test_hide_rename import hb, make_app  # noqa: E402


def status(level=100, charging=True, **kw):
    return DeviceStatus("pulsar:3554fb50", "VGN Dragonfly F1 SE", level,
                        charging, True, "pulsar", **kw)


class FullStateTests(unittest.TestCase):
    def test_a_charging_reading_at_100_becomes_full(self):
        got = hb.with_power_state(status(100, True))
        self.assertTrue(got.full)
        self.assertTrue(got.charging, "full is on power: the charging flag stays")
        self.assertEqual("100%, fully charged", hb.device_state(got))

    def test_a_provider_with_a_charged_flag_can_say_full_below_100(self):
        got = hb.with_power_state(status(95, True, full=True))
        self.assertTrue(got.full)
        self.assertEqual("95%, fully charged", hb.device_state(got))

    def test_a_device_off_the_charger_is_never_full(self):
        self.assertFalse(hb.with_power_state(status(100, False)).full)

    def test_an_unknown_charger_is_not_turned_into_full(self):
        # charging_known=False means the level is a placeholder, not a charge state
        self.assertFalse(hb.with_power_state(status(100, False, charging_known=False)).full)

    def test_an_already_full_reading_is_left_alone(self):
        original = status(100, True, full=True)
        self.assertIs(original, hb.with_power_state(original))

    def test_a_full_icon_does_not_breathe(self):
        app = make_app({"animation": True})
        self.assertFalse(app.icon_render_state(status(100, True, full=True))[6],
                         "a full device must show a steady ring")
        self.assertTrue(app.icon_render_state(status(99, True))[6],
                        "still charging below 100 %: keep the animation")


class DeviceEventTests(unittest.TestCase):
    def test_a_device_event_wakes_the_poll_loop(self):
        app = make_app()
        app.device_events = None
        app.device_event_at = 0.0
        app._on_device_change("device")
        self.assertTrue(app.wake.is_set(), "an arrival must not wait for the interval")
        self.assertEqual("device", app.device_event_reason)
        self.assertGreater(app.device_event_at, 0.0)


class AsleepStateTests(unittest.TestCase):
    def test_a_retained_reading_says_asleep_and_is_not_charging(self):
        kept = DeviceStatus("pulsar:3554fb50", "VGN Dragonfly F1 SE", 64,
                            False, False, "pulsar", asleep=True, wake_on_input=True)
        self.assertEqual("64% (last known value, device asleep)", hb.device_state(kept))

    def test_the_asleep_flag_is_separate_from_offline(self):
        off = DeviceStatus("pulsar:3554fb50", "VGN Dragonfly F1 SE", 64, False, False, "pulsar")
        self.assertFalse(off.asleep, "a device that is off is not asleep")


class FullStateLanguageTests(unittest.TestCase):
    def test_every_language_says_fully_charged_in_its_own_words(self):
        english = hb.device_state(hb.with_power_state(status()), language="en")
        for code, language in hb.i18n.LANGUAGES.items():
            with self.subTest(language=code):
                self.assertIn("state.fully_charged", language.catalog)
                text = hb.device_state(hb.with_power_state(status()), language=code)
                self.assertIn("100", text)
                if code != "en":
                    self.assertNotEqual(english, text)


class AsleepPowerStateTests(unittest.TestCase):
    """A kept reading is the device's last level, not a charger report (#109)."""

    def kept(self, level=100, charging=True, **kw):
        return DeviceStatus("pulsar:3554fb50", "VGN Dragonfly F1 SE", level, charging,
                            False, "pulsar", asleep=True, wake_on_input=True, **kw)

    def test_an_asleep_reading_is_neither_charging_nor_full(self):
        st = hb.with_power_state(self.kept(full=True))
        self.assertEqual((False, False), (st.charging, st.full))
        self.assertEqual("100% (last known value, device asleep)", hb.device_state(st))

    def test_an_asleep_reading_without_a_charging_flag_is_left_alone(self):
        st = self.kept(64, charging=False)
        self.assertIs(st, hb.with_power_state(st))

    def test_an_offline_reading_is_not_called_full(self):
        # switched off on its cable: still on the charger, but not a live 100 %
        st = hb.with_power_state(DeviceStatus("logitech:4082", "PRO X", 100, True, False,
                                              "logitech"))
        self.assertTrue(st.charging)
        self.assertFalse(st.full)

    def test_applying_it_twice_changes_nothing(self):
        for st in (status(), status(95, full=True), status(80), self.kept(), self.kept(64, False)):
            once = hb.with_power_state(st)
            self.assertIs(once, hb.with_power_state(once))


if __name__ == "__main__":
    unittest.main()
