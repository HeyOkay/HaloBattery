"""Typed companion protocol and lifecycle tests without windows or hardware."""
import base64
import io
import json
from pathlib import Path
import queue
import subprocess
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
        devices=[dict(key="a", name="Mouse", status=status), dict(key="b", name="Keyboard", status=status)],
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
        return self.drain()

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

    def test_handshake_then_typed_state_without_forms_or_raw_status(self):
        welcome, state = self.hello()
        self.assertEqual(welcome, dict(type="welcome", protocol=1, sessionId=self.session.id))
        self.assertEqual(state["revision"], 1)
        self.assertEqual(state["selectedDeviceKey"], "a")
        self.assertEqual(state["statusPath"], "status.json")
        self.assertNotIn("pages", state)
        self.assertNotIn("names", state["settings"])
        self.assertNotIn("status", state["devices"][0])
        self.assertIsNone(state["devices"][0]["charging"])
        self.assertEqual(state["devices"][0]["level"], 42)
        self.assertEqual(len(state["options"]["languages"]), 6)
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
        self.assertEqual(self.drain(session)[-1]["deviceKey"], "b")

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
        self.assertEqual([m["type"] for m in self.drain(successor)], ["navigate"])

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
                self.assertTrue(all(type(value) in (bool, int, str) for value in state["settings"].values()))
                host.encode_message(state)

    def test_paths_are_bundle_or_explicit_source_override(self):
        with patch.object(host.sys, "frozen", True, create=True), patch.object(host.sys, "_MEIPASS", "bundle", create=True):
            self.assertEqual(host.executable_path(), Path("bundle/settings-ui/HaloBattery.Settings.exe"))
        with patch.dict(host.os.environ, {"HALOBATTERY_SETTINGS_EXE": "custom.exe"}):
            self.assertEqual(host.executable_path(), Path("custom.exe"))


if __name__ == "__main__":
    unittest.main()
