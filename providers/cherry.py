"""CHERRY MX 3.0S Wireless (vendor 046A), receiver 00EA or cable 00E3.

A 64-byte vendor channel on usage page FF1C / usage 0092. The frame has
report id 4, a 16-bit LE checksum of bytes 3..63, type 0x1A and payload
03 00 00 55 for the read-only status query.

The reply keeps report id 4 and type 0x1A; byte 8 is the battery percentage
and byte 9 is non-zero while USB powered.

Protocol from B1Chat/BatteryWatch (MIT), verified on a real MX 3.0S through
the dongle; the cable is assumed to use the same query.
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

VID = 0x046A
CABLE = 0x00E3
DONGLE = 0x00EA
PIDS = (CABLE, DONGLE)
PAGE, USAGE = 0xFF1C, 0x0092
LEN = 64
REPORT = 0x04
STATUS = 0x1A
KEY = "cherry:mx30s"
NAME = "CHERRY MX 3.0S Wireless"
READ_TIMEOUT_MS = 250
LISTEN_MS = 400
ATTEMPTS = 2


def build_query() -> bytes:
    f = bytearray(LEN)
    f[0] = REPORT
    f[3] = STATUS
    f[4] = 0x03
    f[7] = 0x55
    total = sum(f[3:]) & 0xFFFF
    f[1] = total & 0xFF
    f[2] = total >> 8
    return bytes(f)


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    if not r or len(r) < 10:
        return None
    b = list(r)
    if b[0] != REPORT or b[3] != STATUS:
        return None
    if b[8] > 100:
        return None
    return b[8], b[9] != 0


class CherryProvider(Provider):
    name = "cherry"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool]] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(cherry): %s", e)
            return []

        eligible = False
        for d in infos:
            pid = d.get("product_id")
            if pid not in PIDS:
                continue
            if ((d.get("usage_page"), d.get("usage")) != (PAGE, USAGE)
                    or (d.get("input_report_byte_length") or 0) != LEN
                    or (d.get("output_report_byte_length") or 0) != LEN):
                continue
            eligible = True
            got = self._read(d["path"], pid)
            if got is None:
                continue
            self._last = got
            self._diag.append(f"  -> {got[0]}% charging={got[1]}")
            return [DeviceStatus(KEY, NAME, got[0], got[1], True, "cherry",
                                 kind="keyboard",
                                 via="USB cable" if pid == CABLE else "2.4 GHz receiver")]
        if not eligible:
            return []
        self._diag.append("  no answer (keyboard off or asleep)")
        if self._last is not None:
            return [DeviceStatus(KEY, NAME, self._last[0], self._last[1], False,
                                 "cherry", kind="keyboard")]
        return [DeviceStatus(KEY, NAME, None, False, False, "cherry", kind="keyboard")]

    def _read(self, path, pid: int) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open {pid:04x}: {e}")
            return None
        query = build_query()
        try:
            for attempt in range(ATTEMPTS):
                if attempt:
                    time.sleep(0.1)
                try:
                    dev.write(query)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  write: {e}")
                    continue
                deadline = time.time() + LISTEN_MS / 1000.0
                while time.time() < deadline:
                    r = dev.read(LEN, READ_TIMEOUT_MS)
                    if not r:
                        continue
                    self._diag.append(f"  {hexdump(r, 16)}")
                    parsed = parse_reply(r)
                    if parsed is not None:
                        return parsed
            return None
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def diagnostics(self) -> List[str]:
        return list(self._diag)
