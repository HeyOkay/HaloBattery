"""Steam Controller (2025) over its puck: the battery from the controller's own
input reports.

The puck (dongle) relays one controller per slot: its vendor collection (usage
page 0xFF00 / usage 0x0001) on interfaces 2 to 5, one interface per slot, the
way SDL's HIDAPI driver reads it
(src/joystick/hidapi/SDL_hidapi_steam_triton.c). SDL names the frames:

  * input report 0x43 (ID_TRITON_BATTERY_STATUS): byte 0 the charge state
    (1 discharging, 2 charging, 4 charging done), byte 1 the level in %.
  * 0x79 and 0x46 (ID_TRITON_WIRELESS_STATUS): byte 0 the state, 2 a controller
    connected, 1 disconnected.
  * 0x42, 0x45 and 0x47: the controller's state reports. No battery, but their
    arrival means the controller in that slot is switched on.

Listen only, never written: SDL sends rumble, IMU and a "lizard mode" setting
over the same report - the setting is re-sent every 3 seconds - but the app must
not, because disabling lizard mode changes how the controller behaves for the
user's games. Nothing is ever sent from here.

How often the puck sends 0x43 was not measured, so the last level is kept
between reports (that is what SDL does too). A slot that goes quiet keeps its
last value greyed for ASLEEP_KEEP; a disconnect report removes the icon at once
and clears the value.
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

VENDOR_VALVE = 0x28DE

# pid -> puck name. The older (2015) Steam Controller's dongle (0x1142) speaks a
# different protocol and is not read here.
PUCKS = {
    0x1304: "Steam Controller Puck (Proteus)",
    0x1305: "Steam Controller Puck (Nereid)",
}
NAME = "Steam Controller"

# One vendor collection per controller slot, on its own interface (SDL's driver).
# The same interface also carries the mouse and keyboard collections of the
# controller's "lizard mode"; those are ignored.
USAGE_PAGE = 0xFF00
USAGE = 0x0001
SLOT_INTERFACES = (2, 3, 4, 5)

REPORT_BATTERY = 0x43
REPORTS_WIRELESS = (0x79, 0x46)
REPORTS_STATE = (0x42, 0x45, 0x47)

CHARGE_DISCHARGING = 1
CHARGE_CHARGING = 2
CHARGE_DONE = 4

# s: how long one slot is listened to per poll. A live slot streams the state
# reports fast (SDL: about 4 ms apart), so the window is plenty to see it; a
# quiet slot costs the whole window, and four of them a couple of seconds.
WINDOW = 0.4
MAX_REPORTS = 1024   # a safety valve against a pathological stream

ASLEEP_KEEP = 300    # s: a slot that went quiet keeps its last value (greyed)

NOT_REPORTED = "connected, battery level not reported yet"


def parse_charge(byte: int) -> Optional[bool]:
    """The charge state byte -> charging, or None for the states that mean
    nothing (0 reset, 3 source validate, anything else)."""
    if byte == CHARGE_DISCHARGING:
        return False
    if byte in (CHARGE_CHARGING, CHARGE_DONE):
        return True
    return None


def parse_battery(data) -> Optional[Tuple[int, bool]]:
    """Input report 0x43 -> (level %, charging), or None when the frame carries
    no usable level. SDL wants the full battery struct: 1 + 14 bytes."""
    if len(data) < 15 or data[0] != REPORT_BATTERY:
        return None
    charging = parse_charge(data[1])
    level = data[2]
    if charging is None or level > 100:
        return None
    return level, charging


class SteamProvider(Provider):
    name = "steam"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[int, bool]] = {}   # key -> level, charging
        self._seen: Dict[str, float] = {}              # key -> when it was last seen

    def _read(self, path) -> dict:
        """Listen on one slot -> {"battery": (level, charging) or None,
        "state": bool, "wireless": 1 / 2 / None, "seen": {report id: [count, bytes]}}"""
        res: dict = {"battery": None, "state": False, "wireless": None, "seen": {}}
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return res
        try:
            try:
                dev.set_nonblocking(True)
            except Exception:
                pass
            deadline = time.time() + WINDOW
            count = 0
            while time.time() < deadline and count < MAX_REPORTS:
                try:
                    data = dev.read(64)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    break
                if not data:
                    time.sleep(0.005)
                    continue
                count += 1
                entry = res["seen"].setdefault(data[0], [0, list(data)])
                entry[0] += 1
                if data[0] in REPORTS_STATE:
                    res["state"] = True
                elif data[0] in REPORTS_WIRELESS:
                    if len(data) >= 2:
                        res["wireless"] = data[1]
                elif data[0] == REPORT_BATTERY:
                    parsed = parse_battery(data)
                    if parsed is not None:
                        res["battery"] = parsed
                    else:
                        self._diag.append(f"    report 0x43 carries no level: "
                                          f"{hexdump(data, 16)}")
            return res
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VENDOR_VALVE)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(steam): %s", e)
            return []

        slots = []                       # (pid, interface, path), one per slot
        for d in infos:
            pid = d["product_id"]
            if pid not in PUCKS:
                continue
            if d.get("interface_number") not in SLOT_INTERFACES:
                continue
            if (d.get("usage_page"), d.get("usage")) != (USAGE_PAGE, USAGE):
                continue
            slots.append((pid, d.get("interface_number"), d["path"]))
        slots.sort(key=lambda s: (s[0], s[1]))

        out: List[DeviceStatus] = []
        now = time.time()
        for pid, iface, path in slots:
            key = f"steam:{pid:04x}:{iface}"
            self._diag.append(f"[Steam] {NAME} slot iface={iface} pid={pid:04x}: "
                              f"{PUCKS[pid]}, listening on usage=ff00:0001")
            res = self._read(path)
            for rid, (n, sample) in sorted(res["seen"].items()):
                self._diag.append(f"    report {rid:#04x} x{n} len={len(sample)}: "
                                  f"{hexdump(sample, 20)}")
            wireless = res["wireless"]
            if wireless == 1:
                self._diag.append("    the puck reports the controller disconnected")
                if key in self._last:
                    log.info("[Steam] %s slot %d: disconnected", NAME, iface)
                self._last.pop(key, None)
                self._seen.pop(key, None)
                continue
            active = res["state"] or wireless == 2 or REPORT_BATTERY in res["seen"]
            if active:
                if res["battery"] is not None:
                    self._last[key] = res["battery"]
                self._seen[key] = now
                last = self._last.get(key)
                if last is None:
                    self._diag.append(f"    -> {NOT_REPORTED}")
                    out.append(DeviceStatus(key, NAME, None, False, True, "steam",
                                            NOT_REPORTED, kind="gamepad"))
                else:
                    level, charging = last
                    self._diag.append(f"    -> {level}%"
                                      f"{', charging' if charging else ''}")
                    out.append(DeviceStatus(key, NAME, level, charging, True, "steam",
                                            kind="gamepad"))
                continue
            last = self._last.get(key)
            seen_at = self._seen.get(key, 0.0)
            if last is not None and now - seen_at < ASLEEP_KEEP:
                self._diag.append("    nothing in the window: keeping the last value")
                out.append(DeviceStatus(key, NAME, last[0], False, False, "steam",
                                        kind="gamepad"))
            elif last is not None:
                self._diag.append("    nothing for a while: dropping the icon")
                self._last.pop(key, None)
                self._seen.pop(key, None)
            else:
                self._diag.append("    nothing in the window (no controller in this slot)")
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
