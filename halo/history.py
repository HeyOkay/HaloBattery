"""Battery history: how fast each device drains, and roughly how much use it has left.

Every poll feeds each device's reading in here. Time only counts while the device is
awake and on battery: a mouse that is asleep, a headset that is switched off or a PC
that is suspended adds nothing, so the estimate is "hours of use left", not hours on
the clock. A long gap between two readings (the PC slept, the app was closed) counts
as MAX_GAP at most.

The history starts over when the device charges: seen charging, or its level jumps up
by RESET_RISE or more (charged while the app was not running, or a fresh battery). The
drain rate is a least-squares fit of the level against the time used since then, and
no estimate is given until the device has been used for MIN_SPAN and dropped MIN_DROP
percentage points, so the first reading after a charge does not produce a wild guess.

The history is kept in %APPDATA%\\HaloBattery\\history.json, so an estimate survives a
restart of the app or of Windows.
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

MAX_GAP = 600.0          # s: the most one gap between two readings may add
MIN_SPAN = 1800.0        # s of use since the charge before an estimate is given
MIN_DROP = 3             # percentage points dropped since the charge, likewise
RESET_RISE = 3           # a rise this large without "charging" means it was charged
MAX_SAMPLES = 400        # per device; the oldest are dropped
FORGET_AFTER = 60 * 86400.0   # a device not seen for this long is forgotten
SAVE_EVERY = 300.0       # s between writes of history.json


def format_left(seconds: float) -> str:
    """12600 -> 'about 4 h of use left'."""
    hours = seconds / 3600.0
    if hours < 1:
        return "less than 1 h of use left"
    if hours < 48:
        return f"about {round(hours)} h of use left"
    return f"about {round(hours / 24)} days of use left"


class History:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        # key -> {"use": s used since the charge, "last": wall time of the last counted
        #         reading or None, "seen": wall time, "samples": [[use, level], ...]}
        self.devices: Dict[str, dict] = {}
        self.dirty = False
        self._saved = 0.0

    # ---------------- storage
    def load(self) -> None:
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        now = time.time()
        for key, d in (data.get("devices") or {}).items() if isinstance(data, dict) else ():
            try:
                samples = [[float(u), int(l)] for u, l in d.get("samples", [])][-MAX_SAMPLES:]
                entry = {"use": float(d.get("use", 0.0)), "last": None,
                         "seen": float(d.get("seen", now)), "samples": samples}
            except (AttributeError, TypeError, ValueError):
                continue                      # a damaged entry: start that device over
            if now - entry["seen"] < FORGET_AFTER:
                self.devices[str(key)] = entry

    def save(self, force: bool = False) -> None:
        """Write history.json when something changed, at most every SAVE_EVERY
        seconds unless forced (on exit)."""
        if not self.path or not self.dirty:
            return
        now = time.time()
        if not force and now - self._saved < SAVE_EVERY:
            return
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"devices": self.devices}, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
            self._saved = now
        except OSError:
            pass

    # ---------------- readings
    def record(self, key: str, level: Optional[int], charging: bool, online: bool,
               now: float, coarse: bool = False) -> None:
        """One poll's reading of one device. `coarse`: the device only reports rough
        steps (XInput, Switch controllers), too rough to measure a rate from."""
        if coarse or level is None:
            return
        d = self.devices.get(key)
        if d is None:
            d = self.devices[key] = {"use": 0.0, "last": None, "seen": now, "samples": []}
        d["seen"] = now
        if charging:
            if d["samples"] or d["use"]:
                self._reset(d)
            d["last"] = None
            return
        if not online:
            d["last"] = None                  # asleep: the time until it wakes does not count
            return
        if d["last"] is not None:
            d["use"] += min(max(now - d["last"], 0.0), MAX_GAP)
        d["last"] = now
        samples = d["samples"]
        if samples and level >= samples[-1][1] + RESET_RISE:
            self._reset(d)                    # charged somewhere the app did not see
            samples = d["samples"]
        if not samples or level < samples[-1][1]:
            samples.append([d["use"], level])  # a small rise is jitter: not a new sample
            del samples[:-MAX_SAMPLES]
            self.dirty = True

    def _reset(self, d: dict) -> None:
        d["use"] = 0.0
        d["samples"] = []
        self.dirty = True

    # ---------------- estimate
    def rate(self, key: str) -> Optional[float]:
        """Percentage points used per second of use, or None when not known yet."""
        d = self.devices.get(key)
        if d is None or len(d["samples"]) < 2:
            return None
        pts = list(d["samples"])
        # the time since the last drop counts too: a level that sits still for hours
        # slows the rate down instead of keeping the rate of the last drop
        last_level = pts[-1][1]
        if d["use"] > pts[-1][0]:
            pts.append([d["use"], last_level])
        span = pts[-1][0] - pts[0][0]
        if span < MIN_SPAN or pts[0][1] - last_level < MIN_DROP:
            return None
        n = len(pts)
        mean_t = sum(p[0] for p in pts) / n
        mean_l = sum(p[1] for p in pts) / n
        var = sum((p[0] - mean_t) ** 2 for p in pts)
        if var <= 0:
            return None
        slope = sum((p[0] - mean_t) * (p[1] - mean_l) for p in pts) / var
        return -slope if slope < 0 else None

    def seconds_left(self, key: str, level: Optional[int]) -> Optional[float]:
        r = self.rate(key)
        if r is None or level is None:
            return None
        return level / r

    def report(self) -> List[str]:
        """Lines for the diagnostics report."""
        lines = []
        for key, d in sorted(self.devices.items()):
            r = self.rate(key)
            s = d["samples"]
            est = (f"{r * 3600:.2f} %/h of use" if r is not None else "no estimate yet")
            lines.append(f"[{key}] {len(s)} sample(s), {d['use'] / 3600:.1f} h of use since "
                         f"the last charge, {est}")
        return lines or ["(no history yet)"]
