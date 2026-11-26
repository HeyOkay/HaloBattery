"""MCHOSE V9 Pro 2 on its 2.4 GHz dongle `3837:6011`.

The dongle exposes usage `ff07:0222` with 63-byte input/output payloads behind
report id `0x55`. A read-only request asks for the battery:

    write  55 AA 01 00 01 00 01 0B 00 ...
    read   55 AA 01 <flags> <len> 00 01 0B <body...> <checksum>

Frame bytes after the report id: start `AA`, version `01`, flags (bit 0 = XOR
checksum), length, message type `00 01` for a reply or `00 04` for a
notification, then the sub-command. A battery body starts with a presence byte:
zero means the headset is off or asleep; otherwise the next byte is
`charging << 7 | percent`. The same body arrives unsolicited as notification
`0x22`.

Only `3837:6011` is claimed; the R9 shares the protocol but is untested.
Protocol source: BatteryHub 1.1.1 src/drivers/mchose_headset.cpp
(GPL-3.0-or-later; protocol reference only), from M HUB and a V9 Pro 2.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x3837
PID = 0x6011
USAGE = (0xFF07, 0x0222)
REPORT_ID = 0x55
REPORT_LEN = 64
START = 0xAA
VERSION = 0x01
SUB_READ = 0x0B
SUB_NOTIFY = 0x22
TIMEOUT_MS = 1000
KEY = "mchose:v9pro2"
NAME = "MCHOSE V9 Pro 2"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[:9] = [REPORT_ID, START, VERSION, 0x00, 0x01, 0x00, 0x01, SUB_READ, 0x00]
    return request


def parse_reply(report) -> Optional[Tuple[str, int, bool]]:
    """('off', 0, False), ('level', percent, charging), or None."""
    b = bytes(report or b"")
    if len(b) < 9 or b[0] != REPORT_ID:
        return None
    f = b[1:]
    if f[0] != START or f[1] != VERSION or f[4] != 0x00:
        return None
    reply = f[5] == 0x01 and f[6] == SUB_READ
    notify = f[5] == 0x04 and f[6] == SUB_NOTIFY
    if not reply and not notify:
        return None
    length = f[3]
    if length < 3 or 6 + length >= len(f):
        return None
    checksum = 0
    if f[2] == 0x01:
        checksum = f[2] ^ f[3] ^ f[4] ^ f[5]
        for i in range(length):
            checksum ^= f[6 + i]
    if f[6 + length] != checksum:
        return None
    body = f[7:]
    if body[0] == 0:
        return "off", 0, False
    level = body[1] & 0x7F
    if level > 100:
        return None
    return "level", level, bool(body[1] & 0x80)


class MchoseV9Pro2Provider(Provider):
    name = "mchose_v9pro2"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("input_report_byte_length") or REPORT_LEN) >= REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) >= REPORT_LEN):
                return d
        return None

    def _query(self, path) -> Optional[Tuple[str, int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                for _ in range(4):
                    if not dev.read(REPORT_LEN, 20):
                        break
            except (OSError, ValueError):
                pass
            try:
                dev.write(make_request())
            except (OSError, ValueError) as e:
                self._diag.append(f"  write: {e}")
                return None
            remaining = TIMEOUT_MS
            while remaining > 0:
                try:
                    reply = dev.read(REPORT_LEN, min(remaining, 200))
                except (OSError, ValueError) as e:
                    self._diag.append(f"  read: {e}")
                    return None
                remaining -= 200
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 12)}")
                parsed = parse_reply(reply)
                if parsed is not None:
                    return parsed
            self._diag.append("  no battery reply")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(mchose v9 pro 2): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[MCHOSE V9 Pro 2] pid={PID:04x} iface={d.get('interface_number')}")
        parsed = self._query(d["path"])
        if parsed is None:
            return []
        state, level, charging = parsed
        if state == "off":
            return [DeviceStatus(KEY, NAME, None, False, False, "mchose",
                                 kind="headset", charging_known=True)]
        return [DeviceStatus(KEY, NAME, level, charging, True, "mchose", kind="headset")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
