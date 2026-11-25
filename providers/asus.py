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

The Harpe II receiver keeps the captured mi_02&col03 FF01:0001 mouse query
(upstream #364); its cable uses report 0 in 64 bytes. The shared receiver's pair
list can positively identify Azoth 96 HE (1C12, #238), in which case no Harpe
query is sent. There is no sourced paired Harpe PID; do not infer one from USB.

Not included (they use a different collection, report id or byte position in
G-Helper, and none was on hand to test): Keris II Ace / Origin and
Harpe Ace Mini / Extreme on their own receiver or cable, Strix Carry, Gladius II Wireless
and the MD200.

The ROG Strix Go 2.4 headset (0B05:18D6; the wired cable id 0B05:18D7) is a
different family in the same brand. The first port followed @vancinis's G-Helper
branch - whose "byte 13 is the level" reading @berkaykrc's G-Helper work (#6067)
corrected: byte 13 is a constant 0x40 (their UI froze at 64 %), byte 9 is the
charging state, and the level is the ARMOURY CRATE quarter step of the 16-bit
little-endian battery voltage at reply bytes 11-12 (LSB first). The reporter's
frame carries MSB 0x0F and Armoury Crate shows 75 % - the pair this file maps:

    request   feature report 0xFF, 64 bytes: FF 08 00 FD 04 12 F1 03 52 01 00 ...
    reply     feature report: FF 1B ... volt lsb at 11, msb at 12 (>= 3800 mV -> 75 %)
    charging  byte 9 = 0x0A while charging, 0x03 on battery; the wired id always charges
    error     FF AA at bytes 1-2: the packet is not known to the firmware
    zeros     bytes 1-3 all zero, or byte 1 not 0x1B: off, asleep or disconnected

The collection is picked as G-Helper picks it: the first of the receiver's
collections whose feature report is at least 64 bytes long (HidP_GetCaps), not by
interface number or usage page. The voltage steps and the charging byte are the
reference's readings of this model, with the 75 % floor moved from 3850 to 3800 mV
on the reporter's own measurements (3743 mV on battery with Armoury Crate at 50 %,
3831 mV on battery at 75 %). Armoury Crate's panel holds its last number across a
plug or unplug until the level settles (100 % kept after unplugging at 4077 mV,
50 % kept while charging at 3897 mV); this provider shows the live frame instead.
Only frames from a live headset (header 0x1B) are read at all.

The TUF Gaming H3 Wireless (0B05:1963) speaks the same family on its dongle's
interface 3. Source: USB captures of Armoury Crate 6.0.0 reading the headset's Power
page (HaloBattery maintainer captures, Armoury Crate at 100 % and at 75 %). Armoury
Crate polls with this request about every 9 s; only that read is sent here, never
the `FF 08 00 FF 04 0C F1 03 02 xx` write it sends after each read:

    request   feature report 0xFF, 64 bytes: FF 07 00 FD 04 0C F1 02 01 00 ...
    reply     FF 12 05 FE 0C 04 1F 0B 01 03 05 <mV lsb> <mV msb> 00 00 03 68 10
    captured  3970 mV with Armoury Crate at 100 %, 3913-3919 mV at 75 %

Bytes 16-17 (0x1068, 4200 mV) stayed the same in both captures; bytes 11-12 are the
voltage, as on the Strix Go. The level is Solaar's Li-ion curve (the one logitech.py
uses) rounded UP to Armoury Crate's quarter step: 3970 -> 77 -> 100 % and 3916 -> 69
-> 75 %, the two captured readings (the Strix Go's 3831 / 3743 mV -> 54 / 28 % round
up to its reporter's 75 / 50 % the same way).

Byte 9 is the charger: measured on the headset with this provider's own read and all
ASUS software stopped, every frame is 0x03 (byte 10 0x05) on battery (218 of 218)
and 0x05 (byte 10 0x03) on the USB-C cable (217 of 217). While ASUS software
(asus_framework.exe, Armoury Crate) polls the same dongle, its traffic shows through:
on battery a 0x0A frame now and then, and on the cable a rotation of 0x0A, 0x03 and
0x05 frames of about 3 s each (a 0x03 frame ~2.5 s). So 0x05 is charging at once; a
0x03 frame is read again 3 s later, and two 0x03 frames 3 s apart are on battery;
0x0A says nothing. At most two re-reads (6 s), and only while the answer is open;
once two 0x03 frames have said battery, a later poll's 0x03 frame is battery at once
(no wait) until a frame says anything else.
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
from .gwolves import feature_length
from .logitech import voltage_to_percent

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

# the ROG Strix Go 2.4 headset (#190): a feature-report exchange of its own
HEADSET_PID = 0x18D6
HEADSET_WIRED_PID = 0x18D7     # the headset on its USB-C cable: always charging
HEADSET_NAME = "ROG Strix Go 2.4"
HEADSET_REPORT_ID = 0xFF
HEADSET_REQUEST = [0xFF, 0x08, 0x00, 0xFD, 0x04, 0x12, 0xF1, 0x03, 0x52, 0x01]
HEADSET_PACKET_LENGTH = 64
# reply bytes 11-12: the battery voltage, 16-bit little-endian (LSB first); the level
# is the Armoury Crate quarter step it falls into, byte 12 as the reference's guard
# (berkaykrc's G-Helper #6067 corrected the earlier byte-13 read)
HEADSET_VOLTAGE_LSB = 11
HEADSET_VOLTAGE_MSB = 12
HEADSET_LEVEL_STEPS = ((0x10, 4100, 100), (0x0F, 3800, 75), (None, 3700, 50),
                       (0x0E, 3500, 25))
HEADSET_LEVEL_LOW = 10
HEADSET_CHARGING_BYTE = 9
HEADSET_CHARGING_VALUE = 0x0A
# The charger lifts the pack's voltage by about 160 mV (the reporter's own pairs: 3743
# -> 3897 mV and 3665 -> 3828 mV within a minute of plugging in), so the live table read
# one step high while charging (#190). Below the CV plateau the lift is removed before
# the table; at or above it the pack is essentially full and the raw value is right.
HEADSET_CHARGING_LIFT_MV = 160
HEADSET_CHARGING_FULL_MV = 4100
HEADSET_HEADER = 0x1B          # reply byte 1 of a live frame; anything else = gone
HEADSET_SLEEP = 0.035                # G-Helper waits 35 ms between send and read
HEADSET_ATTEMPTS = 3                 # its retry count on a read timeout
HEADSET_ERROR = (0xFF, 0xAA)         # reply bytes 1-2: packet not known to firmware

# the TUF Gaming H3 Wireless (docstring): Armoury Crate captures, read request only
TUF_H3_PID = 0x1963
TUF_H3_NAME = "TUF Gaming H3 Wireless"
TUF_H3_REQUEST = [0xFF, 0x07, 0x00, 0xFD, 0x04, 0x0C, 0xF1, 0x02, 0x01]
TUF_H3_HEADER = 0x12                 # reply byte 1 of the battery frame
TUF_H3_MV_RANGE = (3000, 4500)       # one Li-ion cell; anything else is not a reading
# byte 9 (docstring): 0x03 on battery, 0x05 on the cable; other values (0x0A while ASUS
# software talks to the dongle too) say nothing about the charger
TUF_H3_STEP = 25                      # Armoury Crate's quarter steps (docstring)
TUF_H3_CHARGING = {0x03: False, 0x05: True}
TUF_H3_RECHECK_S = 3.0               # a 0x03 frame lasts ~2.5 s on the cable (docstring)
TUF_H3_RECHECKS = 2

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
    0x1C5A: ("TX Gaming Mouse Mini Miku", PERCENT),
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
    0x1C25: ("ROG Azoth X", 6),        # #269: reporter's OMNI pair list, byte 6
    0x1AB0: ("ROG Strix Scope II 96 Wireless", 6),
    0x1B7A: ("ROG Strix Scope II 96 RX Wireless", 6),
    0x1B06: ("ROG Falchion RX Low Profile", 11),
    # #340: the pair list's own id for the Morph 96 ("01 a0 00 01 00 ea 1c 02 04");
    # G-Helper has no entry for it yet, so it takes the family default byte 6 -
    # unverified until the reporter's test build is read
    0x1CEA: ("ROG Strix Morph 96 Wireless", 6),
}

