"""Logitech G325 LIGHTSPEED over its vendor 0x50/0x23 report (#232).

The G325 does not answer HID++ on its `ffa0:0001` collection, and it is not the
older G522 layout either.  The working exchange was captured by @Platerios in
Sapd/HeadsetControl issue #581:

    request (64-byte output report, report id 0x50):
        50 23 0b 00 03 1a 00 03 00 07 0a 00 ...
    first reply:  50 23 03 00 03 1a 00 00 ...          (ACK)
    battery reply: 50 23 0b 00 03 10 00 06 00 07 0a XX YY ZZ
        byte 11 = internal/raw value
        byte 12 = displayed percentage (matches G HUB)
        byte 13 = 0x00 on battery, 0x02 charging

The same collection (usage page ffa0, interface 3) carries both directions.
Only the G325's own pid is claimed here; the HID++ reader in logitech.py does
not know it.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x046D
PID = 0x0B21
NAME = "Logitech G325 LIGHTSPEED"
KEY = "logitech:g325"
KIND = "headset"

REPORT_ID = 0x50
PACKET_SIZE = 64
READ_ATTEMPTS = 4
READ_TIMEOUT_MS = 1500
ASLEEP_KEEP = 300

BATTERY_REQUEST = [0x50, 0x23, 0x0B, 0x00, 0x03, 0x1A, 0x00, 0x03,
                   0x00, 0x07, 0x0A]


def make_request() -> List[int]:
    frame = [0x00] * PACKET_SIZE
    frame[:len(BATTERY_REQUEST)] = BATTERY_REQUEST
    return frame


def parse_reply(r) -> Optional[Tuple[int, bool, bool]]:
    """(level, charging, charging_known) from a G325 battery reply, or None.

    Only the battery packet may become a reading; the ACK and any other
    packet are ignored.
    """
    if not r or len(r) < 14:
        return None
    data = list(r)
    if data[0] != 0x50 or data[1] != 0x23:
        return None
    if data[2] != 0x0B or data[5] != 0x10:
        return None
    level = data[12]
    if not 1 <= level <= 100:
        return None
    state = data[13]
    if state == 0x00:
        return level, False, True
    if state == 0x02:
        return level, True, True
    return level, False, False


def pick(infos: List[dict]) -> List[dict]:
    """The ffa0:0001 collection on interface 3, if Windows lists it first."""
    vendor = [d for d in infos if (d.get("usage_page") or 0) == 0xFFA0
              and (d.get("usage") or 0) == 0x0001]
    vendor.sort(key=lambda d: 0 if d.get("interface_number") == 3 else 1)
    return vendor


class LogitechG325Provider(Provider):
    name = "logitech_g325"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(logitech g325): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        self._diag.append(f"[Logitech G325] pid={PID:04x} '{NAME}'")
        parsed = None
        for d in pick(mine):
            self._diag.append(f"  trying iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            parsed = self._ask(d["path"])
            if parsed is not None:
                break
        if parsed is None:
            self._diag.append("  no battery reply (headset asleep or off)")
            if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
                level, charging, _ = self._last
                return [DeviceStatus(KEY, NAME, level, charging, False,
                                     "logitech", kind=KIND, wake_on_input=True)]
            return [DeviceStatus(KEY, NAME, None, False, False, "logitech", kind=KIND)]
        level, charging, known = parsed
        self._last = (level, charging, time.time())
        self._diag.append(f"  -> {level}%{' charging' if charging else ' on battery'}")
        return [DeviceStatus(KEY, NAME, level, charging, True, "logitech",
                             kind=KIND, wake_on_input=True, charging_known=known)]

    def _ask(self, path) -> Optional[Tuple[int, bool, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            sent = dev.write(bytes(make_request()))
            self._diag.append(f"    write -> {sent}")
            if sent is not None and sent < 0:
                return None
            for _ in range(READ_ATTEMPTS):
                try:
                    r = dev.read(PACKET_SIZE, READ_TIMEOUT_MS)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    continue
                if r:
                    self._diag.append(f"    reply: {hexdump(r, 16)}")
                parsed = parse_reply(r)
                if parsed is not None:
                    return parsed
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
