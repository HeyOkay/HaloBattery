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
import re
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


# the vendors whose controllers this provider reads; Windows.Gaming.Input also reports
# controllers of other vendors (a DualSense, a wheel) and they are not XInput pads
NAMED_VIDS = frozenset(vid for vid, _word, _name in NAMED)


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


# HID interfaces of Bluetooth devices: classic HID {00001124-...} or HID over
# GATT {00001812-...}, with the vendor and product id in the path
_BT_HID = re.compile(r"\{0000(?:1124|1812)-0000-1000-8000-00805f9b34fb\}[^#]*?vid&([0-9a-f]+)_pid&([0-9a-f]{4})")


_HID_VID = re.compile(r"vid[_&]([0-9a-f]{4})")

# Product ids that Microsoft's controllers use only over Bluetooth (classic or LE).
# Source: SDL, src/joystick/usb_ids.h (USB_PRODUCT_XBOX_*_BLUETOOTH / *_BLE). Over USB
# and the Xbox Wireless Adapter the same controllers use other ids (02EA, 0B12, 0B00 ...),
# so one of these ids alone says "Bluetooth", with or without the service guid in a path.
XBOX_BLUETOOTH_PIDS = frozenset((
    0x02E0,     # Xbox One S, first firmware, Bluetooth
    0x02FD,     # Xbox One S, Bluetooth
    0x0B05,     # Elite Series 2, Bluetooth
    0x0B0C,     # Adaptive Controller, Bluetooth
    0x0B13,     # Xbox Series X|S, Bluetooth LE
    0x0B20,     # Xbox One S, Bluetooth LE
    0x0B21,     # Adaptive Controller, Bluetooth LE
    0x0B22,     # Elite Series 2, Bluetooth LE
))
MICROSOFT_VID = 0x045E


def is_xbox_bluetooth(vid: int, pid: int) -> bool:
    return vid == MICROSOFT_VID and pid in XBOX_BLUETOOTH_PIDS


def bluetooth_only_vids(paths) -> set:
    """Vendor ids whose HID interfaces are *all* on Bluetooth paths.

    Windows.Gaming.Input is the only API that says how a controller is connected, and
    it can fail, time out or return no report at all; the device paths are always
    there. A vendor that also has an interface on a non-Bluetooth path (a dongle, a
    cable) is not Bluetooth-only, so a 2.4 GHz receiver cannot be mistaken for one.
    """
    bt, other = set(), set()
    for p in paths or ():
        s = p.decode("ascii", "ignore") if isinstance(p, (bytes, bytearray)) else str(p)
        s = s.lower()
        m = _BT_HID.search(s)
        if m:
            bt.add(int(m.group(1)[-4:], 16))
            continue
        m = _HID_VID.search(s)
        if m:
            other.add(int(m.group(1)[-4:], 16))
    return bt - other


def bluetooth_ids(paths) -> set:
    """(vid, pid) of HID devices connected over Bluetooth, from the device paths."""
    out = set()
    for p in paths or ():
        m = _BT_HID.search(p.lower())
        if m:
            out.add((int(m.group(1)[-4:], 16), int(m.group(2), 16)))
    return out


def controller_name(hid_devices: List[dict]):
    """-> (display name, vendor id). The vendor id lets the poll tell whether the
    controller is on Bluetooth when Windows.Gaming.Input reports nothing."""
    for vid, word, name in NAMED:
        for d in hid_devices:
            if d.get("vendor_id") == vid and word in (d.get("product_string") or "").lower():
                return name, vid
    return "Gamepad", None


