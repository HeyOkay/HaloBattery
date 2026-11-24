"""Disposable Iconify processes; only the tray process can commit a selection."""
from __future__ import annotations

from collections import OrderedDict
import json
import logging
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading

MAX_WIRE = 4 * 1024 * 1024
CLOSE_TIMEOUT = 30
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_slots = threading.BoundedSemaphore(2)  # One view plus one draining accepted apply.
log = logging.getLogger("halo_battery")


class _QueuedMessage:
    """Reserve bounded pipe capacity before advancing a selection revision."""
    def __init__(self):
        self.ready = threading.Event()
        self.wire = None
        self.selected_bytes = 0


def encode(value):
    wire = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    if len(wire.encode("utf-8")) > MAX_WIRE:
        raise ValueError("Icon worker message too large")
    return wire


def decode(line):
    if not line.endswith("\n") or len(line.encode("utf-8")) > MAX_WIRE:
        raise ValueError("Invalid icon worker message length")
    def invalid(_value):
        raise ValueError("Invalid icon worker number")
    value = json.loads(line, parse_constant=invalid)
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str):
        raise ValueError("Invalid icon worker message")
    return value


def worker_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--iconify-worker"]
    return [sys.executable, "-m", "halo.iconify_worker"]


class IconifyProcessJobs:
    """Settings-compatible broker with bounded pipes and authoritative commits.

    Closing a view cancels browsing. Already admitted apply/restore requests
    finish independently of that view, subject to the controller's revisions.
    """
    def __init__(self, controller, send, changed, current, *, popen=None, close_timeout=CLOSE_TIMEOUT):
        self.controller, self.send, self.changed, self.current = controller, send, changed, current
        self._popen = popen or subprocess.Popen
        self._close_timeout = close_timeout
        self._lock = threading.RLock()
        self._outgoing = queue.Queue(maxsize=32)
        self._requests = OrderedDict()
        self._accepted = set()
        self._committed = set()
        self._selected_snapshot = frozenset()
        self._selected_queued_bytes = 0
        self._last_id = -1
        self._closed = False
        self._process = None
        self._thread = None
        self._stopped = threading.Event()
        self._cleanup_done = threading.Event()
        self._data_dir = str(Path(controller.context["data_dir"]).resolve())

    def _start(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="iconify-process", daemon=True)
                self._thread.start()

    def _queue(self, message):
        wire = encode(message)
        self._outgoing.put_nowait(wire)
        self._start()

    def prewarm(self):
        with self._lock:
            if self._closed or self._stopped.is_set():
                return
            try:
                self._queue({"kind": "warm"})
            except queue.Full:
                pass

    def submit(self, request):
        from .iconify_host import validate_request
        validate_request(request)
        with self._lock:
            if self._closed:
                return
            if self._stopped.is_set():
                self._reply(request, errorCode="api_error")
                return
            if request["id"] <= self._last_id:
                raise ValueError("duplicate or out of order catalogue request")
            self._last_id = request["id"]
            action = request["action"]
            if action in ("apply", "restore") and len(self._accepted) >= 3:
                self._reply(request, errorCode="busy")
                return
            state, actual_size = (None, None)
            if action in ("search", "details", "apply"):
                state, actual_size = self.controller.catalog_render_state(request["deviceKey"])
            record = dict(request, selectionRevision=None, _done=False)
            if action == "details":
                bindings = getattr(self.controller, "custom_icon_bindings", None)
                record["expectedBindings"] = bindings(request["iconId"]) if callable(bindings) else {}
            selection = action in ("apply", "restore")
            selected = self.controller.custom_icon_ids()
            if (not isinstance(selected, list) or len(selected) > 20000
                    or any(not isinstance(value, str) or not _DIGEST.fullmatch(value) for value in selected)):
                raise ValueError("Invalid selected icon identities")
            selected = frozenset(selected)
            selected_update = None if selected == self._selected_snapshot else {
                "add": sorted(selected - self._selected_snapshot),
                "remove": sorted(self._selected_snapshot - selected)}
            selected_bytes = len(encode(selected_update).encode("utf-8")) if selected_update is not None else 0
            message = dict(kind="request", request=request, state=state, actualSize=actual_size,
                           revision=1 if selection else None, selected=selected_update)
            if action == "details":
                in_use = getattr(self.controller, "custom_icon_in_use", None)
                if callable(in_use):
                    value = in_use(request["iconId"])
                    if type(value) is not bool:
                        raise ValueError("Invalid configured icon binding")
                    message["inUse"] = value
                elif callable(bindings) and isinstance(record["expectedBindings"], dict):
                    message["inUse"] = bool(record["expectedBindings"])
            try:
                encode(message)  # Reject invalid/oversized snapshots before admission.
                if self._selected_queued_bytes + selected_bytes > MAX_WIRE:
                    raise queue.Full
                reserved = _QueuedMessage()
                self._outgoing.put_nowait(reserved)
            except (queue.Full, ValueError, TypeError):
                self._reply(request, errorCode="busy")
                return
            try:
                revision = self.controller.begin_custom_selection(request["deviceKey"]) if selection else None
                message["revision"] = record["selectionRevision"] = revision
                # Record admission before the writer can receive a commit.
                self._requests[request["id"]] = record
                if selection:
                    self._accepted.add(request["id"])
                reserved.wire = encode(message)
                reserved.selected_bytes = selected_bytes
                self._selected_queued_bytes += selected_bytes
                self._selected_snapshot = selected
                while len(self._requests) > 32:
                    old = next((key for key in self._requests if key not in self._accepted), None)
                    if old is None:
                        break
                    self._requests.pop(old)
                    self._committed.discard(old)
            except (ValueError, TypeError):
                self._requests.pop(request["id"], None)
                self._accepted.discard(request["id"])
                self._reply(request, errorCode="busy")
            finally:
                reserved.ready.set()
                self._start()

    def _reply(self, request, **result):
        if self.current():
            self.send(dict(type="catalog", id=request["id"], action=request["action"],
                           deviceKey=request["deviceKey"], query=request["query"], start=request["start"],
                           size=request["size"], **result))

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._thread is None:
                self._stopped.set()
                self._cleanup_done.set()
                return
            try:
                self._outgoing.put_nowait(encode({"kind": "close"}))
            except queue.Full:
                # An unresponsive worker must still meet the disposal deadline.
                pass
        threading.Thread(target=self._deadline, name="iconify-process-close", daemon=True).start()

    def _deadline(self):
        if self._cleanup_done.wait(self._close_timeout):
            return
        self._stopped.set()
        with self._lock:
            process = self._process
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass

    def _write(self, process):
        try:
            while not self._stopped.is_set():
                try:
                    wire = self._outgoing.get(timeout=.1)
                except queue.Empty:
                    continue
                selected_bytes = 0
                if isinstance(wire, _QueuedMessage):
                    while not wire.ready.wait(.1):
                        if self._stopped.is_set():
                            return
                    selected_bytes, wire = wire.selected_bytes, wire.wire
                    if wire is None:
                        continue
                try:
                    process.stdin.write(wire)
                    process.stdin.flush()
                finally:
                    if selected_bytes:
                        with self._lock:
                            self._selected_queued_bytes = max(0, self._selected_queued_bytes - selected_bytes)
        except (OSError, ValueError):
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _stderr(process):
        try:
            while True:
                line = process.stderr.readline(8192)
                if not line:
                    return
                log.warning("icon worker: %s", line.rstrip())
        except (OSError, ValueError):
            pass

    def _load_asset(self, asset_id, icon_id):
        if not isinstance(asset_id, str) or not _DIGEST.fullmatch(asset_id):
            raise ValueError("Invalid worker asset identity")
        # Refuse a legacy record before load() could consult the full registry.
        path = Path(self._data_dir) / "iconify" / "assets" / asset_id / "metadata.json"
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Oversized worker selection")
        metadata = json.loads(raw)
        from .iconify_catalog import IconifyCatalog, SELECTED_REVISION
        if (not isinstance(metadata, dict) or metadata.get("asset_revision") != SELECTED_REVISION
                or metadata.get("icon_id") != icon_id):
            raise ValueError("Worker selection binding changed")
        catalog = IconifyCatalog(self._data_dir)
        try:
            asset = catalog.load(asset_id)
            if asset is None or asset.id != asset_id or asset.icon_id != icon_id:
                raise ValueError("Worker selection cannot be verified")
            return asset
        finally:
            catalog.close_transport()

    def _commit(self, message):
        fields = {"kind", "rpc", "requestId", "op", "assetId", "iconId", "deviceKey", "revision"}
        if (set(message) != fields or type(message["rpc"]) is not int or message["rpc"] < 1
                or type(message["requestId"]) is not int or message["op"] not in ("apply", "adopt")):
            raise ValueError("Invalid worker commit")
        with self._lock:
            request = self._requests.get(message["requestId"])
            if request is None and message["op"] == "adopt":
                # Details are disposable: a newer browse can evict their
                # context while the child finishes an optional upgrade. Refuse
                # that proposal without aborting unrelated admitted applies.
                return False
            if request is None or message["requestId"] in self._committed:
                raise ValueError("Unadmitted worker commit")
            if (message["deviceKey"] != request["deviceKey"] or message["iconId"] != request["iconId"]
                    or message["revision"] != request["selectionRevision"]
                    or type(message["revision"]) is not type(request["selectionRevision"])
                    or message["op"] == "apply" and request["action"] not in ("apply", "restore")
                    or message["op"] == "adopt" and request["action"] != "details"):
                raise ValueError("Worker commit does not match admitted selection")
            self._committed.add(message["requestId"])
        applied = False
        try:
            if message["op"] == "adopt" and (self._closed or not self.current()):
                return False
            asset = None
            if request["action"] != "restore":
                asset = self._load_asset(message["assetId"], request["iconId"])
            elif message["assetId"] is not None:
                raise ValueError("Restore cannot supply an asset")
            if message["op"] == "apply":
                applied = self.controller.apply_custom_icon(request["deviceKey"], asset,
                                                            revision=request["selectionRevision"]) is True
            else:
                applied = self.controller.adopt_custom_icon_upgrade(
                    asset, expected=request.get("expectedBindings", {})) is True
            if applied:
                self.changed()
            return applied
        except (OSError, ValueError, TypeError, KeyError):
            log.warning("settings: icon worker selection could not be adopted", exc_info=True)
            return False

    def _receive(self, message):
        kind = message["kind"]
        if kind == "commit":
            applied = self._commit(message)
            # Commits are part of an admitted operation and must drain on close.
            self._outgoing.put(encode(dict(kind="committed", rpc=message["rpc"], applied=applied)), timeout=1)
        elif kind == "reply":
            if set(message) != {"kind", "result"} or not isinstance(message["result"], dict):
                raise ValueError("Invalid worker result")
            result = message["result"]
            with self._lock:
                if result.get("type") != "catalog" or type(result.get("id")) is not int:
                    raise ValueError("Invalid worker result identity")
                request = self._requests.get(result.get("id"))
                if request is None:
                    return
                if any(result.get(key) != request.get(key) for key in ("action", "deviceKey", "query", "size")):
                    raise ValueError("Worker result binding changed")
                prefetch = result.get("prefetch", False)
                if type(prefetch) is not bool or type(result.get("start")) is not int:
                    raise ValueError("Invalid worker result page")
                if prefetch:
                    if (request["action"] != "search" or result.get("anchorId") != request["id"]
                            or result["start"] not in (request["start"] - 50, request["start"] + 50)
                            or result["start"] < 0):
                        raise ValueError("Invalid worker prefetch binding")
                elif result["start"] != request["start"]:
                    raise ValueError("Worker result page changed")
                if not result.get("prefetch"):
                    self._accepted.discard(request["id"])
                    request["_done"] = True
            if not self._closed and self.current():
                self.send(result)
        elif kind == "closed" and set(message) == {"kind"}:
            with self._lock:
                if self._accepted:
                    raise ValueError("Icon worker closed before admitted selections completed")
        else:
            raise ValueError("Unexpected icon worker message")

    def _run(self):
        process = None
        acquired = False
        helpers = []
        try:
            while not _slots.acquire(timeout=.1):
                if self._stopped.is_set() or self._closed and not self._accepted:
                    return
            acquired = True
            if self._stopped.is_set():
                return
            process = self._popen(worker_command(), cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="strict", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            with self._lock:
                self._process = process
            process.stdin.write(encode(dict(kind="init", protocol=1, dataDir=self._data_dir)))
            process.stdin.flush()
            for target, name in ((self._write, "iconify-process-writer"), (self._stderr, "iconify-process-log")):
                worker = threading.Thread(target=target, args=(process,), name=name, daemon=True)
                helpers.append(worker)
                worker.start()
            while not self._stopped.is_set():
                line = process.stdout.readline(MAX_WIRE + 1)
                if not line:
                    break
                message = decode(line)
                self._receive(message)
                if message["kind"] == "closed":
                    break
        except (OSError, ValueError, TypeError, KeyError, queue.Full):
            log.warning("settings: disposable icon worker failed", exc_info=True)
        finally:
            self._stopped.set()
            try:
                if process is not None:
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                log.warning("settings: icon worker process wait failed", exc_info=True)
            finally:
                try:
                    exited = process is None or process.poll() is not None
                except OSError:
                    exited = False
                if process is not None and exited:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
                if not exited:
                    # Retain this slot if Windows refused to end the process.
                    acquired = False
                    log.error("settings: icon worker did not terminate; worker capacity retained")
                for worker in helpers:
                    worker.join(timeout=2)
                with self._lock:
                    failed = [request for request in self._requests.values()
                              if not request.get("_done") and request["action"] != "cancel"]
                    self._accepted.clear()
                    self._requests.clear()
                    self._committed.clear()
                    self._selected_snapshot = frozenset()
                    self._selected_queued_bytes = 0
                    self._process = None
                    while True:
                        try:
                            self._outgoing.get_nowait()
                        except queue.Empty:
                            break
                try:
                    for request in failed:
                        self._reply(request, errorCode="api_error")
                finally:
                    if acquired:
                        _slots.release()
                    self._cleanup_done.set()
