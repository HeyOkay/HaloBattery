"""Finalmouse UltralightX (ULX), on its 2.4 GHz dongle (361D:0100).

Protocol from Finalmouse's own web configurator, XPanel (xpanel.finalmouse.com), which
talks to the dongle over WebHID:
  * the vendor collection of the dongle, usage page 0xFF00
  * requests are output report 04, 63 bytes: <length> <0x80 | command> <payload length>
    <payload>, where length counts the two bytes after it plus the payload
  * the dongle relays them to the mouse, and the answers come back as input report 05:
    <length> <command> <payload length> <payload> (the high bit of the command is
    masked off, as XPanel does)
  * commands used here:
      36 link state        payload <state>, 0 = the mouse is not connected
      38 battery status    payload <state of charge 0..100> <voltage mV, 16-bit LE>
       5 battery voltage   payload <voltage mV, 16-bit LE>; converted with XPanel's curve
      37 charging          payload <1 = charging>
  * the settings writes captured by johnklucinec/ulx_reverse (04 03 92 01 01 ... for
    motion sync) have the same framing
Battery status is asked first. The voltage is the fallback for firmware that does not
answer it, and a state of charge above 100 is refused, as XPanel does. The fallback is
not theoretical: the ULX dongle this was tested on (firmware behind product string
'UltralightX dongle', 361D:0100) never answers command 38, and every reading comes from
the voltage curve.

The mouse on its USB cable (361D:0102) only takes firmware updates: XPanel reads it
through the dongle, and so does this provider.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

FINALMOUSE_VID = 0x361D
DONGLES = {
    0x0100: "Finalmouse UltralightX",
}
VENDOR_USAGE_PAGE = 0xFF00

OUT_REPORT = 0x04
IN_REPORT = 0x05
REPORT_SIZE = 63                 # without the report id

CMD_VBAT = 5
CMD_LINK_STATE = 36
CMD_BATTERY_CHARGING = 37
CMD_BATTERY_STATUS = 38

TIMEOUT = 0.5                    # per command: the dongle has to reach the mouse first

# how long a silent mouse keeps its (greyed-out) icon before it is hidden, s
ASLEEP_KEEP = 300

# XPanel's discharge curve: volts -> percent, linear between the points
VOLTS = (3.0, 3.62, 3.66, 3.74, 3.88, 4.17, 4.38)
PERCENT = (0.2, 5, 10, 25, 50, 75, 100)


def request(cmd: int, payload: Optional[List[int]] = None) -> List[int]:
    payload = list(payload or [])
    body = [2 + len(payload), 0x80 | cmd, len(payload)] + payload
    return [OUT_REPORT] + body + [0x00] * (REPORT_SIZE - len(body))


def parse_frame(r) -> Optional[Tuple[int, List[int]]]:
    """Input report 05 -> (command, payload), or None for anything else."""
    r = list(r or [])
    if len(r) < 4 or r[0] != IN_REPORT:
        return None
    plen = r[3]
    if len(r) < 4 + plen:
        return None
    return r[2] & 0x7F, r[4:4 + plen]


def volts_to_percent(mv: int) -> Optional[int]:
    if mv in (0, 0xFFFF):
        return None
    v = mv / 1000
    if v > VOLTS[-1]:
        return 100
    if v < VOLTS[0]:
        return 0
    for i in range(len(VOLTS) - 1):
        lo, hi = VOLTS[i], VOLTS[i + 1]
        if lo <= v <= hi:
            return math.floor(PERCENT[i] + (PERCENT[i + 1] - PERCENT[i]) * (v - lo) / (hi - lo)
                              + 0.5)
    return 100


def parse_battery_status(payload: List[int]) -> Optional[int]:
    if len(payload) < 3 or payload[0] > 100:
        return None
    return payload[0]


def parse_vbat(payload: List[int]) -> Optional[int]:
    if len(payload) < 2:
        return None
    return volts_to_percent(payload[0] | (payload[1] << 8))


class FinalmouseProvider(Provider):
    name = "finalmouse"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[int, bool, float]] = {}

    def _ask(self, dev, cmd: int) -> Optional[List[int]]:
        """Send one command and return the payload of the answer to it, or None."""
        try:
            dev.write(request(cmd))
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  cmd {cmd}: write: {e}")
            return None
        end = time.time() + TIMEOUT
        while time.time() < end:
            r = dev.read(64, 50)
            frame = parse_frame(r)
            if frame is not None and frame[0] == cmd:
                self._diag.append(f"  cmd {cmd}: {hexdump(r, 4 + len(frame[1]))}")
                return frame[1]
        self._diag.append(f"  cmd {cmd}: no reply")
        return None

    def _read(self, path: bytes) -> Tuple[Optional[int], bool, Optional[bool]]:
        """-> (level, charging, linked). linked is None when the link state is unknown."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None, False, None
        try:
            link = self._ask(dev, CMD_LINK_STATE)
            linked = None if not link else link[0] != 0
            if linked is False:
                return None, False, False
            level = None
            payload = self._ask(dev, CMD_BATTERY_STATUS)
            if payload is not None:
                level = parse_battery_status(payload)
            if level is None:
                payload = self._ask(dev, CMD_VBAT)
                if payload is not None:
                    level = parse_vbat(payload)
            if level is None:
                return None, False, linked
            chg = self._ask(dev, CMD_BATTERY_CHARGING)
            return level, bool(chg) and chg[0] == 1, True
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  error: {e}")
            return None, False, None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(FINALMOUSE_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(finalmouse): %s", e)
            return []
        out = []
        for pid, name in DONGLES.items():
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            vendor = sorted((d for d in mine if d.get("usage_page") == VENDOR_USAGE_PAGE),
                            key=lambda d: d.get("usage") != 0x0001)
            if not vendor:
                offered = ", ".join(f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                                    for d in mine)
                self._diag.append(f"[Finalmouse] pid={pid:04x}: no usage page "
                                  f"{VENDOR_USAGE_PAGE:04x} collection (found: {offered})")
                continue
            self._diag.append(f"[Finalmouse] pid={pid:04x} '{name}'")
            key = f"finalmouse:{pid:04x}"
            level, chg, linked = self._read(vendor[0]["path"])
            if level is not None:
                self._last[key] = (level, chg, time.time())
                out.append(DeviceStatus(key, name, level, chg, True, "finalmouse", kind="mouse"))
                continue
            if linked is False:
                self._diag.append("  the mouse is not connected to the dongle (off or asleep)")
            # the dongle cannot tell a switched-off mouse from one that is asleep:
            # keep the last level, greyed out, for a while. wake_on_input: a sleeping
            # mouse is awake by the time its first movement lands, so the tray may poll
            # at once instead of waiting out the interval. The gate #202 warned about
            # stays narrow here - unlike a receiver that keeps its greyed icon for as
            # long as it is plugged in, this one expires after ASLEEP_KEEP.
            last = self._last.get(key)
            if last and time.time() - last[2] < ASLEEP_KEEP:
                out.append(DeviceStatus(key, name, last[0], last[1], False, "finalmouse",
                                        kind="mouse", wake_on_input=True))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