# G-Helper at f62d584, app/Peripherals/Mouse/Models/HarpeIIAce.cs and
# AsusMouse.cs: report 3 on mi_02&col03 of the 1ad0 SPEEDNOVA receiver,
# report 0 on interface 0 over the 1c69 cable, both 64 bytes (upstream #364).
HARPE_II_RECEIVER, HARPE_II_CABLE = 0x1AD0, 0x1C69

# #238's Gear Link capture and reporter-confirmed azoth96he-test2 implementation:
# https://github.com/HeyOkay/HaloBattery/issues/238#issuecomment-6876578460
# https://github.com/casparas123/HaloBattery/blob/azoth96he-test2/providers/asus.py#L470-L499
# Interface 2, FF02:0001 accepts the 64-byte 01 A0 00 00 pair-list request;
# paired PID 1C12 accepts 02 12 01 on FF00:0001, level byte 6, charging byte 9.
SPEEDNOVA_PID = HARPE_II_RECEIVER
SPEEDNOVA_KEYBOARD_PID = 0x1C12
SPEEDNOVA_KEYBOARD_NAME = "ROG Azoth 96 HE"

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


def _headset_frame(r, header: int) -> Optional[Tuple[int, Optional[int]]]:
    """(voltage mV at bytes 11-12, byte 9) from a headset feature-report reply whose
    byte 1 is `header`; None for the error marker, all-zero bytes 1-3, another header
    or a reply too short to hold the voltage. Positions count the report id 0xFF;
    a reply without it is read one byte earlier."""
    if not r or len(r) < 4:
        return None
    skip = 0 if r[0] == HEADSET_REPORT_ID else 1

    def at(n):                       # the byte G-Helper's buffer has at n
        i = n - skip
        return r[i] if 0 <= i < len(r) else None

    if at(1) == HEADSET_ERROR[0] and at(2) == HEADSET_ERROR[1]:
        return None
    if at(1) == 0 and at(2) == 0 and at(3) == 0:
        return None
    if at(1) != header:
        return None                  # the header dropped: not a live frame
    lsb, msb = at(HEADSET_VOLTAGE_LSB), at(HEADSET_VOLTAGE_MSB)
    if lsb is None or msb is None:
        return None
    return (msb << 8) | lsb, at(HEADSET_CHARGING_BYTE)


