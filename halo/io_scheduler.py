"""Bounded I/O scheduling for the in-process Windows device transport.

Exactly two daemon workers perform every request factory/start/poll/cancel/
dispose call. One optional daemon coordinator only maintains Python state.
A timeout expires publication, not ownership: buffers and the device lease stay
pinned until actual completion AND successful disposal. Never create another
scheduler as a replacement for one whose close() returned False.

Requests implement the native_io.IoRequest interface. In particular,
cancel() on an unstarted request must make it terminal without issuing I/O.
Factories own any partial resources if they raise before returning a request.
The same device_key must cover every operation sharing a handle/device. Owners
must not close or reuse borrowed handles while snapshot().busy remains True.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field, replace
import math
import threading
import time

MAX_DEVICES = 16
MAX_BUFFER = 32768
POLL_INTERVAL = 0.01
QUARANTINE_INTERVAL = 0.05


@dataclass(frozen=True)
class IOResult:
    device_key: str
    generation: int
    status: str
    data: bytes = b""
    error: int | str | None = None
    stale: bool = False
    fresh: bool = False
    transferred: int = 0


@dataclass(frozen=True)
class DeviceSnapshot:
    device_key: str
    generation: int
    state: str
    busy: bool
    last_result: IOResult | None
    last_good: IOResult | None
    expired: bool
    quarantined: bool
    last_error: str = ""


@dataclass(frozen=True)
class _Completion:
    status: str
    data: bytes
    error: int | str | None
    transferred: int


@dataclass
class _Context:
    device_key: str
    generation: int
    deadline: float
    factory: object
    request: object = None
    factory_started: bool = False
    factory_finished: bool = False
    issued: bool = False
    invalid: bool = False
    reason: str = ""
    cancel_sent: bool = False
    cancel_finished: bool = False
    completion: _Completion | None = None
    next_probe: float = 0.0
    shutdown_probe_at: float = 0.0
    operations: set = field(default_factory=set)
    running: set = field(default_factory=set)
    finished: dict = field(default_factory=dict)
    last_error: str = ""


@dataclass
class _Device:
    generation: int = 0
    active: _Context | None = None
    last_result: IOResult | None = None
    last_good: IOResult | None = None
    last_error: str = ""


class IOScheduler:
    """A fixed two-worker owner, with at most sixteen leased device contexts.

    autostart=False disables only the coordinator; tests drive pump() explicitly.
    The two issue workers always exist and are never replaced. After shutdown,
    free workers also drive housekeeping, so eventual completion can release
    quarantined contexts even when the normal coordinator was disabled.
    """
    def __init__(self, *, clock=time.monotonic, autostart=True, max_devices=MAX_DEVICES):
        if type(max_devices) is not int or not 1 <= max_devices <= MAX_DEVICES:
            raise ValueError("max_devices must be between 1 and 16")
        self._clock = clock
        self._max_devices = max_devices
        self._condition = threading.Condition(threading.RLock())
        self._wake = threading.Event()
        self._devices = {}
        self._results = OrderedDict()
        self._cancel_queue = deque()
        self._cleanup_queue = deque()
        self._issue_queue = deque()
        self._closed = False
        self._workers_stop = False
        self._running = self._peak_running = 0
        self._coordinator = None
        self._workers = tuple(threading.Thread(target=self._worker_loop, daemon=True,
                              name="halo-io-worker-" + str(index)) for index in range(2))
        for worker in self._workers:
            worker.start()
        if autostart:
            self._coordinator = threading.Thread(target=self._coordinate, daemon=True,
                                                name="halo-io-coordinator")
            self._coordinator.start()

    def submit(self, device_key, request_factory, *, timeout):
        """Return a generation, or None if closed, leased, or at capacity."""
        if not isinstance(device_key, str) or not device_key or len(device_key) > 256:
            raise ValueError("device_key must be a nonempty string of at most 256 characters")
        if not callable(request_factory):
            raise TypeError("request_factory must be callable")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        with self._condition:
            if self._closed:
                return None
            device = self._devices.get(device_key)
            if device is None:
                if len(self._devices) >= self._max_devices:
                    return None
                device = self._devices[device_key] = _Device()
            if device.active is not None:
                return None
            device.generation += 1
            context = _Context(device_key, device.generation, self._clock() + timeout, request_factory)
            device.active = context
            self._queue_locked(context, "start")
            self._wake.set()
            return context.generation

    def cancel(self, device_key):
        with self._condition:
            device = self._devices.get(device_key)
            if device is None or device.active is None:
                return False
            self._invalidate_locked(device.active, "cancelled", "cancel requested")
            self._wake.set()
            return True

    def take_results(self):
        """Consume at most one latest publication per device; no native calls."""
        with self._condition:
            result = list(self._results.values())
            self._results.clear()
            return result

    def snapshot(self):
        """Immutable cached views; their results always have fresh=False."""
        with self._condition:
            result = {}
            for key, device in self._devices.items():
                context = device.active
                state = device.last_result.status if device.last_result else "idle"
                if context is not None:
                    state = ("quarantined" if context.invalid else "disposing" if context.completion
                             else "pending" if context.issued else "starting" if context.factory_started
                             else "queued")
                result[key] = DeviceSnapshot(
                    key, device.generation, state, context is not None,
                    device.last_result, device.last_good,
                    context.reason == "expired" if context else bool(device.last_result and device.last_result.status == "expired"),
                    bool(context and context.invalid), context.last_error if context else device.last_error)
            return result

    def stats(self):
        with self._condition:
            active = [d.active for d in self._devices.values() if d.active is not None]
            return dict(workers=2, worker_alive=sum(w.is_alive() for w in self._workers),
                        coordinator_alive=bool(self._coordinator and self._coordinator.is_alive()),
                        active=len(active), quarantined=sum(c.invalid for c in active),
                        queued=sum(map(len, (self._cancel_queue, self._cleanup_queue, self._issue_queue))),
                        operations=sum(len(c.operations) for c in active), running=self._running,
                        peak_running=self._peak_running, results=len(self._results),
                        devices=len(self._devices), closed=self._closed)

    def forget(self, device_key):
        """Release an idle cache entry so another device can use its capacity."""
        with self._condition:
            device = self._devices.get(device_key)
            if device is None or device.active is not None:
                return False
            del self._devices[device_key]
            self._results.pop(device_key, None)
            return True

    def stop_admission(self):
        """Close admission and request cancellation without native calls/waits."""
        with self._condition:
            self._closed = True
            for device in tuple(self._devices.values()):
                if device.active is not None:
                    self._invalidate_locked(device.active, "shutdown", "scheduler shutdown")
                    if device.active is not None:
                        device.active.shutdown_probe_at = 0.0
            self._maybe_stop_locked()
            self._condition.notify_all()
            self._wake.set()

    def close(self, timeout=0.5):
        """Wait at most 500 ms; False means native cleanup is unconfirmed.

        Existing daemon workers/coordinator keep ownership and continue cleanup
        after False. No new thread, replacement request, or unsafe handle close
        is used to make shutdown appear successful.
        """
        if type(timeout) not in (int, float) or not math.isfinite(timeout):
            raise ValueError("timeout must be finite")
        deadline = time.monotonic() + min(0.5, max(0.0, timeout))
        self.stop_admission()
        while True:
            self.pump()
            with self._condition:
                clean = self._workers_stop
            remaining = deadline - time.monotonic()
            if clean or remaining <= 0:
                break
            self._wake.wait(min(0.005, remaining))
            self._wake.clear()
        if clean:
            for thread in (*self._workers, self._coordinator):
                if thread is not None and thread is not threading.current_thread():
                    thread.join(max(0.0, deadline - time.monotonic()))
        return clean

    def _coordinate(self):
        while True:
            self.pump()
            with self._condition:
                if self._workers_stop:
                    return
            self._wake.wait(POLL_INTERVAL)
            self._wake.clear()

    def _queue_locked(self, context, kind):
        if kind in context.operations:
            return
        context.operations.add(kind)
        target = (self._cancel_queue if kind == "cancel" else self._cleanup_queue
                  if kind == "dispose" else self._issue_queue)
        target.append((context, kind))
        self._condition.notify()

    def _current_locked(self, context):
        device = self._devices.get(context.device_key)
        return device is not None and device.active is context

    def _publish_locked(self, context, status, *, completion=None, error=None):
        device = self._devices[context.device_key]
        good = status == "success"
        data = completion.data if good else device.last_good.data if device.last_good else b""
        result = IOResult(context.device_key, context.generation, status, data,
                          completion.error if completion is not None else error,
                          stale=not good, fresh=good,
                          transferred=completion.transferred if good else 0)
        # Snapshot is a cache view, never another fresh observation. Only the
        # one-shot result mailbox carries fresh=True; bytes are shared safely.
        cached = replace(result, fresh=False)
        device.last_result = cached
        if good:
            device.last_good = cached
        self._results[context.device_key] = result
        self._results.move_to_end(context.device_key)

    def _invalidate_locked(self, context, reason, error):
        if not self._current_locked(context):
            return
        if not context.invalid:
            context.invalid, context.reason = True, reason
            context.last_error = str(error)[:512]
            self._publish_locked(context, reason, error=context.last_error)
        # No native call can have begun while this start is still queued.
        if (context.request is None and not context.factory_started
                and not context.running):
            self._issue_queue = deque((c, kind) for c, kind in self._issue_queue if c is not context)
            context.operations.clear()
            self._retire_locked(context)
        elif context.request is not None and context.completion is None and not context.cancel_sent:
            context.cancel_sent = True
            self._queue_locked(context, "cancel")

    def _retire_locked(self, context):
        if self._current_locked(context):
            device = self._devices[context.device_key]
            device.last_error = context.last_error
            device.active = None
        context.factory = None
        context.request = None
        context.completion = None
        self._maybe_stop_locked()

    def _maybe_stop_locked(self):
        if self._closed and not any(d.active is not None for d in self._devices.values()):
            self._workers_stop = True
            self._condition.notify_all()
            self._wake.set()

    @staticmethod
    def _completion(value):
        if value is None:
            return None
        success = getattr(value, "success")
        if type(success) is not bool:
            raise TypeError("completion.success must be bool")
        data = getattr(value, "data", b"")
        size = data.nbytes if isinstance(data, memoryview) else len(data) if isinstance(data, (bytes, bytearray)) else -1
        if size < 0 or size > MAX_BUFFER:
            raise ValueError("completion data exceeds 32 KiB or is not bytes")
        transferred = getattr(value, "transferred", len(data))
        if type(transferred) is not int or not 0 <= transferred <= MAX_BUFFER:
            raise ValueError("invalid completion byte count")
        error = getattr(value, "error", None)
        if error is not None and type(error) is not int:
            error = str(error)[:512]
        cancelled = bool(getattr(value, "cancel_requested", False)) or error == 995
        status = "success" if success else "cancelled" if cancelled else "error"
        return _Completion(status, bytes(data), error, transferred)

    def _execute(self, context, kind):
        if kind == "start":
            # The factory may itself open a native handle or block in CreateFile.
            request = context.factory()
            if request is None or any(not callable(getattr(request, method, None))
                                      for method in ("start", "poll", "cancel", "dispose")):
                raise TypeError("factory must return a request with start/poll/cancel/dispose")
            with self._condition:
                context.request = request
                context.factory_finished = True
                if self._clock() >= context.deadline and not context.invalid:
                    self._invalidate_locked(context, "expired", "request deadline expired")
                if self._closed and not context.invalid:
                    self._invalidate_locked(context, "shutdown", "scheduler shutdown")
                if context.invalid:
                    if not context.cancel_sent:
                        context.cancel_sent = True
                        self._queue_locked(context, "cancel")
                    return "unstarted"
                context.issued = True
            request.start()
            return "started"
        if kind == "cancel":
            return context.request.cancel()
        if kind == "poll":
            return self._completion(context.request.poll())
        if kind == "dispose":
            result = context.request.dispose()
            if type(result) is not bool:
                raise TypeError("request.dispose() must return bool")
            return result
        raise ValueError("unknown operation")

    def _worker_loop(self):
        while True:
            with self._condition:
                while not any((self._cancel_queue, self._cleanup_queue, self._issue_queue)):
                    if self._workers_stop:
                        return
                    self._condition.wait(QUARANTINE_INTERVAL)
                    if self._closed:
                        break
                task = None
                for source in (self._cancel_queue, self._cleanup_queue, self._issue_queue):
                    if source:
                        task = source.popleft()
                        break
                if task is not None:
                    context, kind = task
                    if not self._current_locked(context) or kind not in context.operations:
                        task = None
                    elif kind == "start" and (context.invalid or self._closed
                                               or self._clock() >= context.deadline):
                        reason = "shutdown" if self._closed else "expired"
                        self._invalidate_locked(context, reason, "request expired before issue")
                        task = None
                    else:
                        context.running.add(kind)
                        if kind == "start":
                            context.factory_started = True
                        self._running += 1
                        self._peak_running = max(self._peak_running, self._running)
            if task is None:
                # Native-free housekeeping lets manual-pump shutdown also finish
                # later, using these same workers rather than a replacement.
                with self._condition:
                    closing = self._closed
                if closing:
                    self.pump()
                continue
            value = error = None
            try:
                value = self._execute(context, kind)
            except BaseException as exc:
                # A request exception must never shrink or replace the fixed pool.
                try:
                    detail = str(exc)[:480]
                except BaseException:
                    detail = "exception text unavailable"
                error = type(exc).__name__ + ": " + detail
            finally:
                with self._condition:
                    self._running -= 1
                    context.running.discard(kind)
                    context.finished[kind] = (value, error)
                    self._wake.set()
                    closing = self._closed
                if closing:
                    self.pump()

    def pump(self):
        """Run only bounded Python bookkeeping; all backend calls stay on workers."""
        with self._condition:
            now = self._clock()
            for device in tuple(self._devices.values()):
                context = device.active
                if context is None:
                    continue
                if not context.invalid and now >= context.deadline:
                    self._invalidate_locked(context, "expired", "request deadline expired")
                if not self._current_locked(context):
                    continue
                for kind, (value, error) in tuple(context.finished.items()):
                    del context.finished[kind]
                    context.operations.discard(kind)
                    if error is not None:
                        context.last_error = error
                        self._invalidate_locked(context, "error", error)
                        context.next_probe = now + QUARANTINE_INTERVAL
                        context.shutdown_probe_at = time.monotonic() + QUARANTINE_INTERVAL
                        if kind == "start" and context.request is None:
                            self._retire_locked(context)
                            break
                    elif kind == "poll":
                        context.completion = value
                        context.next_probe = (now if value is not None else now +
                                              (QUARANTINE_INTERVAL if context.invalid else POLL_INTERVAL))
                        context.shutdown_probe_at = (0.0 if value is not None else
                                                     time.monotonic() + QUARANTINE_INTERVAL)
                    elif kind == "cancel":
                        context.cancel_finished = True
                        context.next_probe = now
                        context.shutdown_probe_at = 0.0
                    elif kind == "dispose":
                        if value:
                            if not context.invalid and context.completion is not None:
                                self._publish_locked(context, context.completion.status,
                                                     completion=context.completion)
                            self._retire_locked(context)
                            break
                        context.next_probe = now + QUARANTINE_INTERVAL
                        context.shutdown_probe_at = time.monotonic() + QUARANTINE_INTERVAL
                if not self._current_locked(context):
                    continue
                if (context.invalid and context.request is not None and context.completion is None
                        and not context.cancel_sent):
                    context.cancel_sent = True
                    self._queue_locked(context, "cancel")
                probe_due = (time.monotonic() >= context.shutdown_probe_at if self._closed
                             else now >= context.next_probe)
                if context.operations or context.request is None or not probe_due:
                    continue
                if context.completion is not None:
                    self._queue_locked(context, "dispose")
                else:
                    self._queue_locked(context, "poll")
            self._maybe_stop_locked()
