"""Attack Shark R5 Ultra on its 2.4 GHz receiver (373E:0047).

The R5 Ultra uses the same 64-byte feature-report protocol as the R6/R8/M5
Ultra, recovered from the vendor's WebHID driver by
``mohammed-just/attack-shark-r6-cli`` (MIT).  Its ``get_battery`` sends
``cmd 2,2,0,0x83`` and parses a reply with the 0xA1 marker; the battery is
read-only.

The receiver exposes several vendor collections.  The reference driver probes
each one with a read-only profile request and keeps the first that answers;
this provider does the same with the battery request on every vendor-page
collection until one replies.  No configuration, DPI, lighting or firmware
command is sent.

The wired id 0x0046 is not claimed here: the report only had the wireless
receiver, so its collection layout is unverified.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x373E
PID = 0x0047
NAME = "Attack Shark R5 Ultra"
REPORT_ID = 0
REPORT_LEN = 65                 # report id + 64-byte command/response
COMMAND = 0x83
DELAY_S = 0.05
READ_ATTEMPTS = 2
TIMEOUT_MS = 250

# These are the vendor collections seen on the R5 Ultra receiver; the mouse,
# keyboard and consumer collections are left alone.
CANDIDATE_PAGES = (0xFFA0, 0xFFFF, 0xFF00)


def make_request() -> List[int]:
    """64-byte command buffer with command 2,2,0,0x83, plus report id 0."""
    buf = bytearray(64)
    buf[2] = 2
    buf[3] = 2
    buf[4] = 0
    buf[5] = COMMAND
    return [REPORT_ID] + list(buf)


def _normalize(a: int, b: int) -> Tuple[Optional[int], bool]:
    """The reference parser: some firmware returns (pct, charge), others reversed."""
    if a in (0, 1) and 0 <= b <= 100:
        return b, a == 1
    if b in (0, 1) and 0 <= a <= 100:
        return a, b == 1
    if 0 <= a <= 100:
        return a, b == 1
    if 0 <= b <= 100:
        return b, a == 1
    return None, False


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a battery reply, or None when it is not one."""
    d = list(r or [])
    if len(d) < 9:
        return None
    # The reference accepts both positions of the report-id-like 0xA1 marker.
    if d[0] == 0xA1 and d[3] == 2 and d[5] == COMMAND:
        level, charging = _normalize(d[6], d[7])
        return (level, charging) if level is not None else None
    if d[1] == 0xA1 and d[4] == 2 and d[6] == COMMAND:
        level, charging = _normalize(d[7], d[8])
        return (level, charging) if level is not None else None
    return None


def _candidate(d: dict) -> bool:
    page = d.get("usage_page") or 0
    usage = d.get("usage") or 0
    return (page in CANDIDATE_PAGES or page >= 0xFF00) and usage != 0


class AttackSharkR5Provider(Provider):
    name = "attackshark_r5"

    def __init__(self):
        self._diag: List[str] = []

    def _ask(self, info: dict) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(info["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for attempt in range(READ_ATTEMPTS):
                try:
                    sent = dev.send_feature_report(bytes(make_request()))
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    feature write: {e}")
                    return None
                if sent is not None and sent < 0:
                    self._diag.append(f"    feature write refused ({sent})")
                    return None
                time.sleep(DELAY_S)
                try:
                    raw = dev.get_feature_report(REPORT_ID, REPORT_LEN)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    feature read: {e}")
                    return None
                got = parse_reply(raw)
                if got is not None:
                    self._diag.append(f"    reply: {hexdump(raw, 12)} -> {got[0]}% "
                                      f"charging={got[1]}")
                    return got
                if raw:
                    self._diag.append(f"    other frame: {hexdump(raw, 12)}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:  # pragma: no cover
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(attackshark_r5): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID and _candidate(d)]
        if not mine:
            return []
        self._diag.append(f"[Attack Shark R5] pid={PID:04x} candidates={len(mine)}")
        # The collector that answers is the one used; stop after the first reply.
        for d in mine:
            self._diag.append(f"  collection iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            got = self._ask(d)
            if got is not None:
                level, charging = got
                return [DeviceStatus(f"attackshark:{PID:04x}", NAME, level,
                                     charging, True, "attackshark_r5", kind="mouse")]
        self._diag.append("  no battery reply (mouse off or asleep)")
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
