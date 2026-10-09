"""A valid percentage with unknown charging must not produce misleading alerts."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_hide_rename import HideRenameTestCase, hb, make_app
from providers.base import DeviceStatus


def pad(level=19, known=False, charging=False):
    return DeviceStatus("shield:one", "NVIDIA SHIELD Controller", level,
                        charging, source="shield", kind="gamepad",
                        charging_known=known)


class UnknownChargingTests(HideRenameTestCase):
    def test_keeps_percentage_and_explains_unknown_state(self):
        app = make_app()
        app.apply([pad()])
        self.assertEqual(app.icons["shield:one"].status.level, 19)
        self.assertEqual(hb.describe(pad(), left="about 5 h of use left"),
                         "NVIDIA SHIELD Controller: 19%, charging status unknown")

    def test_no_low_alert_or_sound_until_noncharging_confirmed(self):
        app = make_app({"low_sound": True})
        with mock.patch.object(hb, "play_low_sound") as sound:
            app.apply([pad()])
            self.assertEqual(app.notes, [])
            sound.assert_not_called()
            app.apply([pad(known=True)])
            self.assertEqual(len(app.notes), 1)
            sound.assert_called_once()

    def test_unknown_does_not_reset_existing_alert(self):
        app = make_app()
        for known in (True, False, True):
            app.apply([pad(known=known)])
        self.assertEqual(len(app.notes), 1)

    def test_no_full_charge_alert_until_state_confirmed(self):
        app = make_app()
        app.apply([pad(99, known=True, charging=True)])
        app.apply([pad(100)])
        self.assertEqual(app.notes, [])
        app.apply([pad(100, known=True)])
        self.assertEqual(len(app.notes), 1)

    def test_unknown_pauses_drain_history_and_estimate(self):
        app = make_app()
        app.apply([pad(70, known=True)])
        app.apply([pad(69)])
        self.assertIsNone(app.history.devices["shield:one"]["last"])
        self.assertEqual(app.time_left_text(pad(69)), "")

    def test_pending_low_alert_dropped_when_charging_becomes_unknown(self):
        app = make_app()
        app.apply([pad()])
        app.held[("shield:one", "Low battery")] = ("Time to charge", None)
        with mock.patch.object(app, "notify_any") as notify:
            app.flush_held()
            notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
