"""The wiring that makes the ported provider PRs reachable from the app."""
import unittest

import providers
from test_hide_rename import HideRenameTestCase, hb, make_app, dev
from dataclasses import replace

NEW = ["AulaProvider", "MchoseV9Provider", "DareuProvider", "ShieldProvider", "LamzuAtlantisProvider",
       "InphicProvider", "TurtleBeachProvider", "SkullcandyProvider", "RapooProvider", "AjazzProvider",
       "SonyInzoneProvider", "AtkNearlinkProvider", "FinalmouseProvider", "AtkKeyboardProvider",
       "HyperXAlphaWirelessProvider", "JabraProvider", "HyperXCloudFlightProvider", "MonsGeekProvider",
       "EweadnProvider", "MchoseKbProvider"]


class RegistrationTests(unittest.TestCase):
    def test_every_exported_provider_is_registered_once(self):
        classes = [type(p).__name__ for p in hb.make_providers()]
        self.assertEqual(len(classes), len(set(classes)))
        for name in NEW + ["LamzuProvider"]:
            self.assertIn(name, classes)
            self.assertTrue(hasattr(providers, name))

    def test_every_provider_name_has_a_label(self):
        names = {p.name for p in hb.make_providers()}
        missing = names - set(hb.PROVIDER_LABELS) - {"bluetooth"}
        self.assertEqual(missing, set())

    def test_aula_is_polled_before_lofree(self):
        order = [type(p).__name__ for p in hb.make_providers()]
        self.assertLess(order.index("AulaProvider"), order.index("LofreeProvider"))


class StateTests(HideRenameTestCase):
    def test_unknown_charge_state_is_stated_and_gives_no_time_left(self):
        st = replace(dev(), level=40, charging=False, charging_known=False)
        self.assertIn("charging status unknown", hb.device_state(st, "about 5 h left"))
        self.assertNotIn("5 h", hb.device_state(st, "about 5 h left"))

    def test_unknown_charge_state_never_alerts(self):
        app = make_app({"low": 50})
        app.apply([replace(dev(), level=10, charging_known=False)])
        self.assertEqual(app.notes, [])
        app = make_app({"low": 50})                      # the same level with a known state does alert
        app.apply([replace(dev(), level=10)])
        self.assertEqual(len(app.notes), 1)

    def test_paused_polling_shows_the_last_level_without_asleep_text(self):
        st = replace(dev(), level=64, online=False, polling_paused=True)
        self.assertEqual(hb.device_state(st), "64%")


if __name__ == "__main__":
    unittest.main()
