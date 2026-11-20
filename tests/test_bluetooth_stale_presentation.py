"""Last-known Bluetooth presentation with the original single-process poller."""
from dataclasses import replace
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import HideRenameTestCase, hb, make_app, dev


class BluetoothStalePresentationTests(HideRenameTestCase):
    def test_failed_reading_pauses_history_without_alerts(self):
        app = make_app()
        reading = replace(dev(level=50), key='bt:test', source='bluetooth')
        app.apply([reading])
        stale = replace(reading, level=10, online=False, charging_known=False, polling_stale=True)
        app.apply([stale])
        self.assertEqual(app.notes, [])
        self.assertIsNone(app.history.devices[reading.key]['last'])
        self.assertIn(reading.key, app.icons)
        self.assertTrue(app.status_data([stale])['devices'][0]['polling_stale'])

    def test_stale_bluetooth_does_not_hide_live_controller(self):
        controller = replace(dev(), key='xinput:0', source='xinput', kind='gamepad',
                             name='Xbox Wireless Controller', via='bluetooth')
        stale = replace(controller, key='bt:controller', source='bluetooth',
                        online=False, charging_known=False, polling_stale=True)
        app = make_app()
        app.bt_cache = [stale]
        app._bt_dup_logged = set()
        self.assertEqual(app.merge_bluetooth([controller]), [controller])

    def test_every_locale_labels_last_known_levels(self):
        for language in hb.i18n.LANGUAGES:
            for level in (None, 0, 100):
                stale = replace(dev(), level=level, polling_stale=True)
                text = hb.device_state(stale, language=language)
                self.assertTrue(text)
                self.assertNotIn('state.poll_', text)
                if level is not None:
                    self.assertIn(str(level), text)


if __name__ == '__main__':
    unittest.main()
