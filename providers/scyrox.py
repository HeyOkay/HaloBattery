"""Scyrox V12M on its 2.4 GHz receiver `395a:0006`.

The receiver exposes ``ff00:0002`` on interface 2 with 64-byte input and output
reports. A profile read is requested with:

    08 01 00 ... 00 09        (last byte = low byte of 08 + 01)

The receiver streams one ``08 01`` frame per profile, plus axis frames 23/25.
Only the active profile frame is accepted: ``[3] != 0``, the active profile is
``[4] == [5]``, the DPI-stage count is 1..5 at ``[7]``, no error at ``[62]``,
and the battery percent is ``[20]``. The stream ends with an ``08 01`` frame
whose ``[3]`` is zero.

The protocol has no charging field that the references confirm, so charging is
reported as unknown. Only the profile read is written; no settings are changed.

Protocol source: BatteryHub 1.1.1 src/drivers/scyrox.cpp (GPL-3.0-or-later;
protocol reference only), from late-autumn-gif/mousedock (MIT) and BrimDeck.
"""
from __future__ import annotations

import time
from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x395A
PID = 0x0006
REPORT_LEN = 64
READ_TIMEOUT_MS = 100
DEADLINE_S = 1.5
KEY = "scyrox:v12m"
NAME = "Scyrox V12M"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[0] = 0x08
    request[1] = 0x01
    request[63] = 0x09
    return request


def parse_profile(report) -> Optional[int]:
    """Battery percent from the active profile frame, or None."""
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == 0x08:
        b = b[1:]
    if len(b) < REPORT_LEN or b[0] != 0x08 or b[1] != 0x01:
        return None
    if b[3] == 0:                      # end of the profile stream
        return None
    if b[62] == 1 or b[4] > 3 or b[4] != b[5] or not 1 <= b[7] <= 5:
        return None
    level = b[20]
    return level if level <= 100 else None


class ScyroxProvider(Provider):
    name = "scyrox"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and (d.get("usage_page"), d.get("usage")) == (0xFF00, 0x0002)
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
            for _ in range(4):
                if not dev.read(REPORT_LEN, 30):
                    break
            dev.write(make_request())
            deadline = time.monotonic() + DEADLINE_S
            while time.monotonic() < deadline:
                reply = dev.read(REPORT_LEN, READ_TIMEOUT_MS)
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 24)}")
                level = parse_profile(reply)
                if level is not None:
                    return level
            self._diag.append("  no active profile frame")
            return None
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
            log.warning("hid.enumerate(scyrox): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[Scyrox] pid={PID:04x} iface={d.get('interface_number')} "
                          f"product={d.get('product_string')!r}")
        level = self._query(d["path"])
        if level is None:
            return []
        return [DeviceStatus(KEY, NAME, level, False, True, "scyrox",
                             kind="mouse", charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
