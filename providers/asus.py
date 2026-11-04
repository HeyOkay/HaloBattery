"""ASUS ROG / TUF wireless mice, on their 2.4 GHz receiver or on the cable.

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

The Harpe II Ace (G-Helper app/Peripherals/Mouse/Models/HarpeIIAce.cs) asks the same
12 07 on another channel: on its receiver (0B05:1AD0) the collection "mi_02&col03" with
report id 3, on the cable (0B05:1C69) interface 0 with report id 0, 64-byte packets. The
receiver is shared with the Harpe II Extreme; G-Helper tells them apart by the product
name.

Keyboards on the OMNI receiver (0B05:1ACE), from G-Helper's PeripheralsProvider.cs
(DedectOmniMouse, KeyboardFromOmniPid), Keyboard/AsusKeyboard.cs (ReadBattery) and the
model files StrixScopeII.cs and Azoth.cs:

    pair list   01 a0 00 00 on "mi_02&col01" (report id 1, 64 bytes); the reply lists the
                paired devices from byte 5 on, 4 bytes each, the product id little-endian
                first; a product id of 0 ends the list
    battery     02 12 01 on "mi_02&col02" of the same receiver (report id 2, 64 bytes)
    reply       02 12 01 ... byte 6 battery %, byte 9 charging when 1

The battery channel is opened only when the pair list names a known keyboard. hidapi
keeps a report id that is not 0 at the front of a reply, so byte positions count it.

Not included (they use a different collection, report id or byte position in
G-Helper, and none was on hand to test): mice on the OMNI receiver, Keris II Ace /
Origin, Harpe Ace Mini / Extreme, Strix Carry, Gladius II Wireless, the MD200, and the
keyboards on their own receiver or cable.
"""
from __future__ import annotations

import re
import time
from typing import Dict, List, NamedTuple, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

ASUS_VID = 0x0B05
INTERFACE = 0

REQUEST = [0x00, 0x12, 0x07]         # report id 0, battery command
COMMAND = (0x12, 0x07)
ECHO = COMMAND
ERROR = (0xFF, 0xAA)
PACKET_LENGTH = 65                   # G-Helper's default packet size (report id + 64)
LEVEL_BYTE = 4                       # after the echo is found at 0 and 1
CHARGING_BYTE = 9

READ_TIMEOUT_MS = 300                # G-Helper's USB timeout
READ_ATTEMPTS = 8                    # other reports on the collection are skipped
WRITE_ATTEMPTS = 3

PERCENT, STEPS = 1, 25               # scale of the battery byte


class Model(NamedTuple):
    name: str
    scale: int = PERCENT
    report_id: int = 0x00
    length: int = PACKET_LENGTH          # output report, report id included
    collection: str = ""                 # e.g. "mi_02&col03": only the collection whose
                                         # path has it; "" = vendor collection of interface 0


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

# models on another channel than report id 0 on interface 0 (see the module docstring)
HARPE_II_RECEIVER = 0x1AD0
CHANNELS: Dict[int, Model] = {
    HARPE_II_RECEIVER: Model("ROG Harpe II Ace", PERCENT, 0x03, 64, "mi_02&col03"),
    0x1C69: Model("ROG Harpe II Ace", PERCENT, 0x00, 64),
}
HARPE_II_EXTREME = "ROG Harpe II Extreme"     # same receiver, "EXTREME" in its name

MODELS: Dict[int, Model] = {pid: Model(name, scale) for pid, (name, scale) in KNOWN.items()}
MODELS.update(CHANNELS)

# OMNI receiver and the keyboards it can carry
OMNI_PID = 0x1ACE
OMNI_LIST = "mi_02&col01"
OMNI_KEYBOARD = "mi_02&col02"
OMNI_LIST_REQUEST = [0x01, 0xA0, 0x00, 0x00]
OMNI_LIST_FIRST = 5                  # first slot in the pair list reply (report id included)
OMNI_LIST_STEP = 4
KB_REPORT_ID = 0x02
KB_COMMAND = (0x12, 0x01)
KB_LENGTH = 64
KB_LEVEL_BYTE = 5                    # after the echo: byte 6 with the report id in front
KB_CHARGING_BYTE = 8                 # byte 9
OMNI_KEYBOARDS: Dict[int, str] = {   # pid in the pair list -> name
    0x1AB0: "ROG Strix Scope II 96",
    0x1B7A: "ROG Strix Scope II 96 RX",
    0x1A85: "ROG Azoth",
    0x1B42: "ROG Azoth Extreme",
    0x1CF1: "ROG Azoth Extreme SE",
}