def parse_tuf_h3_reply(r) -> Optional[Tuple[int, Optional[bool]]]:
    """(level, charging or None = unknown) from a TUF Gaming H3 Wireless reply
    (docstring), or None when this is not a battery frame or the voltage is not one
    Li-ion cell's."""
    frame = _headset_frame(r, TUF_H3_HEADER)
    if frame is None:
        return None
    mv, state = frame
    if not TUF_H3_MV_RANGE[0] <= mv <= TUF_H3_MV_RANGE[1]:
        return None
    # shortcut: no charger-lift correction (the Strix Go's 160 mV is that model's),
    # so the level may read high while charging; calibrate from a capture on the cable
    level = -(-voltage_to_percent(mv) // TUF_H3_STEP) * TUF_H3_STEP   # round up
    return level, TUF_H3_CHARGING.get(state)


def parse_headset_reply(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a Strix Go 2.4 reply, or None when this is not one.

    Byte 13 is NOT the level - it is a constant 0x40 (64), which is what the first
    port read while Armoury Crate showed a different number (#190). The level is
    the Armoury Crate quarter step of the battery voltage at reply bytes 11-12,
    16-bit little-endian (LSB first, byte 12's high part as the reference's guard);
    byte 9 is 0x0A while charging and 0x03 on battery; while charging, the charger's
    ~160 mV lift is removed before the table (see HEADSET_CHARGING_LIFT_MV), so
    plugging the cable no longer jumps the level up a step. hidapi keeps the report id
    of a numbered feature report in front as well; a reply without it is accepted
    too, with the same positions applied to the bytes after it."""
    frame = _headset_frame(r, HEADSET_HEADER)
    if frame is None:
        return None
    mv, state = frame
    msb = mv >> 8
    charging = state == HEADSET_CHARGING_VALUE
    if charging and mv < HEADSET_CHARGING_FULL_MV:
        mv = max(0, mv - HEADSET_CHARGING_LIFT_MV)
        msb = mv >> 8
    for nibble, ceil_mv, level in HEADSET_LEVEL_STEPS:
        if (nibble is not None and msb >= nibble) or mv >= ceil_mv:
            return level, charging
    return HEADSET_LEVEL_LOW, charging


# (name, pids, request, parse, recheck) of each headset read with a feature-report
# exchange; recheck: the charging state is settled over a few frames (_settle_charging)
HEADSETS = ((HEADSET_NAME, (HEADSET_PID, HEADSET_WIRED_PID), HEADSET_REQUEST,
             parse_headset_reply, False),
            (TUF_H3_NAME, (TUF_H3_PID,), TUF_H3_REQUEST, parse_tuf_h3_reply, True))


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
        self._settled: Dict[object, bool] = {}   # path -> last settled TUF H3 charging

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
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    read: {e}")
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

    def _poll_speednova_keyboard(self, infos: List[dict]):
        """Ask only the captured Azoth 96 HE identity on its own receiver channels."""
        receivers: Dict[str, Dict[int, bytes]] = {}
        for d in infos:
            if (d.get("product_id") != SPEEDNOVA_PID
                    or d.get("interface_number") != OMNI_INTERFACE
                    or d.get("usage") != 0x0001
                    or d.get("usage_page") not in (OMNI_CONTROL, OMNI_KEYBOARD)):
                continue
            receivers.setdefault(_instance(d["path"]), {})[d["usage_page"]] = d["path"]

        out: List[Tuple[str, str, Reading]] = []
        keyboard_receivers = set()
        for instance, cols in receivers.items():
            if OMNI_CONTROL not in cols:
                continue
            self._diag.append(f"[ASUS] ROG SpeedNova receiver pid={SPEEDNOVA_PID:04x}")
            r = self._ask(cols[OMNI_CONTROL], OMNI_PAIRS, lambda r: bool(parse_pairs(r)),
                          OMNI_PACKET_LENGTH)
            # A partial pair record must not authorize a model-specific query.
            pids = parse_pairs(r) if r is not None and len(r) == OMNI_PACKET_LENGTH else []
            self._diag.append("  paired: " + (" ".join(f"{p:04x}" for p in pids) or "none"))
            if SPEEDNOVA_KEYBOARD_PID not in pids:
                continue
            keyboard_receivers.add(instance)
            if OMNI_KEYBOARD not in cols:
                continue
            self._diag.append(f"  keyboard {SPEEDNOVA_KEYBOARD_NAME} pid={SPEEDNOVA_KEYBOARD_PID:04x}")
            r = self._ask(cols[OMNI_KEYBOARD], OMNI_KEYBOARD_REQUEST, is_omni_keyboard_reply,
                          OMNI_PACKET_LENGTH)
            res = self._parsed(r, lambda r: parse_omni_keyboard(r, 6))
            if res is not None:
                out.append((SPEEDNOVA_KEYBOARD_NAME, "keyboard", res))
        return out, keyboard_receivers

    def _read_headset(self, path, request=HEADSET_REQUEST,
                      parse=parse_headset_reply) -> Optional[Tuple[int, Optional[bool]]]:
        """One feature-report exchange with a headset (a mirror of G-Helper's
        WriteForResponse for the ROG Strix Go 2.4): send the packet, wait 35 ms, read
        the feature report back; a read that comes back empty re-sends, up to its
        retry count. None for the error marker, for a reply whose header is not a
        live headset's, and for an all-zero reply (off or asleep)."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            request = request + [0x00] * (HEADSET_PACKET_LENGTH - len(request))
            for attempt in range(HEADSET_ATTEMPTS):
                try:
                    dev.send_feature_report(request)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    send: {e}")
                    return None
                time.sleep(HEADSET_SLEEP)
                try:
                    r = list(dev.get_feature_report(HEADSET_REPORT_ID,
                                                    HEADSET_PACKET_LENGTH) or [])
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"    read (attempt {attempt + 1}): {e}")
                    continue
                if not r:
                    self._diag.append(f"    read (attempt {attempt + 1}): empty")
                    continue
                self._diag.append(f"    reply: {hexdump(r, 16)}")
                res = parse(r)
                if res is None:
                    self._diag.append("    not a battery reply (error, off/asleep or "
                                      "not a live headset frame)")
                return res
            self._diag.append("    no feature-report reply")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _settle_charging(self, path, request, parse, res):
        """The TUF H3's charging state (docstring): a 0x05 frame is the cable at once;
        otherwise the frame is read again TUF_H3_RECHECK_S later, up to TUF_H3_RECHECKS
        times, until two "on battery" frames that far apart say battery. The level is
        the last reading's; charging stays unknown if no frame said either way."""
        if res is None:
            return None                      # off or asleep: nothing to settle
        if res[1] is False and self._settled.get(path) is False:
            return res                       # still on battery: no re-read
        seen = [res]
        for _ in range(TUF_H3_RECHECKS):
            states = [r[1] for r in seen]
            if True in states or states.count(False) >= 2:
                break
            time.sleep(TUF_H3_RECHECK_S)
            more = self._read_headset(path, request, parse)
            if more is not None:
                seen.append(more)
        states = [r[1] for r in seen]
        charging = True if True in states else (False if states.count(False) >= 2
                                                 else None)
        if charging is not None:
            self._settled[path] = charging
        return seen[-1][0], charging

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

        speednova, keyboard_receivers = self._poll_speednova_keyboard(infos)
        for d in infos:
            pid = d.get("product_id")
            if pid == HARPE_II_RECEIVER:
                path = d.get("path", b"")
                shown = path.decode("utf-8", "replace") if isinstance(path, bytes) else str(path)
                if (_instance(path) in keyboard_receivers
                        or d.get("interface_number") != OMNI_INTERFACE
                        or (d.get("usage_page"), d.get("usage")) != (OMNI_MOUSE, 1)
                        or "mi_02&col03" not in shown.lower()):
                    continue
                request, wanted, parse = OMNI_MOUSE_REQUEST, is_omni_mouse_reply, parse_omni_mouse
            elif pid == HARPE_II_CABLE:
                if d.get("interface_number") != INTERFACE or d.get("usage_page", 0) < 0xFF00:
                    continue
                request, wanted = REQUEST, has_echo
                parse = lambda r: parse_reply(r, PERCENT)
            else:
                continue
            name = "ROG Harpe II Extreme" if "EXTREME" in (d.get("product_string") or "").upper() else "ROG Harpe II Ace"
            self._diag.append(f"[ASUS] {name} pid={pid:04x}")
            res = self._parsed(self._ask(d["path"], request, wanted, 64), parse)
            if res is not None:
                keep(name, "mouse", res)

        # A positive paired Azoth identity takes precedence over the legacy
        # Harpe receiver route. Do not invent a paired Harpe PID.
        for name, kind, res in speednova:
            self._diag.append(f"  -> {name}: {res[0]}%{' charging' if res[1] else ''}")
            keep(name, kind, res)

        for name, kind, res in self._poll_omni(infos):
            self._diag.append(f"  -> {name}: {res[0]}%{' charging' if res[1] else ''}")
            keep(name, kind, res)

        out: List[DeviceStatus] = []
        for name, (level, charging, text) in found.items():
            key = "asus:" + name.lower().replace(" ", "-")
            out.append(DeviceStatus(key, name, level, charging, True, "asus", text, kind=kinds[name],
                                    ui_message="state.approx" if text else "",
                                    ui_params={"level": level, "charging": charging} if text else {}))
        # the headsets (docstring): their own collection rule and an icon key of their own
        for hname, pids, request, parse, recheck in HEADSETS:
            hinfos = [d for d in infos if d["product_id"] in pids]
            if not hinfos:
                continue
            chosen = None
            for d in hinfos:
                n = feature_length(d["path"])
                if n is None or n >= HEADSET_PACKET_LENGTH:
                    chosen = d
                    break
                self._diag.append(f"  {hname} "
                                  f"{d.get('usage_page', 0):04x}:"
                                  f"{d.get('usage', 0):04x} feature={n}: too short; "
                                  "skipped")
            if chosen is None:
                self._diag.append(f"[ASUS] {hname}: no collection with a "
                                  "64-byte feature report")
                continue
            self._diag.append(f"[ASUS] {hname} pid={chosen['product_id']:04x} "
                              f"usage={chosen.get('usage_page', 0):04x}:"
                              f"{chosen.get('usage', 0):04x}")
            res = self._read_headset(chosen["path"], request, parse)
            if recheck:
                res = self._settle_charging(chosen["path"], request, parse, res)
            if res is None:
                continue
            level, charging = res
            if chosen["product_id"] == HEADSET_WIRED_PID:
                charging = True        # cable mode draws power by definition
            self._diag.append(f"  -> {level}%{' charging' if charging else ''}"
                              f"{' (charging unknown)' if charging is None else ''}")
            out.append(DeviceStatus("asus:" + hname.lower().replace(" ", "-"),
                                    hname, level, bool(charging), True, "asus",
                                    kind="headset", charging_known=charging is not None))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
