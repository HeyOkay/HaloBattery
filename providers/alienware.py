"""Alienware Pro Wireless Gaming Keyboard over its 2.4 GHz receiver (#287).

The receiver (413c:211d, "Alienware Pro KB Gaming Receiver") publishes one vendor
collection, ff00:0001 on interface 1. Alienware Command Center's own traffic, captured
with USBPcap for #287, shows the read:

  * SET_REPORT, class/interface, wValue 0x0300 (unnumbered, report type 3 = feature),
    wIndex 1, 64 bytes: ``0e a1 00 ...``
  * the answer arrives as a 64-byte input report on the same collection:
    ``0e a1 25 ...`` - 0x25 = 37, which is exactly the percentage Command Center showed
    in both captures (process1 and process2).

Only the level command (0xA1) is mirrored here. The captures were taken on the wireless
link and carry no charging bit, so the charger state is reported as unknown rather than
guessed (``charging_known=False`` keeps alerts quiet).
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, log

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x413C
PID = 0x211D
NAME = "Alienware Pro Wireless Gaming Keyboard"
KEY = f"alienware:{PID:04x}"

VENDOR_PAGE = 0xFF00             # the receiver's vendor collection (#287 diagnostics)
REPORT_SIZE = 64                 # feature write and input report, as captured
BATTERY_CMD = 0xA1               # Command Center's battery read
REPORT_MARK = 0x0E               # first byte of every vendor frame

READ_TIMEOUT_MS = 250
READ_ATTEMPTS = 6                # ~1.5 s; the answer comes within milliseconds awake
ASLEEP_KEEP = 300                # s, as in the other receiver providers


def make_request() -> List[int]:
    """The captured feature report: mark, 0xA1, zero padding to 64 bytes."""
    frame = [0] * REPORT_SIZE
    frame[0] = REPORT_MARK
    frame[1] = BATTERY_CMD
    return frame


def parse_reply(frame) -> Optional[int]:
    """The level from ``0e a1 <level> ...``, or None for any other report."""
    if frame is None or len(frame) < 3:
        return None
    if frame[0] != REPORT_MARK or frame[1] != BATTERY_CMD:
        return None
    level = frame[2]
    return level if 0 < level <= 100 else None


def pick(infos: List[dict]) -> Optional[dict]:
    """The vendor collection; the keyboard/consumer ones carry no battery exchange."""
    for d in infos:
        if d.get("usage_page") == VENDOR_PAGE:
            return d
    return infos[0] if infos else None


class AlienwareProvider(Provider):
    name = "alienware"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, float]] = None
        self._chosen: Optional[bytes] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(alienware): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        d = pick(mine)
        if d is None:  # pragma: no cover - pick() only returns None for an empty list
            return []
        self._diag.append(f"[Alienware] pid={PID:04x} '{NAME}' "
                          f"iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
        level = self._ask(d)
        if level is not None:
            self._last = (level, time.time())
            return [DeviceStatus(KEY, NAME, level, False, True, "alienware",
                                 kind="keyboard", charging_known=False)]
        # The receiver is still plugged in, so the keyboard is asleep or switched off:
        # keep the last reading greyed while it is fresh, then show "no link".
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, self._last[0], False, False, "alienware",
                                 kind="keyboard", charging_known=False)]
        return [DeviceStatus(KEY, NAME, None, False, False, "alienware",
                             kind="keyboard", charging_known=False)]

    def _ask(self, d: dict) -> Optional[int]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
        except Exception as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            sent = dev.send_feature_report(bytes(make_request()))
            if sent is not None and sent < 0:
                self._diag.append(f"  feature write refused ({sent})")
                return None
            for _ in range(READ_ATTEMPTS):
                frame = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
                level = parse_reply(frame)
                if level is not None:
                    return level
        except Exception as e:  # pragma: no cover - hidapi raises OSError
            self._diag.append(f"  read: {e}")
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