Reading = Tuple[int, bool, str]     # level %, charging, approximate text ("" = exact)


def _offset(r: List[int], pair: Tuple[int, int], report_id: int = 0x00) -> Optional[int]:
    """Where `pair` starts in a reply: 0 (hidapi left out report id 0) or 1 (report id
    in front). None when the reply does not start with it."""
    if len(r) > 1 and (r[0], r[1]) == pair:
        return 0
    if len(r) > 2 and r[0] == report_id and (r[1], r[2]) == pair:
        return 1
    return None


def has_echo(r: List[int], report_id: int = 0x00, pair: Tuple[int, int] = ECHO) -> bool:
    return _offset(r, pair, report_id) is not None


def is_error(r: List[int], report_id: int = 0x00) -> bool:
    return _offset(r, ERROR, report_id) is not None


def is_empty(r: List[int], report_id: int = 0x00) -> bool:
    """All zeros after the report id: no answer from the device (off or asleep)."""
    return not any(r[1:] if r and r[0] == report_id else r)


def request(report_id: int, command: Tuple[int, int], length: int) -> List[int]:
    return [report_id, *command] + [0x00] * (length - 3)


def parse_reply(r: List[int], scale: int, report_id: int = 0x00) -> Optional[Reading]:
    """A reply that echoes 12 07 -> (level, charging, text). None for anything else,
    for the standby value (0 and not charging) and for a value out of range."""
    m = _offset(r, ECHO, report_id)
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


def parse_keyboard_reply(r: List[int]) -> Optional[Reading]:
    """A keyboard's reply to 12 01 -> (level, charging, ""). 0 without charging is taken
    as no reading, as for the mice."""
    m = _offset(r, KB_COMMAND, KB_REPORT_ID)
    if m is None or len(r) < m + KB_CHARGING_BYTE + 1:
        return None
    raw = r[m + KB_LEVEL_BYTE]
    charging = r[m + KB_CHARGING_BYTE] == 1
    if raw > 100 or (raw == 0 and not charging):
        return None
    return raw, charging, ""


def parse_pair_list(r: List[int]) -> List[int]:
    """The product ids in the OMNI receiver's pair list reply."""
    pids = []
    for i in range(OMNI_LIST_FIRST, len(r) - 1, OMNI_LIST_STEP):
        pid = r[i] | (r[i + 1] << 8)
        if pid == 0:
            break
        pids.append(pid)
    return pids


def path_text(d: dict) -> str:
    p = d.get("path", b"")
    if isinstance(p, (bytes, bytearray)):
        p = p.decode("utf-8", "replace")
    return str(p).lower()


def collection_of(d: dict) -> str:
    """'mi_02&col03' and the like, from a Windows HID path; '' when it has none."""
    m = re.search(r"mi_[0-9a-f]{2}(&col[0-9a-f]{2})?", path_text(d))
    return m.group(0) if m else ""


def receiver_instance(d: dict) -> str:
    """The part of a Windows HID path that is the same for every collection of one
    receiver (G-Helper's OmniInstance): the instance id without its last '&' field."""
    parts = path_text(d).split("#")
    if len(parts) < 3:
        return path_text(d)
    cut = parts[2].rfind("&")
    return parts[2][:cut] if cut > 0 else parts[2]


