"""ASUS ROG / TUF wireless mice, on their 2.4 GHz receiver or on the cable, and ROG mice
and keyboards on the ROG OMNI receiver.

Source: G-Helper (seerge/g-helper, app/Peripherals/Mouse/AsusMouse.cs and the model
files in app/Peripherals/Mouse/Models). Only the protocol facts are used here (ids,
command, byte positions); the code is written for this app.

The exchange, on the vendor collection of interface 0 ("mi_00" in G-Helper):

    request   00 12 07          report id 0, command 12 07 (battery), zeros up to 65 bytes
    reply     00 12 07 ...      the echo of the command; after the report id:
                  byte 4        battery: a percentage, or a level 0..4 on older models
                  byte 9        charging when not 0
    error     00 ff aa ...      the mouse does not know the command
    zeros     00 00 00 ...      no reply (the mouse is off or asleep)

hidapi on Windows leaves out report id 0, so a reply starts with 12 07. A reply with
the report id in front is accepted too.

G-Helper takes a battery value of 0 without charging as "the mouse is in standby",
not as an empty battery. This provider does the same: no reading.

The ROG OMNI receiver (0B05:1ACE; G-Helper's PeripheralsProvider.cs DedectOmniMouse and
the *Omni model classes) holds a mouse and a keyboard. Its interface 0 is a plain
keyboard; the vendor collections are on interface 2, each with its own report id and
64-byte reports (hidapi keeps a report id that is not 0 in front of the reply):

    col01 ff02  report 1   01 a0 00 00  pair list: from byte 5, four bytes a slot, the
                                        device pid little-endian in the first two, 0 ends
    col03 ff01  report 3   03 12 07     the mouse: the reply above, one byte later
    col02 ff00  report 2   02 12 01     the keyboard: battery in byte 6 (byte 11 on the
                                        Falchion family, where byte 6 is a 0..10 gauge),
                                        charging in byte 9

A keyboard asleep on battery answers ff aa instead, and a mouse asleep all zeros or
battery 0: no reading, as for the mice above.

The pair list names the devices; a pid that is not known is skipped, as G-Helper does.
The collections of one receiver are matched by the device instance in their path, so two
OMNI receivers do not mix. Confirmed on a Harpe Ace Mini and a Falchion RX Low Profile.

Not included (they use a different collection, report id or byte position in
G-Helper, and none was on hand to test): the Harpe II Ace, Keris II Ace / Origin and
Harpe Ace Mini / Extreme on their own receiver or cable, Strix Carry, Gladius II Wireless
and the MD200.
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

ASUS_VID = 0x0B05
INTERFACE = 0

REQUEST = [0x00, 0x12, 0x07]         # report id 0, battery command
ECHO = (0x12, 0x07)
ERROR = (0xFF, 0xAA)
PACKET_LENGTH = 65                   # G-Helper's default packet size (report id + 64)
LEVEL_BYTE = 4                       # after the echo is found at 0 and 1
CHARGING_BYTE = 9

READ_TIMEOUT_MS = 300                # G-Helper's USB timeout
READ_ATTEMPTS = 8                    # other reports on the collection are skipped
WRITE_ATTEMPTS = 3

PERCENT, STEPS = 1, 25               # scale of the battery byte

# pid -> (name, scale). Wired and wireless pids of one mouse share the name, so they
# share one icon.
KNOWN: Dict[int, Tuple[str, int]] = {
    0x1A72: ("ROG Gladius III Aimpoint", PERCENT),
    0x1A70: ("ROG Gladius III Aimpoint", PERCENT),
    0x1B0C: ("ROG Gladius III Eva 2", PERCENT),
    0x1B0A: ("ROG Gladius III Eva 2", PERCENT),
    0x197F: ("ROG Gladius III Wireless", PERCENT),
    0x197D: ("ROG Gladius III Wireless", PERCENT),
    0x1A1A: ("ROG Chakram X", PERCENT),
    0x1A18: ("ROG Chakram X", PERCENT),
    0x1A94: ("ROG Harpe Ace Aim Lab Edition", PERCENT),
    0x1A92: ("ROG Harpe Ace Aim Lab Edition", PERCENT),
    0x1A68: ("ROG Keris Wireless Aimpoint", PERCENT),
    0x1A66: ("ROG Keris Wireless Aimpoint", PERCENT),
    0x1979: ("ROG Spatha X", PERCENT),
    0x1977: ("ROG Spatha X", PERCENT),
    0x19F4: ("TUF Gaming M4 Wireless", PERCENT),
    0x1A8D: ("TX Gaming Mouse", PERCENT),
    0x1AF5: ("TX Gaming Mouse Mini", PERCENT),
    0x1AF3: ("TX Gaming Mouse Mini", PERCENT),
    0x1C57: ("TUF Gaming Mini Miku Edition", PERCENT),
    0x1C56: ("TUF Gaming Mini Miku Edition", PERCENT),
    # older models: the battery byte is a level 0..4, G-Helper multiplies it by 25
    0x18E5: ("ROG Chakram", STEPS),
    0x18E3: ("ROG Chakram", STEPS),
    0x1960: ("ROG Keris Wireless", STEPS),
    0x195E: ("ROG Keris Wireless", STEPS),
    0x1A59: ("ROG Keris EVA Edition", STEPS),
    0x1A57: ("ROG Keris EVA Edition", STEPS),
    0x1908: ("ROG Pugio II", STEPS),
    0x1906: ("ROG Pugio II", STEPS),
    0x1949: ("ROG Strix Impact II Wireless", STEPS),
    0x1947: ("ROG Strix Impact II Wireless", STEPS),
}

# ROG OMNI receiver (G-Helper PeripheralsProvider.cs)
OMNI_PID = 0x1ACE
OMNI_INTERFACE = 2
OMNI_CONTROL, OMNI_KEYBOARD, OMNI_MOUSE = 0xFF02, 0xFF00, 0xFF01    # col01, col02, col03
OMNI_PACKET_LENGTH = 64
OMNI_PAIRS = [0x01, 0xA0, 0x00, 0x00]
OMNI_FIRST_SLOT, OMNI_SLOT = 5, 4
OMNI_MOUSE_REQUEST = [0x03, 0x12, 0x07]
OMNI_KEYBOARD_REQUEST = [0x02, 0x12, 0x01]
KEYBOARD_ECHO = (0x12, 0x01)
KEYBOARD_CHARGING_BYTE = 9           # counted with the report id in front, as G-Helper does

# pids in the pair list (MouseFromOmniPid). The names match KNOWN, so a mouse on the OMNI
# receiver and on its own receiver or cable share one icon.
OMNI_MICE: Dict[int, str] = {
    0x1B65: "ROG Harpe Ace Mini",
    0x1C0E: "ROG Keris II Origin",
    0x1D4E: "ROG Keris II Origin KJP",
    0x1A94: "ROG Harpe Ace Aim Lab Edition",
    0x1AD7: "ROG Strix Impact III Wireless",
    0x1A72: "ROG Gladius III Aimpoint",
    0x1A68: "ROG Keris Wireless Aimpoint",
    0x1A6A: "ROG Keris Wireless Aimpoint",
    0x1B1A: "ROG Keris II Ace",
    0x1B18: "ROG Keris II Ace",
    0x1B68: "ROG Harpe Ace Extreme",
    0x1B69: "ROG Harpe Ace Extreme",
}

# pid -> (name, battery byte) (KeyboardFromOmniPid; AsusKeyboard.ParseBattery reads byte 6,
# Falchion.ParseBattery byte 11)
OMNI_KEYBOARDS: Dict[int, Tuple[str, int]] = {
    0x1A85: ("ROG Azoth", 6),
    0x1B42: ("ROG Azoth Extreme", 6),
    0x1CF1: ("ROG Azoth Extreme SE", 6),
    0x1AB0: ("ROG Strix Scope II 96 Wireless", 6),
    0x1B7A: ("ROG Strix Scope II 96 RX Wireless", 6),
    0x1B06: ("ROG Falchion RX Low Profile", 11),
}

Reading = Tuple[int, bool, str]     # level %, charging, approximate text ("" = exact)


def _offset(r: List[int], pair: Tuple[int, int]) -> Optional[int]:
    """Where `pair` starts in a reply: 0 (hidapi left out report id 0) or 1 (report id
    in front). None when the reply does not start with it."""
    if len(r) > 1 and (r[0], r[1]) == pair:
        return 0
    if len(r) > 2 and r[0] == 0x00 and (r[1], r[2]) == pair:
        return 1
    return None


def has_echo(r: List[int]) -> bool:
    return _offset(r, ECHO) is not None


def is_error(r: List[int]) -> bool:
    return _offset(r, ERROR) is not None


def parse_reply(r: List[int], scale: int) -> Optional[Reading]:
    """A reply that echoes 12 07 -> (level, charging, text). None for anything else,
    for the standby value (0 and not charging) and for a value out of range."""
    m = _offset(r, ECHO)
    if m is None or len(r) < m + CHARGING_BYTE + 1:
        return None
    raw = r[m + LEVEL_BYTE]
    charging = r[m + CHARGING_BYTE] != 0
    if raw == 0 and not charging:
        return None                                  # standby, not empty
    if scale == STEPS:
        if raw > 4:
            return None
        level = raw * STEPS
        text = f"about {level}%" + (", charging" if charging else "")
        return level, charging, text
    if raw > 100:
        return None
    return raw, charging, ""


def parse_pairs(r: List[int]) -> List[int]:
    """The pair list of an OMNI receiver -> the pids of the paired devices."""
    if len(r) < 2 or (r[0], r[1]) != (OMNI_PAIRS[0], OMNI_PAIRS[1]):
        return []
    pids = []
    for i in range(OMNI_FIRST_SLOT, len(r) - 1, OMNI_SLOT):
        pid = r[i] | (r[i + 1] << 8)
        if pid == 0:
            break
        pids.append(pid)
    return pids


def is_omni_mouse_reply(r: List[int]) -> bool:
    return len(r) > 2 and r[0] == OMNI_MOUSE_REQUEST[0] and (r[1], r[2]) == ECHO


def is_omni_keyboard_reply(r: List[int]) -> bool:
    return len(r) > 2 and r[0] == OMNI_KEYBOARD_REQUEST[0] and (r[1], r[2]) == KEYBOARD_ECHO


def parse_omni_mouse(r: List[int]) -> Optional[Reading]:
    """The mouse reply on the OMNI receiver: report id 3, then the 12 07 reply."""
    if not is_omni_mouse_reply(r):
        return None
    return parse_reply(r[1:], PERCENT)


def parse_omni_keyboard(r: List[int], battery_byte: int) -> Optional[Reading]:
    """The keyboard reply on the OMNI receiver: 02 12 01, the battery in `battery_byte`
    and charging in byte 9. 0 without charging is taken as standby, as for the mice."""
    if not is_omni_keyboard_reply(r) or len(r) <= max(battery_byte, KEYBOARD_CHARGING_BYTE):
        return None
    level = r[battery_byte]
    charging = r[KEYBOARD_CHARGING_BYTE] != 0
    if level > 100 or (level == 0 and not charging):
        return None
    return level, charging, ""


def _instance(path) -> str:
    """The device instance in a HID path, without the collection number:
    ...#7&abc&0&0001#{guid} -> 7&abc&0. The collections of one receiver share it."""
    p = (path.decode("utf-8", "replace") if isinstance(path, bytes) else str(path)).lower()
    parts = p.split("#")
    if len(parts) < 3:
        return p
    cut = parts[2].rfind("&")
    return parts[2][:cut] if cut > 0 else parts[2]


