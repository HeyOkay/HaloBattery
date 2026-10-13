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

Over USB the feature report is harmless. Over Bluetooth it is NOT: the switch to
the full report stays on until the controller is turned off, and in that mode games
and launchers that read the controller through DirectInput stop seeing its input
(issue #96; SDL documents the same for its own "enhanced" mode, and #101 shows it on
the 8BitDo Pro 2 too). So over Bluetooth the app only listens by default: when Steam
or a game has already switched the controller to the full report, the battery is
read from it; otherwise the icon shows the controller without a level. The switch is
sent over Bluetooth only when the user turns on "PlayStation over Bluetooth: full
mode" in Preferences (`switch_bluetooth`).
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

# How long a controller that is connected but reports no battery keeps the app on its fast
# (3 s) re-check path. Another app holding the controller never lets go, and that path costs
# a full poll of every provider each time it runs.
PENDING_WINDOW = 120.0

# pid -> (display name, is_dualsense)
# The DS4 USB wireless adapter: it streams report 01 whether or not a controller is
# paired with it, and fills the battery field with zeros in that case.
ADAPTER_PID = 0x0BA0

KNOWN = {
    0x05C4: ("Sony DualShock 4", False),      # 2013 model
    0x09CC: ("Sony DualShock 4", False),      # 2016 model
    0x05C5: ("Sony DualShock 4", False),
    ADAPTER_PID: ("Sony DualShock 4", False),  # USB wireless adapter
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

# "Wake up the full report" feature report id per family. Reading it switches a
# Bluetooth controller into full-report mode, which stays on until the controller is
# turned off and hides it from DirectInput games (#96). Harmless over USB.
TRIGGER_FEATURE = {False: 0x02, True: 0x05}   # is_dualsense -> feature id

# Bluetooth HID paths carry this service GUID and the "VID&" spelling; USB paths
# use "VID_" instead.
_BT_HID_GUID = "{00001124-0000-1000-8000-00805f9b34fb}"

# How long to wait for the battery report after the feature-report trigger, and how
# long one controller may cost in total. A DualSense or DualShock 4 exposes several
# collections (audio, touch, sensors, gamepad) and the battery rides on the gamepad
# one; an interface that never answers used to cost the full window each, so three or
# four of them held the poll loop for up to 6 s and delayed every other device's
# update with it.
WINDOW = 1.5
BUDGET = 2.5


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


def _battery_first(d) -> int:
    """Sort key for the candidate interfaces: the gamepad collection carries the
    battery, so it is tried first and the rest only if it says nothing."""
    return 0 if (d.get("usage_page") == 0x01 and d.get("usage") in (0x04, 0x05)) else 1


def _instance(path) -> str:
    r"""The device instance from a Windows HID path, without the collection number:
    `\\?\hid#vid_054c&pid_0ce6&mi_03#8&1234abcd&0&0000#{...}` -> `8&1234abcd&0`.

    Over USB hidapi reports no serial number for these controllers, so two of the same
    model had one group and one icon. The instance tells them apart, and it does not
    change while the controller stays on the same port. The last `&` part is the
    collection number and is dropped, so the collections of one controller stay
    together (the same rule as `_instance` in providers/logitech.py)."""
    s = path.decode("ascii", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
    parts = s.split("#")
    return parts[2].lower().rsplit("&", 1)[0] if len(parts) > 2 else ""


class PlayStationProvider(Provider):
    name = "playstation"

    def __init__(self):
        self._diag: List[str] = []
        self.pending = False        # a controller is connected but has not reported battery yet
        self._pending_since: Dict[str, float] = {}   # key -> when its reading first went missing
        # False (default): a Bluetooth controller is never switched to its full report, see
        # the module docstring. Set by the app from Preferences before each poll.
        self.switch_bluetooth = False
        self._basic: bool = False   # the last _read saw a Bluetooth controller in its basic mode

    # ---- low level -------------------------------------------------------
    @staticmethod
    def _is_bluetooth(path) -> bool:
        s = path.decode("ascii", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        s = s.lower()
        return "vid&" in s or _BT_HID_GUID in s

    def _read(self, path, is_dualsense: bool, window: float = WINDOW,
              adapter: bool = False) -> Optional[Tuple[int, bool]]:
        """-> (level, charging) or None if no battery report arrived.

        `adapter` is the DS4 USB wireless adapter, which also reports when no controller
        is paired with it."""
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
            # This feature report switches a Bluetooth controller from its minimal
            # report (no battery) to the full one, and that breaks DirectInput games
            # until the controller is turned off (#96). Over Bluetooth it is sent only
            # when the user allowed it; over USB it is harmless.
            listen_only = bluetooth and not self.switch_bluetooth
            if not listen_only:
                trigger = TRIGGER_FEATURE[is_dualsense]
                try:
                    dev.get_feature_report(trigger, 64)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    feature {trigger:#04x}: {e}")
            deadline = time.time() + window
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
                    if listen_only and data[0] == 0x01:
                        # the controller streams its basic report: nobody has switched it
                        # to the full one, and we must not (see above), so no level now
                        self._basic = True
                        self._diag.append("    basic Bluetooth mode (report 0x01): not "
                                          "switched to the full report, see #96")
                        return None
                    continue
                if adapter and len(data) > 31 and data[31] & 0x04:
                    # No controller is paired with the adapter: its report carries zeros
                    # in the battery field, which showed up as a 0% icon and, at that
                    # level, a low-battery alert for a controller that is not there. Bit 2
                    # of status[1] is DS4_STATUS1_DONGLE_STATE in the Linux driver
                    # (hid-playstation.c), where it means "not connected".
                    self._diag.append(f"    the adapter reports no controller attached "
                                      f"(status[1]={data[31]:#04x}), so this is not a reading")
                    return None
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
        now = time.time()
        try:
            infos = hidlist.enumerate(SONY_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(playstation): %s", e)
            return []

        # group interfaces by device (PID + serial); over Bluetooth the serial is
        # the controller's MAC, over USB it is the same string for one controller.
        # Over USB with no serial, the device instance from the path stands in for it.
        groups: Dict[Tuple[int, str], List[dict]] = {}
        for d in infos:
            pid = d["product_id"]
            if pid not in KNOWN:
                continue
            serial = d.get("serial_number") or ""
            if not serial and not self._is_bluetooth(d["path"]):
                serial = "usb-" + _instance(d["path"])
            groups.setdefault((pid, serial), []).append(d)

        conns: List[dict] = []          # one entry per connection (transport)
        for (pid, serial), ifaces in groups.items():
            name, is_dualsense = KNOWN[pid]
            product = (ifaces[0].get("product_string") or "").strip()
            bluetooth = self._is_bluetooth(ifaces[0]["path"])
            self._diag.append(f"[PlayStation] {name} pid={pid:04x} "
                              f"{'Bluetooth' if bluetooth else 'USB'} interfaces={len(ifaces)} '{product}'")
            res = None
            basic = False
            ordered = sorted(ifaces, key=_battery_first)
            budget_end = time.time() + BUDGET
            for d in ordered:
                self._diag.append(
                    f"  iface={d.get('interface_number')} usage="
                    f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                left = budget_end - time.time()
                if left <= 0:
                    self._diag.append("  out of time for this controller: "
                                      "the remaining interfaces are skipped")
                    break
                self._basic = False
                res = self._read(d["path"], is_dualsense, min(WINDOW, left),
                                 adapter=pid == ADAPTER_PID)
                if res is not None:
                    break
                if self._basic:
                    basic = True
                    break
            if res is not None:
                self._diag.append(f"  -> {res[0]}%{' charging' if res[1] else ''}")
            conns.append({"pid": pid, "name": name, "bluetooth": bluetooth,
                          "mac": serial if bluetooth and serial else "", "reading": res,
                          "basic": basic, "usb": "" if bluetooth else serial})

        devs = self._merge(conns)
        # A USB controller has no MAC here, so its key is "ps:<pid>:". Two of the same
        # model on USB shared that key and showed as one icon: only then does each one
        # add its USB id. A single controller keeps the plain key, so a name or "hidden"
        # saved for it still applies.
        usb_count: Dict[int, int] = {}
        for dev in devs:
            if not dev["mac"]:
                usb_count[dev["pid"]] = usb_count.get(dev["pid"], 0) + 1
        out: List[DeviceStatus] = []
        for dev in devs:
            ident = dev["mac"] or (dev["usb"] if usb_count[dev["pid"]] > 1 else "")
            key = f"ps:{dev['pid']:04x}:{ident}"
            res = dev["reading"]
            if res is None and dev.get("basic"):
                # Bluetooth, basic mode, not switched on purpose: this is the normal state,
                # not a controller that is still starting, so no fast re-check either
                self._pending_since.pop(key, None)
                out.append(DeviceStatus(key, dev["name"], None, False, True, "playstation",
                                        "level shown over Bluetooth only while Steam or a game "
                                        "uses it", ui_message="state.playstation_basic"))
                continue
            if res is None:
                # Present but no battery read: just connected, or another app (DS4Windows,
                # HidHide) is holding the controller so `open` fails on every poll. Show the
                # icon without an arc and re-check soon - but only for a while. `pending`
                # makes the app poll every provider every 3 s, and a controller that can
                # never be opened kept that up for as long as it stayed plugged in.
                since = self._pending_since.get(key)
                if since is None:
                    since = self._pending_since[key] = now
                    log.info("[PlayStation] %s: connected, battery not reported yet", dev["name"])
                if now - since < PENDING_WINDOW:
                    self.pending = True
                    approx = "connected, battery level not reported yet"
                    ui_message = "state.pending"
                else:
                    del self._pending_since[key]
                    self._diag.append(f"  {dev['name']}: no battery report for "
                                      f"{PENDING_WINDOW:.0f} s, another app may be holding the "
                                      f"controller - back to the normal poll interval")
                    log.info("[PlayStation] %s: no battery report for %.0f s, not re-checking "
                             "every 3 s any more", dev["name"], PENDING_WINDOW)
                    approx = "connected, battery not readable (another app may hold it)"
                    ui_message = "state.unreadable"
                out.append(DeviceStatus(key, dev["name"], None, False, True, "playstation", approx,
                                        ui_message=ui_message))
                continue
            level, charging = res
            self._pending_since.pop(key, None)
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
                # more controllers of this model on USB only: each keeps its own icon
                out.extend(wired[1:])
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
