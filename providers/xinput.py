"""Xbox-compatible controllers (GameSir G7 Pro, Xbox Wireless Controller and
other controllers that speak the Xbox protocol).

Two sources, best first:
  1. Windows.Gaming.Input battery report (see wgi.py): a real percentage and
     the charging state. Works for controllers that never report through XInput.
  2. XInputGetBatteryInformation: only four levels (empty / low / medium / full),
     so the ring shows an approximate level and the tooltip says so.

    XINPUT_BATTERY_INFORMATION { BYTE BatteryType; BYTE BatteryLevel; }
    BatteryType:  0x00 disconnected, 0x01 wired, 0x02 alkaline, 0x03 NiMH, 0xFF unknown
    BatteryLevel: 0 empty, 1 low, 2 medium, 3 full

XInput also tells which controllers are connected at all, cheaply enough to
check every couple of seconds.
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Dict, List, Optional

from . import hidlist, wgi
from .base import DeviceStatus, Provider, log

ERROR_SUCCESS = 0
BATTERY_DEVTYPE_GAMEPAD = 0

TYPE_DISCONNECTED, TYPE_WIRED, TYPE_ALKALINE, TYPE_NIMH, TYPE_UNKNOWN = 0x00, 0x01, 0x02, 0x03, 0xFF
TYPE_NAMES = {0x00: "disconnected", 0x01: "wired", 0x02: "alkaline", 0x03: "NiMH", 0xFF: "unknown"}

# how long to keep re-checking every few seconds for a controller that is
# connected but has not reported its battery yet
PENDING_WINDOW = 120

# how often Windows.Gaming.Input is queried (PowerShell, ~1-2 s per call)
WGI_REFRESH = 8

# coarse level -> (ring percentage, tooltip text). "low" maps to 20% so it
# turns red and triggers the alert at the default threshold.
LEVELS = {
    0: (5, "empty"),
    1: (20, "low"),
    2: (55, "medium"),
    3: (100, "full"),
}

# (vendor id, required word in the product string or "") -> display name.
# GameSir first: its own receiver is the most specific hint. For Microsoft the
# product string must mention a controller, so a Microsoft mouse or keyboard
# does not rename the gamepad.
NAMED = [
    (0x3537, "", "GameSir controller"),
    (0x045E, "controller", "Xbox controller"),
]


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte),
                ("bRightTrigger", ctypes.c_ubyte), ("sThumbLX", ctypes.c_short),
                ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", XINPUT_GAMEPAD)]


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


def load_xinput():
    """xinput1_4 (Windows 8+) has XInputGetBatteryInformation; 9_1_0 does not."""
    if sys.platform != "win32":
        return None
    for name in ("xinput1_4", "xinput1_3"):
        try:
            dll = ctypes.WinDLL(name)
            dll.XInputGetBatteryInformation  # noqa: B018  (raises if missing)
            return dll
        except (OSError, AttributeError):
            continue
    return None


def controller_name(hid_devices: List[dict]) -> str:
    for vid, word, name in NAMED:
        for d in hid_devices:
            if d.get("vendor_id") == vid and word in (d.get("product_string") or "").lower():
                return name
    return "Gamepad"


def interpret(btype: int, blevel: int, last: Optional[int]):
    """-> (level, charging, approx text) or None if the controller has no battery info."""
    if btype == TYPE_DISCONNECTED:
        return None
    if btype == TYPE_WIRED:
        # on the cable: charging; keep the last wireless reading if there is one
        lvl = last if last is not None else LEVELS.get(blevel, (100, ""))[0]
        return lvl, True, "on cable, charging"
    if btype == TYPE_UNKNOWN or blevel not in LEVELS:
        return None
    pct, text = LEVELS[blevel]
    return pct, False, f"about {pct}% ({text})"


class XInputProvider(Provider):
    name = "xinput"

    def __init__(self):
        self._dll = None
        self._loaded = False
        self._diag: List[str] = []
        self._last: Dict[int, int] = {}        # slot -> last wireless level
        self.pending = False                   # a controller is connected but has no battery info yet
        self._waiting: Dict[int, float] = {}   # slot -> when it connected without battery info
        self._wgi_res = None                   # last Windows.Gaming.Input result
        self._wgi_at = 0.0
        self._wgi_slots: Optional[frozenset] = None
        self._wgi_diag: List[str] = []
        self._wgi_logged = ""

    def _ensure_dll(self):
        if not self._loaded:
            self._dll, self._loaded = load_xinput(), True
        return self._dll

    def connected_slots(self) -> Optional[frozenset]:
        """Which XInput slots have a controller right now. Microseconds per call,
        so the app checks it every couple of seconds to react to a controller
        being switched on or off without waiting for the next full poll."""
        dll = self._ensure_dll()
        if dll is None:
            return None
        state = XINPUT_STATE()
        return frozenset(i for i in range(4) if dll.XInputGetState(i, ctypes.byref(state)) == ERROR_SUCCESS)

    def _wgi(self, now: float, slots: frozenset):
        """Windows.Gaming.Input battery reports, cached for a few seconds."""
        if slots != self._wgi_slots or now - self._wgi_at >= WGI_REFRESH:
            diag: List[str] = []
            self._wgi_res = wgi.query(diag)
            self._wgi_at, self._wgi_slots = now, slots
            self._wgi_diag = diag
            summary = "\n".join(diag)
            if summary != self._wgi_logged:             # log only when something changes
                for line in diag:
                    log.info("%s", line)
                self._wgi_logged = summary
        self._diag += self._wgi_diag
        return self._wgi_res or []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        self.pending = False
        if self._ensure_dll() is None:
            if sys.platform == "win32":
                self._diag.append("[XInput] xinput1_4.dll not available")
            return []

        # only the vendors that can name the controller, from the cached list:
        # no full hid.enumerate() (which opens every HID device) on each poll
        hid_devices: List[dict] = []
        for vid, _word, _name in NAMED:
            try:
                hid_devices += hidlist.enumerate(vid)
            except Exception:
                pass
        base_name = controller_name(hid_devices)

        now = time.time()
        slots = []                                   # (slot, XInput reading or None)
        for slot in range(4):
            state = XINPUT_STATE()
            if self._dll.XInputGetState(slot, ctypes.byref(state)) != ERROR_SUCCESS:
                self._waiting.pop(slot, None)
                continue
            info = XINPUT_BATTERY_INFORMATION()
            rc = self._dll.XInputGetBatteryInformation(slot, BATTERY_DEVTYPE_GAMEPAD, ctypes.byref(info))
            btype, blevel = info.BatteryType, info.BatteryLevel
            self._diag.append(f"[XInput] slot {slot}: rc={rc} type={TYPE_NAMES.get(btype, hex(btype))} "
                              f"level={blevel}")
            res = interpret(btype, blevel, self._last.get(slot)) if rc == ERROR_SUCCESS else None
            slots.append((slot, res))
        if not slots:
            self._diag.append("[XInput] no controllers connected")
            return []

        # Windows.Gaming.Input gives a real percentage and works for controllers
        # that never report through XInput; match its controllers to the XInput
        # slots in order (with one controller, which is the usual case, it is exact)
        reports = [c for c in self._wgi(now, frozenset(s for s, _ in slots)) if c.level is not None]

        connected = []
        for n, (slot, res) in enumerate(slots):
            rep = reports[n] if n < len(reports) else None
            name = (rep.name if rep and rep.name else None) or base_name
            if rep is not None:
                self._waiting.pop(slot, None)
                if not rep.charging:
                    self._last[slot] = rep.level
                connected.append((slot, name, rep.level, rep.charging, ""))
                continue
            if res is not None:
                if slot in self._waiting:
                    log.info("[XInput] slot %d battery reported after %.0f s", slot, now - self._waiting.pop(slot))
                level, charging, approx = res
                if not charging:
                    self._last[slot] = level
                connected.append((slot, name, level, charging, approx))
                continue
            # connected, but neither API reports the battery (yet): show the icon
            # without an arc and re-check often for a while
            since = self._waiting.setdefault(slot, now)
            if since == now:
                log.info("[XInput] slot %d connected, battery not reported yet", slot)
            if now - since < PENDING_WINDOW:
                self.pending = True
            connected.append((slot, name, None, False, "connected, battery level not reported yet"))

        out: List[DeviceStatus] = []
        for n, (slot, name, level, charging, approx) in enumerate(connected):
            if len(connected) > 1:
                name = f"{name} {n + 1}"
            out.append(DeviceStatus(f"xinput:{slot}", name, level, charging, True, "xinput", approx))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