def interpret(btype: int, blevel: int, last: Optional[int]):
    """-> (level, charging, approx text) or None if the controller has no battery info."""
    if btype == TYPE_DISCONNECTED:
        return None
    if btype == TYPE_WIRED:
        # On the cable: charging; keep the last wireless reading if there is one.
        # BatteryLevel is documented as valid only for wireless devices with a known
        # battery type, and a wired pad carries whatever the driver left in that field
        # - reading it turned a pad on the cable into "5% (empty)" whenever the byte
        # happened to be 0. A wired controller has no battery to report, so say full
        # rather than invent a level, and never read that byte here.
        lvl = last if last is not None else 100
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
        base_name, base_vid = controller_name(hid_devices)

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
        # one-to-one with the slot list: dropping the reports without a level first
        # shifted every later controller onto the wrong slot, and with it that
        # controller's level and name
        # RawGameControllers is not the XInput slot list: it also holds controllers XInput
        # cannot see, and the order is not the slot order, so indexing it blindly handed a
        # pad a DualSense's name and level. Only the vendors this provider reads are kept,
        # and the list is paired with the slots only when the two counts agree - a mismatch
        # means the pairing cannot be trusted and the coarse XInput level is used instead.
        all_reports = self._wgi(now, frozenset(s for s, _ in slots))
        # XInput's "wired" type does not always mean a cable: the 2.4 GHz dongle of an
        # 8BitDo Ultimate dock (2dc8:3106) says "wired" for a pad that is off the dock
        # and off the cable, while Windows.Gaming.Input says Discharging for it (#110).
        # Its report is not in the vendor list below, so only its status is kept here,
        # paired by the same count rule; its level (a constant remain=full=1000) is not.
        statuses = [r.status for r in all_reports] if len(all_reports) == len(slots) else []
        reports = [r for r in all_reports if r.vid in NAMED_VIDS]
        if len(reports) != len(slots):
            if reports:
                self._diag.append(f"[XInput] {len(reports)} Windows.Gaming.Input report(s) for "
                                  f"{len(slots)} slot(s): not paired, the XInput levels are used")
            reports = []

        # separately: one of them failing must not blind the other
        try:
            paths = hidlist.interface_paths()
        except Exception:
            paths = []
        try:
            bt_ids = bluetooth_ids(paths)
        except Exception:
            bt_ids = set()
        try:
            bt_only = bluetooth_only_vids(paths)
        except Exception:
            bt_only = set()
        # A game controller of one of these vendors on a Bluetooth path counts as Bluetooth
        # on its own. bluetooth_only_vids() above needs *every* interface of the vendor to be
        # on Bluetooth, so any Microsoft mouse or keyboard on USB switched it off for Xbox
        # pads - and then a pad on Bluetooth kept a second icon.
        for d in hid_devices:
            path = d.get("path") or b""
            s = path.decode("ascii", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
            if not _BT_HID.search(s.lower()):
                continue
            page, usage = d.get("usage_page") or 0, d.get("usage") or 0
            if (page == 0x0001 and usage in (0x04, 0x05)) or page >= 0xFF00:
                bt_only = bt_only | {d.get("vendor_id") or 0}

        connected = []
        vias = {}
        presentation = {}       # slot -> message key and parameters, independent of English text
        for n, (slot, res) in enumerate(slots):
            rep = reports[n] if n < len(reports) and reports[n].level is not None else None
            if rep is None and base_vid is not None and base_vid in bt_only:
                # Windows.Gaming.Input said nothing, but every HID interface this vendor
                # has sits on a Bluetooth path: the controller is on Bluetooth, which
                # dedupe_controllers() needs to know to drop this entry in favour of the
                # Bluetooth device Windows itself reports. The XInput level is kept -
                # it is coarse but real, and better than an icon with no arc.
                vias[slot] = "bluetooth"
                self._diag.append(f"[XInput] slot {slot}: connected over Bluetooth "
                                  "(from the device paths)")
            name = (rep.name if rep and rep.name else None) or base_name
            # the path test alone missed an Xbox One S (045e:02fd) whose path had no
            # Bluetooth service guid: its report (remain=100 of full=1000) then showed as
            # "10%" whenever "Windows Bluetooth devices" was off (#97, #108)
            if rep is not None and ((rep.vid, rep.pid) in bt_ids or is_xbox_bluetooth(rep.vid, rep.pid)):
                vias[slot] = "bluetooth"
                self._diag.append(f"[XInput] slot {slot}: connected over Bluetooth")
                # Over Bluetooth, Windows.Gaming.Input's report is not usable: an
                # Xbox Wireless Controller reported remain=100 full=1000 (10%) at
                # 82%. The real level comes from the Bluetooth device instead.
                connected.append((slot, name, None, False,
                                  "connected over Bluetooth; turn on \"Windows Bluetooth devices\" "
                                  "to see its battery"))
                presentation[slot] = ("state.bluetooth_hint", {})
                continue
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
                if charging and n < len(statuses) and statuses[n] == "discharging":
                    # "wired" from XInput, but Windows says the battery discharges: not
                    # on a cable, and neither API has a real level for it
                    self._diag.append(f"[XInput] slot {slot}: type wired, but Windows.Gaming.Input "
                                      "says discharging: not shown as on cable")
                    connected.append((slot, name, None, False, "connected, battery level not reported"))
                    presentation[slot] = ("state.unreported", {})
                    continue
                if not charging:
                    self._last[slot] = level
                connected.append((slot, name, level, charging, approx))
                if charging:
                    presentation[slot] = ("state.cable", {})
                else:
                    grade = next(word for pct, word in LEVELS.values() if pct == level)
                    presentation[slot] = ("state.approx_grade", {"level": level, "grade": grade})
                continue
            # connected, but neither API reports the battery (yet): show the icon
            # without an arc and re-check often for a while
            since = self._waiting.setdefault(slot, now)
            if since == now:
                log.info("[XInput] slot %d connected, battery not reported yet", slot)
            if now - since < PENDING_WINDOW:
                self.pending = True
            connected.append((slot, name, None, False, "connected, battery level not reported yet"))
            presentation[slot] = ("state.pending", {})

        out: List[DeviceStatus] = []
        for n, (slot, name, level, charging, approx) in enumerate(connected):
            if len(connected) > 1:
                name = f"{name} {n + 1}"
            ui_message, ui_params = presentation.get(slot, ("", {}))
            out.append(DeviceStatus(f"xinput:{slot}", name, level, charging, True, "xinput", approx,
                                    kind="gamepad", via=vias.get(slot, ""),
                                    ui_message=ui_message, ui_params=ui_params))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
