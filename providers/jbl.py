"""JBL Quantum 910 Wireless over USB/HID, without JBL QuantumENGINE.

Protocol from plugato/JBL_Baterry_Monitor, which reads this headset on Linux for
exactly this USB id (0ecb:2088):

  * the headset pushes its own reports on the receiver's vendor collection
    (ff13:0001, reported on interface 5 by a real unit); there is no request to send.
    The battery arrives as report id 0x08 with the level in byte 1 - the pattern that
    project confirmed against the hardware ([Report ID, Battery%] -> 08 1e = 30%).
  * report id 0x2f carries the microphone mute state, not a level.
  * the headset can stay quiet for a long time, so the last level heard is kept and
    shown greyed out until something arrives again - the project reaches for the
    headset's own buttons to make it talk, and a probe here does the same by
    reporting what it saw.

Charging is not in the frame that carries the level, so none is reported.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

JBL_VID = 0x0ECB
JBL_PID_QUANTUM910 = 0x2088

USAGE_PAGE = 0xFF13             # the receiver's vendor collection
USAGE = 0x0001

REPORT_ID_BATTERY = 0x08
REPORT_ID_MUTE = 0x2F
LEVEL_INDEX = 1

# The headset talks when it feels like it: one second was demonstrably too short on a
# real unit (a reporter pressed every button and rolled the volume, and nothing arrived),
# so listen for about three seconds before concluding it is quiet.
READ_ATTEMPTS = 12
READ_TIMEOUT_MS = 250

PIDS = {
    JBL_PID_QUANTUM910: "JBL Quantum 910 Wireless",
}


def parse_level(r) -> Optional[int]:
    """Percent from a battery report, or None when this is not one."""
    if not r or len(r) <= LEVEL_INDEX:
        return None
    if r[0] != REPORT_ID_BATTERY:
        return None
    level = r[LEVEL_INDEX]
    return level if 0 <= level <= 100 else None


class JblProvider(Provider):
    name = "jbl"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[bytes, int] = {}     # the headset goes quiet; keep what we heard

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        # pick the collection by usage, never by interface number or position: a
        # reporter's dump of a real receiver shows exactly one vendor collection,
        # 0ecb:2088 as ff13:0001 on interface 5, while the Linux reference reaches the
        # same receiver through its interrupt endpoint
        for d in infos:
            if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
                return d
        self._diag.append(f"  no {USAGE_PAGE:04x}:{USAGE:04x} collection among {len(infos)}; "
                          f"using the first")
        return infos[0] if infos else None

    def _listen(self, path: bytes) -> Optional[int]:
        """Read whatever the headset has sent since the last poll."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            for attempt in range(READ_ATTEMPTS):
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue        # the headset goes quiet between reports; keep listening
                self._diag.append(f"  report {attempt + 1}: {hexdump(r)}")
                level = parse_level(r)
                if level is not None:
                    return level
                if r[0] == REPORT_ID_MUTE:
                    self._diag.append("  (mute report, not a level)")
                time.sleep(0.02)
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  read error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(JBL_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(jbl): %s", e)
            return []
        out = []
        for pid in PIDS:
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            d = self._pick(mine)
            if d is None:
                continue
            name = PIDS[pid]
            key = f"jbl:{pid:04x}"
            path = d["path"]
            self._diag.append(f"[JBL] pid={pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            level = self._listen(path)
            if level is not None:
                self._last[path] = level
                out.append(DeviceStatus(key, name, level, False, True, "jbl", kind="headset"))
                continue
            if path in self._last:
                self._diag.append("  nothing new; keeping the last level, greyed out")
                out.append(DeviceStatus(key, name, self._last[path], False, False,
                                        "jbl", kind="headset"))
            else:
                self._diag.append("  nothing heard yet and no earlier level")
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
