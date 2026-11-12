"""Alienware Pro Wireless Gaming Keyboard over its 2.4 GHz receiver (#287).

The receiver (413c:211d, "Alienware Pro KB Gaming Receiver") publishes one vendor
collection, ff00:0001 on interface 1. Alienware Command Center's own traffic, captured
with USBPcap for #287, shows the read:

  * SET_REPORT, class/interface, wValue 0x0300 (report id 0, report type 3 = feature),
    wIndex 1: ``00 | 0e a1 00 ...`` - 65 bytes on the wire, the report id in front
  * the answer arrives as an input report on the same collection:
    ``0e a1 25 [state] ...`` - 0x25 = 37, exactly the percentage Command Center showed in
    both captures; the charging capture (process3) shows byte 3 at 0x04 while the cable is
    in and 0x00 otherwise.

The first build sent the frame without the report id, so hidapi took 0x0e as the id and the
receiver never answered (#287: "no link" on every poll). Byte 3 is the charger state: 0 on
battery, 4 while charging - the only two values the captures contain - so it is reported as
charging while 4 and not charging otherwise; a value never seen is logged rather than
treated as a claim about the cable.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

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
REPORT_ID = 0x00                 # wValue 0x0300 in the capture
REPORT_SIZE = 64                 # the frame itself, without the report id
WIRE_SIZE = REPORT_SIZE + 1      # what hidapi is handed: [report id, frame...]
BATTERY_CMD = 0xA1               # Command Center's battery read
REPORT_MARK = 0x0E               # first byte of every vendor frame

READ_TIMEOUT_MS = 250
READ_ATTEMPTS = 6                # ~1.5 s; the answer comes within milliseconds awake
ASLEEP_KEEP = 300                # s, as in the other receiver providers


def make_request() -> List[int]:
    """``00 | 0e a1 00 ...`` - report id 0, then the captured 64-byte frame."""
    frame = [0] * REPORT_SIZE
    frame[0] = REPORT_MARK
    frame[1] = BATTERY_CMD
    return [REPORT_ID] + frame


def parse_reply(frame) -> Optional[Tuple[int, bool]]:
    """(level, charging) from ``[00 |] 0e a1 <level> <state> ...``, or None.

    The mark is looked for at 0 and 1 because hidapi returns the report id in front on
    some Windows builds and strips it on others.
    """
    if frame is None or len(frame) < 4:
        return None
    start = next((i for i in (0, 1) if len(frame) > i + 2 and frame[i] == REPORT_MARK
                  and frame[i + 1] == BATTERY_CMD), None)
    if start is None:
        return None
    level = frame[start + 2]
    if not 0 < level <= 100:
        return None
    state = frame[start + 3] if len(frame) > start + 3 else 0
    return level, state == 0x04


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
        parsed = self._ask(d)
        if parsed is not None:
            level, charging = parsed
            self._last = (level, time.time())
            return [DeviceStatus(KEY, NAME, level, charging, True, "alienware",
                                 kind="keyboard")]
        # The receiver is still plugged in, so the keyboard is asleep or switched off:
        # keep the last reading greyed while it is fresh, then show "no link".
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, self._last[0], False, False, "alienware",
                                 kind="keyboard")]
        return [DeviceStatus(KEY, NAME, None, False, False, "alienware",
                             kind="keyboard")]

    def _ask(self, d: dict) -> Optional[Tuple[int, bool]]:
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
                frame = dev.read(WIRE_SIZE, READ_TIMEOUT_MS)
                if not frame:
                    continue
                self._diag.append(f"  reply: {bytes(frame[:8]).hex()}")
                parsed = parse_reply(frame)
                if parsed is not None:
                    return parsed
        except Exception as e:  # pragma: no cover - hidapi raises OSError
            self._diag.append(f"  read: {e}")
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
