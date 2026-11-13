"""WinUI companion transport over inherited private UTF-8 standard handles."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import io
import json
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid

from . import i18n

log = logging.getLogger("halo_battery")
MAX_MESSAGE = 1024 * 1024
PAGES = frozenset(("general", "devices", "icons", "notifications", "advanced", "about"))
GLOBAL_KEYS = frozenset(("language", "interval", "low", "bluetooth", "keep_disconnected", "filled_icons",
    "badges", "animation", "time_left", "icon_theme", "full_alert", "low_sound", "quiet_fullscreen",
    "playstation_full_mode", "status_file", "update_check", "percent_in_icon"))


def executable_path():
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "settings-ui" / "HaloBattery.Settings.exe"
    override = os.environ.get("HALOBATTERY_SETTINGS_EXE")
    return Path(override) if override else Path(__file__).resolve().parent.parent / ".build" / "settings-ui" / "HaloBattery.Settings.exe"


def encode_message(message):
    return json.dumps(message, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def decode_message(line):
    if len(line.encode("utf-8")) > MAX_MESSAGE:
        raise ValueError("settings message too large")
    def invalid(value):
        raise ValueError("invalid JSON number")
    message = json.loads(line, parse_constant=invalid)
    if not isinstance(message, dict) or not isinstance(message.get("type"), str):
        raise ValueError("invalid settings message")
    return message


@dataclass
class _Session:
    page: str
    device_key: str | None
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    outgoing: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=32))
    stop: threading.Event = field(default_factory=threading.Event)
    ready: threading.Event = field(default_factory=threading.Event)
    process: object = None
    cleanup_lock: threading.Lock = field(default_factory=threading.Lock)
    revision: int = 0
    last_state: str | None = None
    last_command: int = -1
    catalogs: dict = field(default_factory=dict)


class SettingsHost:
    """Each session owns its process, queue, workers, and handshake deadline."""
    def __init__(self, controller, *, popen=None, poll_interval=.75, startup_timeout=15):
        self.controller = controller
        self._popen = popen or subprocess.Popen
        self._poll_interval = poll_interval
        self._startup_timeout = startup_timeout
        self._lock = threading.Lock()
        self._model_lock = threading.RLock()
        self._session = None
        self._closed = False

    def open(self, device_key=None, *, page="general"):
        if page not in PAGES or device_key is not None and not isinstance(device_key, str):
            raise ValueError("invalid settings navigation")
        with self._lock:
            if self._closed:
                return False
            session = self._session
            if session is None or session.stop.is_set():
                session = _Session(page, device_key)
                self._session = session
                self._thread(self._launch, session, "settings-launch")
            else:
                session.page, session.device_key = page, device_key
            self._send(session, dict(type="navigate", page=page, deviceKey=device_key))
        return True

    @staticmethod
    def _thread(target, session, name):
        threading.Thread(target=target, args=(session,), daemon=True, name=name).start()

    def _current(self, session):
        with self._lock:
            return self._session is session and not session.stop.is_set()

    def _send(self, session, message):
        if session.stop.is_set():
            return False
        try:
            session.outgoing.put_nowait(encode_message(message))
            return True
        except queue.Full:
            log.warning("settings: companion writer queue is full")
            # Never block a caller behind a stalled native UI.
            self._thread(self._cleanup, session, "settings-backpressure")
            return False

    def _launch(self, session):
        try:
            process = self._popen([str(executable_path())], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="strict", bufsize=1, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            session.process = process
            if not self._current(session):
                self._cleanup(session)
                return
            for target, name in ((self._write, "settings-writer"), (self._read, "settings-commands"),
                                 (self._stderr, "settings-log"), (self._poll, "settings-state")):
                self._thread(target, session, name)
        except (OSError, ValueError):
            log.exception("settings: unable to start WinUI companion at %s", executable_path())
            self._cleanup(session)

    def _write(self, session):
        try:
            while not session.stop.is_set():
                try:
                    wire = session.outgoing.get(timeout=.2)
                except queue.Empty:
                    continue
                session.process.stdin.write(wire)
                session.process.stdin.flush()
        except (OSError, ValueError):
            self._cleanup(session)

    def _read(self, session):
        try:
            while self._current(session):
                line = session.process.stdout.readline(MAX_MESSAGE + 1)
                if not line:
                    break
                if not line.endswith("\n"):
                    raise ValueError("unterminated or oversized settings message")
                self._handle(session, decode_message(line))
        except (OSError, UnicodeError, ValueError):
            log.warning("settings: companion protocol closed", exc_info=True)
        finally:
            self._cleanup(session)

    def _stderr(self, session):
        try:
            while not session.stop.is_set():
                line = session.process.stderr.readline(8192)
                if not line:
                    return
                log.warning("settings UI: %s", line.rstrip())
        except (OSError, ValueError):
            pass

    def _poll(self, session):
        if not session.ready.wait(self._startup_timeout):
            log.warning("settings: companion handshake timed out")
            self._cleanup(session)
            return
        while not session.stop.wait(self._poll_interval):
            if not self._current(session):
                return
            try:
                with self._model_lock:
                    self._publish(session)
            except Exception:
                log.exception("settings: snapshot failed")

    def _state(self, session):
        state = self.controller.snapshot()
        cfg = state["cfg"]
        language = cfg.get("language", "en")
        tr = lambda key, **kw: i18n.translate(key, language=language, **kw)
        if language not in session.catalogs:
            catalog = i18n.LANGUAGES.get(language, i18n.LANGUAGES["en"]).catalog
            session.catalogs[language] = {key: value for key, value in catalog.items() if isinstance(value, str)}
        devices = []
        for item in state.get("devices", []):
            status = item["status"]
            level = status.level
            if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
                level = None
            devices.append(dict(key=item["key"], name=item["name"], description=item.get("state", ""),
                pictogram=item.get("pictogram", ""), controller=bool(item.get("controller")),
                brand=item.get("brand", "default"), brandColor=bool(item.get("brand_color")),
                lowOverride=item.get("low_override"), hidden=bool(item.get("hidden")), level=level,
                charging=status.charging if type(status.charging) is bool else None, online=bool(status.online)))
        with self._lock:
            keys = [item["key"] for item in devices]
            if session.device_key not in keys:
                session.device_key = keys[0] if keys else None
            selected, page = session.device_key, session.page
        def options(values):
            return [dict(value=value, label=label) for value, label in values]
        intervals = [(value, tr("interval." + str(index))) for index, value in enumerate((15, 30, 60, 120, 300))]
        if cfg.get("interval") not in [value for value, _ in intervals]:
            intervals.append((cfg["interval"], str(cfg["interval"]) + " s"))
        thresholds = [(0, tr("choice.off"))] + [(value, str(value) + "%") for value in range(5, 31, 5)]
        if cfg.get("low") not in [value for value, _ in thresholds]:
            thresholds.append((cfg["low"], str(cfg["low"]) + "%"))
        themes = [(value, tr("choice." + value)) for value in ("auto", "white", "black")]
        if cfg.get("icon_theme") not in [value for value, _ in themes]:
            themes.append((cfg["icon_theme"], cfg["icon_theme"]))
        update = state.get("update") or {}
        progress = state.get("update_progress")
        return dict(type="state", protocol=1, sessionId=session.id, version=str(state.get("version", "")),
            language=language, dataDir=str(state.get("data_dir", "")), statusPath=str(state.get("status_path", "")), selectedDeviceKey=selected, page=page,
            labels=session.catalogs[language], settings={key: cfg[key] for key in GLOBAL_KEYS if key in cfg and type(cfg[key]) in (str, int, bool)},
            autostart=bool(state.get("autostart")), iconLightTaskbar=bool(state.get("icon_light_taskbar")), devices=devices,
            providers=[dict(name=p["name"], label=p["label"], enabled=bool(p["enabled"])) for p in state.get("providers", [])],
            options=dict(languages=options((key, value.name) for key, value in i18n.LANGUAGES.items()),
                intervals=options(intervals), thresholds=options(thresholds),
                pictograms=options((value, tr("icon." + value) if value else tr("choice.auto")) for value in
                    ("", "mouse", "keyboard", "microphone", "speaker", "webcam", "health", "smart_home", "watch", "display", "tracker", "headset", "gamepad", "bluetooth")),
                brands=options((value, tr("controller." + value)) for value in ("default", "xbox", "playstation", "nintendo")), iconThemes=options(themes)),
            update=dict(version=update.get("version", ""), progress=progress, installOk=bool(state.get("install_ok")),
                        status=state.get("update_status", ""), message=state.get("update_message", "")))

    def _publish(self, session, result=None):
        if not self._current(session):
            return
        state = self._state(session)
        wire = encode_message(state)
        changed = wire != session.last_state
        if changed:
            session.revision += 1
            session.last_state = wire
        if result is not None:
            self._send(session, dict(result, revision=session.revision))
        if changed or result is not None:
            self._send(session, dict(state, revision=session.revision))

    def _handle(self, session, message):
        with self._model_lock:
            if not self._current(session):
                return
            kind = message["type"]
            if kind == "hello":
                if session.ready.is_set() or set(message) != {"type", "protocol", "uiVersion"} or type(message["protocol"]) is not int or message["protocol"] != 1 or not isinstance(message["uiVersion"], str):
                    raise ValueError("unsupported settings handshake")
                self._send(session, dict(type="welcome", protocol=1, sessionId=session.id))
                self._publish(session)
                session.ready.set()
                return
            if not session.ready.is_set():
                raise ValueError("settings handshake required")
            request_id = message.get("id")
            if type(request_id) is not int or not 0 <= request_id <= 2147483647:
                raise ValueError("invalid settings request id")
            if kind == "preview":
                if set(message) != {"type", "id", "deviceKey"} or message["deviceKey"] is not None and not isinstance(message["deviceKey"], str):
                    raise ValueError("invalid preview request")
                image = self.controller.preview(message["deviceKey"], size=80)
                if image.size != (80, 80):
                    raise ValueError("invalid settings preview size")
                output = io.BytesIO()
                image.save(output, format="PNG")
                self._send(session, dict(type="preview", id=request_id, deviceKey=message["deviceKey"], size=80,
                    png=base64.b64encode(output.getvalue()).decode("ascii")))
                return
            result = dict(type="result", id=request_id, ok=False)
            deferred = None
            try:
                if kind != "command" or set(message) != {"type", "protocol", "id", "name", "args"} or type(message["protocol"]) is not int or message["protocol"] != 1:
                    raise ValueError("invalid settings command")
                if request_id <= session.last_command:
                    raise ValueError("duplicate or out of order command")
                session.last_command = request_id
                deferred = self._command(session, message["name"], message["args"])
                result["ok"] = True
            except (ValueError, TypeError, KeyError) as exc:
                result.update(errorCode="invalidCommand", message=str(exc))
            except Exception as exc:
                log.exception("settings: command failed")
                result.update(errorCode="commandFailed", message=str(exc))
            self._publish(session, result)
            if deferred is not None:
                deferred()

    def _command(self, session, name, args):
        fields = dict(setGlobal={"key", "value"}, setDevice={"deviceKey", "field", "value"},
            setProvider={"name", "enabled"}, setIconContent={"mode"}, selectDevice={"deviceKey"})
        actions = dict(refresh="refresh", diagnostics="diagnostics", checkUpdate="check_update", startUpdate="start_update",
            openData="open_data", openProject="open_project", openIssue="open_issue", quit="quit")
        if not isinstance(name, str) or not isinstance(args, dict) or name not in fields and name not in actions:
            raise ValueError("unknown settings command")
        if set(args) != fields.get(name, set()):
            raise ValueError("invalid settings command fields")
        if name == "setGlobal":
            key = args["key"]
            if not isinstance(key, str) or key not in GLOBAL_KEYS | {"autostart"}:
                raise ValueError("unknown global setting")
            self.controller.set_global(key, args["value"])
        elif name in ("setDevice", "selectDevice"):
            key = args["deviceKey"]
            if not isinstance(key, str) or key not in [d["key"] for d in self.controller.snapshot()["devices"]]:
                raise ValueError("device no longer available")
            if name == "selectDevice":
                with self._lock:
                    session.device_key = key
            else:
                if not isinstance(args["field"], str):
                    raise ValueError("invalid device field")
                self.controller.set_device(key, args["field"], args["value"])
        elif name == "setProvider":
            if not isinstance(args["name"], str) or type(args["enabled"]) is not bool:
                raise ValueError("invalid provider setting")
            self.controller.set_provider(args["name"], args["enabled"])
        elif name == "setIconContent":
            self.controller.set_icon_content(args["mode"])
        elif name == "quit":
            # Ack and state enter the writer queue before App.quit enqueues
            # shutdown. App.quit may be called on this very reader thread.
            return lambda: self.controller.action("quit")
        else:
            self.controller.action(actions[name])

    def _cleanup(self, session, graceful=False):
        if not session.cleanup_lock.acquire(blocking=False):
            return
        try:
            if graceful:
                self._send(session, {"type": "shutdown"})
            if not graceful:
                session.stop.set()
            process = session.process
            if process is not None:
                try:
                    process.wait(timeout=.6 if graceful else .1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        log.warning("settings: companion did not exit after termination")
                finally:
                    session.stop.set()
                    # Once the child has exited, inherited handles cannot leave
                    # writers blocked indefinitely. Never join the calling reader.
                    if process.poll() is not None:
                        for stream in (process.stdin, process.stdout, process.stderr):
                            try:
                                stream.close()
                            except (OSError, ValueError):
                                pass
            session.stop.set()
            session.ready.set()  # Wake a pending startup deadline worker.
            with self._lock:
                if self._session is session:
                    self._session = None
        finally:
            session.cleanup_lock.release()

    def close(self):
        with self._lock:
            self._closed = True
            session = self._session
        if session is not None:
            self._cleanup(session, graceful=True)
