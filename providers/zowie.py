"""BenQ ZOWIE wireless mice (vendor 04A5).

Two read-only protocols:

  * EC-CW (receiver 8006, cable 8005): a 3-byte unnumbered request
    `00 A5 03`; the reply starts with millivolts (LE), then the percentage.
    A zero voltage means the mouse is not linked.
  * DW / U2 (U2 receiver 8008, DW receiver 800A): command report 0x08
    `B4 15` on usage page FF03, reply report 0x09 on FF04 with millivolts
    scaled by 10 in bytes 1-2, and byte 4 == 0xC8.

ZOWIE has no charging contact on USB; charging is inferred from the voltage
trend, exactly as the MIT zowie-battery-tray does. Only the battery commands
are sent - other EC-CW commands reboot the receiver.
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

VID = 0x04A5
CW_CABLE = 0x8005
CW_RECEIVER = 0x8006
DW_RECEIVER = 0x800A
U2_RECEIVER = 0x8008
ALL_PIDS = (CW_CABLE, CW_RECEIVER, DW_RECEIVER, U2_RECEIVER)
READ_TIMEOUT_MS = 250
LISTEN_MS = 1000

# Mouse LED thresholds, from zowie-battery / zowie-battery-tray.
_V = (4200, 4000, 3890, 3760, 3680, 3500)
_P = (100, 75, 50, 25, 10, 0)


def percent_for(mv: int) -> int:
    if mv >= _V[0]:
        return 100
    if mv <= _V[-1]:
        return 0
    for (v1, p1), (v2, p2) in zip(zip(_V, _P), list(zip(_V, _P))[1:]):
        if v2 <= mv <= v1:
            return round(p2 + (mv - v2) * (p1 - p2) / (v1 - v2))
    return 0


class ZowieProvider(Provider):
    name = "zowie"

    def __init__(self):
        self._diag: List[str] = []
        self._trend: Dict[int, Tuple[bool, bool, int, int]] = {}

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(zowie): %s", e)
            return []

        self._infos = infos
        by_pid: Dict[int, List[dict]] = {}
        for d in infos:
            pid = d.get("product_id")
            if isinstance(pid, int) and pid in ALL_PIDS:
                by_pid.setdefault(pid, []).append(d)

        out: List[DeviceStatus] = []
        for pid in (CW_CABLE, CW_RECEIVER, DW_RECEIVER, U2_RECEIVER):
            for d in by_pid.get(pid, []):
                if pid in (CW_CABLE, CW_RECEIVER):
                    got = self._read_cw(d)
                else:
                    got = self._read_dw(d, pid)
                if got is None:
                    continue
                level, charging, mv = got
                key = f"zowie:{pid:04x}"
                name = "ZOWIE U2" if pid == U2_RECEIVER else (
                    "ZOWIE EC-CW" if pid in (CW_CABLE, CW_RECEIVER) else
                    (d.get("product_string") or "ZOWIE DW mouse"))
                via = "USB cable" if pid == CW_CABLE else "2.4 GHz receiver"
                self._diag.append(f"[{key}] {level}% charging={charging} mv={mv}")
                out.append(DeviceStatus(key, name, level, charging, True, "zowie",
                                        kind="mouse", via=via))
                break
        return out

    def _read_cw(self, d) -> Optional[Tuple[int, bool, int]]:
        if d.get("output_report_byte_length") != 65:
            return None
        if not (d.get("usage_page") == 0x00FF or (d.get("usage_page") or 0) >= 0xFF00):
            return None
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
            dev.write(bytes([0x00, 0xA5, 0x03]) + bytes(62))
            end = time.time() + LISTEN_MS / 1000.0
            while time.time() < end:
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  CW {hexdump(r, 16)}")
                if len(r) < 3:
                    continue
                mv = r[0] | (r[1] << 8)
                if mv != 0 and not 3000 <= mv <= 4500:
                    continue
                if mv == 0:
                    return None
                pct = r[2] if 1 <= r[2] <= 100 else percent_for(mv)
                return pct, d.get("product_id") == CW_CABLE, mv
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  CW error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _read_dw(self, d, pid: int) -> Optional[Tuple[int, bool, int]]:
        # The reply and command live on separate collections; find both by page.
        infos = self._infos or []
        cmd = next((x for x in infos if x.get("product_id") == pid
                    and x.get("usage_page") == 0xFF03
                    and x.get("output_report_byte_length") == 16), None)
        rep = next((x for x in infos if x.get("product_id") == pid
                    and x.get("usage_page") == 0xFF04
                    and x.get("input_report_byte_length") == 16), None)
        if cmd is None or rep is None:
            return None
        in_dev = hid.device()  # type: ignore[union-attr]
        out_dev = hid.device()  # type: ignore[union-attr]
        try:
            in_dev.open_path(rep["path"])
            out_dev.open_path(cmd["path"])
            out_dev.write(bytes([0x08, 0xB4, 0x15]) + bytes(13))
            end = time.time() + LISTEN_MS / 1000.0
            while time.time() < end:
                r = in_dev.read(16, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  DW {hexdump(r, 16)}")
                if len(r) < 5 or r[0] != 0x09 or r[1] == 0xE6 or r[4] != 0xC8:
                    continue
                mv = (r[1] * 256 + r[2]) * 10
                if mv == 0:
                    return None
                charging = self._update_trend(pid, mv)
                return percent_for(mv), charging, mv
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  DW error: {e}")
        finally:
            for dev in (in_dev, out_dev):
                try:
                    dev.close()
                except Exception:
                    pass
        return None

    def _update_trend(self, pid: int, mv: int) -> bool:
        state = self._trend.get(pid)
        if state is None:
            charging = mv >= 4250
            self._trend[pid] = (True, charging, mv, mv)
            return charging
        _, charging, peak, trough = state
        if charging:
            peak = max(peak, mv)
            if mv <= peak - 20:
                charging, trough = False, mv
        else:
            trough = min(trough, mv)
            if mv >= trough + 50:
                charging, peak = True, mv
        self._trend[pid] = (True, charging, peak, trough)
        return charging

    _infos: List[dict] = []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
