"""CROCIRIS X7 / X7 Ultra Max on the 8K receiver `36f9:aa20`.

The receiver exposes ``ff0a:0001`` with 64-byte payloads (65 bytes with the
report id). One read-only status request:

    request  00 AA 12 00 00 ...
    reply    55 12 02 00 <charging> <percent> <full> ...

``charging`` is 1 while charging; ``percent`` is 1..100. While the mouse sleeps
the receiver answers with a zero level, which is not a real reading: it is
ignored and the previous value is kept dimmed for a short while.

Protocol source: BatteryHub 1.1.1 src/drivers/crociris.cpp (GPL-3.0-or-later;
protocol reference only), decoded from the vendor's WebHID driver and tested on
hardware at 97 % over the receiver.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x36F9
PID = 0xAA20
REPORT_LEN = 65
REQUEST_HEADER = 0xAA
REPLY_HEADER = 0x55
CMD_STATUS = 0x12
READ_TIMEOUT_MS = 100
DEADLINE_S = 1.0
ASLEEP_KEEP_S = 300.0
KEY = "crociris:x7"


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[0] = 0x00
    request[1] = REQUEST_HEADER
    request[2] = CMD_STATUS
    request[3] = 0x00
    request[4] = 0x00
    return request


def parse_reply(report) -> Optional[Tuple[int, bool]]:
    """(percent, charging) from the status reply, or None when it is not one."""
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == REPLY_HEADER:
        b = b[1:]
    if len(b) < 6 or b[0] != REPLY_HEADER or b[1] != CMD_STATUS:
        return None
    length = b[2]
    if length < 2 or len(b) < 4 + length:
        return None
    if b[4] not in (0, 1) or not 1 <= b[5] <= 100:
        return None
    return b[5], b[4] == 1


class CrocirisProvider(Provider):
    name = "crociris"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float, str]] = None

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and (d.get("usage_page"), d.get("usage")) == (0xFF0A, 0x0001)
                    and (d.get("input_report_byte_length") or REPORT_LEN) >= 6
                    and (d.get("output_report_byte_length") or REPORT_LEN) >= 5):
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
            for _ in range(4):
                if not dev.read(REPORT_LEN, 30):
                    break
            dev.write(make_request())
            deadline = time.monotonic() + DEADLINE_S
            while time.monotonic() < deadline:
                reply = dev.read(REPORT_LEN, READ_TIMEOUT_MS)
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 12)}")
                parsed = parse_reply(reply)
                if parsed is not None:
                    return parsed
            self._diag.append("  no status reply")
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
            log.warning("hid.enumerate(crociris): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            self._last = None
            return []
        self._diag.append(f"[CROCIRIS] pid={PID:04x} iface={d.get('interface_number')} "
                          f"product={d.get('product_string')!r}")
        parsed = self._query(d["path"])
        if parsed is not None:
            level, charging = parsed
            product = (d.get("product_string") or "").strip()
            name = product if product and "receiver" not in product.lower() else "CROCIRIS X7"
            self._last = (level, charging, time.monotonic(), name)
            return [DeviceStatus(KEY, name, level, charging, True, "crociris", kind="mouse")]
        if self._last is not None and time.monotonic() - self._last[2] <= ASLEEP_KEEP_S:
            level, charging, _seen, name = self._last
            return [DeviceStatus(KEY, name, level, charging, False, "crociris",
                                 kind="mouse", wake_on_input=True)]
        self._last = None
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
