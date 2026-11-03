import types
import unittest
from unittest import mock
from test_hide_rename import hb, make_app
from providers.base import DeviceStatus

class PausedIconTests(unittest.TestCase):
    def status(self, level=82, paused=True):
        return DeviceStatus("atk_keyboard:320f:5088", "ATK V75X", level,
                            False, not paused, "atk_keyboard", kind="keyboard",
                            polling_paused=paused)

    def icon(self, app):
        ic=hb.DeviceIcon.__new__(hb.DeviceIcon)
        ic.app=app
        ic.key="atk_keyboard:320f:5088"
        ic.status=ic.frames=ic._state=None
        ic._images={}
        ic.icon=types.SimpleNamespace(title="",visible=True,update_menu=mock.Mock())
        app.anim_tick=0
        return ic

    def test_idle_keeps_same_art_percentage_and_tooltip_but_refreshes_menu(self):
        app=make_app({"percent_in_icon":True})
        ic=self.icon(app)
        ic.update(self.status(paused=False))
        art=ic.icon.icon.tobytes()
        title=ic.icon.title
        calls=ic.icon.update_menu.call_count
        ic.update(self.status())
        self.assertEqual(ic.icon.icon.tobytes(),art)
        self.assertEqual(ic.icon.title,title)
        self.assertEqual(ic._state[7],"82")
        self.assertGreater(ic.icon.update_menu.call_count,calls)

    def test_pause_description_is_only_in_menu_header(self):
        app=make_app()
        owner=types.SimpleNamespace(status=self.status())
        self.assertIn("polling paused",app.build_menu(owner).items[0].detail())
        self.assertEqual(hb.device_state(owner.status),"82%")

    def test_pending_idle_icon_is_not_dimmed(self):
        app=make_app()
        ic=self.icon(app)
        ic.update(self.status(level=None))
        self.assertGreater(max(ic.icon.icon.getchannel("A").getdata()),200)
        self.assertNotIn("paused",ic.icon.title)

    def test_cached_low_value_does_not_issue_a_fresh_alert(self):
        app=make_app()
        app.check_alert(types.SimpleNamespace(icon=mock.Mock()),self.status(level=1))
        self.assertEqual(app.notes,[])
