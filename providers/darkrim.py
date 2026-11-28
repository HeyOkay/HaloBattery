"""DARKRIM SHADOW headset on its `3654:6901` receiver.

The receiver exposes a 64-byte, unnumbered vendor mailbox on interface 3,
usage FF02:0002. The command frame is

    FE seq cmdLE type index count lengthLE payload CRC16

with a nonzero four-bit sequence counter and CRC-16/CCITT-FALSE (poly 0x1021,
init 0xffff, stored big-endian). Only the battery GET (`cmd 0x0010`, request
type 0x01, payload `01 00`) is sent. A matching response has type 0x02, index
zero, count zero or one, and a single well-formed TLV 1 (battery). Its value is
`bit 7 = charging`, `bits 0..6 = percent`.

Protocol and hardware evidence: BatteryHub 1.1.1 docs/darkrim.md (GPL-3.0-or-
later; protocol reference only), from a hardware-tested `3654:6901` receiver.
The receiver reports coarse 10 % steps in practice; the parser keeps the value
the device sends, as the source does, and does not round it.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x3654
PID = 0x6901
USAGE = (0xFF02, 0x0002)
INTERFACE = 3

REPORT_LEN = 64
CMD_BATTERY = 0x0010
TYPE_REQUEST = 0x01
TYPE_RESPONSE = 0x02
DEADLINE_S = 0.8
FLUSH_TIMEOUT_MS = 30
READ_TIMEOUT_MS = 100

KEY = "darkrim:6901"
NAME = "DARKRIM SHADOW"


def crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE, the variant the receiver uses."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def make_request(seq: int, info: int = 0x01) -> bytes:
    """The 64-byte wire report: report id 0, then the 13-byte GET frame."""
    frame = bytes([0xFE, seq & 0x0F, CMD_BATTERY & 0xFF, CMD_BATTERY >> 8,
                   TYPE_REQUEST, 0x00, 0x01, 0x02, 0x00, info, 0x00])
    check = crc16(frame)
    body = frame + bytes([check >> 8, check & 0xFF])
    return bytes([0x00]) + body + bytes(REPORT_LEN - len(body))


def parse_reply(response, seq: int) -> Optional[Tuple[int, bool]]:
    """(percent, charging) from a response to this sequence, or None."""
    if not response:
        return None
    b = bytes(response)
    if len(b) == REPORT_LEN + 1 and b[0] == 0x00:
        b = b[1:]
    if len(b) < 14:
        return None
    if b[0] != 0xFE or b[1] != (seq & 0x0F) or b[2] != (CMD_BATTERY & 0xFF) or b[3] != 0:
        return None
    if b[4] != TYPE_RESPONSE or b[5] != 0x00 or b[6] not in (0x00, 0x01):
        return None
    length = b[7] | (b[8] << 8)
    if length < 3 or 9 + length + 2 > len(b):
        return None
    payload = b[9:9 + length]
    stored = (b[9 + length] << 8) | b[9 + length + 1]
    if crc16(b[:9 + length]) != stored:
        return None
    if len(payload) != 3 or payload[0] != 0x01 or payload[1] != 0x01:
        return None
    raw = payload[2]
    level = raw & 0x7F
    if level > 100:
        return None
    return level, bool(raw & 0x80)


def pick(infos: List[dict]) -> Optional[dict]:
    for d in infos:
        if (d.get("interface_number") == INTERFACE
                and (d.get("usage_page"), d.get("usage")) == USAGE):
            return d
    return None


class DarkrimProvider(Provider):
    name = "darkrim"

    def __init__(self):
        self._diag: List[str] = []
        self._sequence = 0

    def _next_sequence(self) -> int:
        self._sequence = self._sequence % 15 + 1
        return self._sequence

    def _query(self, path: bytes) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        seq = self._next_sequence()
        try:
            for _ in range(4):
                if not dev.read(REPORT_LEN, FLUSH_TIMEOUT_MS):
                    break
            dev.write(make_request(seq))
            deadline = time.monotonic() + DEADLINE_S
            while time.monotonic() < deadline:
                try:
                    r = dev.read(REPORT_LEN, READ_TIMEOUT_MS)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  read: {e}")
                    return None
                if not r:
                    continue
                self._diag.append(f"  reply: {hexdump(r, 16)}")
                parsed = parse_reply(r, seq)
                if parsed is not None:
                    return parsed
            self._diag.append("  no matching battery response")
            return None
        except (OSError, ValueError) as e:
            self._diag.append(f"  query: {e}")
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
            log.warning("hid.enumerate(darkrim): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        d = pick(mine)
        if d is None:
            self._diag.append(f"[DARKRIM] no interface {INTERFACE} FF02:0002 collection "
                              f"among {len(mine)}; not queried")
            return []
        self._diag.append(f"[DARKRIM] iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
        parsed = self._query(d["path"])
        if parsed is None:
            return []
        level, charging = parsed
        return [DeviceStatus(KEY, NAME, level, charging, True, "darkrim", kind="headset")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
