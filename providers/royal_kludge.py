"""Royal Kludge MG6 Pro wireless mouse (vendor 2E3C).

RK's web driver sends a QiWang configuration frame as output report 0 on
vendor collection FF00:0001 (receiver) or FFF2:0001 (cable):

    01 01 02 01 00 ...          GET_BATTER_VOL

The reply has the same first three bytes; bit 4 of byte 3 is access_res,
bytes 4-5 are millivolts, 6-7 the percentage, and byte 10 says the USB cable
is plugged in. A zero access_res means the receiver answered for a mouse that
did not, so no level is shown.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x2E3C
DONGLE_PIDS = (0x00A6, 0x00E6)
CABLE_PIDS = (0x00A5, 0x00E5)
PIDS = DONGLE_PIDS + CABLE_PIDS
REQUEST = bytes([0x00, 0x01, 0x01, 0x02, 0x01])
READ_TIMEOUT_MS = 250
LISTEN_MS = 600
ATTEMPTS = 3
KEY = "rk:mg6"
NAME = "RK MG6 Pro"


def parse_reply(r) -> Optional[Tuple[int, bool, int]]:
    """(percent, plugged, millivolts) from a battery reply."""
    if not r or len(r) < 11:
        return None
    b = list(r)
    if (b[0] & 0x1F) != 0x01 or (b[1] & 0x0F) != 0x01 or b[2] != 0x02:
        return None
    if not ((b[3] >> 4) & 1):
        return None
    pct = b[6] | (b[7] << 8)
    if pct > 100:
        return None
    mv = b[4] | (b[5] << 8)
    return pct, b[10] != 0, mv


class RoyalKludgeProvider(Provider):
    name = "royal_kludge"

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
            log.warning("hid.enumerate(royal_kludge): %s", e)
            return []

        eligible = False
        for d in infos:
            pid = d.get("product_id")
            if pid not in PIDS:
                continue
            cable = pid in CABLE_PIDS
            page = 0xFFF2 if cable else 0xFF00
            if (d.get("usage_page"), d.get("usage")) != (page, 0x0001):
                continue
            if (d.get("output_report_byte_length") or 0) != 65 \
                    or (d.get("input_report_byte_length") or 0) != 65:
                continue
            eligible = True
            got = self._read(d["path"], pid)
            if got is None:
                continue
            pct, plugged, mv = got
            charging = plugged or cable
            self._last = (pct, charging)
            self._diag.append(f"  -> {pct}% charging={charging} mv={mv}")
            return [DeviceStatus(KEY, NAME, pct, charging, True,
                                 "royal_kludge", kind="mouse",
                                 via="USB cable" if cable else "2.4 GHz receiver")]
        if not eligible:
            return []
        self._diag.append("  no answer (mouse off or asleep)")
        if self._last is not None:
            pct, charging = self._last
            return [DeviceStatus(KEY, NAME, pct, charging, False, "royal_kludge",
                                 kind="mouse")]
        return [DeviceStatus(KEY, NAME, None, False, False, "royal_kludge",
                             kind="mouse")]

    def _read(self, path, pid: int) -> Optional[Tuple[int, bool, int]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open {pid:04x}: {e}")
            return None
        try:
            for attempt in range(ATTEMPTS):
                if attempt:
                    time.sleep(0.1)
                try:
                    dev.write(REQUEST + bytes(65 - len(REQUEST)))
                except (OSError, ValueError) as e:
                    self._diag.append(f"  write: {e}")
                    continue
                deadline = time.time() + LISTEN_MS / 1000.0
                while time.time() < deadline:
                    r = dev.read(64, READ_TIMEOUT_MS)
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
