"""Bounded background provider scheduling with cached-only app snapshots.

Two fixed daemon provider workers call the existing provider instances. The HID
adapter owns its separate two-worker transport scheduler. No native call, wait,
provider diagnostic method, or signature query runs in pump()/consume(). App.loop
must pump at least every 100 ms; free workers also reap ownership during shutdown.
"""
from __future__ import annotations

from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
import logging
import threading
import time

from providers.base import DeviceStatus

log = logging.getLogger("halo_battery")

# Conservative declarations, independent of the last enumeration: protocols
# enumerate again inside poll(), so hotplug must not bypass device ownership.
_CONFLICTS = frozenset((frozenset(("razer", "barracuda")),
                       frozenset(("razer", "barracuda_x")),
                       frozenset(("pulsar", "gwolves")),
                       frozenset(("logitech", "astro")),
                       frozenset(("logitech", "logitech_centurion"))))
_TIMEOUTS = {"logitech": 45.0, "xinput": 35.0, "razer": 25.0,
             "turtlebeach": 30.0, "jbl": 15.0, "dji": 15.0, "discovery": 10.0}
_CIRCUIT_COOLDOWN = 60.0
_MAX_PROVIDER_STATUSES = 256
# Admission bound, not a reserved allocation: allow Phone Link alongside
# additional composed providers while keeping the two existing workers.
_MAX_PROVIDERS = 128


@dataclass
class PollBatch:
    hid: list[DeviceStatus]
    fresh_keys: set[str]
    completed: set[str]
    owners: dict[str, str]
    aliases: dict[str, str] = field(default_factory=dict)


@dataclass
class _Slot:
    owner: str
    name: str
    provider: object
    running: bool = False
    lease: bool = False
    epoch: int = 0
    generation: int = 0
    expired: bool = False
    deadline: float | None = None
    kind: str = ""
    statuses: list = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    lines: list = field(default_factory=list)
    failures: int = 0
    retry_at: float = 0.0
    circuit: bool = False
    recovering: bool = False
    pending: bool = False


