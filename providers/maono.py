"""MAONO PD200W microphone on its 2.4 GHz receiver `352f:040e`.

The receiver exposes interface 3, usage `000c:0001`, with 64-byte input/output
reports and no feature reports. The battery is a register read:

    request  C4 09 00 00 04 42 00 <checksum lo> <checksum hi> ...
    reply    C4 <len> 00 00 04 <register lo> <register hi> <value lo> <value hi> ... <checksum>

The request checksum is the two's complement of the sum of bytes 0..6; the
reply checksum is stored at `len - 2` and covers bytes 0..`len - 3`. Only GET
(`0x04`) is used, never a SET command. Register `0x0042` carries the percentage.

The receiver stays enumerated when the microphone is off, so silence is the
offline signal. Charging and link-state registers are not established and are
not inferred from the level.

Protocol source: BatteryHub 1.1.1 src/drivers/maono.cpp (GPL-3.0-or-later;
protocol reference only), from the PD200W owner's hardware test and the
PD100W/PD200W protocol documents.
"""
from __future__ import annotations

import time
from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x352F
PID = 0x040E
USAGE = (0x000C, 0x0001)
REPORT_ID = 0xC4
REPORT_LEN = 64
GET = 0x04
BATTERY_REGISTER = 0x0042
TIMEOUT_S = 1.0
KEY = "maono:pd200w"
NAME = "MAONO PD200W"


def checksum(data: bytes) -> int:
    return (-sum(data)) & 0xFFFF


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[0] = REPORT_ID
    request[1] = 9
    request[4] = GET
    request[5] = BATTERY_REGISTER & 0xFF
    request[6] = BATTERY_REGISTER >> 8
    value = checksum(bytes(request[:7]))
    request[7] = value & 0xFF
    request[8] = value >> 8
    return request


def read_le16(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def parse_reply(report) -> Optional[int]:
    b = bytes(report or b"")
    if len(b) < 11 or b[0] != REPORT_ID:
        return None
    length = b[1]
    if length < 11 or length > len(b):
        return None
    if b[2] != 0 or b[3] != 0 or b[4] != GET:
        return None
    body_end = length - 2
    if body_end < 5 or (body_end - 5) % 4 != 0:
        return None
    if read_le16(b, body_end) != checksum(b[:body_end]):
        return None
    for offset in range(5, body_end, 4):
        if read_le16(b, offset) != BATTERY_REGISTER:
            continue
        value = read_le16(b, offset + 2)
        return value if value <= 100 else None
    return None


class MaonoProvider(Provider):
    name = "maono"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and d.get("interface_number") == 3
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("input_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and not (d.get("feature_report_byte_length") or 0)):
                return d
        return None

    def _query(self, path) -> Optional[int]:
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
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    report = dev.read(REPORT_LEN, 100)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  read: {e}")
                    return None
                if not report:
                    continue
                self._diag.append(f"  reply: {hexdump(report, 12)}")
                level = parse_reply(report)
                if level is not None:
                    return level
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
            log.warning("hid.enumerate(maono): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[MAONO PD200W] pid={PID:04x} iface={d.get('interface_number')}")
        level = self._query(d["path"])
        if level is None:
            return [DeviceStatus(KEY, NAME, None, False, False, "maono",
                                 kind="microphone", charging_known=False)]
        return [DeviceStatus(KEY, NAME, level, False, True, "maono",
                             kind="microphone", charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
