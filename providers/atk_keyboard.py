"""ATK V75X battery over its 2.4 GHz receiver.

ATK HUB's Evision keyboard driver uses output/input report 04 on FF1C:0092:
31 payload bytes, a little-endian request id at 0..1, command 1A at 2,
and battery/charge state at 7..8. No setup or configuration writes are needed.
The receiver id is shared with other keyboards, so also match the product name.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from .atk_activity import KeyboardActivity
from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID, PID = 0x320F, 0x5088
USAGE_PAGE, USAGE = 0xFF1C, 0x0092
PRODUCT = "ATK V75X 2.4G Dongle"
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
        self._path = None
        self._paused = None

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
        for d in infos:
            if (d.get("product_id") != PID
                    or (d.get("usage_page"), d.get("usage")) != (USAGE_PAGE, USAGE)
                    or (d.get("product_string") or "").strip().casefold() != PRODUCT.casefold()):
                continue
            self._diag.append(f"[ATK V75X] {VID:04x}:{PID:04x} 2.4 GHz")
            paths = [e["path"] for e in infos if e.get("product_id") == PID
                     and (e.get("product_string") or "").strip().casefold() == PRODUCT.casefold()
                     and (e.get("usage_page"), e.get("usage")) == (1, 6)]
            self.activity.configure(paths)
            if self._path != d["path"]:
                self._path = d["path"]
                self._cached = None
            if not self.activity.start() or not self.activity.active():
                if self._paused is not True:
                    log.info("ATK battery polling paused; waiting for keyboard activity")
                self._paused = True
                self._diag.append("  queries paused: waiting for ATK keyboard activity")
                return [self._idle_status()]
            if self._paused is not False:
                log.info("ATK battery polling resumed after keyboard activity")
            self._paused = False
            answer = self._read(d["path"])
            if answer is not None:
                self._cached = answer
                log.info("ATK battery refreshed: %d%%, charging=%s", *answer)
            else:
                return [self._idle_status(paused=False)]
            level, charging = answer if answer is not None else (None, False)
            return [DeviceStatus("atk_keyboard:320f:5088", "ATK V75X", level, charging,
                                 answer is not None, self.name, kind="keyboard",
                                 wake_on_input=answer is None)]
        self._cached = None
        self._path = None
        self.activity.configure([])
        return []

    def _idle_status(self, paused=True):
        level = self._cached[0] if self._cached else None
        text = "" if paused else (f"{level}% (last known; no fresh reply)" if level is not None
                                  else "battery not reported yet")
        return DeviceStatus("atk_keyboard:320f:5088", "ATK V75X", level, False, False,
                            self.name, approx=text, kind="keyboard", polling_paused=paused)

    def close(self):
        self.activity.close()

    def diagnostics(self) -> List[str]:
        return list(self._diag)
