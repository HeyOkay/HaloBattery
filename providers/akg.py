"""AKG N9 Hybrid on its USB-C dongle `0ecb:20d9`.

The Airoha dongle exposes interface 3, usage `ff13:0001`, with 62-byte
input/output reports. A read-only RACE request asks for the battery:

    write  06 07 80 05 5A 03 00 D6 0C 00 ...
    read   07 <valid> 80 ... 05 5D 05 00 D6 0C 00 00 <percent> ...

Byte 1 of the report is how many bytes from byte 3 are valid; the rest is
leftover from earlier notifications, so only that window is searched. Several
notifications can sit in one report; the newest matching battery frame wins.
There is no charging flag in the battery reply.

Protocol source: BatteryHub 1.1.1 src/drivers/akg.cpp (GPL-3.0-or-later;
protocol reference only), from Airoha RACE captures on an N9 Hybrid.
"""
from __future__ import annotations

import time
from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x0ECB
PID = 0x20D9
USAGE = (0xFF13, 0x0001)
REPORT_LEN = 62
REPORT_ID = 0x07
BATTERY_MARK = bytes([0x05, 0x5D, 0x05, 0x00, 0xD6, 0x0C, 0x00])
TIMEOUT_S = 0.6
KEY = "akg:20d9"
NAME = "AKG N9 Hybrid"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[:10] = [0x06, 0x07, 0x80, 0x05, 0x5A, 0x03, 0x00, 0xD6, 0x0C, 0x00]
    return request


def find_level(report) -> Optional[int]:
    """The newest valid battery level in a report 07 buffer, or None."""
    b = bytes(report or b"")
    if len(b) < 4 or b[0] != REPORT_ID:
        return None
    end = min(len(b), 3 + b[1])
    level = None
    for i in range(3, end - 8):
        if b[i:i + 7] != BATTERY_MARK:
            continue
        if b[i + 8] <= 100:
            level = b[i + 8]
    return level


class AkgProvider(Provider):
    name = "akg"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and d.get("interface_number") == 3
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("input_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) == REPORT_LEN):
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
                dev.write(make_request())
            except (OSError, ValueError) as e:
                self._diag.append(f"  write: {e}")
                return None
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                time.sleep(0.015)
                try:
                    report = dev.get_input_report(REPORT_ID, REPORT_LEN)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  read: {e}")
                    return None
                if not report:
                    continue
                self._diag.append(f"  report: {hexdump(report, 12)}")
                level = find_level(report)
                if level is not None:
                    return level
            self._diag.append("  no battery frame")
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
            log.warning("hid.enumerate(akg): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[AKG] pid={PID:04x} iface={d.get('interface_number')}")
        level = self._query(d["path"])
        if level is None:
            return []
        return [DeviceStatus(KEY, NAME, level, False, True, "akg",
                             kind="headset", charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
