"""WLmouse mice (Beast X, Beast X Max, Beast X Mini Pro; 8K and 1K receivers).

Protocol (reverse-engineered by @len0c, see incconutwo/mouse-battery-tray, MIT):
  * request: feature report id 0, payload 00 00 02 02 00 83 ...
  * reply:   a1|a2 00 02 02 00 83 <charging> <battery%>
  * fallback: the mouse periodically sends an input report 03 00 <battery%> <charging>
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from .base import DeviceStatus, Provider, hexdump, log

WLMOUSE_VIDS = (0x36A7,)

KNOWN = {
    0xA887: "WLmouse Beast X",
    0xA868: "WLmouse Beast X Mini Pro",
    0xA880: "WLmouse Beast X Max",          # 8K receiver, confirmed on hardware
}

# receiver PIDs (a mouse on the cable shows up in Windows with its own PID)
RECEIVERS = {0xA887, 0xA868, 0xA880}

QUERY = [0x00, 0x00, 0x02, 0x02, 0x00, 0x83]


def parse_feature(resp) -> Tuple[Optional[int], Optional[bool]]:
    if not resp:
        return None, None
    r = list(resp)
    for i in range(5, len(r) - 2):
        if r[i] == 0x83 and r[i - 1] == 0x00 and r[i - 2] == 0x02 and r[i - 5] in (0xA1, 0xA2):
            charge, batt = r[i + 1], r[i + 2]
            if 0 <= batt <= 100:
                return batt, bool(charge)
    return None, None


def parse_heartbeat(data) -> Tuple[Optional[int], Optional[bool]]:
    d = list(data or [])
    for off in (0, 1):
        if len(d) >= off + 4 and d[off] == 0x03 and d[off + 1] == 0x00:
            batt = d[off + 2]
            if 0 <= batt <= 100:
                return batt, bool(d[off + 3])
    return None, None


class WLmouseProvider(Provider):
    name = "wlmouse"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[int, bool, float]] = {}
        self._name: Optional[str] = None      # mouse name (remembered from a known PID)

    def _read_feature(self, path: bytes) -> Tuple[Optional[int], Optional[bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None, None
        try:
            report = [0x00] + QUERY + [0x00] * (64 - len(QUERY))
            try:
                dev.send_feature_report(report)
            except (OSError, ValueError) as e:
                self._diag.append(f"    send: {e}")
            for _ in range(15):
                time.sleep(0.05)
                for length in (65, 64):
                    try:
                        resp = dev.get_feature_report(0, length)
                    except (OSError, ValueError):
                        resp = None
                    if resp:
                        batt, chg = parse_feature(resp)
                        if batt is not None:
                            self._diag.append(f"    reply: {hexdump(resp, 12)}")
                            return batt, chg
            self._diag.append("    no reply to request 0x83")
            return None, None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _read_passive(self, path: bytes, seconds: float) -> Tuple[Optional[int], Optional[bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
            dev.set_nonblocking(True)
        except (OSError, IOError):
            return None, None
        deadline = time.time() + seconds
        try:
            while time.time() < deadline:
                try:
                    data = dev.read(64)
                except (OSError, ValueError):
                    break
                if data:
                    batt, chg = parse_heartbeat(data)
                    if batt is not None:
                        self._diag.append(f"    heartbeat: {hexdump(data, 8)}")
                        return batt, chg
                time.sleep(0.02)
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None, None

    def _read_group(self, ifaces: List[dict]) -> Tuple[Optional[int], Optional[bool]]:
        vendor = [d for d in ifaces if d.get("usage_page") == 0xFFFF]
        vendor.sort(key=lambda d: 0 if d.get("usage") == 0 else 1)
        for d in vendor:
            self._diag.append(f"  iface={d.get('interface_number')} usage="
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            batt, chg = self._read_feature(d["path"])
            if batt is not None:
                return batt, chg
        # fallback: listen for the heartbeat, but only on vendor interfaces and
        # for ~2 s in total (listening on all 7 interfaces took 2 s each)
        listen = [d for d in ifaces if d.get("usage_page", 0) >= 0xFF00][:2]
        for d in listen:
            batt, chg = self._read_passive(d["path"], 2.0 / max(1, len(listen)))
            if batt is not None:
                return batt, chg
        return None, None

    def poll(self) -> List[DeviceStatus]:
        """One icon per mouse, however it is connected.

        On the cable the mouse shows up in Windows as a separate USB device with
        its own PID while the receiver stays plugged in. Treating every PID as a
        device gave a second icon on plugging in the charger while the old one
        lingered until it timed out. Now every WLmouse PID is just a data source
        for a single icon; the source that reports charging (the cable) wins.
        """
        self._diag = []
        infos = []
        for vid in WLMOUSE_VIDS:
            try:
                infos += hid.enumerate(vid)
            except Exception as e:  # pragma: no cover
                log.warning("hid.enumerate(wlmouse): %s", e)
        if not infos:
            return []

        groups: Dict[int, List[dict]] = {}
        for d in infos:
            groups.setdefault(d["product_id"], []).append(d)

        key = "wlmouse"
        readings = []           # (batt, charging, pid)

        def is_receiver(item):
            pid, ifaces = item
            return "RECEIVER" in (ifaces[0].get("product_string") or "").upper() or pid in RECEIVERS

        # the mouse on the cable first, then the receiver
        for pid, ifaces in sorted(groups.items(), key=is_receiver):
            if readings and readings[0][1] and is_receiver((pid, ifaces)):
                self._diag.append(f"[WLmouse] pid={pid:04x}: mouse is on the cable, skipping the receiver")
                continue
            product = (ifaces[0].get("product_string") or "").strip()
            if pid in KNOWN:
                self._name = KNOWN[pid]
            elif not getattr(self, "_name", None) and product:
                self._name = product if "wl" in product.lower() else f"WLmouse {product}"
            self._diag.append(f"[WLmouse] pid={pid:04x} product='{product}'")
            batt, chg = self._read_group(ifaces)
            if batt is not None:
                readings.append((batt, bool(chg), pid))
        name = getattr(self, "_name", None) or "WLmouse"

        if readings:
            # the cable (charging) beats the receiver: a wired mouse may be silent on the radio
            readings.sort(key=lambda r: (not r[1],))
            batt, chg, pid = readings[0]
            self._diag.append(f"  -> using pid={pid:04x}: {batt}%{' (charging)' if chg else ''}")
            self._last[key] = (batt, chg, time.time())
            return [DeviceStatus(key, name, batt, chg, True, "wlmouse")]

        # the mouse is asleep and the receiver is silent: show the last value for up to 30 min
        last = self._last.get(key)
        if last and time.time() - last[2] < 1800:
            return [DeviceStatus(key, name, last[0], last[1], False, "wlmouse")]
        return [DeviceStatus(key, name, None, False, False, "wlmouse")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
