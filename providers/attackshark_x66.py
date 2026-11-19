"""ATTACKSHARK X66 on its 2.4 GHz receiver (0C45:FEFE).

The receiver exposes a 33-byte vendor output/input report on `ff60:0061`
(interface 3).  The exchange is the Sonix dongle family documented and tested
on an AJAZZ AK980 V2 by YellowHearth1/ajazz-ak980-v2-linux (`docs/PROTOCOL.md`,
`crates/ak980-core/src/device.rs`):

    request:  report id 0 + 20 01 + zero padding, byte sum in the last byte
    reply:    repeats 20 01 00 and carries the level in byte 3; the reply is
              `20 01 00 LEVEL ... <sum>`

The keyboard only answers in 2.4 GHz mode, and the first request after the
radio sleeps may only wake it.  The request is still sent once per poll and is
never re-sent after silence or after a refused write: the next poll reads the
woken link.  Only this model's product string is claimed; other keyboards on
the same OEM dongle need their own identity checks.
"""
from __future__ import annotations

import time
from typing import List, Optional

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x0C45
PID = 0xFEFE
NAME = "ATTACKSHARK X66"
KEY = "attackshark:x66"
KIND = "keyboard"
PRODUCT = "X66"

REPORT_LEN = 33
BATTERY_COMMAND = [0x20, 0x01]
READ_ATTEMPTS = 8
READ_TIMEOUT_MS = 300
ASLEEP_KEEP = 300


def make_request() -> List[int]:
    """The 33-byte output report: id 0, 20 01, zero padding, byte sum."""
    frame = [0x00] * REPORT_LEN
    frame[1:1 + len(BATTERY_COMMAND)] = BATTERY_COMMAND
    frame[-1] = sum(frame[:-1]) & 0xFF
    return frame


def _strip_id(data: List[int]) -> List[int]:
    # Windows keeps the report id; hidraw may drop it.  A 33-byte reply whose
    # first byte is 0 is the id-prefixed form.
    if len(data) == REPORT_LEN and data[0] == 0x00:
        return data[1:]
    return data


def parse_reply(r) -> Optional[int]:
    """The battery percentage from a dongle reply, or None."""
    if not r:
        return None
    data = _strip_id(list(r))
    if len(data) < 5 or data[:3] != [0x20, 0x01, 0x00]:
        return None
    level = data[3]
    if not 0 <= level <= 100:
        return None
    # The reply repeats 20 01 00 and carries the level in byte 3.  Do not
    # require the trailing byte sum: receivers trim trailing zeroes, and the
    # reference reader only matches the command prefix.
    return level


def pick(infos: List[dict]) -> List[dict]:
    return [d for d in infos
            if d.get("product_id") == PID
            and PRODUCT.lower() in (d.get("product_string") or "").lower()
            and (d.get("usage_page") or 0) == 0xFF60
            and (d.get("usage") or 0) == 0x0061]


class AttackSharkX66Provider(Provider):
    name = "attackshark_x66"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[tuple] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(attackshark x66): %s", e)
            return []
        mine = pick(infos)
        if not mine:
            return []
        self._diag.append(f"[ATTACKSHARK X66] pid={PID:04x} '{NAME}'")
        parsed = None
        for d in mine:
            self._diag.append(f"  trying iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            parsed = self._ask(d["path"])
            if parsed is not None:
                break
        if parsed is None:
            self._diag.append("  no battery reply (keyboard asleep, off, or wired)")
            if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
                level = self._last[0]
                return [DeviceStatus(KEY, NAME, level, False, False, "attackshark",
                                     kind=KIND, wake_on_input=True,
                                     charging_known=False)]
            return [DeviceStatus(KEY, NAME, None, False, False, "attackshark",
                                 kind=KIND, charging_known=False)]
        self._last = (parsed, time.time())
        self._diag.append(f"  -> {parsed}%")
        return [DeviceStatus(KEY, NAME, parsed, False, True, "attackshark",
                             kind=KIND, wake_on_input=True, charging_known=False)]

    def _ask(self, path) -> Optional[int]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            # One write per poll: never re-sent after silence or a refused write.
            sent = dev.write(bytes(make_request()))
            self._diag.append(f"    write -> {sent}")
            if sent is not None and sent < 0:
                self._diag.append("    the write was refused")
                return None
            for _ in range(READ_ATTEMPTS):
                try:
                    r = dev.read(REPORT_LEN, READ_TIMEOUT_MS)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    continue
                if r:
                    self._diag.append(f"    reply: {hexdump(r, 12)}")
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
