"""Tests for the optional sound with the low battery alert (#66) in halo_battery.pyw.
No tray, no hardware, no sound: the app module is loaded with the fake icons of
test_hide_rename.py and winsound is replaced by a mock.

Run from the repository root:

    python -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import HideRenameTestCase, hb, make_app  # noqa: E402
from test_tray_features import KEY, mouse  # noqa: E402

REPEAT = hb.LOW_SOUND_REPEAT


class LowSoundDecisionTests(unittest.TestCase):
    """low_battery_sound(): play now or not, and which sound. Threshold 20 %."""

    def sound(self, level, charging=False, online=True, low=20, last=None, now=1000.0):
        return hb.low_battery_sound(level, charging, online, low, last, now)

    def test_repeat_is_five_minutes(self):
        self.assertEqual(REPEAT, 300)

    def test_first_alert_plays_the_low_sound(self):
        self.assertEqual(self.sound(20), "low")
        self.assertEqual(self.sound(12), "low")

    def test_repeats_after_five_minutes(self):
        self.assertEqual(self.sound(15, last=1000.0, now=1000.0 + REPEAT), "low")
        self.assertEqual(self.sound(15, last=1000.0, now=1000.0 + REPEAT + 60), "low")

    def test_not_before_five_minutes(self):
        self.assertIsNone(self.sound(15, last=1000.0, now=1000.0))
        self.assertIsNone(self.sound(15, last=1000.0, now=1000.0 + REPEAT - 1))

    def test_critical_sound_at_5_percent_or_below(self):
        self.assertEqual(self.sound(5), "critical")
        self.assertEqual(self.sound(0), "critical")
        self.assertEqual(self.sound(6), "low")
        self.assertEqual(self.sound(3, last=1000.0, now=1000.0 + REPEAT), "critical")

    def test_no_sound_while_charging(self):
        self.assertIsNone(self.sound(10, charging=True))

    def test_no_sound_above_the_threshold(self):
        self.assertIsNone(self.sound(21))
        self.assertIsNone(self.sound(80))

    def test_no_sound_when_offline_or_level_unknown(self):
        self.assertIsNone(self.sound(10, online=False))
        self.assertIsNone(self.sound(None))

    def test_no_sound_when_the_low_battery_alert_is_off(self):
        self.assertIsNone(self.sound(3, low=0))
        self.assertIsNone(self.sound(0, low=0))


class LowSoundWiringTests(HideRenameTestCase):
    """check_alert() plays the sound through winsound, only when the option is on."""

    def setUp(self):
        super().setUp()
        self.ws = mock.Mock(SND_FILENAME=0x20000, SND_ASYNC=0x1, SND_NODEFAULT=0x2,
                            SND_ALIAS=0x10000)
        self.now = 1000.0
        self.wav = True                # does the file in %WINDIR%\Media exist?
        for p in [mock.patch.object(hb, "winsound", self.ws),
                  mock.patch.object(hb.time, "monotonic", lambda: self.now),
                  mock.patch.object(hb.os.path, "isfile", lambda path: self.wav)]:
            p.start()
            self.addCleanup(p.stop)

    def app(self, **cfg):
        return make_app(dict({"low": 20, "low_sound": True}, **cfg))

    def played(self):
        return [c.args for c in self.ws.PlaySound.call_args_list]

    def test_off_by_default(self):
        self.assertIs(hb.DEFAULTS["low_sound"], False)
        app = make_app({"low": 20})
        app.apply([mouse(15)])
        self.assertEqual(len(app.notes), 1, "the notification is unchanged")
        self.ws.PlaySound.assert_not_called()

    def test_alert_plays_windows_battery_low_async(self):
        app = self.app()
        app.apply([mouse(15)])
        self.assertEqual(len(app.notes), 1)
        (path, flags), = self.played()
        self.assertEqual(os.path.basename(path), "Windows Battery Low.wav")
        self.assertEqual(os.path.basename(os.path.dirname(path)), "Media")
        self.assertEqual(flags, self.ws.SND_FILENAME | self.ws.SND_ASYNC | self.ws.SND_NODEFAULT)

    def test_critical_level_plays_windows_battery_critical(self):
        app = self.app()
        app.apply([mouse(4)])
        (path, flags), = self.played()
        self.assertEqual(os.path.basename(path), "Windows Battery Critical.wav")
        self.assertTrue(flags & self.ws.SND_ASYNC)

    def test_missing_file_falls_back_to_a_system_sound(self):
        self.wav = False
        app = self.app()
        app.apply([mouse(15)])
        app.apply([mouse(15, key="razer:00c8:2")])
        self.now += REPEAT
        app.apply([mouse(4)])
        self.assertEqual(self.played(), [("SystemExclamation", self.ws.SND_ALIAS | self.ws.SND_ASYNC),
                                         ("SystemExclamation", self.ws.SND_ALIAS | self.ws.SND_ASYNC),
                                         ("SystemHand", self.ws.SND_ALIAS | self.ws.SND_ASYNC)])

    def test_plays_while_quiet_while_gaming_holds_the_notification(self):
        """The sound is for full-screen games: "Quiet while gaming" holds the
        notification, not the sound."""
        app = self.app(quiet_fullscreen=True)
        with mock.patch.object(hb, "fullscreen_app_running", lambda: True):
            app.apply([mouse(15)])
        self.assertEqual(len(app.held), 1, "the notification is held")
        self.assertEqual(len(self.played()), 1, "the sound still plays")

    def test_follows_the_alert_level_of_the_device(self):
        app = self.app()
        app.cfg["lows"] = {mouse(25).key: 30}
        app.apply([mouse(25)])
        self.assertEqual(len(self.played()), 1)

    def test_repeats_every_five_minutes_while_low(self):
        app = self.app()
        app.apply([mouse(15)])
        self.now += 60
        app.apply([mouse(15)])
        self.assertEqual(len(self.played()), 1, "not again after one minute")
        self.now += REPEAT - 60
        app.apply([mouse(14)])
        self.assertEqual(len(self.played()), 2)
        self.assertEqual(len(app.notes), 1, "the notification still comes once")

    def test_stops_when_charging_and_starts_again_after(self):
        app = self.app()
        app.apply([mouse(15)])
        self.now += 60
        app.apply([mouse(15, True)])
        self.now += REPEAT
        app.apply([mouse(15, True)])
        self.assertEqual(len(self.played()), 1, "no sound while charging")
        app.apply([mouse(15)])             # off the charger, still low: a new alert
        self.now += 60
        app.apply([mouse(15, True)])
        self.now += 1
        app.apply([mouse(15)])             # a minute on the charger: the next alert has its sound too
        self.assertEqual(len(self.played()), 3)
        self.assertEqual(len(app.notes), 3)

    def test_stops_above_the_threshold(self):
        app = self.app()
        app.apply([mouse(20)])
        self.now += REPEAT
        app.apply([mouse(22)])
        self.assertEqual(len(self.played()), 1)

    def test_stops_while_offline(self):
        app = self.app()
        app.apply([mouse(15)])
        self.now += REPEAT
        app.apply([mouse(15, online=False)])
        self.assertEqual(len(self.played()), 1)

    def test_stops_when_the_option_is_turned_off(self):
        app = self.app()
        app.apply([mouse(15)])
        app.cfg["low_sound"] = False
        self.now += REPEAT
        app.apply([mouse(15)])
        self.assertEqual(len(self.played()), 1)

    def test_a_sound_error_does_not_break_the_poll(self):
        self.ws.PlaySound.side_effect = RuntimeError("Failed to play sound")
        app = self.app()
        app.apply([mouse(15)])
        self.assertIn(KEY, app.icons)
        self.assertEqual(len(app.notes), 1)

    def test_setting_is_in_preferences_and_toggles(self):
        app = make_app()
        prefs = next(i for i in app.build_menu(None).items if i.text == "Preferences").submenu
        item = next(i for i in prefs.items if i.text == "Sound with the low battery alert")
        self.assertFalse(item.checked)
        item(None)
        self.assertTrue(app.cfg["low_sound"])
        self.assertTrue(self.saved[-1]["low_sound"])
        self.assertTrue(item.checked)

    def test_setting_is_read_from_the_settings_file(self):
        self.assertTrue(hb._valid_setting("low_sound", True))
        self.assertFalse(hb._valid_setting("low_sound", "yes"))


if __name__ == "__main__":
    unittest.main()
