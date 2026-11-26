"""ROCCAT Syn Max Air on its 2.4 GHz receiver `1e7d:3a3f`.

The receiver's vendor collection is usage `ff00:0001` on interface 0, with
64-byte unnumbered input/output reports. The read-only status request is

    write  8A 07 06 01 ...
    read   8A 83 06 02 <coarse 0..5> 00 <mv lo> <mv hi> <mv2 lo> <mv2 hi> <charging>

The reply tag is `8A`; the receiver also pushes `E0..E6` events, which are
skipped. No reply within 1.5 s means the headset is off or asleep.

Discharging percentage comes from the measured voltage curve in BatteryHub;
charging voltage is not a reliable state of charge, so while charging the
reader shows a charging message instead of a percentage. `4/5` and `Full`
states are derived from the same reply.

Protocol source: BatteryHub 1.1.1 src/drivers/roccat.cpp (GPL-3.0-or-later;
protocol reference only), from Swarm captures and ferloom's Rust port.
"""
from __future__ import annotations

import time
from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x1E7D
PID = 0x3A3F
USAGE = (0xFF00, 0x0001)
REPORT_LEN = 64
TAG = 0x8A
TIMEOUT_S = 1.5
KEY = "roccat:3a3f"
NAME = "ROCCAT Syn Max Air"

# Discharging under load: (millivolts, percent), measured on one unit.
CURVE = (
    (4080, 100), (4040, 97), (4000, 92), (3960, 86), (3920, 78), (3900, 74),
    (3880, 70), (3840, 66), (3820, 63), (3800, 60), (3780, 53), (3760, 45),
    (3740, 34), (3720, 26), (3700, 20), (3680, 15), (3660, 11), (3640, 7),
    (3600, 3), (3500, 0),
)


def percent_from_mv(mv: int) -> int:
    if mv >= CURVE[0][0]:
        return CURVE[0][1]
    if mv <= CURVE[-1][0]:
        return CURVE[-1][1]
    for (mv_a, pct_a), (mv_b, pct_b) in zip(CURVE, CURVE[1:]):
        if mv_b <= mv <= mv_a:
            return pct_b + (mv - mv_b) * (pct_a - pct_b) // (mv_a - mv_b)
    return 0


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[:5] = [0x00, TAG, 0x07, 0x06, 0x01]
    return request


def _payload(report) -> bytes:
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == TAG:
        b = b[1:]
    return b


def parse_reply(report) -> Optional[dict]:
    b = _payload(report)
    if len(b) < 10 or b[0] != TAG or b[1] != 0x83 or b[2] != 0x06:
        return None
    level = b[4]
    mv = b[6] | (b[7] << 8)
    charging = len(b) > 10 and b[10] == 0x01
    return {"level": level, "mv": mv, "charging": charging}


class RoccatProvider(Provider):
    name = "roccat"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and d.get("interface_number") == 0
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("input_report_byte_length") or REPORT_LEN) >= REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) >= REPORT_LEN):
                return d
        return None

    def _query(self, path) -> Optional[dict]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                for _ in range(4):
                    if not dev.read(REPORT_LEN, 20):
                        break
            except (OSError, ValueError):
                pass
            try:
                dev.write(make_request())
            except (OSError, ValueError) as e:
                self._diag.append(f"  write: {e}")
                return None
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                try:
                    report = dev.read(REPORT_LEN, 100)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  read: {e}")
                    return None
                if not report:
                    continue
                self._diag.append(f"  report: {hexdump(report, 12)}")
                payload = _payload(report)
                if not payload or payload[0] != TAG:
                    continue
                parsed = parse_reply(report)
                if parsed is not None:
                    return parsed
            self._diag.append("  no status reply")
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
            log.warning("hid.enumerate(roccat): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[ROCCAT Syn Max Air] pid={PID:04x} iface={d.get('interface_number')}")
        got = self._query(d["path"])
        if got is None:
            return [DeviceStatus(KEY, NAME, None, False, False, "roccat",
                                 kind="headset", charging_known=True)]
        mv = got["mv"]
        if got["charging"]:
            approx = "on cable, full" if mv >= 4180 else "charging (level unavailable)"
            return [DeviceStatus(KEY, NAME, None, True, True, "roccat",
                                 approx=approx, kind="headset")]
        return [DeviceStatus(KEY, NAME, percent_from_mv(mv), False, True, "roccat",
                             kind="headset")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
