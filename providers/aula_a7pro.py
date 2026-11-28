"""AULA A7Pro headset on its 2.4 GHz receiver `38a6:0001`.

Interface 0 exposes usage `ff90:0001` with 64-byte input and output reports.
The AULA Audio Center sends the read-only query `55 65 01` and receives

    55 65 <percent> <power> ...

Power is `00` with the cable connected and `02` on battery. At 100 % on the
cable the charge light is off, so `00` means external power rather than active
charging; the reader reports charging only below 100 %. The same VID:PID is
used by the A700, so the product string must be `AULA-A7Pro`.

Protocol source: BatteryHub 1.1.1 src/drivers/aula_headset.cpp
(GPL-3.0-or-later; protocol reference only), verified by its author on hardware.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x38A6
PID = 0x0001
USAGE = (0xFF90, 0x0001)
REPORT_LEN = 64
REQUEST = bytes([0x55, 0x65, 0x01])
PRODUCT = "aula-a7pro"
TIMEOUT_MS = 750
KEY = "aula:a7pro"
NAME = "AULA A7Pro"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[:3] = REQUEST
    return request


def parse_reply(report) -> Optional[Tuple[int, int]]:
    """(percent, power) from a valid reply, or None. Power 0 = cable, 2 = battery."""
    b = bytes(report or b"")
    if len(b) < 4 or b[0] != 0x55 or b[1] != 0x65:
        return None
    if b[2] > 100 or b[3] not in (0x00, 0x02):
        return None
    return b[2], b[3]


class AulaA7ProProvider(Provider):
    name = "aula_a7pro"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and (d.get("product_string") or "").strip().lower() == PRODUCT
                    and d.get("interface_number") == 0
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("input_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and not (d.get("feature_report_byte_length") or 0)):
                return d
        return None

    def _query(self, path) -> Optional[Tuple[int, int]]:
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
            deadline = time.monotonic() + TIMEOUT_MS / 1000
            while time.monotonic() < deadline:
                try:
                    report = dev.read(REPORT_LEN, 100)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  read: {e}")
                    return None
                if not report:
                    continue
                self._diag.append(f"  reply: {hexdump(report, 8)}")
                parsed = parse_reply(report)
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
            log.warning("hid.enumerate(aula a7pro): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[AULA A7Pro] pid={PID:04x} iface={d.get('interface_number')}")
        parsed = self._query(d["path"])
        if parsed is None:
            return []
        level, power = parsed
        external_power = power == 0x00
        # At 100 % on the cable the vendor app shows no charge light: a full
        # battery on external power is reported as 100 %, not as charging.
        charging = external_power and level < 100
        self._diag.append(f"  -> {level}%, {'external power' if external_power else 'battery'}")
        return [DeviceStatus(KEY, NAME, level, charging, True, "aula", kind="headset")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
