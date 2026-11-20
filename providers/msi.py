"""MSI wireless mice (vendor 0DB0).

Two read-only paths from MSI Center's Gaming Gear module:

  * VERSA 300 ELITE (9E22) and Clutch GM31 (receiver 0D3C / cable 0D3B):
    output request `00 0F 20 00 ...` on FF0D:00B0 or FF00:0001, reply
    `0F 20 ...` with byte 14 = charging bit 7 + percentage.
  * Clutch GM41 (receiver 0D4C / cable 0D4B): passive `A0 <raw> 07 ...`
    reports on FF10:0006; the raw value maps to a percentage.

MSI Center's `41 80` remote-control request is deliberately not sent.
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

VID = 0x0DB0
MODELS = {
    0x9E22: ("msi:9e22", "MSI VERSA 300 ELITE WIRELESS", False, "query", 0xFF0D, 0x00B0),
    0x0D3C: ("msi:0d31", "MSI Clutch GM31 Lightweight Wireless", False, "query", 0xFF00, 0x0001),
    0x0D3B: ("msi:0d31", "MSI Clutch GM31 Lightweight Wireless", True, "query", 0xFF00, 0x0001),
    0x0D4C: ("msi:0d41", "MSI Clutch GM41 Lightweight Wireless", False, "passive", 0xFF10, 0x0006),
    0x0D4B: ("msi:0d41", "MSI Clutch GM41 Lightweight Wireless", True, "passive", 0xFF10, 0x0006),
}
INTERFACE = 1
REQUEST = bytes([0x00, 0x0F, 0x20])
READ_TIMEOUT_MS = 250
QUERY_LISTEN_MS = 1000
PASSIVE_LISTEN_MS = 2000


def parse_query(r) -> Optional[Tuple[int, bool]]:
    if not r or len(r) < 15:
        return None
    b = list(r)
    if len(b) > 1 and b[0] == 0x00 and b[1] == 0x0F:
        b = b[1:]
    if b[0] != 0x0F or b[1] != 0x20:
        return None
    level = b[14] & 0x7F
    if level > 100:
        return None
    return level, bool(b[14] & 0x80)


def gm41_percent(raw: int) -> int:
    return max(0, min(100, (raw * 51 + 1760 + 50) // 100))


def parse_passive(r) -> Optional[Tuple[int, int]]:
    if not r or len(r) < 4:
        return None
    b = list(r)
    if b[0] != 0xA0 or b[2] != 0x07:
        return None
    return gm41_percent(b[1]), False


class MsiProvider(Provider):
    name = "msi"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(msi): %s", e)
            return []

        out: List[DeviceStatus] = []
        seen = set()
        for pid, (key, name, cable, mode, page, usage) in MODELS.items():
            if key in seen:
                continue
            for d in infos:
                if d.get("product_id") != pid or d.get("interface_number") != INTERFACE \
                        or d.get("usage_page") != page or d.get("usage") != usage:
                    continue
                if mode == "query":
                    if (d.get("output_report_byte_length") or 0) < 65 \
                            or (d.get("input_report_byte_length") or 0) < 65:
                        continue
                    got = self._query(d)
                else:
                    if (d.get("input_report_byte_length") or 0) < 7:
                        continue
                    got = self._passive(d)
                if got is None:
                    continue
                level, charging = got
                seen.add(key)
                out.append(DeviceStatus(key, name, level, charging, level > 0 or charging or not cable,
                                        "msi", kind="mouse",
                                        via="USB cable" if cable else "2.4 GHz receiver"))
                break
        return out

    def _query(self, d) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
            out_len = d.get("output_report_byte_length") or 65
            dev.write(REQUEST + bytes(max(0, out_len - len(REQUEST))))
            end = time.time() + QUERY_LISTEN_MS / 1000.0
            while time.time() < end:
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  {hexdump(r, 16)}")
                got = parse_query(r)
                if got is not None:
                    return got
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _passive(self, d) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
            end = time.time() + PASSIVE_LISTEN_MS / 1000.0
            while time.time() < end:
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  {hexdump(r, 16)}")
                got = parse_passive(r)
                if got is not None:
                    return got
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)

