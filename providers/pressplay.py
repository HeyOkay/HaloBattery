"""Press Play ICARUS V2 Nearlink mouse on its receiver `7768:6434`.

The receiver exposes ``ffb2:0001`` with 64-byte input/output reports. One
read-only output report asks for the battery:

    request  06 05 00 ...
    reply    06 05 26 .. .. .. .. .. .. .. .. .. .. .. <percent> ...

Only this model's id is claimed. The protocol has no charging field, so the
charging state is marked unknown. The AJAZZ AJ159NL P (`7768:6425`) uses a
different status exchange and is not part of this reader.

Protocol source: BatteryHub 1.1.1 src/drivers/pressplay_icarus.cpp
(GPL-3.0-or-later; protocol reference only).
"""
from __future__ import annotations

from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x7768
PID = 0x6434
USAGE = (0xFFB2, 0x0001)
REPORT_LEN = 64
REPORT_ID = 0x06
COMMAND = 0x05
MARKER = 0x26
PERCENT_INDEX = 13
TIMEOUT_S = 0.5
KEY = "pressplay:icarus-v2"
NAME = "Press Play ICARUS V2 Nearlink"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[0] = REPORT_ID
    request[1] = COMMAND
    return request


def _payload(report) -> bytes:
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == REPORT_ID:
        b = b[1:]
    return b


def parse_reply(report) -> Optional[int]:
    b = _payload(report)
    if len(b) < PERCENT_INDEX + 1:
        return None
    if b[0] != REPORT_ID or b[1] != COMMAND or b[2] != MARKER:
        return None
    level = b[PERCENT_INDEX]
    return level if level <= 100 else None


class PressPlayProvider(Provider):
    name = "pressplay"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
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
            for _ in range(4):
                if not dev.read(REPORT_LEN, 30):
                    break
            dev.write(make_request())
            for _ in range(8):
                reply = dev.read(REPORT_LEN, 80)
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 16)}")
                level = parse_reply(reply)
                if level is not None:
                    return level
            self._diag.append("  no battery reply")
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
            log.warning("hid.enumerate(press play): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[Press Play] pid={PID:04x} iface={d.get('interface_number')}")
        level = self._query(d["path"])
        if level is None:
            return []
        return [DeviceStatus(KEY, NAME, level, False, True, "pressplay",
                             kind="mouse", charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
