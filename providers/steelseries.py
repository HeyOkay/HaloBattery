"""SteelSeries wireless headsets (Arctis Nova 7 family), directly over USB/HID,
without SteelSeries GG. Works alongside GG.

Protocol (as implemented in Sapd/HeadsetControl):
  * output report 00 b0 on the vendor interface (usage page 0xFFC0)
  * reply: b0 <?> <battery 0..100> <status> ...
      status 00 = headset off / out of range, 01 = charging, 03 = on battery

New models go into MODELS: product id -> (name, interface number, request).
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

STEELSERIES_VID = 0x1038

MODELS = {
    0x22A1: ("Arctis Nova 7", 3, [0x00, 0xB0]),     # confirmed on hardware
}

STATUS_OFFLINE, STATUS_CHARGING = 0x00, 0x01


def parse_nova(resp) -> Tuple[Optional[int], bool, bool]:
    """(level, charging, online) from an Arctis Nova reply."""
    r = list(resp or [])
    if len(r) < 4 or r[0] != 0xB0:
        return None, False, False
    if r[3] == STATUS_OFFLINE:
        return None, False, False
    level = r[2] if r[2] <= 100 else None
    return level, r[3] == STATUS_CHARGING, True


class SteelSeriesProvider(Provider):
    name = "steelseries"

    def __init__(self):
        self._diag: List[str] = []

    def _read(self, path: bytes, request: List[int]):
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            dev.write(request)
            resp = dev.read(64, 1000)
            self._diag.append(f"  reply: {hexdump(resp, 8)}")
            return resp
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(STEELSERIES_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(steelseries): %s", e)
            return []
        out = []
        seen = set()
        for d in infos:
            model = MODELS.get(d["product_id"])
            if not model or d["product_id"] in seen:
                continue
            name, iface, request = model
            if d.get("interface_number") != iface:
                continue
            seen.add(d["product_id"])
            self._diag.append(f"[SteelSeries] pid={d['product_id']:04x} '{name}'")
            level, chg, online = parse_nova(self._read(d["path"], request))
            if online and level is not None:
                out.append(DeviceStatus(f"steelseries:{d['product_id']:04x}", name, level, chg,
                                        True, "steelseries"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
