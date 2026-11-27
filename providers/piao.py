"""IPI PIAO wireless mice on vendor `372e`.

IDs: ``1014`` 2.4 GHz receiver, ``1015`` / ``1028`` / ``1056`` USB cable. The
feature-report collection is usage ``ff00:0001`` with 64-byte reports:

    request  03 <sum> 50 00 00 <cmd> <sub> 00 ...
    reply    03 50 00 <len> 00 <cmd/sub echo> <data...>

The checksum is the low byte of the sum of bytes 1..63. For the wireless id the
reader first sends the read-only online check `47 00`; a zero first data byte
means the mouse is asleep or off. The battery query `4f 81` returns a byte whose
bit 7 is charging and bits 0..6 are the percent. A sleeping mouse may need the
link poke and a few retries. While it sleeps its last reading stays greyed
for ``ASLEEP_KEEP_S``.

Only these PIDs are claimed; other 372E products have their own drivers.
Protocol source: BatteryHub 1.1.1 src/drivers/piao.cpp (GPL-3.0-or-later;
protocol reference only), from IPI's web driver and hardware captures.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x372E
WIRELESS = 0x1014
PIDS: Dict[int, bool] = {
    0x1014: True,      # 2.4 GHz receiver
    0x1015: False,     # wired
    0x1028: False,     # wired
    0x1056: False,     # wired
}
REPORT_ID = 0x03
REPORT_LEN = 64
CMD_LINK = 0x47
CMD_BATTERY = 0x4F
SUB_BATTERY = 0x81
ASLEEP_KEEP_S = 600.0              # BatteryHub's grace for this family


def make_request(cmd: int, sub: int) -> List[int]:
    request = [0x00] * REPORT_LEN
    request[0] = REPORT_ID
    request[2] = 0x50
    request[5] = cmd
    request[6] = sub
    request[1] = sum(request[1:]) & 0xFF
    return request


def _payload(report) -> Optional[bytes]:
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == REPORT_ID:
        b = b[1:]
    if len(b) < 7 or b[0] != REPORT_ID or b[2] != 0x00 or b[4] != 0x00:
        return None
    return b


def parse_data(report, cmd: int, sub: int) -> Optional[bytes]:
    """The data bytes from a reply to (cmd, sub), or None."""
    b = _payload(report)
    if b is None:
        return None
    offset = 0
    if b[5] == cmd:
        offset = 7
    elif b[5] in (sub, sub & 0x0F, sub & 0x7F):
        offset = 6
    if offset == 0:
        return None
    length = b[3]
    if length == 0 or offset + length > len(b):
        return None
    return b[offset:offset + length]


def parse_battery(data: bytes) -> Optional[Tuple[int, bool]]:
    if not data:
        return None
    level = data[0] & 0x7F
    if level > 100:
        return None
    return level, bool(data[0] & 0x80)


class PiaoProvider(Provider):
    name = "piao"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[int, Tuple[int, bool, float]] = {}

    def _pick(self, infos: List[dict], pid: int) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == pid
                    and (d.get("usage_page"), d.get("usage")) == (0xFF00, 0x0001)
                    and (d.get("feature_report_byte_length") or REPORT_LEN) == REPORT_LEN):
                return d
        return None

    def _query(self, path, cmd: int, sub: int) -> Optional[bytes]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            dev.send_feature_report(make_request(cmd, sub))
            for attempt in range(6):
                if attempt == 3 and cmd == CMD_BATTERY:
                    try:
                        dev.send_feature_report(make_request(CMD_LINK, 0x00))
                    except (OSError, ValueError):
                        pass
                time.sleep(0.12)
                reply = dev.get_feature_report(REPORT_ID, REPORT_LEN)
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 12)}")
                data = parse_data(reply, cmd, sub)
                if data is not None:
                    return data
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
            log.warning("hid.enumerate(piao): %s", e)
            return []
        out = []
        for pid, wireless in PIDS.items():
            d = self._pick(infos, pid)
            if d is None:
                self._last.pop(pid, None)
                continue
            self._diag.append(f"[Piao] pid={pid:04x}")
            parsed = None
            link = self._query(d["path"], CMD_LINK, 0x00) if wireless else b"\x01"
            if not link or link[0] == 0:
                self._diag.append("  mouse asleep or off")
            else:
                data = self._query(d["path"], CMD_BATTERY, SUB_BATTERY)
                parsed = parse_battery(data) if data else None
            key = f"piao:{pid:04x}"
            if parsed is not None:
                level, charging = parsed
                self._last[pid] = (level, charging, time.monotonic())
                out.append(DeviceStatus(key, "IPI PIAO mouse", level, charging,
                                        True, "piao", kind="mouse"))
                continue
            last = self._last.get(pid)
            if last is not None and time.monotonic() - last[2] <= ASLEEP_KEEP_S:
                out.append(DeviceStatus(key, "IPI PIAO mouse", last[0], last[1], False,
                                        "piao", kind="mouse", wake_on_input=True))
            else:
                self._last.pop(pid, None)
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
