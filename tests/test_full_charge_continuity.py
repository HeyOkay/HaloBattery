"""Full-charge alerts cannot cross a disconnect or stale transport gap."""
from dataclasses import replace
from unittest import mock
from test_hide_rename import HideRenameTestCase, make_app, dev

class FullChargeContinuityTests(HideRenameTestCase):
    def app(self, keep=False):
        app=make_app({"full_alert":True,"keep_disconnected":keep})
        app.notify=mock.Mock()
        return app

    def test_reconnection_at_full_is_not_a_new_full_charge(self):
        for keep in (False,True):
            with self.subTest(keep=keep):
                app=self.app(keep)
                app.apply([replace(dev(level=50),charging=True)])
                app.apply([])
                app.apply([])
                app.apply([dev(level=100)])
                app.notify.assert_not_called()

    def test_offline_or_stale_observation_breaks_charge_continuity(self):
        for gap in (replace(dev(),online=False),replace(dev(),polling_stale=True)):
            with self.subTest(gap=gap):
                app=self.app()
                app.apply([replace(dev(level=50),charging=True)])
                app.apply([gap])
                app.apply([dev(level=100)])
                app.notify.assert_not_called()

    def test_another_provider_completion_keeps_valid_charge_continuity(self):
        app=self.app()
        app.apply([replace(dev(level=50),charging=True)])
        app.apply([],may_go=lambda key:False)
        app.apply([dev(level=100)])
        self.assertEqual(app.notify.call_count,1)
        self.assertEqual(app.notify.call_args.kwargs["kind"],"full")

    def test_continuous_charge_still_alerts_only_once(self):
        app=self.app()
        for level in (50,99,100,99,100):
            app.apply([replace(dev(level=level),charging=True)])
        self.assertEqual(app.notify.call_count,1)

    def test_online_unknown_samples_wait_without_losing_confirmed_charge(self):
        for gap in (replace(dev(level=100),charging_known=False),dev(level=None)):
            with self.subTest(gap=gap):
                app=self.app()
                app.apply([replace(dev(level=50),charging=True)])
                app.apply([gap])
                app.notify.assert_not_called()
                app.apply([dev(level=100)])
                self.assertEqual(app.notify.call_count,1)
