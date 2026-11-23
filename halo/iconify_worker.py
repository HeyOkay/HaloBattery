"""Private, disposable Iconify child entry point. No tray or device imports."""
from __future__ import annotations

from collections import deque
import io
import os
import queue
import sys
import threading
import time

from .iconify_process import MAX_WIRE, decode, encode


def _standard_stream(name, number, writing=False):
    stream = getattr(sys, name)
    if stream is not None:
        return stream
    # PyInstaller's windowed bootloader clears Python streams even when our
    # parent explicitly supplies inherited stdin/stdout/stderr pipe handles.
    if os.name != "nt":
        raise OSError("Icon worker has no inherited stream")
    import ctypes
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
    kernel.GetStdHandle.restype = ctypes.c_void_p
    handle = kernel.GetStdHandle(number & 0xffffffff)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError("Icon worker has no inherited pipe")
    fd = msvcrt.open_osfhandle(handle, (os.O_WRONLY if writing else os.O_RDONLY) | os.O_BINARY)
    return io.TextIOWrapper(os.fdopen(fd, "wb" if writing else "rb"), encoding="utf-8",
                            errors="strict", line_buffering=writing)


class _Controller:
    def __init__(self, server, data_dir):
        self.server, self.data_dir = server, data_dir
        self.selected = []
        self.revision = None
        self.catalog = None
        self.jobs = None

    def create_browse_catalog(self):
        from .iconify_catalog import IconifyCatalog
        self.catalog = IconifyCatalog(self.data_dir, transient=True)
        self._seed()
        return self.catalog

    def _seed(self):
        if self.catalog is not None:
            from .iconify_catalog import RAM_CACHE_LIMIT
            for asset_id in self.selected[-RAM_CACHE_LIMIT:]:
                self.catalog.load(asset_id)

    def custom_icon_ids(self):
        return self.selected[:]

    def custom_icon_in_use(self, icon_id):
        if self.catalog is None:
            return False
        with self.catalog._lock:
            return any(asset.id in self.selected and asset.icon_id == icon_id
                       for asset in self.catalog._assets.values())

    def catalog_render_state(self, key):
        request = self.jobs._active[0]
        return request["_renderSnapshot"]

    def begin_custom_selection(self, key):
        return self.revision

    def _commit(self, op, key, asset, revision):
        request = self.jobs._active[0]
        return self.server.commit(dict(kind="commit", requestId=request["id"], op=op,
            assetId=asset.id if asset is not None else None, iconId=request["iconId"],
            deviceKey=request["deviceKey"], revision=revision))

    def apply_custom_icon(self, key, asset, *, revision=None):
        return self._commit("apply", key, asset, revision)

    def adopt_custom_icon_upgrade(self, asset):
        return self._commit("adopt", None, asset, None)


