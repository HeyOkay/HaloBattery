"""Alienware AW920H Tri-Mode headset on its 2.4 GHz dongle `413c:a515`.

The dongle exposes interface 5, usage `ff13:0001`, with 62-byte output/input
reports. A read-only request `06 c0 0a 00 ...` is answered with

    07 c0 0a 09 00 <linked> <percent> <charging> <critical> .. <critical pct> ...

Byte 13 is the XOR of bytes 3..12, and byte 6 must be nonzero. When byte 8 is
1, the level is read from byte 11 instead of byte 6. Charging is byte 7 == 1.
A reply of `07 00 80` means "not ready"; the dongle normally answers after
30-100 ms, but one query in about a hundred is dropped, so it is resent.

The Bluetooth identity (`413c:a514`) is not claimed: Windows reports that level
itself and Halo's Bluetooth reader already covers it.

Protocol source: BatteryHub 1.1.1 src/drivers/alienware.cpp (GPL-3.0-or-later;
protocol reference only), from Alienware Command Center's AW920H plugin.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x413C
PID = 0xA515
USAGE = (0xFF13, 0x0001)
REPORT_IN = 0x07
REPORT_OUT = 0x06
REPORT_LEN = 62
CMD = bytes([0xC0, 0x0A, 0x09])
TOTAL_TIMEOUT_S = 0.6
ATTEMPT_S = 0.18
ATTEMPTS = 3
KEY = "alienware:a515"
NAME = "Alienware AW920H"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[:3] = [REPORT_OUT, CMD[0], CMD[1]]
    return request


def parse_reply(report) -> Optional[Tuple[int, bool]]:
    """(percent, charging) from a valid AW920H battery reply, or None."""
    b = bytes(report or b"")
    if b[:2] == CMD[:2] and b[0] != REPORT_IN:
        b = bytes([REPORT_IN]) + b
    if len(b) < 14 or b[0] != REPORT_IN:
        return None
    if b[1:4] != CMD or b[6] == 0:
        return None
    checksum = 0
    for value in b[3:13]:
        checksum ^= value
    if checksum != b[13]:
        return None
    level = b[11] if b[8] == 1 else b[6]
    if level > 100:
        return None
    return level, b[7] == 1


class AlienwareAw920hProvider(Provider):
    name = "alienware_aw920h"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and d.get("interface_number") == 5
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("input_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) == REPORT_LEN):
                return d
        return None

    def _query(self, path) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            deadline = time.monotonic() + TOTAL_TIMEOUT_S
            for _ in range(ATTEMPTS):
                if time.monotonic() >= deadline:
                    break
                try:
                    dev.write(make_request())
                except (OSError, ValueError) as e:
                    self._diag.append(f"  write: {e}")
                    return None
                attempt_end = min(deadline, time.monotonic() + ATTEMPT_S)
                while time.monotonic() < attempt_end:
                    time.sleep(0.015)
                    for report in self._replies(dev):
                        if not report:
                            continue
                        self._diag.append(f"  report: {hexdump(report, 16)}")
                        parsed = parse_reply(report)
                        if parsed is not None:
                            return parsed
            self._diag.append("  no valid battery reply")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _replies(self, dev):
        """Interrupt and control-pipe replies, in that order."""
        try:
            report = dev.read(REPORT_LEN, 10)
        except (OSError, ValueError):
            report = None
        if report:
            yield report
        try:
            yield dev.get_input_report(REPORT_IN, REPORT_LEN)
        except (OSError, ValueError):
            return

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(alienware aw920h): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[Alienware AW920H] pid={PID:04x} iface={d.get('interface_number')}")
        parsed = self._query(d["path"])
        if parsed is None:
            return []
        level, charging = parsed
        return [DeviceStatus(KEY, NAME, level, charging, True, "alienware", kind="headset")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
