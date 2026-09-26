"""PlayStation controllers: DualShock 4 (PS4) and DualSense / DualSense Edge (PS5).

These connect straight to the PC over USB or Bluetooth, not through XInput, so
Windows' XInput / Windows.Gaming.Input battery APIs never report them. The
battery level is read directly from the controller's HID input report, the same
source the Linux hid-sony / hid-playstation drivers and DS4Windows use.

  * USB: the controller streams its full input report immediately.
      DualShock 4  report id 0x01: battery in byte 30
                   (low nibble = level 0..10, bit 4 = cable connected / charging)
      DualSense    report id 0x01: status  in byte 53
                   (low nibble = level 0..10, high nibble = charging state)
  * Bluetooth: the controller only sends a minimal report (id 0x01, ~10 bytes,
    no battery) until the host reads a feature report (DS4 0x02, DualSense 0x05);
    after that it streams the full report, shifted by a couple of header bytes:
      DualShock 4  report id 0x11: battery in byte 32
      DualSense    report id 0x31: status  in byte 54

Reading the feature report to switch a Bluetooth controller into full mode is
harmless over USB, so the same code path covers both connections.
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

SONY_VID = 0x054C

# pid -> (display name, is_dualsense)
KNOWN = {
    0x05C4: ("Sony DualShock 4", False),      # 2013 model
    0x09CC: ("Sony DualShock 4", False),      # 2016 model
    0x05C5: ("Sony DualShock 4", False),
    0x0BA0: ("Sony DualShock 4", False),      # USB wireless adapter
    0x0CE6: ("Sony DualSense", True),
    0x0DF2: ("Sony DualSense Edge", True),
}

# bluetooth -> (input report id, battery/status byte offset), per controller family.
# Over USB the controller streams report 0x01 with the battery inside; over
# Bluetooth report 0x01 is a stripped-down report with NO battery (sticks and
# buttons only), and the battery arrives in the larger report 0x11 / 0x31 once
# the full mode has been switched on (see TRIGGER_FEATURE).
DS4_REPORT = {False: (0x01, 30), True: (0x11, 32)}
DUALSENSE_REPORT = {False: (0x01, 53), True: (0x31, 54)}

# Bluetooth "wake up the full report" feature report id per family. Reading it
# switches the controller into full-report mode; it is harmless over USB.
TRIGGER_FEATURE = {False: 0x02, True: 0x05}   # is_dualsense -> feature id

# Bluetooth HID paths carry this service GUID and the "VID&" spelling; USB paths
# use "VID_" instead.
_BT_HID_GUID = "{00001124-0000-1000-8000-00805f9b34fb}"


def parse_ds4(byte: int) -> Tuple[int, bool]:
    """DualShock 4 battery byte -> (level %, charging)."""
    raw = byte & 0x0F                # 0..10 on battery, 11 = full while charging
    charging = bool(byte & 0x10)     # bit 4: USB cable connected
    level = min(raw, 10) * 10
    return level, charging


def parse_dualsense(byte: int) -> Optional[Tuple[int, bool]]:
    """DualSense status byte -> (level %, charging).

    The low nibble is the real battery level (0..10) and is used as-is, even
    while charging: the DualSense keeps reporting the true level on the cable, so
    it must not be forced to 100 %. The high nibble is the charging state
    (0 = on battery, 1 = charging, 2 = charge complete but still plugged in,
    0xa/0xb/0xf = temperature / charging error)."""
    level = min(byte & 0x0F, 10) * 10
    charge = (byte >> 4) & 0x0F
    if charge in (0x1, 0x2):        # on the cable
        return level, True
    return level, False             # on battery, or an error state: show the level, no arc pulse


class PlayStationProvider(Provider):
    name = "playstation"

    def __init__(self):
        self._diag: List[str] = []
        self.pending = False        # a controller is connected but has not reported battery yet

    # ---- low level -------------------------------------------------------
    @staticmethod
    def _is_bluetooth(path) -> bool:
        s = path.decode("ascii", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        s = s.lower()
        return "vid&" in s or _BT_HID_GUID in s

    def _read(self, path, is_dualsense: bool) -> Optional[Tuple[int, bool]]:
        """-> (level, charging) or None if no battery report arrived."""
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
            bluetooth = self._is_bluetooth(path)
            rid, off = (DUALSENSE_REPORT if is_dualsense else DS4_REPORT)[bluetooth]
            self._diag.append(f"    {'Bluetooth' if bluetooth else 'USB'}: "
                              f"waiting for report {rid:#04x}, battery byte {off}")
            # Bluetooth: reading this feature report switches the controller from
            # its minimal report (no battery) to the full one. Harmless over USB.
            trigger = TRIGGER_FEATURE[is_dualsense]
            try:
                dev.get_feature_report(trigger, 64)
            except (OSError, ValueError) as e:
                self._diag.append(f"    feature {trigger:#04x}: {e}")
            deadline = time.time() + 1.5
            while time.time() < deadline:
                try:
                    data = dev.read(78)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    break
                if not data:
                    time.sleep(0.005)
                    continue
                # skip the stripped-down Bluetooth report 0x01 (no battery) and any
                # other report; only the expected full report carries the battery
                if data[0] != rid or len(data) <= off:
                    continue
                self._diag.append(f"    report {data[0]:#04x} len={len(data)}: {hexdump(data, 64)}")
                byte = data[off]
                return parse_dualsense(byte) if is_dualsense else parse_ds4(byte)
            self._diag.append(f"    no {rid:#04x} report received")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    # ---- high level ------------------------------------------------------
    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        self.pending = False
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(SONY_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(playstation): %s", e)
            return []

        # group interfaces by device (PID + serial); over Bluetooth the serial is
        # the controller's MAC, over USB it is the same string for one controller
        groups: Dict[Tuple[int, str], List[dict]] = {}
        for d in infos:
            pid = d["product_id"]
            if pid not in KNOWN:
                continue
            serial = d.get("serial_number") or ""
            groups.setdefault((pid, serial), []).append(d)

        conns: List[dict] = []          # one entry per connection (transport)
        for (pid, serial), ifaces in groups.items():
            name, is_dualsense = KNOWN[pid]
            product = (ifaces[0].get("product_string") or "").strip()
            bluetooth = self._is_bluetooth(ifaces[0]["path"])
            self._diag.append(f"[PlayStation] {name} pid={pid:04x} "
                              f"{'Bluetooth' if bluetooth else 'USB'} interfaces={len(ifaces)} '{product}'")
            res = None
            for d in ifaces:
                self._diag.append(
                    f"  iface={d.get('interface_number')} usage="
                    f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                res = self._read(d["path"], is_dualsense)
                if res is not None:
                    break
            if res is not None:
                self._diag.append(f"  -> {res[0]}%{' charging' if res[1] else ''}")
            conns.append({"pid": pid, "name": name, "bluetooth": bluetooth,
                          "mac": serial if bluetooth and serial else "", "reading": res})

        out: List[DeviceStatus] = []
        for dev in self._merge(conns):
            key = f"ps:{dev['pid']:04x}:{dev['mac']}"
            res = dev["reading"]
            if res is None:
                # present but battery not read (just connected, or another app holds it):
                # show the icon without an arc and re-check soon
                self.pending = True
                log.info("[PlayStation] %s: connected, battery not reported yet", dev["name"])
                out.append(DeviceStatus(key, dev["name"], None, False, True, "playstation",
                                        "connected, battery level not reported yet"))
                continue
            level, charging = res
            out.append(DeviceStatus(key, dev["name"], level, charging, True, "playstation"))
        return out

    def _merge(self, conns: List[dict]) -> List[dict]:
        """One physical controller can be connected over Bluetooth AND the cable at
        the same time, showing up as two connections of the same PID. Collapse them
        into a single device (keyed by the Bluetooth MAC, so the icon does not
        change when the cable is plugged in), preferring the reading that reports
        charging (the cable)."""
        by_pid: Dict[int, List[dict]] = {}
        for c in conns:
            by_pid.setdefault(c["pid"], []).append(c)
        out: List[dict] = []
        for pid, group in by_pid.items():
            wireless = [c for c in group if c["bluetooth"]]
            wired = [c for c in group if not c["bluetooth"]]
            if len(wireless) == 1 and wired:
                dev = dict(wireless[0])
                dev["reading"] = self._prefer([wired[0]["reading"], wireless[0]["reading"]])
                self._diag.append(f"[PlayStation] pid={pid:04x}: same controller on cable and "
                                  f"Bluetooth, showing one icon")
                out.append(dev)
            else:
                out.extend(group)
        return out

    @staticmethod
    def _prefer(readings: List[Optional[Tuple[int, bool]]]) -> Optional[Tuple[int, bool]]:
        """Pick the cable reading (the one that reports charging), else any reading."""
        for r in readings:
            if r is not None and r[1]:
                return r
        for r in readings:
            if r is not None:
                return r
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
