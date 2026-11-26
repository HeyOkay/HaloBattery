"""VGN OMNISOUND (ZHOUTING) headset on its 2.4 GHz dongle `372e:10bb`.

The control collection is `000c:0001` with 32-byte input reports, 64-byte
output reports and 16-byte feature reports, using report id `0x55`. A read-only
output report asks for the battery:

    write  55 65 01 ...
    read   55 65 <percent>       (numbered reply)
             65 <percent>       (unnumbered reply)

The wired variant and Siren V2 share the frame shape but are untested, so only
the wireless OMNISOUND is claimed. VGN's app forces charging = false for this
dongle and no charging bit is known, so `charging_known` is false.

Protocol source: BatteryHub 1.1.1 src/drivers/vgn.cpp (GPL-3.0-or-later;
protocol reference only), from V HUB and a ZHOUTING dongle.
"""
from __future__ import annotations

from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x372E
PID = 0x10BB
USAGE = (0x000C, 0x0001)
REPORT_ID = 0x55
REPORT_LEN = 64
READ_LEN = 32
CMD = 0x65
KEY = "vgn:10bb"
NAME = "VGN OMNISOUND"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[:3] = [REPORT_ID, CMD, 0x01]
    return request


def parse_reply(report) -> Optional[int]:
    b = bytes(report or b"")
    if len(b) >= 3 and b[0] == REPORT_ID and b[1] == CMD:
        level = b[2]
    elif len(b) >= 2 and b[0] == CMD:
        level = b[1]
    else:
        return None
    return level if level <= 100 else None


class VgnProvider(Provider):
    name = "vgn"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("input_report_byte_length") or READ_LEN) >= 3
                    and (d.get("output_report_byte_length") or REPORT_LEN) >= REPORT_LEN - 1):
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
                    if not dev.read(READ_LEN, 20):
                        break
            except (OSError, ValueError):
                pass
            try:
                dev.write(make_request())
            except (OSError, ValueError) as e:
                self._diag.append(f"  write: {e}")
                return None
            for _ in range(6):
                try:
                    reply = dev.read(READ_LEN, 120)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  read: {e}")
                    return None
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 6)}")
                level = parse_reply(reply)
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
            log.warning("hid.enumerate(vgn): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[VGN OMNISOUND] pid={PID:04x} iface={d.get('interface_number')}")
        level = self._query(d["path"])
        if level is None:
            return []
        return [DeviceStatus(KEY, NAME, level, False, True, "vgn",
                             kind="headset", charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
