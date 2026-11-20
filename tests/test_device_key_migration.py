"""One-time unambiguous provider identity migration, without devices or a UI."""
import threading
from types import SimpleNamespace
from unittest import mock
from test_hide_rename import HideRenameTestCase, hb, make_app

class DeviceKeyMigrationTests(HideRenameTestCase):
    def test_all_device_preferences_and_history_move_together(self):
        app=make_app()
        old,new="keychron:d028","keychron:d028:receiver-A"
        for field in ("names","hidden","icons","lows","controller_brands","controller_colors","custom_icons"):
            app.cfg[field]={old:"old-value"}
        app.history.record(old,60,False,True,1)
        app.migrate_device_keys({old:new})
        for field in ("names","hidden","icons","lows","controller_brands","controller_colors","custom_icons"):
            self.assertEqual(app.cfg[field],{new:"old-value"})
        self.assertNotIn(old,app.history.devices)
        self.assertIn(new,app.history.devices)
        self.assertIsNone(app.history.devices[new]["last"])
        self.assertEqual(len(self.saved),1)
        app.migrate_device_keys({old:new})
        self.assertEqual(len(self.saved),1)

    def test_existing_new_preferences_and_history_win_and_legacy_retires(self):
        app=make_app({"names":{"old":"previous","new":"current"}})
        app.history.record("old",80,False,True,1)
        app.history.record("new",30,False,True,2)
        app.migrate_device_keys({"old":"new"})
        self.assertEqual(app.cfg["names"],{"new":"current"})
        self.assertEqual(set(app.history.devices),{"new"})
        self.assertEqual(app.history.devices["new"]["samples"][-1][1],30)
        app.migrate_device_keys({"old":"different-receiver"})
        self.assertNotIn("different-receiver",app.history.devices)

    def test_invalid_config_or_alias_shapes_do_not_lose_preferences(self):
        app=make_app({"names":["damaged"],"custom_icons":{"old":"saved"}})
        for aliases in (None,[],{"old":None},{"old":"old"},{"": "new"}):
            app.migrate_device_keys(aliases)
        self.assertEqual(app.cfg["names"],["damaged"])
        self.assertEqual(app.cfg["custom_icons"],{"old":"saved"})
        self.assertFalse(self.saved)

    def test_cached_batch_alias_is_applied_before_results_are_presented(self):
        app=make_app({"bluetooth":False,"names":{"old":"My keyboard"}})
        app.bt_cache=[]
        app._polling_runtime=SimpleNamespace(consume=lambda:SimpleNamespace(
            hid=[],owners={},fresh_keys=set(),completed=set(),aliases={"old":"new"}))
        app.consume_polling()
        self.assertEqual(app.cfg["names"],{"new":"My keyboard"})

    def test_shutdown_refuses_late_migration_and_status_publication(self):
        app=make_app({"bluetooth":False,"names":{"old":"Saved"}})
        app.stop_evt=threading.Event()
        app.stop_evt.set()
        app.hid_results=[]
        app._polling_runtime=SimpleNamespace(consume=lambda:SimpleNamespace(
            hid=[object()],owners={},fresh_keys=set(),completed=set(),aliases={"old":"new"}))
        app.migrate_device_keys({"old":"new"})
        self.assertEqual(app.consume_polling(),[])
        self.assertEqual(app.cfg["names"],{"old":"Saved"})
        self.assertEqual(app.hid_results,[])
        self.assertFalse(self.saved)
