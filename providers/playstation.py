"""PlayStation controllers: DualShock 4 (PS4) and DualSense / DualSense Edge (PS5).

These connect straight to the PC over USB or Bluetooth, not through XInput, so
Windows' XInput / Windows.Gaming.Input battery APIs never report them. The
battery level is read directly from the controller's HID input report, the same
source the Linux hid-sony / hid-playstation drivers and DS4Windows use.

USB identity reads: SDL at c8da179691f909734cd1b124c930e49311428996,
SDL_hidapi_ps4.c:266-277,317-344 (GET_FEATURE 0x12, 64-byte buffer) and
SDL_hidapi_ps5.c:375-433 (GET_FEATURE 0x09, 128-byte buffer). The six bytes
at indices 1..6 are the controller's Bluetooth address in reverse byte order.
Linux hid-playstation.c at the commit below defines the complete 16-/20-byte
payloads and validates the echoed report id (799-818, 1183-1199, 2148-2166).
Only Sony 054c:05c4/09cc/0ce6/0df2 USB gamepad collections (01:05) are asked.
Not Bluetooth: SDL explicitly warns that 0x09 enables enhanced reports there.
The Strikepad and USB wireless adapter are not in this new-read allowlist.

Battery-state reference: Sony's drivers/hid/hid-playstation.c in Linux commit
666539eecdf4db623f5e3c3e55cf9f2dfad24aa4, dualsense_parse_report (1610-1638)
and dualshock4_parse_report (2550-2588). Error/undefined status codes are not
battery percentages. Existing level granularity is retained.
https://github.com/torvalds/linux/blob/666539eecdf4db623f5e3c3e55cf9f2dfad24aa4/drivers/hid/hid-playstation.c

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

import hashlib
import re
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

# report id, caller buffer size used by SDL, complete payload size from Linux.
USB_IDENTITY_REPORTS = {0x05C4: (0x12, 64, 16), 0x09CC: (0x12, 64, 16),
                        0x0CE6: (0x09, 128, 20), 0x0DF2: (0x09, 128, 20)}


def usb_identity_request(info: dict) -> Optional[Tuple[int, int, int]]:
    """The new read requires an explicit Sony USB gamepad, never a guessed collection."""
    pid = info.get("product_id")
    if (info.get("vendor_id") != SONY_VID or pid not in USB_IDENTITY_REPORTS
            or (info.get("usage_page"), info.get("usage")) != (1, 5)):
        return None
    path = info.get("path")
    text = path.decode("ascii", "ignore") if isinstance(path, bytes) else str(path)
    if not re.search(r"(?:^|#)vid_054c&pid_" + f"{pid:04x}" + r"(?:[&#]|$)", text.lower()):
        return None
    # If hidapi exposes a bus type, it must also agree with the USB path.
    if type(info.get("bus_type", 1)) is not int or info.get("bus_type", 1) != 1:
        return None
    return USB_IDENTITY_REPORTS[pid]


def parse_usb_identity(report, request: Tuple[int, int, int]) -> str:
    """Strict complete USB pairing-info payload; no host address or padding is an identity."""
    if not isinstance(report, (bytes, bytearray, list, tuple)):
        return ""
    if isinstance(report, (list, tuple)) and any(type(value) is not int for value in report):
        return ""
    try:
        data = bytes(report)
    except (TypeError, ValueError, OverflowError):
        return ""
    report_id, maximum, minimum = request
    if not minimum <= len(data) <= maximum or data[0] != report_id:
        return ""
    return _mac(data[1:7][::-1].hex())


def parse_ds4(byte: int) -> Optional[Tuple[int, bool]]:
    """Valid DualShock 4 battery byte, or no reading for error/reserved codes."""
    if type(byte) is not int or not 0 <= byte <= 255:
        return None
    raw = byte & 0x0F
    charging = bool(byte & 0x10)
    if raw <= 10:
        return raw * 10, charging
    if charging and raw == 11:       # documented full code, only with the cable
        return 100, True
    # The pinned driver documents no-cable bins only through 10 (2550-2557),
    # although its no-cable implementation saturates every value >=10 (2580-2588).
    # Deliberately refuse undocumented wireless 11..15 rather than invent full.
    # Cable codes 14/15 are charging faults; 12/13 are undefined.
    return None


def parse_dualsense(byte: int) -> Optional[Tuple[int, bool]]:
    """DualSense status: valid battery bins, charge complete, or no reading.

    Charging states 0/1 contain level 0..10. State 2 explicitly means full,
    independent of the low nibble. States a/b/f are faults; others are reserved.
    """
    if type(byte) is not int or not 0 <= byte <= 255:
        return None
    raw, charge = byte & 0x0F, byte >> 4
    if charge == 2:
        return 100, True             # retain the existing plugged-in presentation
    if charge not in (0, 1) or raw > 10:
        return None
    return raw * 10, charge == 1


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


def _mac(serial) -> str:
    """Canonical physical address; only a proven MAC can migrate an old BT key."""
    if type(serial) is not str:
        return ""
    if not re.fullmatch(r"(?:[0-9a-fA-F]{12}|(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|(?:[0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2})", serial):
        return ""
    value = serial.replace(":", "").replace("-", "").lower()
    if value in ("000000000000", "ffffffffffff"):
        return ""
    return value


def _endpoint_identity(path, prefix="usb") -> str:
    """Port identity without extra device queries; a port move is a new identity."""
    instance = _instance(path)
    if instance:
        return prefix + "-" + instance
    raw = bytes(path) if isinstance(path, (bytes, bytearray)) else str(path).encode("utf-8", "surrogatepass")
    return prefix + "-path-" + hashlib.sha256(raw.lower()).hexdigest()


class PlayStationProvider(Provider):
    name = "playstation"

    def __init__(self):
        self._diag: List[str] = []
        self._aliases: Dict[str, str] = {}
        self.pending = False        # a controller is connected but has not reported battery yet
        self._pending_since: Dict[str, float] = {}   # key -> when its reading first went missing
        self._gave_up: set = set()  # keys whose pending window ran out (reported once)
        # False (default): a Bluetooth controller is never switched to its full report, see
        # the module docstring. Set by the app from Preferences before each poll.
        self.switch_bluetooth = False
        self._basic: bool = False   # the last _read saw a Bluetooth controller in its basic mode
        self._identity_mac = ""    # from this opened handle only, never cached by USB path
        self._status_error = False

    # ---- low level -------------------------------------------------------
    @staticmethod
    def _is_bluetooth(path) -> bool:
        s = path.decode("ascii", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        s = s.lower()
        return "vid&" in s or _BT_HID_GUID in s

    def _read(self, path, is_dualsense: bool, window: float = WINDOW,
              adapter: bool = False, identity_info: Optional[dict] = None) -> Optional[Tuple[int, bool]]:
        """-> (level, charging) or None if no battery report arrived.

        `adapter` is the DS4 USB wireless adapter, which also reports when no controller
        is paired with it."""
        self._identity_mac = ""
        self._status_error = False
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
            identity_request = usb_identity_request(identity_info) if identity_info is not None else None
            if not bluetooth and identity_request is not None:
                try:
                    self._identity_mac = parse_usb_identity(
                        dev.get_feature_report(identity_request[0], identity_request[1]), identity_request)
                    self._diag.append("    USB physical identity read: "
                                      + ("valid" if self._identity_mac else "unavailable"))
                except (OSError, ValueError) as e:
                    self._diag.append(f"    USB identity read unavailable: {e}")
            rid, off = (DUALSENSE_REPORT if is_dualsense else DS4_REPORT)[bluetooth]
            report_size = 78 if bluetooth else 64  # full input reports in the pinned driver
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
            invalid_reports = 0
            while time.time() < deadline:
                try:
                    data = dev.read(78)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    break
                if not data:
                    if self._status_error:
                        break       # a recognized error is not a controller still starting
                    time.sleep(0.005)
                    continue
                # skip the stripped-down Bluetooth report 0x01 (no battery) and any
                # other report; only the expected full report carries the battery
                if data[0] != rid or len(data) < report_size:
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
                reading = parse_dualsense(byte) if is_dualsense else parse_ds4(byte)
                if reading is not None:
                    self._status_error = False
                    return reading
                self._status_error = True
                self._diag.append(f"    battery status {byte:#04x}: error or reserved; ignored")
                invalid_reports += 1
                if invalid_reports >= 8:
                    break           # a continuous fault stream must not spin for the whole window
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
        self._aliases = {}
        self.pending = False
        if hid is None:
            return []
        now = time.time()
        try:
            infos = hidlist.enumerate(SONY_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(playstation): %s", e)
            return []

        # USB serials can be missing or shared by clones. The Windows instance
        # identifies one USB endpoint regardless of other connected controllers.
        # Keep transports separate even when their serial strings are identical;
        # only _merge() may combine proven USB/Bluetooth counterparts.
        groups: Dict[Tuple[int, bool, str], List[dict]] = {}
        for d in infos:
            pid = d["product_id"]
            if pid not in KNOWN:
                continue
            bluetooth = self._is_bluetooth(d["path"])
            ident = _endpoint_identity(d["path"], "bt" if bluetooth else "usb")
            groups.setdefault((pid, bluetooth, ident), []).append(d)

        conns: List[dict] = []
        for (pid, bluetooth, ident), ifaces in groups.items():
            name, is_dualsense = KNOWN[pid]
            product = (ifaces[0].get("product_string") or "").strip()
            serials = {d.get("serial_number") for d in ifaces if type(d.get("serial_number")) is str and d.get("serial_number")}
            normalized = {_mac(serial) for serial in serials}
            # Missing serials on other collections do not contradict one known
            # MAC; inconsistent or dummy serials cannot prove cross-transport identity.
            serial = min(serials) if serials and len(normalized) == 1 and "" not in normalized else ""
            self._diag.append(f"[PlayStation] {name} pid={pid:04x} "
                              f"{'Bluetooth' if bluetooth else 'USB'} interfaces={len(ifaces)} '{product}'")
            res = None
            basic = False
            fault = False
            physical_mac = ""
            identity_attempted = False
            ordered = sorted(ifaces, key=lambda d: (usb_identity_request(d) is None, _battery_first(d)))
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
                identity_info = d if not identity_attempted and usb_identity_request(d) is not None else None
                identity_attempted |= identity_info is not None
                res = self._read(d["path"], is_dualsense, min(WINDOW, left),
                                 adapter=pid == ADAPTER_PID, identity_info=identity_info)
                fault |= self._status_error
                if self._identity_mac:
                    physical_mac = self._identity_mac
                if res is not None:
                    break
                if self._basic:
                    basic = True
                    break
            if res is not None:
                self._diag.append(f"  -> {res[0]}%{' charging' if res[1] else ''}")
            metadata_mac = _mac(serial)
            if physical_mac and metadata_mac and physical_mac != metadata_mac:
                self._diag.append("    conflicting USB physical identity and serial metadata; keeping endpoint separate")
                physical_mac = metadata_mac = ""
            mac = physical_mac or metadata_mac
            conns.append({"pid": pid, "name": name, "bluetooth": bluetooth,
                          "mac": mac, "reading": res,
                          "basic": basic, "fault": fault, "usb": "" if bluetooth else ident,
                          "identity": ident, "serial": serial,
                          "legacy_serials": serials or ({""} if bluetooth else {"usb-" + _instance(ifaces[0]["path"])})})

        devs = self._merge(conns)
        # Valid unique physical addresses survive USB port moves. Anonymous or
        # conflicting devices remain endpoint-local; counts never establish ownership.
        out: List[DeviceStatus] = []
        connection_results = {}
        for dev in devs:
            ident = dev["mac"] or dev["identity"]
            key = f"ps:{dev['pid']:04x}:{ident}"
            connection_results[(dev["pid"], dev["bluetooth"], dev["identity"])] = key
            if dev["usb"]:
                connection_results[(dev["pid"], False, dev["usb"])] = key
            res = dev["reading"]
            if res is None and dev.get("fault"):
                self._pending_since.pop(key, None)
                self._gave_up.discard(key)
                out.append(DeviceStatus(key, dev["name"], None, False, True, "playstation",
                                        "battery status unavailable (controller reported an error)",
                                        charging_known=False, ui_message="state.poll_unavailable"))
                continue
            if res is None and dev.get("basic"):
                # Bluetooth, basic mode, not switched on purpose: this is the normal state,
                # not a controller that is still starting, so no fast re-check either
                self._pending_since.pop(key, None)
                self._gave_up.discard(key)
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
                    # keep the timestamp: deleting it would start a new window next poll
                    if key not in self._gave_up:
                        self._gave_up.add(key)
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
            self._gave_up.discard(key)
            out.append(DeviceStatus(key, dev["name"], level, charging, True, "playstation"))
        # a controller that went away gets a fresh window when it comes back
        present = {s.key for s in out}
        for key in [k for k in self._pending_since if k not in present]:
            del self._pending_since[key]
        self._gave_up &= present
        # Only an actually observed MAC-shaped old key proves its old owner.
        # A plain/anonymous/port key could belong to another previously attached
        # pad, even when exactly one pad is present now. Never move those settings.
        legacy_targets = {}
        conflicting = set()
        for connection in conns:
            prefix = f"ps:{connection['pid']:04x}:"
            target = connection_results[(connection["pid"], connection["bluetooth"], connection["identity"])]
            if not connection["mac"] or target != prefix + connection["mac"]:
                continue             # duplicate/anonymous endpoints prove no historical owner
            serials = connection["legacy_serials"]
            for serial in serials:
                if _mac(serial) and _mac(serial) == connection["mac"]:
                    old = prefix + serial
                    legacy_targets.setdefault(old, set()).add(target)
                    if len(serials) > 1:
                        conflicting.add(old)
        self._aliases = {old: next(iter(targets)) for old, targets in legacy_targets.items()
                         if len(targets) == 1 and old not in conflicting and old not in present}
        return out

    def _merge(self, conns: List[dict]) -> List[dict]:
        """Combine only unique USB/BT pairs proven by physical addresses.

        A common PID, battery level or list order is not identity. When USB has
        no matching MAC serial, two connections may remain visible for the same
        physical pad; this is safer than hiding a different controller's battery.
        USB pairing-info reads provide missing identity on the sourced Sony models.
        """
        by_pid: Dict[int, List[dict]] = {}
        for connection in conns:
            by_pid.setdefault(connection["pid"], []).append(connection)
        out: List[dict] = []
        for pid, group in by_pid.items():
            wireless, wired = {}, {}
            for connection in group:
                mac = _mac(connection.get("mac"))
                if mac:
                    (wireless if connection["bluetooth"] else wired).setdefault(mac, []).append(connection)
            pairs = {}
            for mac in wireless.keys() & wired.keys():
                if len(wireless[mac]) == len(wired[mac]) == 1:
                    pairs[id(wireless[mac][0])] = wired[mac][0]
            merged_usb = {id(connection) for connection in pairs.values()}
            for connection in group:
                cable = pairs.get(id(connection))
                if cable is not None:
                    device = dict(connection)
                    device["reading"] = self._prefer([cable["reading"], connection["reading"]])
                    device["usb"] = cable["usb"]
                    device["fault"] = connection.get("fault", False) or cable.get("fault", False)
                    out.append(device)
                    self._diag.append(f"[PlayStation] pid={pid:04x}: matching physical address; showing one icon")
                elif id(connection) not in merged_usb:
                    mac = _mac(connection.get("mac"))
                    if (len(wireless.get(mac, ())) > 1 or len(wired.get(mac, ())) > 1):
                        # A clone's repeated serial is not a unique Bluetooth
                        # identity either. Keep both endpoints instead of using
                        # the same key and hiding one of their readings.
                        connection = dict(connection, mac="")
                    out.append(connection)
        return out

    def legacy_key_aliases(self) -> dict[str, str]:
        """Pure cached migration proof across the latest USB and Bluetooth poll."""
        return dict(self._aliases)

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
