"""Red Square Alumix TKL (RSQ-20055) on its 2.4 GHz receiver (342D:E411), #460.

The receiver exposes the keyboard's config channel as usage page 0xFF60, usage 0x61.
Red Square's own web configurator (conf.red-square.org) sends a 32-byte tunnel frame on
output report 7:

    07 | 05 01 01 0A 07 8B 00 ...            report id 7, command 0x8B

and the keyboard answers on the same report:

    07 | 05 A1 01 0A 07 8B <sleep> <debounce> <battery> <module> ...

The battery is byte 8 of the reply (counted without the report id).  This is the same
settings read the configurator calls "getDeviceSleepTime"; its model table marks
RSQ-20055 as having no battery UI, but the response still carries the level.

The same configurator's wireless-module driver has no charging query or charging event
for this family.  Halo therefore reports the level and leaves charging unknown rather
than guessing; the reporter is asked to check the charger separately (#460).

Only this exact product string is queried, because 342D:E411 is shared by several
Red Square models.  The receiver's DFU collection is never touched.
"""
from __future__ import annotations

import time
from typing import List, Optional

try:
    import hid
except ImportError:  # pragma: no cover - only when the package is missing
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x342D
PID = 0xE411
PRODUCT = "RSQ_20055_2.4G"
NAME = "Red Square Alumix TKL"
KEY = "redsquare:20055"

USAGE_PAGE, USAGE = 0xFF60, 0x0061
REPORT_ID = 0x07
REPORT_SIZE = 32
CMD_SETTINGS = 0x8B

READ_TIMEOUT_MS = 120
READ_ATTEMPTS = 6
ASLEEP_KEEP = 300


def make_request() -> List[int]:
    """``07 | 05 01 01 0A 07 8B 00 ...`` - report id 7, settings read."""
    frame = [0] * REPORT_SIZE
    frame[0:6] = [0x05, 0x01, 0x01, 0x0A, REPORT_ID, CMD_SETTINGS]
    return [REPORT_ID] + frame


def _without_report_id(report) -> List[int]:
    data = list(report)
    if len(data) >= 2 and data[0] == REPORT_ID and data[1] == 0x05:
        return data[1:]
    return data


def parse_reply(report) -> Optional[int]:
    """The level from ``05 A1 01 0A 07 8B ... <level>``, or None."""
    data = _without_report_id(report)
    if len(data) < 9 or data[0:6] != [0x05, 0xA1, 0x01, 0x0A, REPORT_ID, CMD_SETTINGS]:
        return None
    level = data[8]
    if not 1 <= level <= 100:
        return None
    return level


def pick(infos: List[dict]) -> Optional[dict]:
    for d in infos:
        if (d.get("product_id") == PID
                and (d.get("product_string") or "").strip() == PRODUCT
                and d.get("usage_page") == USAGE_PAGE
                and d.get("usage") == USAGE):
            return d
    return None


class RedSquareProvider(Provider):
    name = "redsquare"

    def __init__(self):
        self._diag: List[str] = []
        self._level: Optional[int] = None
        self._last_good = 0.0

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(redsquare): %s", e)
            return []

        mine = [d for d in infos if d.get("product_id") == PID
                and (d.get("product_string") or "").strip() == PRODUCT]
        if not mine:
            return []
        d = pick(mine)
        if d is None:
            self._diag.append(f"[RedSquare] {PRODUCT}: no 0xFF60:0x61 channel")
            return [self._status(None, False)]

        self._diag.append(f"[RedSquare] pid={PID:04x} '{PRODUCT}' "
                          f"iface={d.get('interface_number')} 0xFF60:0x61")
        level = self._read(d["path"])
        if level is None:
            self._diag.append("  no answer (keyboard asleep or off)")
            fresh = self._level is not None and time.time() - self._last_good < ASLEEP_KEEP
            return [self._status(self._level if fresh else None, False)]
        self._level = level
        self._last_good = time.time()
        self._diag.append(f"  -> {level}% (charging not reported by this firmware)")
        return [self._status(level, True)]

    def _read(self, path) -> Optional[int]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except Exception as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            written = dev.write(make_request())
            if written is not None and written < 0:
                self._diag.append(f"  write refused ({written})")
                return None
            for _ in range(READ_ATTEMPTS):
                reply = dev.read(REPORT_SIZE + 1, READ_TIMEOUT_MS)
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 12)}")
                level = parse_reply(reply)
                if level is not None:
                    return level
        except (OSError, ValueError) as e:  # pragma: no cover - hidapi failures
            self._diag.append(f"  read: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _status(self, level: Optional[int], online: bool) -> DeviceStatus:
        return DeviceStatus(KEY, NAME, level, False, online, self.name,
                            kind="keyboard", wake_on_input=True,
                            charging_known=False)

    def diagnostics(self) -> List[str]:
        return list(self._diag)
