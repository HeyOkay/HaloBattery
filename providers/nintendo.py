"""Nintendo Switch controllers over Bluetooth: Pro Controller and Joy-Con.

Windows does not know their battery level, so it is read from the controller's
own input reports. Source: SDL's HIDAPI Switch driver
(src/joystick/hidapi/SDL_hidapi_switch.c) and dekuNukem's
Nintendo_Switch_Reverse_Engineering notes.

  * Input reports 0x21 (the reply to a subcommand), 0x30 and 0x31 (full reports)
    start with the same header:
        [0] report id   [1] timer   [2] battery and connection
    The high nibble of byte 2 is the battery:
        bits 7..5  level 0..8, in steps of 2 (8 full, 6 medium, 4 low,
                   2 critical, 0 empty)
        bit 4      charging
    SDL shows level / 8 as the percentage (100, 75, 50, 25, 0). This provider
    does the same, and the tooltip shows the level name.
  * Report 0x3F (the "simple" mode the controller starts in over Bluetooth)
    has no battery.

What the provider sends. Over Bluetooth, the controller is in the simple mode
until an app changes it. The provider does not change the mode. It first
listens: when Steam or another app has put the controller in the full mode, the
0x30 reports carry the battery and nothing is written. Otherwise it sends one
subcommand that only reads information: 0x02 "request device info". The
controller answers with report 0x21, and the header of that report carries the
battery. The output report is 0x01 (rumble and subcommand), 49 bytes, with the
neutral rumble data SDL uses (00 01 40 40), so the controller does not vibrate.

USB is not read. On the cable the controller charges, and the USB protocol needs
a handshake (0x80 0x02) that changes the controller's USB state.
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

NINTENDO_VID = 0x057E

# pid -> name. The product string over Bluetooth is only "Wireless Gamepad".
KNOWN = {
    0x2006: "Nintendo Joy-Con (L)",
    0x2007: "Nintendo Joy-Con (R)",
    0x2009: "Nintendo Switch Pro Controller",
}

REPORT_SUBCOMMAND_REPLY = 0x21
REPORTS_WITH_BATTERY = (REPORT_SUBCOMMAND_REPLY, 0x30, 0x31)
OUTPUT_RUMBLE_AND_SUBCOMMAND = 0x01
SUBCOMMAND_DEVICE_INFO = 0x02
NEUTRAL_RUMBLE = (0x00, 0x01, 0x40, 0x40)
BLUETOOTH_PACKET_LENGTH = 49

LEVEL_NAMES = {8: "full", 6: "medium", 4: "low", 2: "critical", 0: "empty"}

LISTEN = 0.1        # s: time to wait for a full report before anything is written
TIMEOUT = 0.6       # s: time to wait for the reply to the subcommand
RESEND = 0.3        # s: send the subcommand one more time after this
ASLEEP_KEEP = 300   # s: a controller that stops answering keeps its last value (greyed)

# Bluetooth HID paths carry this service GUID and the "VID&" spelling; USB paths
# use "VID_" instead (the same test as the PlayStation provider).
_BT_HID_GUID = "{00001124-0000-1000-8000-00805f9b34fb}"


def is_bluetooth(path) -> bool:
    s = path.decode("ascii", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
    s = s.lower()
    return "vid&" in s or _BT_HID_GUID in s


def parse_battery(byte: int) -> Optional[Tuple[int, bool, str]]:
    """Byte 2 of a 0x21 / 0x30 / 0x31 report -> (level %, charging, tooltip text).
    None when the level is above 8, which the protocol does not define."""
    level = (byte & 0xE0) >> 4          # 0, 2, 4 ... 14
    if level > 8:
        return None
    charging = bool(byte & 0x10)
    pct = level * 100 // 8
    text = f"about {pct}% ({LEVEL_NAMES[level]})"
    if charging:
        text += ", charging"
    return pct, charging, text


def subcommand_packet(counter: int, subcommand: int) -> List[int]:
    """Output report 0x01: [id, counter, rumble left (4), rumble right (4), subcommand],
    padded with zeros to the Bluetooth packet length."""
    pkt = [OUTPUT_RUMBLE_AND_SUBCOMMAND, counter & 0x0F]
    pkt += list(NEUTRAL_RUMBLE) * 2
    pkt.append(subcommand)
    return pkt + [0] * (BLUETOOTH_PACKET_LENGTH - len(pkt))


class NintendoProvider(Provider):
    name = "nintendo"

    def __init__(self):
        self._diag: List[str] = []
        self._counter = 0
        self._last: Dict[str, Tuple[Tuple[int, bool, str], float]] = {}
        self._failing: Dict[str, bool] = {}

    # ---- low level -------------------------------------------------------
    def _read(self, path) -> Optional[Tuple[int, bool, str]]:
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
            start = time.time()
            sent = []                 # times the subcommand was sent
            while True:
                now = time.time()
                first = not sent and now - start >= LISTEN
                again = len(sent) == 1 and now - sent[0] >= RESEND
                if first or again:
                    if not self._send(dev):
                        return None
                    sent.append(now)
                if sent and now - sent[0] >= TIMEOUT:
                    self._diag.append("    no report with the battery")
                    return None
                try:
                    data = dev.read(64)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    return None
                if not data:
                    time.sleep(0.005)
                    continue
                if data[0] not in REPORTS_WITH_BATTERY or len(data) < 3:
                    continue          # 0x3F (simple mode) and other reports: no battery
                self._diag.append(f"    report {data[0]:#04x}"
                                  f"{' (no write)' if not sent else ''}: {hexdump(data, 16)}")
                res = parse_battery(data[2])
                if res is None:
                    self._diag.append(f"    battery byte {data[2]:#04x}: level not defined, ignored")
                return res
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _send(self, dev) -> bool:
        pkt = subcommand_packet(self._counter, SUBCOMMAND_DEVICE_INFO)
        self._counter = (self._counter + 1) & 0x0F
        try:
            n = dev.write(pkt)
        except (OSError, ValueError) as e:
            self._diag.append(f"    write: {e}")
            return False
        if n is not None and n < 0:
            self._diag.append("    write failed")
            return False
        self._diag.append(f"    sent subcommand {SUBCOMMAND_DEVICE_INFO:#04x} (request device info)")
        return True

    # ---- high level ------------------------------------------------------
    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(NINTENDO_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(nintendo): %s", e)
            return []

        out: List[DeviceStatus] = []
        now = time.time()
        for d in infos:
            pid = d["product_id"]
            name = KNOWN.get(pid)
            if name is None:
                continue
            path = d["path"]
            if not is_bluetooth(path):
                self._diag.append(f"[Nintendo] {name} pid={pid:04x}: USB, not read (it charges on the cable)")
                continue
            mac = d.get("serial_number") or ""
            key = f"switch:{pid:04x}:{mac}"
            self._diag.append(f"[Nintendo] {name} pid={pid:04x} Bluetooth "
                              f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            res = self._read(path)
            if res is not None:
                if self._failing.pop(key, None):
                    log.info("[Nintendo] %s: answering again", name)
                self._last[key] = (res, now)
                level, charging, text = res
                out.append(DeviceStatus(key, name, level, charging, True, "nintendo", text,
                                        kind="gamepad", via="bluetooth", ui_message="state.approx_grade",
                                        ui_params={"level": level, "grade": LEVEL_NAMES[level * 8 // 100],
                                                   "charging": charging}))
                continue
            if not self._failing.get(key):
                self._failing[key] = True
                log.info("[Nintendo] %s: no battery report; not logged again until it answers", name)
            last = self._last.get(key)
            if last and now - last[1] < ASLEEP_KEEP:
                level, charging, text = last[0]
                out.append(DeviceStatus(key, name, level, False, False, "nintendo",
                                        f"{text.split(',')[0]} (last known value)",
                                        kind="gamepad", via="bluetooth", ui_message="state.approx_grade",
                                        ui_params={"level": level, "grade": LEVEL_NAMES[level * 8 // 100],
                                                   "last_known": True}))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
