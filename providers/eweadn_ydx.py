"""EWEADN X23 Pro, 2.4 GHz receiver 04F3:026F (YDX protocol).

Source: https://hub.eweadn.cn/deviceJson/other.json maps this receiver to
https://hub.eweadn.cn/ydx/web/. In assets/index-BvEqCbAL.js, HG() calls
receiveFeatureReport(5) to request status; its inputreport handler decodes
report 4. DeviceView-ss1Fdo4r.js maps battery status 1 to charging, 2 to full.
Both collections have usage page 000A, usage 0000 on interface 1. The Windows
descriptors declare 31 payload bytes: input report 4 on one collection,
feature report 5 on another. Open both for the SAME receiver, then GET_FEATURE
5 triggers input report 4. hidapi includes the report ID; WebHID does not.
No output report, SET_FEATURE, configuration or firmware command is sent.
"""
from __future__ import annotations

import hashlib
import time
from typing import List

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump
from .endgame_we import receiver_path

VID, PID = 0x04F3, 0x026F
REPORT_LEN = 32
TIMEOUT = 1.5


def parse_status(data):
    """(level, charging, online, charging_known), or None for an invalid report."""
    if not data or len(data) != REPORT_LEN or data[0] != 4 or data[1] not in (0, 1):
        return None
    if not data[1]:
        return None, False, False, False
    level, charge = data[4], data[5]
    if not 0 <= level <= 100:
        return None
    return 100 if charge == 2 else level, charge == 1, True, charge in (0, 1, 2)


class EweadnYdxProvider(Provider):
    name = "eweadn_ydx"

    def __init__(self):
        self._diag: List[str] = []

    def _read(self, entries):
        opened = []
        try:
            reader = feature = None
            for entry in entries:
                dev = hid.device()
                opened.append(dev)
                dev.open_path(entry["path"])
                descriptor = bytes(dev.get_report_descriptor())
                if b"\x85\x04" in descriptor and b"\x81\x02" in descriptor:
                    reader = dev
                if b"\x85\x05" in descriptor and b"\xb1\x02" in descriptor:
                    feature = dev
            if reader is None or feature is None:
                self._diag.append("  missing input-4 or feature-5 collection")
                return None
            reader.set_nonblocking(True)
            for _ in range(32):
                if not reader.read(REPORT_LEN):
                    break
            else:
                self._diag.append("  status queue did not drain")
                return None
            reader.set_nonblocking(False)
            feature.get_feature_report(5, REPORT_LEN)
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline:
                data = reader.read(REPORT_LEN, timeout_ms=100)
                result = parse_status(data)
                if result is not None:
                    self._diag.append(f"  input 04: {hexdump(data, 10)}")
                    return result
            self._diag.append("  no fresh status (mouse asleep/off, or receiver busy)")
        except (OSError, ValueError, AttributeError) as error:
            self._diag.append(f"  read: {error}")
        finally:
            for dev in opened:
                try:
                    dev.close()
                except OSError:
                    pass
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        groups = {}
        try:
            for entry in hidlist.enumerate(VID):
                if (entry.get("product_id") != PID or entry.get("interface_number") != 1
                        or entry.get("usage_page") != 0x000A or entry.get("usage") != 0):
                    continue
                path = entry.get("path")
                if path:
                    groups.setdefault(receiver_path(path), []).append(entry)
        except (OSError, ValueError) as error:
            self._diag.append(f"[EWEADN YDX] enumerate: {error}")
            return []
        out = []
        for path, entries in groups.items():
            key = "eweadn_ydx:" + hashlib.sha1(path).hexdigest()[:12]
            self._diag.append(f"[EWEADN YDX] X23 Pro 04f3:026f ({len(entries)} collections)")
            result = self._read(entries)
            level, charging, online, known = result or (None, False, False, False)
            out.append(DeviceStatus(key, "EWEADN X23 Pro", level, charging, online,
                                    self.name, kind="mouse", wake_on_input=True,
                                    charging_known=known))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
