"""Typed companion protocol and lifecycle tests without windows or hardware."""
import base64
import io
import json
from pathlib import Path
import queue
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from halo import settings_host as host


def controller():
    model = Mock()
    status = SimpleNamespace(level=42, charging=None, online=True)
    model.snapshot.return_value = dict(cfg=dict(language="en", low=20, interval=60, icon_theme="auto",
        badges=True, percent_in_icon=False, animation=True, names={"a": "private"}),
        devices=[dict(key="a", name="Mouse", kind="mouse", status=status), dict(key="b", name="Keyboard", status=status)],
        providers=[dict(name="razer", label="Razer", enabled=True)], version="1.1.7",
        data_dir=Path("data"), status_path=Path("status.json"), autostart=False, icon_light_taskbar=False)
    model.preview.return_value = Image.new("RGBA", (80, 80), "red")
    return model


class Process:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = (io.StringIO() for _ in range(3))
        self.code = None
        self.killed = False
    def poll(self):
        return self.code
    def wait(self, timeout):
        self.code = 0
        return 0
    def kill(self):
        self.killed = True
        self.code = -1


class SettingsHostTests(unittest.TestCase):
    def setUp(self):
        self.controller = controller()
        self.manager = host.SettingsHost(self.controller)
        self.session = host._Session("general", None)
        self.manager._session = self.session

    def drain(self, session=None):
        result = []
        outgoing = (session or self.session).outgoing
        while not outgoing.empty():
            result.append(json.loads(outgoing.get_nowait()))
        return result

    def hello(self):
        self.manager._handle(self.session, dict(type="hello", protocol=1, uiVersion="1"))
        welcome, state, shown = self.drain()
        expected = dict(type="show", page=self.session.page, deviceKey=self.session.device_key,
                        requestId=self.session.show_generation)
        if self.session.restore_view:
            expected["restoreView"] = True
        self.assertEqual(shown, expected)
        self.manager._handle(self.session, dict(type="visibility", visible=True, requestId=shown["requestId"]))
        return welcome, state

    def command(self, name, args, request_id=1):
        self.manager._handle(self.session, dict(type="command", protocol=1, id=request_id, name=name, args=args))
        return self.drain()

    def test_wire_is_unicode_strict_and_bounded(self):
        wire = host.encode_message(dict(type="error", message="日本語 Français"))
        self.assertIn("日本語", wire)
        self.assertEqual(host.decode_message(wire)["message"], "日本語 Français")
        for raw in ('[]', 'null', '{}', '{"type":2}', '{"type":"set","value":NaN}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                host.decode_message(raw)
        with self.assertRaises(ValueError):
            host.decode_message('{"type":"x","x":"' + 'é' * host.MAX_MESSAGE + '"}')
        with self.assertRaises(ValueError):
            host.encode_message(dict(value=float('nan')))

    def test_reopen_grants_foreground_to_existing_companion_before_worker(self):
        self.hello()
        self.session.process = SimpleNamespace(pid=43210)
        order = []
        with patch.object(host, "allow_companion_foreground", side_effect=lambda pid: order.append(("grant", pid))), \
                patch.object(self.manager, "_thread", side_effect=lambda *args: order.append(("worker", args[2]))):
            self.assertTrue(self.manager.open("b", page="devices"))
        self.assertEqual(order, [("grant", 43210), ("worker", "settings-show")])
        self.assertEqual((self.session.page, self.session.device_key), ("devices", "b"))

    def test_refused_foreground_grant_does_not_block_settings_navigation(self):
        self.hello()
        self.session.process = SimpleNamespace(pid=43210)
        with patch.object(host, "allow_companion_foreground", return_value=False) as grant, \
                patch.object(self.manager, "_thread") as worker:
            self.assertTrue(self.manager.open("b", page="devices"))
        grant.assert_called_once_with(43210)
        worker.assert_called_once_with(self.manager._show, self.session, "settings-show")

    def test_foreground_grant_is_scoped_and_handles_unavailable_api(self):
        import ctypes
        with patch.object(host.sys, "platform", "win32"), patch.object(ctypes, "WinDLL", create=True) as library:
            for pid in (None, 0, -1, True, "123", 0xFFFFFFFF, 0x100000000):
                self.assertFalse(host.allow_companion_foreground(pid))
            library.assert_not_called()
            allow = library.return_value.AllowSetForegroundWindow
            allow.return_value = 1
            self.assertTrue(host.allow_companion_foreground(43210))
            allow.assert_called_once_with(43210)
            allow.return_value = 0
            self.assertFalse(host.allow_companion_foreground(43210))
            library.side_effect = OSError("unavailable")
            self.assertFalse(host.allow_companion_foreground(43210))
        with patch.object(host.sys, "platform", "linux"), patch.object(ctypes, "WinDLL", create=True) as library:
            self.assertFalse(host.allow_companion_foreground(43210))
            library.assert_not_called()

    def test_handshake_then_typed_state_without_forms_or_raw_status(self):
        welcome, state = self.hello()
        self.assertEqual(welcome, dict(type="welcome", protocol=1, sessionId=self.session.id))
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["selectedDeviceKey"], "a")
        self.assertEqual(state["statusPath"], "status.json")
        self.assertNotIn("pages", state)
        self.assertNotIn("names", state["settings"])
        self.assertNotIn("status", state["devices"][0])
        self.assertEqual(state["devices"][0]["kind"], "mouse")
        self.assertIsNone(state["devices"][0]["charging"])
        self.assertEqual(state["devices"][0]["level"], 42)
        self.assertEqual(len(state["options"]["languages"]), 7)
        self.manager._publish(self.session)
        self.assertEqual(self.drain(), [])

    def test_effective_taskbar_theme_change_publishes_new_boolean_state_revision(self):
        _, initial = self.hello()
        self.assertIs(initial["iconLightTaskbar"], False)
        self.controller.snapshot.return_value["icon_light_taskbar"] = True
        self.manager._publish(self.session)
        changed, = self.drain()
        self.assertIs(changed["iconLightTaskbar"], True)
        self.assertEqual(changed["revision"], initial["revision"] + 1)
        self.manager._publish(self.session)
        self.assertEqual(self.drain(), [])
        self.controller.preview.assert_not_called()

    def test_unknown_charge_remains_nullable_in_device_wire_state(self):
        status = self.controller.snapshot.return_value["devices"][0]["status"]
        for known, charging, expected in ((False, False, None), (False, True, None),
                                           (True, False, False), (True, True, True)):
            with self.subTest(known=known, charging=charging):
                status.charging_known = known
                status.charging = charging
                state = self.manager._state(self.session)
                self.assertIs(state["devices"][0]["charging"], expected)

    def test_last_reported_phone_exports_retained_level_with_offline_unknown_charging(self):
        from providers.base import DeviceStatus
        phone=DeviceStatus('phone_link:test','Phone',46,False,False,'phone_link',kind='phone',
            charging_known=False,ui_message='state.phone_link_last_reported',ui_params={'level':46})
        self.controller.snapshot.return_value['devices']=[dict(key=phone.key,name=phone.name,kind='phone',
            status=phone,state='46% (last reported by Phone Link)')]
        item=self.manager._state(self.session)['devices'][0]
        self.assertEqual(item['level'],46)
        self.assertFalse(item['online'])
        self.assertFalse(item['paused'])
        self.assertIsNone(item['charging'])
        self.assertIn('last reported',item['description'])

    def test_unavailable_saved_icon_is_explained_for_only_its_device_and_clears(self):
        model = self.controller.snapshot.return_value
        model["devices"][0].update(custom_icon_id="a" * 64, custom_icon_name="")
        model["devices"][1].update(custom_icon_id="b" * 64, custom_icon_name="test:keyboard")
        model["custom_icon_errors"] = ["a" * 64]
        _, state = self.hello()
        self.assertIs(state["devices"][0]["customIconUnavailable"], True)
        self.assertEqual(state["devices"][0]["customIconId"], "a" * 64)
        self.assertIs(state["devices"][1]["customIconUnavailable"], False)
        self.assertIn("settings.iconify_saved_unavailable", state["labels"])
        model["custom_icon_errors"].clear()
        model["devices"][0]["custom_icon_id"] = ""
        self.manager._publish(self.session)
        changed, = self.drain()
        self.assertIs(changed["devices"][0]["customIconUnavailable"], False)

    def test_handshake_required_and_version_is_strict_integer(self):
        with self.assertRaises(ValueError):
            self.command("refresh", {})
        with self.assertRaises(ValueError):
            self.manager._handle(self.session, dict(type="hello", protocol=True, uiVersion="x"))
        self.hello()
        with self.assertRaises(ValueError):
            self.hello()

    def test_device_key_is_bound_to_command(self):
        self.hello()
        self.session.device_key = "b"
        result, state = self.command("setDevice", dict(deviceKey="a", field="name", value="Mouse 2"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], state["revision"])
        self.controller.set_device.assert_called_once_with("a", "name", "Mouse 2")

    def test_commands_ack_errors_and_refuse_unknown_fields_or_ids(self):
        self.hello()
        cases = [("setGlobal", dict(key="names", value={})), ("setProvider", dict(name="razer", enabled=1)),
                 ("setDevice", dict(deviceKey="gone", field="name", value="x")),
                 ("setGlobal", dict(key="animation", value=True, extra=1)), ("bogus", {}),
                 ([], {})]
        for request_id, (name, args) in enumerate(cases, 1):
            result, _ = self.command(name, args, request_id)
            self.assertFalse(result["ok"])
            self.assertEqual(result["errorCode"], "invalidCommand")
        result, _ = self.command("refresh", {}, 1)
        self.assertFalse(result["ok"])
        self.controller.set_global.assert_not_called()
        self.controller.set_device.assert_not_called()

    def test_whats_new_is_acknowledged_before_its_background_work(self):
        self.hello()
        with patch.object(self.manager, "_thread") as worker:
            messages = self.command("showWhatsNew", {})
        self.assertTrue(messages[0]["ok"])
        self.assertEqual([m["type"] for m in messages], ["result", "state"])
        self.controller.action.assert_not_called()
        worker.assert_called_once_with(self.manager._show_whats_new, self.session, "settings-whats-new")
        target, session, _ = worker.call_args.args
        target(session)
        self.controller.action.assert_called_once_with("show_whats_new")

    def test_whats_new_rejects_unexpected_arguments(self):
        self.hello()
        with patch.object(self.manager, "_thread") as worker:
            result, _ = self.command("showWhatsNew", {"url": "https://example.com"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["errorCode"], "invalidCommand")
        worker.assert_not_called()
        self.controller.action.assert_not_called()

    def test_stale_whats_new_worker_does_not_open_a_window(self):
        self.manager._session = host._Session("about", None)
        self.manager._show_whats_new(self.session)
        self.controller.action.assert_not_called()

    def test_icon_content_uses_one_semantic_controller_operation(self):
        self.hello()
        self.command("setIconContent", dict(mode="percent"))
        self.controller.set_icon_content.assert_called_once_with("percent")
        self.controller.set_global.assert_not_called()

    def test_preview_is_exact_png_and_request_id_is_preserved(self):
        self.hello()
        self.manager._handle(self.session, dict(type="preview", id=8, deviceKey="b"))
        self.controller.preview.assert_called_once_with("b", size=80)
        reply, = self.drain()
        self.assertEqual(reply["id"], 8)
        self.assertEqual(reply["deviceKey"], "b")
        image = Image.open(io.BytesIO(base64.b64decode(reply["png"])))
        self.assertEqual(image.size, (80, 80))
        self.assertEqual(image.format, "PNG")

    def test_selection_normalizes_missing_devices_and_refuses_bad_levels(self):
        self.session.device_key = "gone"
        for level in (-1, 101, True, "42", None):
            self.controller.snapshot.return_value["devices"][0]["status"].level = level
            state = self.manager._state(self.session)
            self.assertIsNone(state["devices"][0]["level"])
            self.assertEqual(state["selectedDeviceKey"], "a")
        self.controller.snapshot.return_value["devices"] = []
        self.assertIsNone(self.manager._state(self.session)["selectedDeviceKey"])

    def test_open_has_no_process_or_pipe_io_and_reuses_one_session(self):
        manager = host.SettingsHost(self.controller)
        with patch.object(manager, "_thread") as thread:
            self.assertTrue(manager.open("a", page="devices"))
            session = manager._session
            self.assertTrue(manager.open("b", page="icons"))
            self.assertIs(manager._session, session)
            thread.assert_called_once()
        self.assertEqual(self.drain(session), [])
        self.assertEqual((session.page, session.device_key), ("icons", "b"))
        self.controller.snapshot.assert_not_called()

    def test_generic_cold_open_restores_native_saved_view(self):
        manager = host.SettingsHost(self.controller)
        with patch.object(manager, "_thread"):
            manager.open()
        session = manager._session
        manager._handle(session, dict(type="hello", protocol=1, uiVersion="1"))
        welcome, state, shown = self.drain(session)
        self.assertEqual(state["page"], "general")
        self.assertEqual(shown, dict(type="show", page="general", deviceKey=None, restoreView=True, requestId=1))
        self.assertTrue(session.restore_view)

    def test_generic_warm_open_preserves_view_and_explicit_navigation_overrides_it(self):
        self.session.page, self.session.device_key = "devices", "b"
        self.hello()
        with patch.object(self.manager, "_thread"):
            self.manager.open()
        self.assertEqual((self.session.page, self.session.device_key), ("devices", "b"))
        self.manager._show(self.session)
        self.assertEqual(self.drain(),
            [dict(type="show", page="general", deviceKey=None, restoreView=True, requestId=2)])
        with patch.object(self.manager, "_thread"):
            self.manager.open("a", page="icons")
        self.assertFalse(self.session.restore_view)
        self.manager._show(self.session)
        self.assertEqual(self.drain(), [dict(type="show", page="icons", deviceKey="a", requestId=3)])

    def test_explicit_device_without_page_retains_default_general_navigation(self):
        self.hello()
        with patch.object(self.manager, "_thread"):
            self.manager.open("b")
        self.manager._show(self.session)
        self.assertEqual(self.drain(), [dict(type="show", page="general", deviceKey="b", requestId=2)])

    def test_invalid_navigation_values_are_rejected_before_changing_session(self):
        for kwargs in (dict(page=""), dict(page="invalid"), dict(page=[]), dict(page=False),
                       dict(page=1), dict(device_key=2), dict(device_key=[])):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.manager.open(**kwargs)
        self.assertEqual((self.session.page, self.session.device_key), ("general", None))
        self.assertFalse(self.session.restore_view)

    def test_first_show_follows_latest_complete_state(self):
        manager = host.SettingsHost(self.controller)
        with patch.object(manager, "_thread"):
            manager.open("a", page="devices")
            manager.open("b", page="icons")
        session = manager._session
        manager._handle(session, dict(type="hello", protocol=1, uiVersion="1"))
        messages = self.drain(session)
        self.assertEqual([m["type"] for m in messages], ["welcome", "state", "show"])
        self.assertEqual(messages[1]["page"], "icons")
        self.assertEqual(messages[1]["selectedDeviceKey"], "b")
        self.assertEqual(messages[2], dict(type="show", page="icons", deviceKey="b", requestId=2))

    def test_hidden_reopen_reuses_host_and_only_shows_unchanged_state(self):
        self.hello()
        self.session.process = Process()
        self.manager._handle(self.session, dict(type="visibility", visible=False))
        with patch.object(self.manager, "_thread") as worker:
            self.manager.open("b", page="devices")
        worker.assert_called_once_with(self.manager._show, self.session, "settings-show")
        self.manager._show(self.session)
        self.assertEqual(self.drain(), [dict(type="show", page="devices", deviceKey="b", requestId=2)])
        self.assertIs(self.manager._session, self.session)
        self.assertFalse(self.session.stop.is_set())
        self.assertIsNone(self.session.process.poll())
        self.controller.preview.assert_not_called()

    def test_reopen_refreshes_changed_state_before_show_without_polling(self):
        self.hello()
        self.controller.snapshot.return_value["cfg"]["low"] = 25
        with patch.object(self.manager, "_thread"):
            self.manager.open(page="general")
        self.manager._show(self.session)
        state, shown = self.drain()
        self.assertEqual(state["type"], "state")
        self.assertEqual(state["settings"]["low"], 25)
        self.assertEqual(state["revision"], 2)
        self.assertEqual(shown["type"], "show")

    def test_closing_is_strict_and_requires_handshake(self):
        with self.assertRaises(ValueError):
            self.manager._handle(self.session, dict(type="closing"))
        self.hello()
        with self.assertRaises(ValueError):
            self.manager._handle(self.session, dict(type="closing", visible=False))
        self.assertFalse(self.session.closing)
        self.manager._handle(self.session, dict(type="closing"))
        self.assertTrue(self.session.closing)
        self.assertFalse(self.session.stop.is_set())

    def test_closing_drains_final_edit_before_eof_and_queued_reopen(self):
        self.hello()
        self.session.process = Process()
        self.session.process.stdout = io.StringIO(host.encode_message(dict(type="command", protocol=1,
            id=1, name="setDevice", args=dict(deviceKey="a", field="name", value="Final edit"))))
        self.manager._handle(self.session, dict(type="closing"))
        with patch.object(self.manager, "_thread") as worker:
            self.assertTrue(self.manager.open("b", page="devices"))
            worker.assert_not_called()
            self.assertIs(self.manager._session, self.session)
            self.manager._read(self.session)
            successor = self.manager._session
            worker.assert_called_once_with(self.manager._launch, successor, "settings-launch")
        self.controller.set_device.assert_called_once_with("a", "name", "Final edit")
        self.assertIsNot(successor, self.session)
        self.assertEqual((successor.page, successor.device_key), ("devices", "b"))
        self.assertFalse(successor.restore_view)
        self.assertEqual([message["type"] for message in self.drain()], ["result", "state"])

    def test_latest_open_during_closing_wins_including_generic_restore(self):
        for requests, expected in (([("devices", "b"), (None, None)], ("general", None, True)),
                                   ([(None, None), ("icons", "a")], ("icons", "a", False))):
            with self.subTest(requests=requests):
                manager = host.SettingsHost(self.controller)
                session = host._Session("about", "b", closing=True)
                manager._session = session
                with patch.object(manager, "_thread") as worker:
                    for page, device in requests:
                        self.assertTrue(manager.open(device, page=page))
                    worker.assert_not_called()
                    manager._cleanup(session)
                    successor = manager._session
                    worker.assert_called_once_with(manager._launch, successor, "settings-launch")
                self.assertEqual((successor.page, successor.device_key, successor.restore_view), expected)

    def test_show_worker_queued_before_closing_cannot_show_exiting_window(self):
        self.hello()
        with patch.object(self.manager, "_thread") as worker:
            self.manager.open("b", page="devices")
        target, session, _ = worker.call_args.args
        self.manager._handle(self.session, dict(type="closing"))
        target(session)
        self.assertEqual(self.drain(), [])
        with patch.object(self.manager, "_thread") as worker:
            self.manager._cleanup(self.session)
            successor = self.manager._session
            worker.assert_called_once_with(self.manager._launch, successor, "settings-launch")
        self.assertEqual((successor.page, successor.device_key), ("devices", "b"))

    def test_older_visibility_ack_cannot_consume_newer_scheduled_open(self):
        self.hello()
        with patch.object(self.manager, "_thread"):
            self.manager.open("b", page="devices")
        self.manager._handle(self.session, dict(type="visibility", visible=True, requestId=1))
        self.assertEqual(self.session.pending_show, (2, "devices", "b"))
        self.manager._handle(self.session, dict(type="closing"))
        self.assertEqual(self.manager._pending_open, ("devices", "b"))

    def test_unversioned_startup_splash_ack_does_not_consume_newer_open(self):
        manager = host.SettingsHost(self.controller)
        with patch.object(manager, "_thread"):
            manager.open()
            session = manager._session
            session.ready.set()
            manager.open("b", page="devices")
        manager._handle(session, dict(type="visibility", visible=True))
        self.assertEqual(session.pending_show, (2, "devices", "b"))
        manager._handle(session, dict(type="closing"))
        self.assertEqual(manager._pending_open, ("devices", "b"))

    def test_user_close_during_initial_loading_does_not_reopen(self):
        manager = host.SettingsHost(self.controller)
        with patch.object(manager, "_thread") as worker:
            manager.open()
            session = manager._session
            session.ready.set()
            manager._handle(session, dict(type="visibility", visible=True))
            self.assertIsNone(session.pending_show)
            manager._handle(session, dict(type="closing"))
            worker.reset_mock()
            manager._cleanup(session)
            worker.assert_not_called()
        self.assertIsNone(manager._session)

    def test_show_workers_coalesce_latest_request_and_match_visibility_ack(self):
        self.hello()
        with patch.object(self.manager, "_thread"):
            self.manager.open("b", page="devices")
            self.manager.open("a", page="icons")
        self.manager._show(self.session)
        self.manager._show(self.session)
        self.assertEqual(self.drain(), [dict(type="show", page="icons", deviceKey="a", requestId=3)])
        self.manager._handle(self.session, dict(type="visibility", visible=True, requestId=2))
        self.assertEqual(self.session.pending_show, (3, "icons", "a"))
        self.manager._handle(self.session, dict(type="visibility", visible=True, requestId=3))
        self.assertIsNone(self.session.pending_show)

    def test_unacknowledged_show_reopens_after_simultaneous_close(self):
        for device, page, expected in (("b", "devices", ("devices", "b", False)),
                                       (None, None, ("general", None, True))):
            with self.subTest(page=page):
                manager = host.SettingsHost(self.controller)
                session = host._Session("general", None)
                manager._session = session
                manager._handle(session, dict(type="hello", protocol=1, uiVersion="1"))
                self.drain(session)
                manager._handle(session, dict(type="visibility", visible=True))
                with patch.object(manager, "_thread") as worker:
                    manager.open(device, page=page)
                    manager._show(session)
                    manager._handle(session, dict(type="closing"))
                    worker.reset_mock()
                    manager._cleanup(session)
                    successor = manager._session
                    worker.assert_called_once_with(manager._launch, successor, "settings-launch")
                self.assertEqual((successor.page, successor.device_key, successor.restore_view), expected)
                self.assertIsNone(session.pending_show)

    def test_acknowledged_show_does_not_reopen_after_user_close(self):
        self.hello()
        with patch.object(self.manager, "_thread") as worker:
            self.manager.open("b", page="devices")
            self.manager._show(self.session)
            self.assertEqual(self.session.pending_show, (2, "devices", "b"))
            self.manager._handle(self.session, dict(type="visibility", visible=True, requestId=2))
            self.assertIsNone(self.session.pending_show)
            self.manager._handle(self.session, dict(type="closing"))
            worker.reset_mock()
            self.manager._cleanup(self.session)
            worker.assert_not_called()
        self.assertIsNone(self.manager._session)

    def test_acknowledged_initial_show_does_not_reopen_after_user_close(self):
        self.hello()
        self.assertIsNone(self.session.pending_show)
        self.manager._handle(self.session, dict(type="closing"))
        with patch.object(self.manager, "_thread") as worker:
            self.manager._cleanup(self.session)
        worker.assert_not_called()
        self.assertIsNone(self.manager._session)

    def test_failed_show_enqueue_is_not_remembered_for_reopen(self):
        self.hello()
        with patch.object(self.manager, "_thread"):
            self.manager.open(page="general")
        with patch.object(self.manager, "_send", return_value=False):
            self.manager._show_window(self.session)
        self.assertIsNone(self.session.pending_show)
        self.manager._handle(self.session, dict(type="closing"))
        with patch.object(self.manager, "_thread") as worker:
            self.manager._cleanup(self.session)
        worker.assert_not_called()
        self.assertIsNone(self.manager._session)

    def test_host_quit_cancels_pending_reopen(self):
        self.hello()
        self.manager._handle(self.session, dict(type="closing"))
        with patch.object(self.manager, "_thread") as worker:
            self.manager.open("b", page="devices")
            self.manager.close()
            self.manager._cleanup(self.session)
        worker.assert_not_called()
        self.assertIsNone(self.manager._session)
        self.assertIsNone(self.manager._pending_open)
        self.assertFalse(self.manager.open())

    def test_final_quit_command_cancels_pending_reopen_after_ack(self):
        self.hello()
        self.session.process = Process()
        self.controller.action.side_effect = lambda name: self.manager.close()
        self.manager._handle(self.session, dict(type="closing"))
        with patch.object(self.manager, "_thread") as worker:
            self.manager.open("b", page="devices")
            messages = self.command("quit", {})
        worker.assert_not_called()
        self.assertEqual([item["type"] for item in messages], ["result", "state", "shutdown"])
        self.assertTrue(messages[0]["ok"])
        self.assertIsNone(self.manager._session)
        self.assertIsNone(self.manager._pending_open)

    def test_host_quit_during_child_reap_prevents_pending_reopen(self):
        self.hello()
        self.session.process = Process()
        self.session.process.wait = Mock(side_effect=lambda timeout: self.manager.close())
        self.manager._handle(self.session, dict(type="closing"))
        with patch.object(self.manager, "_thread") as worker:
            self.manager.open()
            self.manager._cleanup(self.session)
        worker.assert_not_called()
        self.assertIsNone(self.manager._session)
        self.assertIsNone(self.manager._pending_open)

    def test_reopen_keeps_cached_window_usable_when_snapshot_temporarily_fails(self):
        self.hello()
        self.controller.snapshot.side_effect = OSError("temporary snapshot failure")
        with patch.object(self.manager, "_thread"):
            self.manager.open(page="general")
        with self.assertLogs("halo_battery", level="ERROR"):
            self.manager._show(self.session)
        self.assertEqual(self.drain(), [dict(type="show", page="general", deviceKey=None, requestId=2)])
        self.assertIs(self.manager._session, self.session)
        self.assertFalse(self.session.stop.is_set())
        self.assertEqual(self.session.revision, 1)

    def test_navigation_does_not_publish_a_duplicate_model_on_next_poll(self):
        self.hello()
        self.session.page, self.session.device_key = "icons", "b"
        self.manager._publish(self.session)
        self.assertEqual(self.drain(), [])
        self.assertEqual(self.session.revision, 1)

    def test_visibility_is_strict_and_requires_handshake(self):
        with self.assertRaises(ValueError):
            self.manager._handle(self.session, dict(type="visibility", visible=False))
        self.hello()
        for message in (dict(type="visibility", visible=0), dict(type="visibility"),
                        dict(type="visibility", visible=False, id=1),
                        dict(type="visibility", visible=True, requestId=True),
                        dict(type="visibility", visible=True, requestId=0),
                        dict(type="visibility", visible=True, requestId="1")):
            with self.subTest(message=message), self.assertRaises(ValueError):
                self.manager._handle(self.session, message)
        self.manager._handle(self.session, dict(type="visibility", visible=True))
        self.assertTrue(self.session.visible)
        self.assertEqual(self.drain(), [])

    def test_hide_disposes_only_temporary_catalog_and_suppresses_old_replies(self):
        self.hello()
        with patch("halo.iconify_host.IconCatalogJobs") as jobs_type:
            first = self.manager._ensure_icon_jobs(self.session)
            current = jobs_type.call_args.args[3]
            self.assertTrue(current())
            self.manager._handle(self.session, dict(type="visibility", visible=False))
            first.close.assert_called_once_with()
            self.assertIsNone(self.session.icon_jobs)
            self.assertFalse(current())
            self.assertIs(self.manager._session, self.session)
            self.manager._ensure_icon_jobs(self.session)
            self.assertEqual(jobs_type.call_count, 2)
            self.assertTrue(jobs_type.call_args.args[3]())
        self.controller.action.assert_not_called()
        self.controller.set_device.assert_not_called()

    def test_bootstrap_profile_uses_exact_portable_context_without_snapshot(self):
        portable = Path("portable profile 日本語")
        self.controller.context = {"data_dir": portable}
        self.assertEqual(self.manager._launch_args(), [str(host.executable_path()),
            "--data-dir", str(portable.resolve())])
        self.controller.snapshot.assert_not_called()

    def test_bootstrap_profile_falls_back_for_custom_controller(self):
        self.assertEqual(self.manager._launch_args()[1:],
                         ["--data-dir", str(Path("data").resolve())])

    def test_stale_show_worker_cannot_restore_successor(self):
        self.hello()
        self.manager._session = host._Session("about", None)
        self.controller.snapshot.reset_mock()
        self.manager._show(self.session)
        self.controller.snapshot.assert_not_called()
        self.assertEqual(self.drain(), [])

    def test_failed_spawn_is_retryable_and_does_not_touch_successor(self):
        manager = host.SettingsHost(self.controller, popen=Mock(side_effect=OSError("missing")))
        with patch.object(manager, "_thread"):
            manager.open()
            old = manager._session
            with self.assertLogs("halo_battery", level="ERROR"):
                manager._launch(old)
            self.assertIsNone(manager._session)
            manager.open()
        successor = manager._session
        manager._send(old, {"type": "shutdown"})
        manager._cleanup(old)
        self.assertIs(manager._session, successor)
        self.assertEqual(self.drain(successor), [])

    def test_spawn_uses_private_utf8_handles_and_timeout_reaps_child(self):
        process = Process()
        popen = Mock(return_value=process)
        manager = host.SettingsHost(self.controller, popen=popen, startup_timeout=0)
        manager._session = self.session
        with patch.object(manager, "_thread"):
            manager._launch(self.session)
        kwargs = popen.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        for stream in ("stdin", "stdout", "stderr"):
            self.assertEqual(kwargs[stream], subprocess.PIPE)
        with self.assertLogs("halo_battery", level="WARNING"):
            manager._poll(self.session)
        self.assertIsNone(manager._session)
        self.assertEqual(process.code, 0)

    def test_eof_reaps_child_and_stderr_is_logged(self):
        self.session.process = Process()
        self.session.process.stderr = io.StringIO("native diagnostic\n")
        with self.assertLogs("halo_battery", level="WARNING") as logs:
            self.manager._stderr(self.session)
        self.assertIn("native diagnostic", logs.output[0])
        self.manager._read(self.session)
        self.assertIsNone(self.manager._session)
        self.controller.action.assert_not_called()

    def test_shutdown_queues_without_writing_and_kills_only_owned_child(self):
        process = Process()
        process.wait = Mock(side_effect=[subprocess.TimeoutExpired("settings", .6), 0])
        self.session.process = process
        self.manager.close()
        self.assertTrue(process.killed)
        self.assertEqual(self.drain(), [{"type": "shutdown"}])
        self.assertIsNone(self.manager._session)
        self.assertFalse(self.manager.open())

    def test_quit_ack_is_queued_before_app_shutdown_without_joining_reader(self):
        self.hello()
        self.session.process = Process()
        self.controller.action.side_effect = lambda name: self.manager.close()
        self.manager._handle(self.session, dict(type="command", protocol=1, id=1, name="quit", args={}))
        messages = self.drain()
        self.assertEqual([item["type"] for item in messages], ["result", "state", "shutdown"])
        self.assertTrue(messages[0]["ok"])
        self.controller.action.assert_called_once_with("quit")

    def test_queue_backpressure_is_nonblocking_and_session_local(self):
        for _ in range(32):
            self.assertTrue(self.manager._send(self.session, {"type": "x"}))
        with patch.object(self.manager, "_thread") as cleanup, self.assertLogs("halo_battery", level="WARNING"):
            self.assertFalse(self.manager._send(self.session, {"type": "x"}))
            cleanup.assert_called_once()

    def test_old_session_cannot_mutate_or_publish_to_successor(self):
        self.hello()
        successor = host._Session("devices", "b")
        self.manager._session = successor
        self.manager._handle(self.session, dict(type="command", protocol=1, id=1,
                                               name="setGlobal", args=dict(key="animation", value=False)))
        self.controller.set_global.assert_not_called()
        self.assertEqual(self.drain(successor), [])

    def test_all_languages_have_cached_catalog_and_only_primitive_settings(self):
        for language in host.i18n.LANGUAGES:
            with self.subTest(language=language):
                self.controller.snapshot.return_value["cfg"]["language"] = language
                state = self.manager._state(self.session)
                self.assertIs(state["labels"], self.session.catalogs[language])
                self.assertIn("settings.general", state["labels"])
                self.assertTrue(state["labels"]["whatsnew.title"])
                self.assertTrue(all(type(value) in (bool, int, str) for value in state["settings"].values()))
                host.encode_message(state)

    def test_ready_callback_waits_for_first_visible_ack_and_runs_once(self):
        ready = Mock()
        manager = host.SettingsHost(self.controller, on_ready=ready)
        manager._session = self.session
        with patch.object(manager, "_thread"):
            manager._handle(self.session, dict(type="hello", protocol=1, uiVersion="1"))
            manager._dispatch_notifications()
            ready.assert_not_called()
            for visible in (False, True, True, False, True):
                manager._handle(self.session, dict(type="visibility", visible=visible))
            manager._dispatch_notifications()
        ready.assert_called_once_with()
        self.assertTrue(self.session.first_visible.is_set())

    def test_lifecycle_callbacks_run_outside_host_and_model_locks(self):
        observed = []
        def callback(*args):
            def check_locks():
                locks = (manager._lock, manager._model_lock)
                acquired = [lock.acquire(blocking=False) for lock in locks]
                observed.append(acquired)
                for lock, held in zip(locks, acquired):
                    if held:
                        lock.release()
            worker = threading.Thread(target=check_locks)
            worker.start()
            worker.join(timeout=1)
            observed.append(not worker.is_alive())
        manager = host.SettingsHost(self.controller, on_ready=callback, on_failure=callback)
        manager._session = self.session
        with patch.object(manager, "_thread"):
            manager._handle(self.session, dict(type="hello", protocol=1, uiVersion="1"))
            manager._handle(self.session, dict(type="visibility", visible=True))
            manager._dispatch_notifications()
            manager._cleanup(self.session, failure="unexpectedExit")
            manager._dispatch_notifications()
        self.assertEqual(observed, [[True, True], True, [True, True], True])

    def test_spawn_failure_callback_runs_once_and_can_retry(self):
        failed = []
        def on_failure(reason):
            failed.append(reason)
            self.assertTrue(manager.open(page="advanced"))
        manager = host.SettingsHost(self.controller, popen=Mock(side_effect=FileNotFoundError("missing")),
                                    on_failure=on_failure)
        with patch.object(manager, "_thread"):
            manager.open()
            old = manager._session
            with self.assertLogs("halo_battery", level="ERROR"):
                manager._launch(old)
            self.assertIsNone(manager._session)
            manager._dispatch_notifications()
            successor = manager._session
            manager._cleanup(old, failure="unexpectedExit")
            manager._dispatch_notifications()
        self.assertEqual(failed, ["launchFailed"])
        self.assertIsNot(successor, old)
        self.assertEqual(successor.page, "advanced")

    def test_reader_reports_protocol_failure_and_unexpected_eof(self):
        for wire, reason in (("not JSON\n", "protocolError"),
                             ('{"type":"hello","protocol":2,"uiVersion":"1"}\n', "protocolError"),
                             ("", "unexpectedExit")):
            with self.subTest(reason=reason, wire=wire):
                failed = Mock()
                manager = host.SettingsHost(self.controller, on_failure=failed)
                session = host._Session("general", None, process=Process())
                session.process.stdout = io.StringIO(wire)
                manager._session = session
                with patch.object(manager, "_thread"):
                    if reason == "protocolError":
                        with self.assertLogs("halo_battery", level="WARNING"):
                            manager._read(session)
                    else:
                        manager._read(session)
                    manager._cleanup(session, failure="unexpectedExit")
                    manager._dispatch_notifications()
                failed.assert_called_once_with(reason)

    def test_writer_failure_reports_transport_error(self):
        failed = Mock()
        self.manager._on_failure = failed
        self.session.process = Process()
        self.session.process.stdin.close()
        self.manager._send(self.session, {"type": "welcome"})
        with patch.object(self.manager, "_thread"):
            self.manager._write(self.session)
            self.manager._dispatch_notifications()
        failed.assert_called_once_with("transportError")

    def test_reaping_errors_still_report_failure_and_allow_retry(self):
        for operation in ("wait", "kill", "wait_after_kill", "poll"):
            with self.subTest(operation=operation):
                failed = Mock()
                manager = host.SettingsHost(self.controller, on_failure=failed)
                process = Process()
                if operation == "wait":
                    process.wait = Mock(side_effect=OSError("invalid process handle"))
                elif operation in ("kill", "wait_after_kill"):
                    process.wait = Mock(side_effect=[subprocess.TimeoutExpired("settings", .1),
                                                    OSError("invalid process handle"), 0])
                    if operation == "kill":
                        process.kill = Mock(side_effect=PermissionError("access denied"))
                else:
                    process.poll = Mock(side_effect=OSError("invalid process handle"))
                session = host._Session("general", None, process=process)
                manager._session = session
                with patch.object(manager, "_thread"), self.assertLogs("halo_battery", level="WARNING"):
                    manager._cleanup(session, failure="unexpectedExit")
                    manager._dispatch_notifications()
                    self.assertIsNone(manager._session)
                    self.assertTrue(session.stop.is_set())
                    self.assertTrue(manager.open())
                    successor = manager._session
                    manager._cleanup(session, failure="unexpectedExit")
                    manager._dispatch_notifications()
                failed.assert_called_once_with("unexpectedExit")
                self.assertIsNot(successor, session)
                self.assertIs(manager._session, successor)

    def test_startup_watchdog_requires_hello_and_visible_ack(self):
        for handshake, reason in ((False, "startupTimeout"), (True, "visibilityTimeout")):
            with self.subTest(handshake=handshake):
                failed = Mock()
                ready = Mock()
                manager = host.SettingsHost(self.controller, startup_timeout=0,
                                            on_failure=failed, on_ready=ready)
                session = host._Session("general", None, process=Process())
                manager._session = session
                with patch.object(manager, "_thread"):
                    if handshake:
                        manager._handle(session, dict(type="hello", protocol=1, uiVersion="1"))
                    with self.assertLogs("halo_battery", level="WARNING"):
                        manager._poll(session)
                    manager._dispatch_notifications()
                failed.assert_called_once_with(reason)
                ready.assert_not_called()
                self.assertTrue(session.stop.is_set())
                self.assertIsNone(manager._session)

    def test_intentional_close_and_shutdown_suppress_failure_callbacks(self):
        for shutdown in (False, True):
            with self.subTest(shutdown=shutdown):
                failed = Mock()
                manager = host.SettingsHost(self.controller, on_failure=failed)
                session = host._Session("general", None, process=Process())
                manager._session = session
                with patch.object(manager, "_thread"):
                    manager._handle(session, dict(type="hello", protocol=1, uiVersion="1"))
                    manager._handle(session, dict(type="visibility", visible=True))
                    if shutdown:
                        manager.close()
                    else:
                        manager._handle(session, dict(type="closing"))
                    manager._read(session)
                    manager._dispatch_notifications()
                failed.assert_not_called()

    def test_shutdown_during_reap_suppresses_failure_callback(self):
        failed = Mock()
        self.manager._on_failure = failed
        self.session.process = Process()
        self.session.process.wait = Mock(side_effect=lambda timeout: self.manager.close())
        with patch.object(self.manager, "_thread"):
            self.manager._cleanup(self.session, failure="unexpectedExit")
            self.manager._dispatch_notifications()
        failed.assert_not_called()

    def test_queued_callbacks_are_discarded_after_close_or_superseding_open(self):
        for successor in (False, True):
            for failure in (False, True):
                with self.subTest(successor=successor, failure=failure):
                    failed, ready = Mock(), Mock()
                    manager = host.SettingsHost(self.controller, on_failure=failed, on_ready=ready)
                    session = host._Session("general", None)
                    manager._session = session
                    with patch.object(manager, "_thread"):
                        manager._handle(session, dict(type="hello", protocol=1, uiVersion="1"))
                        manager._handle(session, dict(type="visibility", visible=True))
                        if failure:
                            manager._cleanup(session, failure="unexpectedExit")
                        if successor:
                            session.stop.set()
                            manager.open()
                        else:
                            manager.close()
                        manager._dispatch_notifications()
                    failed.assert_not_called()
                    ready.assert_not_called()

    def test_pending_successor_owns_failure_reporting(self):
        failed, ready = Mock(), Mock()
        manager = host.SettingsHost(self.controller, on_failure=failed, on_ready=ready)
        manager._session = self.session
        manager._pending_open = ("advanced", None)
        with patch.object(manager, "_thread"):
            manager._cleanup(self.session, failure="unexpectedExit")
            successor = manager._session
            manager._handle(successor, dict(type="hello", protocol=1, uiVersion="1"))
            manager._handle(successor, dict(type="visibility", visible=True))
            manager._dispatch_notifications()
        failed.assert_not_called()
        ready.assert_called_once_with()

    def test_ready_and_failure_are_delivered_in_order(self):
        entered, release, completed = (threading.Event() for _ in range(3))
        calls = []
        def on_ready():
            calls.append("ready")
            entered.set()
            release.wait(timeout=2)
        def on_failure(reason):
            calls.append(reason)
            completed.set()
        manager = host.SettingsHost(self.controller, on_ready=on_ready, on_failure=on_failure)
        manager._session = self.session
        manager._handle(self.session, dict(type="hello", protocol=1, uiVersion="1"))
        manager._handle(self.session, dict(type="visibility", visible=True))
        try:
            self.assertTrue(entered.wait(timeout=1))
            manager._cleanup(self.session, failure="unexpectedExit")
            self.assertEqual(calls, ["ready"])
        finally:
            release.set()
        self.assertTrue(completed.wait(timeout=1))
        self.assertEqual(calls, ["ready", "unexpectedExit"])

    def test_failed_session_cannot_notify_after_newer_visible_success(self):
        entered, release, completed = (threading.Event() for _ in range(3))
        calls = []
        def on_failure(reason):
            calls.append(reason)
            entered.set()
            release.wait(timeout=2)
        def on_ready():
            calls.append("ready")
            completed.set()
        manager = host.SettingsHost(self.controller, on_ready=on_ready, on_failure=on_failure)
        manager._session = self.session
        manager._cleanup(self.session, failure="unexpectedExit")
        try:
            self.assertTrue(entered.wait(timeout=1))
            # Start the next session without launching a real process. Its ready
            # notification must wait behind the already running failure callback.
            with patch.object(manager, "_thread"):
                manager.open()
                successor = manager._session
                manager._handle(successor, dict(type="hello", protocol=1, uiVersion="1"))
                manager._handle(successor, dict(type="visibility", visible=True))
            self.assertEqual(calls, ["unexpectedExit"])
        finally:
            release.set()
        self.assertTrue(completed.wait(timeout=1))
        self.assertEqual(calls, ["unexpectedExit", "ready"])

    def test_throwing_callback_does_not_strand_later_notifications(self):
        failed = Mock()
        self.manager._on_ready = Mock(side_effect=RuntimeError("callback failed"))
        self.manager._on_failure = failed
        with patch.object(self.manager, "_thread"):
            self.hello()
            with self.assertLogs("halo_battery", level="ERROR"):
                self.manager._dispatch_notifications()
            self.manager._cleanup(self.session, failure="unexpectedExit")
            self.manager._dispatch_notifications()
        failed.assert_called_once_with("unexpectedExit")

    def test_paths_are_bundle_or_explicit_source_override(self):
        with patch.object(host.sys, "frozen", True, create=True), patch.object(host.sys, "_MEIPASS", "bundle", create=True):
            self.assertEqual(host.executable_path(), Path("bundle/settings-ui/HaloBattery.Settings.exe"))
        with patch.dict(host.os.environ, {"HALOBATTERY_SETTINGS_EXE": "custom.exe"}):
            self.assertEqual(host.executable_path(), Path("custom.exe"))


if __name__ == "__main__":
    unittest.main()
