"""ATK V75X battery over its 2.4 GHz receiver or its USB cable.

ATK HUB's Evision keyboard driver uses output/input report 04 on FF1C:0092:
31 payload bytes, a little-endian request id at 0..1, command 1A at 2,
and battery/charge state at 7..8. No setup or configuration writes are needed.
The cable (5055) answers the same request on the same collection.
These ids are shared with other keyboards, so also match the product name.
Source: ATK HUB 3.2.29, Evision keyboard controller in
https://bpcdn.atkgear.com/hub-v3/production/3.2.29/static/keyboard-info.util-D8BvmqVT.js
Receiver and cable captures are recorded in docs/protocols.md (ATK V75X).
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from .atk_activity import REFRESH, RETRY, KeyboardActivity
from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID, PID = 0x320F, 0x5088
USAGE_PAGE, USAGE = 0xFF1C, 0x0092
PRODUCT = "ATK V75X 2.4G Dongle"
# PR #407, pinned 675198a6b37bb221: captured N75 Pro response to the
# same ATK HUB Evision battery command, on 320F:5088 FF1C:0092.
# The cable identity below remains ATK-only; shared USB ids are not enough.
PRODUCT_NAMES = {PRODUCT: "ATK V75X", "VGN N75 PRO 2.4G Dongle": "VGN N75 PRO"}
WIRED_PID, WIRED_PRODUCT = 0x5055, "ATK V75X"
REPORT_ID, CMD_BATTERY = 0x04, 0x1A
TIMEOUT, READ_MS = 1.0, 100


def battery_reply(report, sequence: int) -> Optional[Tuple[int, bool]]:
    """Only accept the battery response to this request, including the report id."""
    if (len(report) < 10 or report[0] != REPORT_ID
            or report[1] != (sequence & 0xFF) or report[2] != (sequence >> 8)
            or report[3] != CMD_BATTERY or any(report[5:7])):
        return None
    level, state = report[8:10]
    if not 0 <= level <= 100 or state not in (0, 1, 2):
        return None
    # V75X captures: 1 while charging, 0 after unplugging. ATK's driver also
    # recognizes 2 as charging, so retain that documented variant.
    return level, state in (1, 2)


class AtkKeyboardProvider(Provider):
    name = "atk_keyboard"

    def __init__(self, activity=None):
        self._diag: List[str] = []
        self._sequence = 0
        self.activity = activity if activity is not None else KeyboardActivity()
        self._cached = None
        self._fresh = None    # monotonic time of the last good reading
        self._tried = None    # monotonic time of the last request
        self._path = None
        self._paused = None
        self._label = "ATK V75X"

    def _read(self, path) -> Optional[Tuple[int, bool]]:
        self._sequence = self._sequence % 0xFFFF + 1
        sequence = self._sequence
        dev = hid.device()
        try:
            dev.open_path(path)
            # The battery query alone works; do not enter a configuration session.
            dev.write([REPORT_ID, sequence & 0xFF, sequence >> 8, CMD_BATTERY] + [0] * 28)
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline:
                report = dev.read(64, READ_MS)
                answer = battery_reply(report, sequence)
                if answer is not None:
                    self._diag.append(f"  battery: {hexdump(report, 10)} -> {answer[0]}%, charging={answer[1]}")
                    return answer
            self._diag.append("  no valid battery reply (asleep, off, or busy)")
        except (OSError, ValueError) as e:
            self._diag.append(f"  battery read: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(atk_keyboard): %s", e)
            return []
        def found(pid, product, usage=(USAGE_PAGE, USAGE)):
            products = (product,) if isinstance(product, str) else product
            allowed = {value.casefold() for value in products}
            return [e for e in infos if e.get("vendor_id") == VID and e.get("product_id") == pid
                    and (e.get("usage_page"), e.get("usage")) == usage
                    and isinstance(e.get("product_string"), str)
                    and e["product_string"].strip().casefold() in allowed]
        # The cable wins when both are connected: it answers while charging.
        for d in found(WIRED_PID, WIRED_PRODUCT):
            self._select_label("ATK V75X")
            self._diag.append(f"[ATK V75X] {VID:04x}:{WIRED_PID:04x} USB cable")
            self.activity.configure([])
            self._switch(d["path"])
            return [self._poll_wired(d["path"])]
        for d in found(PID, PRODUCT_NAMES):
            product = d["product_string"].strip()
            label = next(name for target, name in PRODUCT_NAMES.items()
                         if product.casefold() == target.casefold())
            self._select_label(label)
            self._diag.append(f"[{self._label}] {VID:04x}:{PID:04x} 2.4 GHz")
            self.activity.configure([e["path"] for e in found(PID, product, (1, 6))])
            self._switch(d["path"])
            if not self.activity.start() or not self.activity.active():
                if self._paused is not True:
                    log.info("ATK battery polling paused; waiting for keyboard activity")
                self._paused = True
                self._diag.append("  queries paused: waiting for ATK keyboard activity")
                return [self._idle_status()]
            if self._paused is not False:
                log.info("ATK battery polling resumed after keyboard activity")
            self._paused = False
            # Battery moves slowly: while typing, one request per REFRESH is plenty,
            # and a failed one is not repeated sooner than RETRY.
            now = time.monotonic()
            if self._fresh is not None and 0 <= now - self._fresh < REFRESH:
                self._diag.append(f"  cached: next query after {REFRESH - (now - self._fresh):.0f} s")
                return [self._status()]
            if self._tried is not None and 0 <= now - self._tried < RETRY:
                self._diag.append("  last query failed; retrying later")
                return [self._idle_status(paused=False)]
            self._tried = now
            answer = self._read(d["path"])
            if answer is None:
                return [self._idle_status(paused=False)]
            self._cached = answer
            self._fresh = time.monotonic()
            self.activity.hold(REFRESH)
            log.info("ATK battery refreshed: %d%%, charging=%s", *answer)
            return [self._status()]
        self._cached = self._fresh = self._tried = None
        self._path = None
        self.activity.configure([])
        return []

    def _select_label(self, label):
        # Another supported brand is a different keyboard, even when its USB
        # path/receiver id was reused. Never relabel the previous one's level.
        if self._label != label:
            self._cached = self._fresh = self._tried = None
            self._paused = None
            self._label = label

    def _switch(self, path):
        """Cable and dongle are the same keyboard: keep its level across a switch,
        but not the charging state or the request timers."""
        if self._path != path:
            self._path = path
            self._cached = (self._cached[0], False) if self._cached else None
            self._fresh = self._tried = None

    def _poll_wired(self, path) -> DeviceStatus:
        # Powered by the cable, so there is no sleep to protect: read every poll.
        self._paused = None
        answer = self._read(path)
        if answer is None:
            return self._idle_status(paused=False)
        if answer != self._cached:
            log.info("ATK battery (cable): %d%%, charging=%s", *answer)
        self._cached = answer
        return self._status()

    def _status(self):
        level, charging = self._cached
        return DeviceStatus("atk_keyboard:320f:5088", self._label, level, charging, True,
                            self.name, kind="keyboard")

    def _idle_status(self, paused=True):
        level = self._cached[0] if self._cached else None
        text = "" if paused else (f"{level}% (last known; no fresh reply)" if level is not None
                                  else "battery not reported yet")
        return DeviceStatus("atk_keyboard:320f:5088", self._label, level, False, False,
                            self.name, approx=text, kind="keyboard", polling_paused=paused)

    def close(self):
        self.activity.close()

    def diagnostics(self) -> List[str]:
        return list(self._diag)
