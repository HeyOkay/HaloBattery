"""8BitDo controllers in their own (D-input) mode: Pro 2, Pro 3, SN30 Pro, SF30 Pro.

In XInput mode these controllers show up as an Xbox controller and the XInput
provider already reads them. In D-input mode (vendor 2DC8) they are plain HID
gamepads, and the battery level is only in the controller's "enhanced" input
report, the one SDL (and so Steam) switches on:

  * SDL reads feature report 0x06 once; after that the controller streams its
    enhanced report: id 0x01 over Bluetooth, id 0x04 over USB.
  * byte 14 of that report: bits 0-6 = level in %, bit 7 = charging.

The Pro 3 (2DC8:6009) differs: its report descriptor declares a single input report,
id 0x04, on both transports, and that report always carries the level in byte 14.
So it needs no switch at all; read on hardware over Bluetooth, with Steam closed and
the controller freshly turned on: 04 0f 80 7f 7f 80 00 00 00 00 00 00 09 60 14 ...
(20 %). Waiting for report 0x01 over Bluetooth discarded every one of its reports.

Switching is NOT harmless (issue #101): the reporter tested it on a Pro 2 over
Bluetooth and after Steam had switched the controller, DirectInput games saw no
input until the controller was turned off. The same thing happens with PlayStation
controllers (#96). So this provider never sends the feature report. It only
listens: when Steam or a game has already switched the controller, the level is
read from the enhanced report; otherwise the icon shows the controller without a
level.

Telling the two reports apart: over Bluetooth the controller's ordinary report
may carry the same id 0x01, and on Windows hidapi pads every report with zeros to
the longest input report of the collection. So a report only counts when byte 14
holds a real level (1..100 %). A zero there is the padding of the ordinary
report, not an empty battery: the controller is running, and taking it as 0 %
would raise a false low-battery alert. The first bytes of what arrived go to the
diagnostics, so a report from a user can confirm the layout.
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

VENDOR_8BITDO = 0x2DC8

# pid -> name. The ids SDL's 8BitDo driver handles, minus the Ultimate 2 Wireless /
# Ultimate 3, whose reports differ. Only the Pro 2 over Bluetooth (6006) has a user
# report so far (#101).
KNOWN = {
    0x6000: "8BitDo SF30 Pro",
    0x6100: "8BitDo SF30 Pro",
    0x6001: "8BitDo SN30 Pro",
    0x6101: "8BitDo SN30 Pro",
    0x6003: "8BitDo Pro 2",
    0x6006: "8BitDo Pro 2",
    0x6009: "8BitDo Pro 3",
}

# The enhanced report id: Bluetooth -> 0x01, USB -> 0x04 (SDL_hidapi_8bitdo.c).
ENHANCED_REPORT = {True: 0x01, False: 0x04}
# pid -> its report id on every transport (see the module docstring)
ENHANCED_REPORT_BY_PID = {0x6009: 0x04}


def enhanced_report(pid: int, bluetooth: bool) -> int:
    return ENHANCED_REPORT_BY_PID.get(pid, ENHANCED_REPORT[bluetooth])
BATTERY_BYTE = 14

# Bluetooth HID paths carry this service GUID and the "VID&" spelling; USB paths "VID_".
_BT_HID_GUID = "{00001124-0000-1000-8000-00805f9b34fb}"

# The enhanced report streams at about 85 Hz (it carries the motion sensors), so a
# switched controller answers well within this window. The ordinary report is sent
# only when a button or stick moves, so an idle controller in the ordinary mode sends
# nothing: the window ending empty is the normal "not switched" case.
WINDOW = 0.4
BUDGET = 1.0
# A switched controller's very first enhanced report carries the level, so a run of
# reports without one means the ordinary mode (a stick being moved, say): stop early
# rather than spin through the whole window.
MAX_REPORTS = 16

NOT_SWITCHED = "level shown only while Steam or a game uses the controller"


def parse_battery(byte: int) -> Optional[Tuple[int, bool]]:
    """Enhanced report byte 14 -> (level %, charging), or None when it holds no level."""
    level = byte & 0x7F
    charging = bool(byte & 0x80)
    if level == 0 or level > 100:
        return None
    return level, charging


def _gamepad_first(d) -> int:
    return 0 if (d.get("usage_page") == 0x01 and d.get("usage") in (0x04, 0x05)) else 1


class EightBitDoProvider(Provider):
    name = "8bitdo"

    def __init__(self):
        self._diag: List[str] = []

    @staticmethod
    def _is_bluetooth(path) -> bool:
        s = path.decode("ascii", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        s = s.lower()
        return "vid&" in s or _BT_HID_GUID in s

    def _read(self, path, rid: int, window: float) -> Optional[Tuple[int, bool]]:
        """Listen only. -> (level, charging) from an enhanced report, or None."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.set_nonblocking(True)
            except Exception:
                pass
            seen: Dict[int, List[int]] = {}       # report id -> the first report of that id
            count = 0
            deadline = time.time() + window
            while time.time() < deadline and count < MAX_REPORTS:
                try:
                    data = dev.read(64)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    break
                if not data:
                    time.sleep(0.005)
                    continue
                seen.setdefault(data[0], list(data))
                count += 1
                if data[0] != rid or len(data) <= BATTERY_BYTE:
                    continue
                res = parse_battery(data[BATTERY_BYTE])
                if res is not None:
                    self._diag.append(f"    report {data[0]:#04x} len={len(data)}: {hexdump(data, 32)}")
                    return res
            if not seen:
                self._diag.append(f"    nothing received in {window:.1f} s: ordinary mode "
                                  f"(not switched by Steam or a game), see #101")
            for r, data in seen.items():
                self._diag.append(f"    report {r:#04x} len={len(data)}, no level in byte "
                                  f"{BATTERY_BYTE}: {hexdump(data, 32)}")
            return None
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
            infos = hidlist.enumerate(VENDOR_8BITDO)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(8bitdo): %s", e)
            return []

        groups: Dict[Tuple[int, str, bool], List[dict]] = {}
        for d in infos:
            pid = d["product_id"]
            if pid not in KNOWN:
                continue
            bluetooth = self._is_bluetooth(d["path"])
            groups.setdefault((pid, d.get("serial_number") or "", bluetooth), []).append(d)

        out: List[DeviceStatus] = []
        for (pid, serial, bluetooth), ifaces in groups.items():
            name = KNOWN[pid]
            self._diag.append(f"[8BitDo] {name} pid={pid:04x} "
                              f"{'Bluetooth' if bluetooth else 'USB'} interfaces={len(ifaces)} "
                              f"(listen only, the controller is never switched)")
            res = None
            budget_end = time.time() + BUDGET
            for d in sorted(ifaces, key=_gamepad_first):
                self._diag.append(f"  iface={d.get('interface_number')} usage="
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                left = budget_end - time.time()
                if left <= 0:
                    self._diag.append("  out of time for this controller")
                    break
                res = self._read(d["path"], enhanced_report(pid, bluetooth), min(WINDOW, left))
                if res is not None:
                    break
            key = f"8bitdo:{pid:04x}:{serial}"
            via = "bluetooth" if bluetooth else ""
            if res is None:
                out.append(DeviceStatus(key, name, None, False, True, "8bitdo", NOT_SWITCHED,
                                        kind="gamepad", via=via))
                continue
            level, charging = res
            self._diag.append(f"  -> {level}%{' charging' if charging else ''}")
            out.append(DeviceStatus(key, name, level, charging, True, "8bitdo",
                                    kind="gamepad", via=via))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
