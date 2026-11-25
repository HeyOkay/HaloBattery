"""ROYUAN 2.4 GHz keyboard receivers (`3151:4011`).

The receiver answers a status request itself, so a keyboard's level is read
without relaying anything to the keyboard. The exchange is documented in
dniminenn/sharkfin's `docs/PROTOCOL.md` ("2.4 GHz receiver" and the status
table), measured there on a `3151:4011` receiver with a paired keyboard. That
project is GPL-3.0 and is used as the protocol reference only - the code below
is written from the wire description, not copied.

    feature report, report id 0, 64 payload bytes, on collection FFFF:0002
    request   F7 00 00 00 00 00 00 08 00 ...   0xF7 + bit7 checksum at byte 7
    reply     [1] keyboard level, percent
              [3] 0 while the keyboard is online, 1 while it is off
              [6] device type: 1 keyboard, 2 mouse, 3 both
              [0] 1 when a relayed reply is waiting (not used here)

A level of 0 is not a level: the receiver answers zeros before the keyboard has
reported. The status carries no charging flag, so `charging_known` stays False
rather than a guess. Only `3151:4011` on that collection is claimed, and the
request is read-only.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:                      # pragma: no cover - only without the package
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x3151
PID = 0x4011
CABLE_PID = 0x4015                      # the keyboard itself, seen only on a USB cable
NAME = "ROYUAN 2.4G keyboard"
KEY = f"royuan:{PID:04x}"

USAGE = (0xFFFF, 0x0002)                 # the receiver's configuration collection
STATUS_CMD = 0xF7
REPORT_ID = 0x00
LENGTH = 64                              # payload bytes after the report id
REPORT_LEN = LENGTH + 1
CHECKSUM_INDEX = 7
KIND_KEYBOARD, KIND_BOTH = 1, 3
READ_TIMEOUT_MS = 250
ATTEMPTS = 3
ATTEMPT_GAP = 0.02
ASLEEP_KEEP = 300                        # s, keep the last level greyed


def status_request() -> List[int]:
    """Report id 0, then 0xF7 with its bit7 checksum at payload byte 7."""
    frame = [REPORT_ID, STATUS_CMD] + [0] * (LENGTH - 1)
    frame[1 + CHECKSUM_INDEX] = (0xFF - STATUS_CMD) & 0xFF
    return frame


def parse_status(reply) -> Optional[Tuple[int, bool]]:
    """(level, online) from a receiver status reply, or None.

    The device type is the field a keyboard's own reply cannot produce by
    accident, so it gates the frame. Offline still carries the receiver's last
    level; the caller decides whether to show it greyed. A 65-byte buffer is
    hidapi's report id (zero) plus the payload; a 64-byte one is the payload
    alone, as some drivers return it.
    """
    if not isinstance(reply, (bytes, bytearray, list, tuple)):
        return None
    try:
        r = bytes(reply)
    except (TypeError, ValueError):
        return None
    if len(r) >= REPORT_LEN and r[0] == REPORT_ID:
        frame = r[1:1 + LENGTH]
    elif len(r) >= LENGTH:
        frame = r[:LENGTH]
    else:
        return None
    if frame[6] not in (KIND_KEYBOARD, KIND_BOTH):
        return None
    level = frame[1]
    if not 1 <= level <= 100:
        return None
    return level, frame[3] == 0


class RoyuanProvider(Provider):
    name = "royuan"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, float]] = None

    def _read(self, path: bytes) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for attempt in range(ATTEMPTS):
                try:
                    dev.send_feature_report(status_request())
                except (OSError, ValueError) as e:
                    self._diag.append(f"    send: {e}")
                    return None
                time.sleep(ATTEMPT_GAP)
                try:
                    resp = dev.get_feature_report(REPORT_ID, REPORT_LEN)
                except (OSError, ValueError):
                    continue
                got = parse_status(resp)
                if got is not None:
                    self._diag.append(f"    answer (attempt {attempt + 1}): "
                                      f"{hexdump(resp, 12)}")
                    return got
            self._diag.append("    no 0xF7 answer")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:            # pragma: no cover - the package misbehaving
            log.warning("hid.enumerate(royuan): %s", e)
            return []

        mine = [d for d in infos
                if d.get("product_id") == PID
                and (d.get("usage_page"), d.get("usage")) == USAGE]
        # A data cable makes the keyboard enumerate as 3151:4015 while the
        # receiver stays plugged in. That is the only charging signal this
        # receiver exposes: its F7 status has no usable charging byte, and a
        # board on a wall charger cannot be seen from here at all.
        on_cable = any(d.get("product_id") == CABLE_PID for d in infos)
        if on_cable:
            self._diag.append(f"[ROYUAN] cable keyboard {CABLE_PID:04x} is present")
        if not mine:
            if on_cable:
                self._diag.append("  no receiver status; the keyboard is on its cable")
                return [self._status(self._kept_level(), online=True,
                                     charging=True, charging_known=True)]
            return []
        # the configuration collection sits on interface 2 where the receiver
        # lists it more than once; the others do not answer the status request
        mine.sort(key=lambda d: (d.get("interface_number") != 2,))
        for d in mine:
            self._diag.append(f"[ROYUAN] pid={PID:04x} "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            got = self._read(d["path"])
            if got is None:
                continue
            level, online = got
            if on_cable:
                if online:
                    self._last = (level, time.time())
                self._diag.append(f"  -> {level}% on its USB cable")
                return [self._status(level, online=True, charging=True, charging_known=True)]
            if online:
                self._last = (level, time.time())
                self._diag.append(f"  -> {level}%")
                return [self._status(level, online=True)]
            kept = self._kept_level()
            self._diag.append("  keyboard offline (switched off or out of range)")
            return [self._status(kept, online=False)]
        self._diag.append("  no 0xF7 answer")
        if on_cable:
            return [self._status(self._kept_level(), online=True,
                                 charging=True, charging_known=True)]
        return [self._status(self._kept_level(), online=False)]

    def _kept_level(self) -> Optional[int]:
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return self._last[0]
        return None

    @staticmethod
    def _status(level: Optional[int], online: bool, charging: bool = False,
                charging_known: bool = False) -> DeviceStatus:
        return DeviceStatus(KEY, NAME, level, charging, online, "royuan",
                            kind="keyboard", charging_known=charging_known)

    def diagnostics(self) -> List[str]:
        return list(self._diag)