class AsusProvider(Provider):
    name = "asus"

    def __init__(self):
        self._diag: List[str] = []
        self._failing: Dict[str, bool] = {}

    def _exchange(self, path, packet: List[int], report_id: int,
                  echo: Optional[Tuple[int, int]]) -> Optional[List[int]]:
        """Send `packet` and return the first reply that echoes `echo` (any reply with
        the report id when `echo` is None). None for an error, all zeros or silence."""
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
                    if not dev.read(PACKET_LENGTH):
                        break
                dev.set_nonblocking(False)
            except (OSError, IOError, ValueError):
                pass
            for _ in range(WRITE_ATTEMPTS):
                try:
                    dev.write(packet)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    write: {e}")
                    return None
                for _ in range(READ_ATTEMPTS):
                    r = list(dev.read(PACKET_LENGTH, READ_TIMEOUT_MS) or [])
                    if not r:
                        break                        # timeout: send the request again
                    if is_error(r, report_id):
                        self._diag.append(f"    reply: error {hexdump(r, 12)}")
                        return None
                    if is_empty(r, report_id):
                        self._diag.append("    reply: all zeros (off or asleep)")
                        return None
                    if echo is None:
                        if r[0] != report_id:
                            continue
                    elif not has_echo(r, report_id, echo):
                        continue                     # a button or profile event: skip it
                    self._diag.append(f"    reply: {hexdump(r, 16)}")
                    return r
            want = "" if echo is None else " with the %02x %02x echo" % echo
            self._diag.append(f"    no reply{want}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _read(self, path, model: Model) -> Optional[Reading]:
        r = self._exchange(path, request(model.report_id, COMMAND, model.length),
                           model.report_id, ECHO)
        if r is None:
            return None
        res = parse_reply(r, model.scale, model.report_id)
        if res is None:
            self._diag.append("    battery 0 and not charging (standby), or out of range")
        return res

    @staticmethod
    def _is_channel(d: dict, model: Model) -> bool:
        if model.collection:
            return model.collection in path_text(d)
        return d.get("interface_number") == INTERFACE and d.get("usage_page", 0) >= 0xFF00

    def _poll_omni(self, infos: List[dict]) -> Dict[str, Reading]:
        """Keyboards on OMNI receivers: the pair list names the keyboard, then its
        battery is asked on the keyboard channel of the same receiver."""
        found: Dict[str, Reading] = {}
        omni = [d for d in infos if d["product_id"] == OMNI_PID]
        for lst in (d for d in omni if OMNI_LIST in path_text(d)):
            inst = receiver_instance(lst)
            self._diag.append(f"[ASUS] OMNI receiver pid={OMNI_PID:04x} {OMNI_LIST}")
            r = self._exchange(lst["path"], OMNI_LIST_REQUEST + [0x00] * (KB_LENGTH - len(OMNI_LIST_REQUEST)),
                               0x01, None)
            if r is None:
                continue
            pids = parse_pair_list(r)
            self._diag.append("    paired: " + (" ".join(f"{p:04x}" for p in pids) or "none"))
            names = [OMNI_KEYBOARDS[p] for p in pids if p in OMNI_KEYBOARDS]
            if not names:
                continue
            channel = next((d for d in omni if OMNI_KEYBOARD in path_text(d)
                            and receiver_instance(d) == inst), None)
            if channel is None:
                self._diag.append(f"    no {OMNI_KEYBOARD} collection on this receiver")
                continue
            name = names[0]
            self._diag.append(f"[ASUS] {name} on OMNI {OMNI_KEYBOARD}")
            r = self._exchange(channel["path"], request(KB_REPORT_ID, KB_COMMAND, KB_LENGTH),
                               KB_REPORT_ID, KB_COMMAND)
            res = parse_keyboard_reply(r) if r is not None else None
            if r is not None and res is None:
                self._diag.append("    battery 0 and not charging, or out of range")
            if res is None:
                continue
            self._diag.append(f"  -> {res[0]}%{' charging' if res[1] else ''}")
            found[name] = res
        return found

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
        for d in infos:
            pid = d["product_id"]
            model = MODELS.get(pid)
            if model is None or not self._is_channel(d, model):
                continue                                 # only the battery channel
            name = model.name
            if pid == HARPE_II_RECEIVER and "EXTREME" in (d.get("product_string") or "").upper():
                name = HARPE_II_EXTREME
            col = collection_of(d)
            self._diag.append(f"[ASUS] {name} pid={pid:04x} usage="
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                              + (f" {col}" if col else ""))
            res = self._read(d["path"], model)
            if res is None:
                continue
            self._diag.append(f"  -> {res[2] or str(res[0]) + '%'}{' charging' if res[1] else ''}")
            # the same mouse on the cable and on the receiver: keep the charging reading
            if name not in found or (res[1] and not found[name][1]):
                found[name] = res

        out: List[DeviceStatus] = []
        for name, (level, charging, text) in found.items():
            key = "asus:" + name.lower().replace(" ", "-")
            out.append(DeviceStatus(key, name, level, charging, True, "asus", text, kind="mouse"))
        for name, (level, charging, text) in self._poll_omni(infos).items():
            key = "asus:" + name.lower().replace(" ", "-")
            out.append(DeviceStatus(key, name, level, charging, True, "asus", text,
                                    kind="keyboard"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
