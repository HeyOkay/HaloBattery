"""Zebronics wireless mouse on the CompX receiver `3554:fc0a`.

The receiver exposes the CompX vendor collection ``ff02:0002``. A single
GET_FEATURE report id 6 returns the cached mouse level:

    06 00 02 00 DB 64 14 80
                     ^^ battery percent (0x64 = 100)

The other bytes stayed constant in the BatteryHub capture, including when the
battery was removed; the receiver answers from its cache. This mouse is
battery-only, so there is no charging field.

Protocol source: BatteryHub 1.1.1 src/drivers/zebronics.cpp (GPL-3.0-or-later;
protocol reference only). The request is read-only.
"""
from __future__ import annotations

from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x3554
PID = 0xFC0A
REPORT_ID = 0x06
REPORT_LEN = 8
KEY = "zebronics:3554fc0a"
NAME = "Zebronics wireless mouse"


def _strip_id(report) -> bytes:
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == 0x06:
        b = b[1:]
    return b


def parse_level(report) -> Optional[int]:
    """Battery percent from the report-6 reply, or None when it is not one."""
    b = _strip_id(report)
    if len(b) < REPORT_LEN:
        return None
    if b[0] != REPORT_ID or b[1] != 0x00 or b[2] != 0x02:
        return None
    level = b[5]
    return level if 0 <= level <= 100 else None


class ZebronicsProvider(Provider):
    name = "zebronics"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and (d.get("usage_page"), d.get("usage")) == (0xFF02, 0x0002)
                    and (d.get("feature_report_byte_length") or REPORT_LEN) >= REPORT_LEN):
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
            reply = dev.get_feature_report(REPORT_ID, REPORT_LEN)
            self._diag.append(f"  reply: {hexdump(reply, 10)}")
            return parse_level(reply)
        except (OSError, IOError, ValueError) as e:
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
            log.warning("hid.enumerate(zebronics): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[Zebronics] pid={PID:04x} iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
        level = self._query(d["path"])
        if level is None:
            return []
        return [DeviceStatus(KEY, NAME, level, False, True, "zebronics",
                             kind="mouse", charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
