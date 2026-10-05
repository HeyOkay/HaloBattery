"""Steam Controller (2025) over its puck: the battery from the controller's own
input reports.

The puck (dongle) relays one controller per slot: interfaces 2 to 5, one
interface per slot, the way SDL's HIDAPI driver reads it
(src/joystick/hidapi/SDL_hidapi_steam_triton.c - it matches the interface
number alone). The collection ON each interface is picked by usage, best
evidence first: usage page 0xFF00 / usage 0x0001, then any vendor page, then
0001:0005 - and the pick is logged, because no dump on the record shows the
puck's exact usages (review by @ahmedkhursheed23). The frames, named by SDL:

  * input report 0x43 (ID_TRITON_BATTERY_STATUS): after the report id, byte 1
    the charge state (1 discharging, 2 charging, 4 charging done), byte 2 the
    level in % (TritonBatteryStatus_t, 14 bytes).
  * 0x79 and 0x46 (ID_TRITON_WIRELESS_STATUS): byte 0 the state, 2 a controller
    connected, 1 disconnected.
  * 0x42, 0x45 and 0x47: the controller's state reports. No battery, but their
    arrival means the controller in that slot is switched on.

Listen only, never written: SDL sends rumble, IMU and a "lizard mode" setting
over the same report - the setting is re-sent every 3 seconds - but the app must
not, because disabling lizard mode changes how the controller behaves for the
user's games. Nothing is ever sent from here.

The puck sends 0x43 about every 2.5 s while the controller is awake (~0.4 Hz,
measured over ~9k frames by CouchTurtle/sc2-research and by eva-val/steambattery;
the first tester run in #58 showed the original 0.4 s listen window missing it),
so a slot with a live controller is listened to for up to BATTERY_WINDOW - twice
the cadence - while a quiet slot costs only the short WINDOW. The last level is
kept between reports (that is what SDL does too). A slot that goes quiet keeps
its last value greyed for as long as the puck is there - no timer, because an
icon that is removed and re-created comes back at a new tray position (#87,
#202 review). A disconnect report ends the read early and removes the icon at
once, and the puck leaving the USB tree clears the value.
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

# One controller slot per interface (SDL's driver matches the interface alone).
# The interface also carries the mouse and keyboard collections of the
# controller's "lizard mode"; the slot collection is picked by usage, in this
# order, and the pick is logged.
USAGE_PAGE = 0xFF00
USAGE = 0x0001
GAMEPAD_PAGE, GAMEPAD_USAGE = 0x0001, 0x0005
SLOT_INTERFACES = (2, 3, 4, 5)

REPORT_BATTERY = 0x43
REPORTS_WIRELESS = (0x79, 0x46)
REPORTS_STATE = (0x42, 0x45, 0x47)

CHARGE_DISCHARGING = 1
CHARGE_CHARGING = 2
CHARGE_DONE = 4

# s: how long one slot is listened to per poll before anything is seen. A live
# slot streams the state reports fast (SDL: about 4 ms apart), so the window is
# plenty to see it; a quiet slot costs the whole window, and four of them a
# couple of seconds.
WINDOW = 0.4
# s: how long a slot with a live controller is listened to for its battery
# report. The puck sends 0x43 about every 2.5 s while the controller is awake
# (~0.4 Hz - CouchTurtle/sc2-research measured it over ~9k frames, eva-val/
# steambattery on live hardware; #58's first tester run is what showed the
# short window missing it), so twice the cadence always catches one. A quiet
# slot never comes here; a disconnect ends the read early.
BATTERY_WINDOW = 5.0
MAX_REPORTS = 4096   # a safety valve against a pathological stream (5 s of the
                     # ~266 Hz state stream is ~1300 reports)

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
            start = time.time()
            limit = start + WINDOW
            count = 0
            while count < MAX_REPORTS:
                if res["wireless"] == 1:
                    break                # the link dropped: nothing more will come
                if (res["state"] or res["wireless"] == 2) \
                        and limit < start + BATTERY_WINDOW:
                    # a live controller: its battery report comes ~every 2.5 s
                    limit = start + BATTERY_WINDOW
                if time.time() >= limit:
                    break
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

        by_iface: Dict[int, List[dict]] = {}
        puck_seen: List[dict] = []
        for d in infos:
            if d.get("product_id") not in PUCKS:
                continue
            puck_seen.append(d)
            iface = d.get("interface_number")
            if iface in SLOT_INTERFACES:
                by_iface.setdefault(iface, []).append(d)

        slots = []                       # (pid, interface, path, how), one per slot
        for iface in sorted(by_iface):
            cols = sorted(by_iface[iface],
                          key=lambda d: (d.get("usage_page") or 0, d.get("usage") or 0))
            pick, how = None, ""
            for d in cols:
                if (d.get("usage_page"), d.get("usage")) == (USAGE_PAGE, USAGE):
                    pick, how = d, "usage ff00:0001"
                    break
            if pick is None:
                for d in cols:           # any vendor collection on the interface
                    if (d.get("usage_page") or 0) >= 0xFF00:
                        pick = d
                        how = f"vendor page {(d.get('usage_page') or 0):04x}"
                        break
            if pick is None:
                for d in cols:           # the controller's own gamepad face
                    if (d.get("usage_page"), d.get("usage")) == (GAMEPAD_PAGE, GAMEPAD_USAGE):
                        pick, how = d, "usage 0001:0005"
                        break
            if pick is None:
                continue                 # nothing plausible: said below
            if how != "usage ff00:0001":
                self._diag.append(f"[Steam] iface={iface}: no usage ff00:0001 on this "
                                  f"interface; picked {how}")
            slots.append((pick["product_id"], iface, pick["path"], how))

        if not slots and puck_seen:
            self._diag.append("[Steam] the puck is present but no slot collection was "
                              "found on interfaces 2-5; its collections:")
            for d in sorted(puck_seen, key=lambda d: (d.get("interface_number") or 0,
                                                      d.get("usage_page") or 0,
                                                      d.get("usage") or 0)):
                self._diag.append(f"    iface={d.get('interface_number')} "
                                  f"usage={(d.get('usage_page') or 0):04x}:"
                                  f"{(d.get('usage') or 0):04x}")

        out: List[DeviceStatus] = []
        for pid, iface, path, how in slots:
            key = f"steam:{pid:04x}:{iface}"
            self._diag.append(f"[Steam] {NAME} slot iface={iface} pid={pid:04x}: "
                              f"{PUCKS[pid]}, listening on {how}")
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
                continue
            active = res["state"] or wireless == 2 or REPORT_BATTERY in res["seen"]
            if active:
                if res["battery"] is not None:
                    self._last[key] = res["battery"]
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
            if last is not None:
                # no timer: the value stays greyed while the puck is there. An icon
                # removed and re-created comes back at a new tray position (#87).
                self._diag.append("    nothing in the window: keeping the last value")
                out.append(DeviceStatus(key, NAME, last[0], False, False, "steam",
                                        kind="gamepad"))
            else:
                self._diag.append("    nothing in the window (no controller in this slot)")
        current = {f"steam:{pid:04x}:{iface}" for pid, iface, path, how in slots}
        for key in list(self._last):
            if key not in current:       # the puck left: nothing is kept for it
                self._last.pop(key, None)
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