class PollingRuntime:
    """Bounded coordinator with cached-only public snapshots.

    Adapter hooks must be Python bookkeeping only: provider_scope(owner) yields
    a status with failed/error; transport_idle(owner) confirms logical operation
    and close cleanup; cancel_owner(owner) schedules cancellation, never executes
    native cancellation in the calling thread. Ordinary persistent passive-read
    IRPs are not unresolved logical calls and must be excluded by the adapter.
    """
    def __init__(self, providers, notify, *, signature_reader=None,
                 diagnostics_reader=None, provider_scope=None, transport_idle=None,
                 cancel_owner=None, clock=time.monotonic):
        providers = tuple(providers)
        if len(providers) > _MAX_PROVIDERS or len({id(p) for p in providers}) != len(providers):
            raise ValueError(f"at most {_MAX_PROVIDERS} distinct existing provider instances are supported")
        self._clock, self._notify = clock, notify
        self._scope = provider_scope or (lambda owner: nullcontext(None))
        self._idle = transport_idle or (lambda owner: True)
        self._cancel = cancel_owner or (lambda owner: None)
        self._signature_reader = signature_reader
        self._diagnostics_reader = diagnostics_reader
        self._slots = {}
        for index, provider in enumerate(providers):
            name = provider.name
            if not isinstance(name, str) or not name or len(name) > 64:
                raise ValueError("invalid provider name")
            owner = name + ":" + str(index)
            self._slots[owner] = _Slot(owner, name, provider)
        self._provider_order = tuple(self._slots)
        self._slots["discovery"] = _Slot("discovery", "discovery", None)
        self._condition = threading.Condition(threading.RLock())
        self._jobs = deque()
        self._queued = set()
        self._disabled = set()
        self._configured = False
        self._fullmode = False
        self._closed = False
        self._workers_stop = False
        self._fresh = set()
        self._edges = set()
        self._dirty_full = self._dirty_display = False
        self._usb_signature = self._xinput_signature = None
        self._signature_due = 0.0
        self._diag_event = None
        self._diag_deadline = None
        self._hid_diagnostics = []
        self._peak_running = 0
        self._workers = tuple(threading.Thread(target=self._worker, daemon=True,
                              name="halo-provider-" + str(i)) for i in range(2))
        for slot in self._slots.values():
            activity = getattr(slot.provider, "activity", None)
            if activity is not None and hasattr(activity, "wake"):
                activity.wake = self._activity_wake
        for worker in self._workers:
            worker.start()

    def configure(self, disabled, fullmode=False):
        with self._condition:
            previous, previous_mode = self._disabled, self._fullmode
            self._disabled = {name for name in disabled if isinstance(name, str)}
            self._fullmode = bool(fullmode)
            self._configured = True
            for owner in self._provider_order:
                slot = self._slots[owner]
                if slot.name in self._disabled and slot.name not in previous:
                    slot.epoch += 1
                    self._fresh.difference_update(s.key for s in slot.statuses)
                    slot.statuses, slot.pending = [], False
                    slot.aliases.clear()
                    self._edges.add(owner)
                    self._dirty_display = True
                    self._cancel_owner_locked(slot)
                elif slot.name not in self._disabled and slot.name in previous:
                    self._edges.discard(owner)
                    self._queue_locked(owner, "poll")
                if slot.name == "playstation" and previous_mode != self._fullmode:
                    slot.epoch += 1
                    self._cancel_owner_locked(slot)
                    if slot.name not in self._disabled:
                        self._queue_locked(owner, "poll")
            self._condition.notify_all()
        self.pump()

    def request_poll(self, force=False):
        with self._condition:
            if self._closed:
                return False
            for owner in self._provider_order:
                slot = self._slots[owner]
                if slot.name not in self._disabled:
                    if force:
                        slot.failures, slot.retry_at, slot.circuit = 0, 0.0, False
                    self._queue_locked(owner, "poll")
            self._condition.notify_all()
        self.pump()
        return True

    def consume(self):
        self.pump()
        with self._condition:
            statuses, owners, groups = [], {}, {}
            for owner in self._provider_order:
                slot = self._slots[owner]
                groups.setdefault(slot.name, []).append(owner)
                if slot.name in self._disabled:
                    continue
                for status in slot.statuses:
                    statuses.append(replace(status, ui_params=dict(status.ui_params)))
                    owners[status.key] = slot.name
            completed = set()
            for name, members in groups.items():
                if (any(owner in self._edges for owner in members)
                        and all(owner in self._edges or name in self._disabled for owner in members)):
                    completed.add(name)
                    self._edges.difference_update(members)
            fresh = self._fresh.intersection(owners)
            candidates, target_count = {}, {}
            for status in statuses:
                target_count[status.key] = target_count.get(status.key, 0) + 1
            for owner in self._provider_order:
                slot = self._slots[owner]
                if slot.name not in self._disabled:
                    current = {status.key for status in slot.statuses
                               if status.key in fresh and status.online and not status.polling_stale}
                    for old, target in slot.aliases.items():
                        if target in current and old not in owners:
                            candidates.setdefault(old, set()).add(target)
                # Metadata and its matching observation are a one-shot edge.
                slot.aliases.clear()
            unique = {old: next(iter(targets)) for old, targets in candidates.items()
                      if len(targets) == 1 and target_count[next(iter(targets))] == 1}
            reverse_count = {}
            for target in unique.values():
                reverse_count[target] = reverse_count.get(target, 0) + 1
            aliases = {old: target for old, target in unique.items() if reverse_count[target] == 1}
            self._fresh.clear()
            return PollBatch(statuses, fresh, completed, owners, aliases)

    def signature(self):
        self.pump()
        with self._condition:
            return self._usb_signature, self._xinput_signature

    def pending(self):
        with self._condition:
            return any(s.pending for s in self._slots.values() if s.name not in self._disabled)

    def diagnostics(self):
        with self._condition:
            lines = list(self._hid_diagnostics)
            for slot in self._slots.values():
                if slot.name in self._disabled:
                    continue
                if slot.lines:
                    lines.append("[Provider: " + slot.owner + "]")
                    lines.extend(slot.lines)
                if slot.lease and not slot.running:
                    lines.append("[" + slot.owner + "] native cleanup pending; device lease retained")
                if slot.circuit:
                    state = ("recovery poll in progress" if slot.recovering else
                             "automatic retry after cooldown when native transport is idle")
                    lines.append("[" + slot.owner + "] paused after three failures; " + state)
            return lines

    def request_diagnostics(self):
        with self._condition:
            if self._diag_event is not None and not self._diag_event.is_set():
                return self._diag_event
            self._diag_event = threading.Event()
            if self._closed or self._diagnostics_reader is None:
                self._diag_event.set()
            else:
                self._diag_deadline = self._clock() + 10.0
                self._queue_locked("discovery", "diagnostics", priority=True)
        return self._diag_event

    def stats(self):
        with self._condition:
            return dict(workers=2, alive=sum(w.is_alive() for w in self._workers),
                        running=sum(s.running for s in self._slots.values()),
                        leased=sum(s.lease for s in self._slots.values()),
                        queued=len(self._jobs), peak_running=self._peak_running,
                        closed=self._closed)

    def stop_admission(self):
        with self._condition:
            self._closed = True
            self._jobs.clear()
            self._queued.clear()
            for slot in self._slots.values():
                slot.epoch += 1
                self._cancel_owner_locked(slot)
            self._finish_diagnostics_locked()
            self._reap_locked()
            self._condition.notify_all()

    def close(self, timeout=0.5):
        deadline = time.monotonic() + min(0.5, max(0.0, timeout))
        self.stop_admission()
        while True:
            self.pump()
            with self._condition:
                clean = self._workers_stop
            remaining = deadline - time.monotonic()
            if clean or remaining <= 0:
                break
            with self._condition:
                self._condition.wait(min(0.01, remaining))
        if clean:
            for worker in self._workers:
                if worker is not threading.current_thread():
                    worker.join(max(0.0, deadline - time.monotonic()))
        return clean

    def _activity_wake(self):
        with self._condition:
            if self._closed:
                return
            self._dirty_full = True
        self._emit()

    def _queue_locked(self, owner, kind, priority=False):
        job = (owner, kind)
        if (self._closed or job in self._queued
                or (kind == "poll" and self._slots[owner].recovering)):
            return
        self._queued.add(job)
        (self._jobs.appendleft if priority else self._jobs.append)(job)
        self._condition.notify_all()

    def _cancel_owner_locked(self, slot):
        if slot.lease:
            try:
                self._cancel(slot.owner)
            except Exception:
                # Failure to request cancellation cannot release ownership.
                slot.lines = ["Cancellation request failed; native ownership retained"]

    def _idle_locked(self, slot):
        try:
            return bool(self._idle(slot.owner))
        except Exception:
            return False

    def _reap_locked(self):
        changed = False
        for slot in self._slots.values():
            if slot.lease and not slot.running and self._idle_locked(slot):
                slot.lease = False
                changed = True
        if (self._closed and not self._workers_stop
                and not any(s.running or s.lease for s in self._slots.values())):
            self._workers_stop = True
            changed = True
        if changed:
            self._condition.notify_all()

    def _fail_locked(self, slot, reason, now):
        if slot.expired:
            return
        slot.expired = True
        log.warning("Provider %s: %s", slot.owner, str(reason)[:1024])
        slot.failures += 1
        slot.retry_at = now + min(60.0, 5.0 * 2 ** min(slot.failures - 1, 4))
        slot.circuit = slot.name != "discovery" and slot.failures >= 3
        if slot.circuit:
            slot.retry_at = now + _CIRCUIT_COOLDOWN
        slot.lines = ["I/O unavailable: " + str(reason)[:1024]]
        slot.pending = False
        slot.aliases.clear()
        self._fresh.difference_update(s.key for s in slot.statuses)
        self._edges.discard(slot.owner)
        slot.statuses = [replace(s, online=False, charging=False, charging_known=False,
                                 polling_stale=True, polling_paused=False, wake_on_input=False)
                         for s in slot.statuses]
        self._cancel_owner_locked(slot)
        if slot.name != "discovery" and slot.name not in self._disabled and not self._closed:
            self._queue_locked(slot.owner, "poll")
        if slot.kind == "diagnostics":
            self._finish_diagnostics_locked()
        self._dirty_display = True

    def _claim_locked(self):
        self._reap_locked()
        now = self._clock()
        for _ in range(len(self._jobs)):
            owner, kind = self._jobs.popleft()
            slot = self._slots[owner]
            if (self._closed or slot.name in self._disabled
                    or (kind == "diagnostics" and (self._diag_event is None or self._diag_event.is_set()))):
                self._queued.discard((owner, kind))
                continue
            blocked = (slot.running or slot.lease or now < slot.retry_at
                       or (slot.circuit and (kind != "poll" or not self._idle_locked(slot)))
                       or (kind == "signature" and (owner, "poll") in self._queued)
                       or any(other.lease and frozenset((slot.name, other.name)) in _CONFLICTS
                              for other in self._slots.values() if other is not slot))
            if blocked:
                self._jobs.append((owner, kind))
                continue
            self._queued.discard((owner, kind))
            slot.recovering = slot.circuit
            slot.running = slot.lease = True
            slot.generation += 1
            slot.expired = False
            slot.kind = kind
            slot.deadline = now + _TIMEOUTS.get(slot.name, 20.0)
            self._peak_running = max(self._peak_running, sum(s.running for s in self._slots.values()))
            return slot, kind, slot.epoch, self._fullmode
        return None

    @staticmethod
    def _error(error):
        try:
            return type(error).__name__ + ": " + str(error)[:1000]
        except BaseException:
            return "Provider failed; exception text unavailable"

    @staticmethod
    def _statuses(values):
        if not isinstance(values, (list, tuple)) or len(values) > _MAX_PROVIDER_STATUSES:
            raise ValueError("invalid or oversized provider result")
        result = []
        for status in values:
            if not isinstance(status, DeviceStatus):
                raise TypeError("provider result is not a DeviceStatus")
            level = status.level
            valid_level = (isinstance(level, (int, float)) and not isinstance(level, bool)
                           and 0 <= level <= 100)
            if level is not None and not valid_level:
                # Derived text must not outlive the rejected measurement.
                result.append(replace(status, level=None, approx="", ui_message="", ui_params={}))
            else:
                # Match PR415's reviewed App backstop: validate before rounding,
                # so fractions outside the range are never rounded into it.
                normalized = int(round(level)) if isinstance(level, float) else level
                result.append(replace(status, level=normalized, ui_params=dict(status.ui_params)))
        return result

    @staticmethod
    def _observed(status):
        return (status.online and not status.polling_stale and not status.polling_paused
                and status.level is not None)

    @classmethod
    def _recovery_values(cls, previous, values):
        """Bound the recovery union without displacing current or stale readings."""
        result, seen = [], set()
        groups = ([s for s in values if cls._observed(s)],
                  [s for s in previous if s.polling_stale and s.level is not None],
                  [s for s in previous if s.polling_stale and s.level is None],
                  values)
        for group in groups:
            for status in group:
                if len(result) >= _MAX_PROVIDER_STATUSES:
                    return result
                if status.key not in seen:
                    result.append(status)
                    seen.add(status.key)
        return result

    @staticmethod
    def _alias_metadata(provider):
        """Validate bounded, cached provider metadata independently of readings."""
        try:
            method = getattr(provider, "legacy_key_aliases", None)
            if method is None:
                return {}, None
            values = method()
            if type(values) is not dict or len(values) > 256:
                raise ValueError("aliases must be a dict with at most 256 entries")
            result = {}
            for old, target in values.items():
                if (type(old) is not str or type(target) is not str
                        or not 1 <= len(old) <= 256 or not 1 <= len(target) <= 256
                        or old == target
                        or any(ord(char) < 32 or ord(char) == 127 for char in old + target)):
                    raise ValueError("invalid alias key")
                result[old] = target
            return result, None
        except BaseException:
            # Metadata is optional: a bad implementation must not erase the
            # successful battery reading or terminate a fixed provider worker.
            return {}, "Legacy device-key metadata unavailable; reading retained"

    def _execute(self, slot, kind, fullmode):
        with self._scope(slot.owner) as scope:
            if kind == "discovery":
                value = self._signature_reader()
            elif kind == "diagnostics":
                value = self._diagnostics_reader()
            elif kind == "signature":
                value = slot.provider.connected_slots()
            else:
                if slot.name == "playstation":
                    slot.provider.switch_bluetooth = fullmode
                value = self._statuses(slot.provider.poll())
            if scope is not None:
                unsafe = getattr(scope, "unsafe", False) or getattr(scope, "cancelled", False)
                failed = getattr(scope, "failed", False)
                # A refused report size followed by a successful documented
                # fallback is ordinary provider behavior. Only current readings
                # and confirmed retirement permit that exception: cached offline
                # values and empty/all-unknown results cannot turn an I/O error
                # into absence. An explicitly connected unknown endpoint does
                # not erase another endpoint's successfully read battery.
                fallback = (kind == "poll" and bool(value)
                            and all(s.online and not s.polling_stale for s in value)
                            and any(s.level is not None for s in value))
                if unsafe or (failed and (not fallback or not self._idle(slot.owner))):
                    raise OSError(str(getattr(scope, "error", "transport unavailable"))[:1024])
            lines, pending = [], False
            if kind == "poll":
                try:
                    lines = [str(line)[:1024] for line in slot.provider.diagnostics()[:128]]
                except Exception:
                    lines = ["Provider diagnostics unavailable"]
                pending = getattr(slot.provider, "pending", False) is True
            return value, lines, pending

    def _worker(self):
        while True:
            with self._condition:
                job = self._claim_locked()
                if job is None:
                    if self._workers_stop:
                        return
                    self._condition.wait(0.05)
                    continue
            slot, kind, epoch, fullmode = job
            value = None
            lines, pending, error, aliases = [], False, None, {}
            try:
                value, lines, pending = self._execute(slot, kind, fullmode)
            except BaseException as exc:
                error = self._error(exc)
            if kind == "poll" and error is None:
                with self._condition:
                    # Do not even request metadata from a cancelled, disabled,
                    # expired or otherwise superseded observation. Keep its
                    # existing worker and device lease while reading metadata.
                    eligible = (not self._closed and epoch == slot.epoch
                                and slot.name not in self._disabled and not slot.expired
                                and slot.deadline is not None and self._clock() < slot.deadline
                                and (not slot.recovering or any(self._observed(s) for s in value)))
                if eligible:
                    aliases, metadata_note = self._alias_metadata(slot.provider)
                    if metadata_note:
                        lines.append(metadata_note)
            with self._condition:
                now = self._clock()
                if slot.deadline is not None and now >= slot.deadline and not slot.expired:
                    self._fail_locked(slot, "provider deadline expired", now)
                accepted = not self._closed and epoch == slot.epoch and slot.name not in self._disabled
                if accepted and error is not None:
                    self._fail_locked(slot, error, now)
                elif accepted and not slot.expired:
                    recovered = (kind == "poll" and slot.recovering)
                    observed = {s.key for s in value if self._observed(s)} if recovered else None
                    if not recovered or observed:
                        slot.failures, slot.retry_at, slot.circuit = 0, 0.0, False
                    else:
                        slot.retry_at = now + _CIRCUIT_COOLDOWN
                    if kind == "poll":
                        self._fresh.difference_update(s.key for s in slot.statuses)
                        if recovered:
                            value = self._recovery_values(slot.statuses, value)
                            if not observed:
                                pending, aliases = False, {}
                                lines.append("Recovery has no current battery reading; cached values retained")
                        slot.statuses, slot.lines, slot.pending, slot.aliases = value, lines, pending, aliases
                        self._fresh.update(observed if recovered else
                                           (s.key for s in value
                                            if not s.polling_stale and not s.polling_paused))
                        if not recovered or observed:
                            self._edges.add(slot.owner)
                        self._dirty_display = True
                    elif kind == "diagnostics":
                        if isinstance(value, (list, tuple)):
                            self._hid_diagnostics = [str(line)[:1024] for line in value[:128]]
                        self._finish_diagnostics_locked()
                        self._dirty_display = True
                    elif value is not None:
                        attribute = "_usb_signature" if kind == "discovery" else "_xinput_signature"
                        if value != getattr(self, attribute):
                            setattr(self, attribute, value)
                            self._dirty_full = True
                            for target in self._slots.values():
                                if target.failures:
                                    target.failures, target.retry_at, target.circuit = 0, 0.0, False
                slot.running = False
                slot.recovering = False
                slot.deadline = None
                self._reap_locked()
                if (slot.failures and slot.name != "discovery"
                        and not self._closed and slot.name not in self._disabled):
                    self._queue_locked(slot.owner, "poll")
            self._emit()

    def _finish_diagnostics_locked(self):
        self._diag_deadline = None
        if self._diag_event is not None:
            self._diag_event.set()

    def _emit(self):
        with self._condition:
            full, display = self._dirty_full, self._dirty_display
            self._dirty_full = self._dirty_display = False
        if full or display:
            try:
                self._notify(full)
            except Exception:
                pass

    def pump(self):
        with self._condition:
            now = self._clock()
            self._reap_locked()
            for slot in self._slots.values():
                if slot.running and slot.deadline is not None and now >= slot.deadline:
                    self._fail_locked(slot, "provider deadline expired", now)
            if self._diag_deadline is not None and now >= self._diag_deadline:
                self._hid_diagnostics.append("HID diagnostics deadline expired; previous cached data retained")
                self._hid_diagnostics = self._hid_diagnostics[-128:]
                self._finish_diagnostics_locked()
                self._dirty_display = True
            if not self._closed and self._configured and now >= self._signature_due:
                self._signature_due = now + 2.5
                if self._signature_reader is not None:
                    self._queue_locked("discovery", "discovery")
                for owner in self._provider_order:
                    slot = self._slots[owner]
                    if slot.name == "xinput" and slot.name not in self._disabled:
                        self._queue_locked(owner, "signature")
        self._emit()
