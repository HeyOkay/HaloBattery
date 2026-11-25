"""EVGA X20 on its receiver `3842:2402` or USB cable `3842:2420`.

Both ids expose the configuration channel on interface 2, usage `ffff:0000`,
as a feature-report-only collection. The read-only battery exchange is:

    SET_FEATURE  00 00 00 LL 02 00 83 00 ...
    GET_FEATURE  00 st 00 LL 02 00 83 CH PCT ...

``LL`` is 02 on the receiver and 00 on the cable; ``st`` is A0 (not ready),
A1 (ok), A3 (rejected) or A4 (no mouse on the wireless link). While the
receiver relays the request it can answer A0 for about 300 ms, so both calls
are repeated until a 2 s deadline.

The cable is queried first. While the mouse is on its cable the receiver only
answers A4; using the cable first avoids a second dimmed entry for the same
mouse. Protocol source: BatteryHub 1.1.1 src/drivers/evga.cpp and
evga_protocol.h (GPL-3.0-or-later; protocol reference only), from EVGA
Unleash captures and OpenRGB's EVGAMouseController.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x3842
CABLE = 0x2420
RECEIVER = 0x2402
USAGE = (0xFFFF, 0x0000)
REPORT_LEN = 65
LINK_WIRED = 0x00
LINK_WIRELESS = 0x02
STATUS_NOT_READY = 0xA0
STATUS_OK = 0xA1
STATUS_REJECTED = 0xA3
STATUS_ASLEEP = 0xA4
DEADLINE_S = 2.0
RETRY_DELAY_S = 0.01
KEY = "evga:x20"
NAME = "EVGA X20"


def make_request(link: int) -> List[int]:
    request = [0x00] * REPORT_LEN
    request[3] = link
    request[4] = 0x02
    request[6] = 0x83
    return request


def _reply_payload(report) -> Optional[bytes]:
    b = bytes(report or b"")
    if len(b) >= 9 and b[0] == 0x00 and b[1] in (STATUS_NOT_READY, STATUS_OK,
                                                 STATUS_REJECTED, STATUS_ASLEEP):
        return b
    if len(b) >= 8 and b[0] in (STATUS_NOT_READY, STATUS_OK,
                                STATUS_REJECTED, STATUS_ASLEEP) and b[1] == 0x00:
        return b"\x00" + b
    return None


def parse_reply(report, link: int) -> Optional[Tuple[int, int, bool]]:
    """(status, percent, charging) for a reply to `link`, or None."""
    b = _reply_payload(report)
    if b is None or b[2:7] != bytes([0x00, link, 0x02, 0x00, 0x83]):
        return None
    status = b[1]
    if status == STATUS_NOT_READY:
        return status, -1, False
    if status in (STATUS_REJECTED, STATUS_ASLEEP):
        return status, -1, False
    if status != STATUS_OK or b[8] > 100:
        return None
    return status, b[8], b[7] != 0


class EvgaProvider(Provider):
    name = "evga"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict], pid: int) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == pid
                    and (d.get("interface_number") in (2, -1))
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("feature_report_byte_length") or REPORT_LEN) > 8):
                return d
        return None

    def _query(self, d: dict, link: int) -> Optional[Tuple[int, int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            deadline = time.monotonic() + DEADLINE_S
            while time.monotonic() < deadline:
                dev.send_feature_report(make_request(link))
                time.sleep(RETRY_DELAY_S)
                reply = dev.get_feature_report(0x00, REPORT_LEN)
                if not reply:
                    continue
                parsed = parse_reply(reply, link)
                if parsed is None:
                    self._diag.append(f"  unexpected reply: {hexdump(reply, 12)}")
                    return None
                status, level, charging = parsed
                if status == STATUS_NOT_READY:
                    continue
                if status == STATUS_OK:
                    self._diag.append(f"  {level}%{' charging' if charging else ''}")
                else:
                    self._diag.append(f"  status {status:02x}")
                return status, level, charging
            self._diag.append("  still not ready")
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
            log.warning("hid.enumerate(evga): %s", e)
            return []
        cable = self._pick(infos, CABLE)
        if cable is not None:
            got = self._query(cable, LINK_WIRED)
            if got is not None and got[0] == STATUS_OK:
                return [DeviceStatus(KEY, NAME, got[1], got[2], True, "evga", kind="mouse")]
        receiver = self._pick(infos, RECEIVER)
        if receiver is not None:
            got = self._query(receiver, LINK_WIRELESS)
            if got is not None and got[0] == STATUS_OK:
                return [DeviceStatus(KEY, NAME, got[1], got[2], True, "evga", kind="mouse")]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
