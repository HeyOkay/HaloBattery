"""Semico tri-mode keyboards: Attack Shark X98 and AJAZZ AK820 Max Plus.

Both use the vendor collection ``ff02:0002`` on interface 0 with 65-byte
input/output reports and the ``AA 55 CC 33`` protocol:

    request  [00][AA 55 CC 33][0E][00 ...]
    reply    [AA 55 CC 33][0E][percent][state]

``state`` 0 means the keyboard is offline, 1..3 means online. The same state
byte is 1 while charging and while on battery (hardware-tested on the X98), so
this reader does not infer charging from it.

IDs: dongle ``1a2c:8fff``; X98 cable ``1a2c:a034``; AK820 Max Plus cable
``1a2c:a036``.

Protocol source: BatteryHub 1.1.1 src/drivers/semico.cpp (GPL-3.0-or-later;
protocol reference only), itself based on openajazz's PROTOCOL.md and Kraber.
Only the status query is written.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x1A2C
DONGLE = 0x8FFF
X98_CABLE = 0xA034
AK820_CABLE = 0xA036
NAMES = {
    X98_CABLE: "Attack Shark X98",
    AK820_CABLE: "AJAZZ AK820 Max Plus",
}
PIDS = (DONGLE, X98_CABLE, AK820_CABLE)
REPORT_LEN = 65
COMMAND = bytes([0xAA, 0x55, 0xCC, 0x33, 0x0E])
READ_TIMEOUT_MS = 600
KEY = "semico:1a2c"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[0] = 0x00
    request[1:1 + len(COMMAND)] = COMMAND
    return request


def _strip_id(report) -> bytes:
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == 0xAA:
        b = b[1:]
    return b


def parse_status(report) -> Optional[Tuple[bool, int]]:
    """(online, percent) from a status reply, or None when it is not one."""
    b = _strip_id(report)
    if len(b) < 7 or b[:5] != COMMAND:
        return None
    state = b[6]
    level = b[5]
    if state > 3 or level > 100:
        return None
    return state != 0, level


class SemicoProvider(Provider):
    name = "semico"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> List[dict]:
        out = []
        for d in infos:
            if (d.get("product_id") in PIDS
                    and d.get("interface_number") == 0
                    and (d.get("usage_page"), d.get("usage")) == (0xFF02, 0x0002)
                    and (d.get("input_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) == REPORT_LEN):
                out.append(d)
        return sorted(out, key=lambda d: 0 if d.get("product_id") == DONGLE else 1)

    def _query(self, path) -> Optional[Tuple[bool, int]]:
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
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                reply = dev.read(REPORT_LEN, 100)
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 10)}")
                parsed = parse_status(reply)
                if parsed is not None:
                    return parsed
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
            log.warning("hid.enumerate(semico): %s", e)
            return []
        candidates = self._pick(infos)
        if not candidates:
            return []
        cable = next((d for d in infos if d.get("product_id") in NAMES), None)
        cable_pid = cable.get("product_id") if cable else None
        name = NAMES.get(cable_pid) if isinstance(cable_pid, int) else None
        for d in candidates:
            self._diag.append(f"[Semico] pid={d.get('product_id', 0):04x} "
                              f"iface={d.get('interface_number')}")
            parsed = self._query(d["path"])
            if parsed is None:
                continue
            online, level = parsed
            if not online:
                self._diag.append("  keyboard is offline")
                continue
            if name is None:
                product = (d.get("product_string") or "").strip()
                name = (product if product and product not in
                        ("2.4 Wireless Device", "USB Gaming Keyboard") else "Wireless keyboard")
            return [DeviceStatus(KEY, name, level, False, True, "semico",
                                 kind="keyboard", charging_known=False)]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