class AsusProvider(Provider):
    name = "asus"

    def __init__(self):
        self._diag: List[str] = []
        self._failing: Dict[str, bool] = {}

    def _ask(self, path, request: List[int], wanted, length: int) -> Optional[List[int]]:
        """Send `request` and return the first reply that `wanted` accepts. None for an
        error reply, all zeros, or no such reply."""
        rid = request[0]                     # a report id that is not 0 stays in the reply

        def body(r):
            return r[1:] if rid and r and r[0] == rid else r

        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            # drop reports that are already waiting, as G-Helper does before a request
            try:
                dev.set_nonblocking(True)
                for _ in range(16):
                    if not dev.read(length):
                        break
                dev.set_nonblocking(False)
            except (OSError, IOError, ValueError):
                pass
            for _ in range(WRITE_ATTEMPTS):
                try:
                    dev.write(request + [0x00] * (length - len(request)))
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    write: {e}")
                    return None
                for _ in range(READ_ATTEMPTS):
                    r = list(dev.read(length, READ_TIMEOUT_MS) or [])
                    if not r:
                        break                        # timeout: send the request again
                    if is_error(body(r)):
                        self._diag.append(f"    reply: error {hexdump(r, 12)} (not known, or asleep)")
                        return None
                    if not any(body(r)):
                        self._diag.append("    reply: all zeros (off or asleep)")
                        return None
                    if not wanted(r):
                        continue                     # a button or profile event: skip it
                    self._diag.append(f"    reply: {hexdump(r, 12)}")
                    return r
            self._diag.append(f"    no reply with the {request[1]:02x} {request[2]:02x} echo")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _parsed(self, r: Optional[List[int]], parse) -> Optional[Reading]:
        if r is None:
            return None
        res = parse(r)
        if res is None:
            self._diag.append("    battery 0 and not charging (standby), or out of range")
        return res

    def _read(self, path, scale: int) -> Optional[Reading]:
        r = self._ask(path, REQUEST, has_echo, PACKET_LENGTH)
        return self._parsed(r, lambda r: parse_reply(r, scale))

    def _poll_omni(self, infos: List[dict]) -> List[Tuple[str, str, Reading]]:
        """(name, kind, reading) of the devices paired to each OMNI receiver."""
        receivers: Dict[str, Dict[int, bytes]] = {}
        for d in infos:
            if d["product_id"] == OMNI_PID and d.get("interface_number") == OMNI_INTERFACE:
                receivers.setdefault(_instance(d["path"]), {})[d.get("usage_page", 0)] = d["path"]

        out: List[Tuple[str, str, Reading]] = []
        for cols in receivers.values():
            if OMNI_CONTROL not in cols:
                continue
            self._diag.append(f"[ASUS] ROG OMNI receiver pid={OMNI_PID:04x}")
            r = self._ask(cols[OMNI_CONTROL], OMNI_PAIRS, lambda r: bool(parse_pairs(r)),
                          OMNI_PACKET_LENGTH)
            pids = parse_pairs(r or [])
            self._diag.append("  paired: " + (" ".join(f"{p:04x}" for p in pids) or "none"))

            mouse = next((p for p in pids if p in OMNI_MICE), None)
            if mouse is not None and OMNI_MOUSE in cols:
                name = OMNI_MICE[mouse]
                self._diag.append(f"  mouse {name} pid={mouse:04x}")
                r = self._ask(cols[OMNI_MOUSE], OMNI_MOUSE_REQUEST, is_omni_mouse_reply,
                              OMNI_PACKET_LENGTH)
                res = self._parsed(r, parse_omni_mouse)
                if res is not None:
                    out.append((name, "mouse", res))

            keyboard = next((p for p in pids if p in OMNI_KEYBOARDS), None)
            if keyboard is not None and OMNI_KEYBOARD in cols:
                name, battery_byte = OMNI_KEYBOARDS[keyboard]
                self._diag.append(f"  keyboard {name} pid={keyboard:04x}")
                r = self._ask(cols[OMNI_KEYBOARD], OMNI_KEYBOARD_REQUEST, is_omni_keyboard_reply,
                              OMNI_PACKET_LENGTH)
                res = self._parsed(r, lambda r: parse_omni_keyboard(r, battery_byte))
                if res is not None:
                    out.append((name, "keyboard", res))
        return out

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(ASUS_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(asus): %s", e)
            return []

        found: Dict[str, Reading] = {}
        kinds: Dict[str, str] = {}

        def keep(name: str, kind: str, res: Reading):
            # the same mouse on the cable and on a receiver: keep the charging reading
            if name not in found or (res[1] and not found[name][1]):
                found[name] = res
                kinds[name] = kind

        for d in infos:
            pid = d["product_id"]
            if pid not in KNOWN:
                continue
            if d.get("interface_number") != INTERFACE or d.get("usage_page", 0) < 0xFF00:
                continue                                 # only the vendor collection
            name, scale = KNOWN[pid]
            self._diag.append(f"[ASUS] {name} pid={pid:04x} usage="
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            res = self._read(d["path"], scale)
            if res is None:
                continue
            self._diag.append(f"  -> {res[2] or str(res[0]) + '%'}{' charging' if res[1] else ''}")
            keep(name, "mouse", res)

        for name, kind, res in self._poll_omni(infos):
            self._diag.append(f"  -> {name}: {res[0]}%{' charging' if res[1] else ''}")
            keep(name, kind, res)

        out: List[DeviceStatus] = []
        for name, (level, charging, text) in found.items():
            key = "asus:" + name.lower().replace(" ", "-")
            out.append(DeviceStatus(key, name, level, charging, True, "asus", text, kind=kinds[name]))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