class WorkerServer:
    def __init__(self, input_stream, output_stream):
        self.input, self.output = input_stream, output_stream
        self._write_lock = threading.Lock()
        self._incoming = queue.Queue(maxsize=32)
        self._rpc_lock = threading.Lock()
        self._rpc = 0
        self._waiting = {}
        self._closing = threading.Event()
        self._finished = threading.Event()
        self.controller = self.jobs = None

    def send(self, message):
        wire = encode(message)
        with self._write_lock:
            self.output.write(wire)
            self.output.flush()

    def _read(self):
        try:
            while not self._finished.is_set():
                line = self.input.readline(MAX_WIRE + 1)
                if not line:
                    break
                self._incoming.put(decode(line))
        except (OSError, ValueError):
            pass
        finally:
            self._incoming.put(None)

    def commit(self, message):
        ready = threading.Event()
        with self._rpc_lock:
            self._rpc += 1
            rpc = self._rpc
            self._waiting[rpc] = [ready, False]
        self.send(dict(message, rpc=rpc))
        try:
            if not ready.wait(25):
                raise OSError("Tray selection acknowledgement timed out")
            with self._rpc_lock:
                return self._waiting[rpc][1]
        finally:
            with self._rpc_lock:
                self._waiting.pop(rpc, None)

    def _close(self):
        if self._closing.is_set():
            return
        self._closing.set()
        jobs = self.jobs
        if jobs is None:
            self._finished.set()
            return
        # Cancel disposable work without cancelling admitted apply/restore.
        with jobs._condition:
            jobs._index_cancel.set()
            if jobs._active is not None and jobs._active[0]["action"] not in ("apply", "restore"):
                jobs._active[1].set()
            jobs._pending = deque(item for item in jobs._pending if item[0]["action"] in ("apply", "restore"))
            if jobs._warming is not None:
                jobs._warming[2].set()
            for item in jobs._warm_pending:
                item[2].set()
            jobs._warm_pending.clear()
            jobs._condition.notify_all()
            selections = (jobs._active is not None and jobs._active[0]["action"] in ("apply", "restore")
                          or any(item[0]["action"] in ("apply", "restore") for item in jobs._pending))
        def finish():
            if selections:
                with jobs._condition:
                    deadline = time.monotonic() + 26
                    while jobs._active is not None or jobs._pending:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        jobs._condition.wait(timeout=min(.1, remaining))
            if self.controller.catalog is not None:
                self.controller.catalog.close_transport()
            jobs.close()
            jobs._cleanup_done.wait(2)
            self._finished.set()
        threading.Thread(target=finish, name="iconify-worker-drain", daemon=True).start()

    def handle(self, message):
        kind = message["kind"]
        if kind == "init":
            if (self.jobs is not None or set(message) != {"kind", "protocol", "dataDir"}
                    or type(message["protocol"]) is not int or message["protocol"] != 1
                    or not isinstance(message["dataDir"], str) or not os.path.isabs(message["dataDir"])):
                raise ValueError("Invalid icon worker initialization")
            from .iconify_host import IconCatalogJobs
            self.controller = _Controller(self, message["dataDir"])
            self.jobs = IconCatalogJobs(self.controller,
                lambda result: self.send(dict(kind="reply", result=result)), lambda: None,
                lambda: True, catalog_factory=self.controller.create_browse_catalog)
            # The process owns this catalog. Exiting reclaims all its RAM;
            # per-view cleanup must not delete another worker's lookup files.
            self.jobs._owns_catalog = False
            self.controller.jobs = self.jobs
        elif kind == "committed":
            if set(message) != {"kind", "rpc", "applied"} or type(message["rpc"]) is not int or type(message["applied"]) is not bool:
                raise ValueError("Invalid selection acknowledgement")
            with self._rpc_lock:
                waiting = self._waiting.get(message["rpc"])
                if waiting is None:
                    raise ValueError("Unknown selection acknowledgement")
                waiting[1] = message["applied"]
                waiting[0].set()
        elif self.jobs is None:
            raise ValueError("Icon worker handshake required")
        elif kind == "close" and set(message) == {"kind"}:
            self._close()
        elif kind == "warm" and set(message) == {"kind"} and not self._closing.is_set():
            self.jobs.prewarm()
        elif kind == "request" and not self._closing.is_set():
            if set(message) != {"kind", "request", "state", "actualSize", "revision", "selected"}:
                raise ValueError("Invalid icon worker request envelope")
            from .iconify_host import validate_request
            from .iconify_process import _DIGEST
            request = message["request"]
            if not isinstance(request, dict):
                raise ValueError("Invalid icon worker request")
            validate_request(request)
            selected = message["selected"]
            if (not isinstance(selected, list) or len(selected) > 20000
                    or any(not isinstance(value, str) or not _DIGEST.fullmatch(value) for value in selected)):
                raise ValueError("Invalid selected icon identities")
            if request["action"] in ("apply", "restore"):
                if type(message["revision"]) is not int or message["revision"] < 1:
                    raise ValueError("Invalid selection revision")
            elif message["revision"] is not None:
                raise ValueError("Unexpected selection revision")
            if request["action"] in ("search", "details", "apply"):
                state, size = message["state"], message["actualSize"]
                if not _valid_snapshot(state, size):
                    raise ValueError("Invalid icon rendering snapshot")
            self.controller.selected = selected
            self.controller.revision = message["revision"]
            self.controller._seed()
            with self.jobs._condition:
                if request["action"] in ("apply", "restore"):
                    # The parent reserved a selection revision after admitting
                    # this request. Disposable queued work must not turn that
                    # admitted selection into a child-side busy rejection.
                    if self.jobs._active is not None and self.jobs._active[0]["action"] not in ("apply", "restore"):
                        self.jobs._active[1].set()
                    self.jobs._pending = deque(item for item in self.jobs._pending
                                               if item[0]["action"] in ("apply", "restore"))
                self.jobs.submit(request)
                if request["action"] in ("search", "details", "apply"):
                    for queued, _cancel in self.jobs._pending:
                        if queued["id"] == request["id"]:
                            queued["_renderSnapshot"] = (tuple(state), size)
                            break
        else:
            raise ValueError("Unknown icon worker command")

    def run(self):
        threading.Thread(target=self._read, name="iconify-worker-reader", daemon=True).start()
        try:
            while not self._finished.is_set():
                try:
                    message = self._incoming.get(timeout=.1)
                except queue.Empty:
                    continue
                if message is None:
                    # A dead parent cannot authorize an apply. Wake any commit
                    # waiter and interrupt network work instead of draining it.
                    with self._rpc_lock:
                        for ready, _applied in self._waiting.values():
                            ready.set()
                    if self.jobs is not None:
                        if self.controller.catalog is not None:
                            self.controller.catalog.close_transport()
                        self.jobs.close()
                    break
                self.handle(message)
        finally:
            self._close()
            self._finished.wait(28)
            self.send({"kind": "closed"})


def _valid_snapshot(state, size):
    if (not isinstance(state, list) or not 13 <= len(state) <= 15
            or type(size) is not int or not 8 <= size <= 192):
        return False
    if state[0] is not None and (type(state[0]) is not int or not 0 <= state[0] <= 100):
        return False
    if any(type(state[index]) is not bool for index in (1, 2, 4, 6, 8, 11, 12)):
        return False
    if (type(state[3]) is not int or not 0 <= state[3] <= 100
            or type(state[9]) is not int or not 8 <= state[9] <= 192
            or any(not isinstance(state[index], str) or len(state[index]) > 160 for index in (5, 7, 10))):
        return False
    if len(state) > 13 and state[13] is not None:
        from .iconify_process import _DIGEST
        if not isinstance(state[13], str) or not _DIGEST.fullmatch(state[13]):
            return False
    return len(state) < 15 or state[14] in ("thin", "classic")


def main():
    try:
        sys.stderr = _standard_stream("stderr", -12, writing=True)
        WorkerServer(_standard_stream("stdin", -10), _standard_stream("stdout", -11, writing=True)).run()
        return 0
    except Exception:
        # Windowed frozen Python otherwise opens a bootloader error dialog.
        # Report failure on the inherited diagnostic pipe and exit silently.
        if sys.stderr is not None:
            try:
                sys.stderr.write("Icon worker protocol or execution failed\n")
                sys.stderr.flush()
            except (OSError, ValueError):
                pass
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
