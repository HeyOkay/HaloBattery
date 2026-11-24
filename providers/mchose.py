"""MCHOSE wireless mice on the 2.4 GHz receiver (M7 Ultra 5253:1020 confirmed on hardware).

Protocol from MCHOSE's own driver, recovered and documented by @alexfrih in
github.com/alexfrih/mchose-linux (PROTOCOL.md, from the M HUB web bundle), which covers
the 0x5253 family: L7/L7 Pro/L7 Ultra, M7, A7, K7. What this mouse forced on top of that
document, all of it measured on the M7 Ultra with firmware 5.2.7.0:

  * the configuration collection is usage page 0xFF01. The sibling collection on the same
    interface (0xFF0B, usage 0x104) never answers anything, on any report id or length.
  * two report ids: 0x11 (20 bytes) and 0x12 (64 bytes), both *feature* reports. Reads are
    written as feature reports too; output reports are not used.
  * every payload byte is inverted (^ 0xFF), padding included, so a zero tail goes on the
    wire as FF FF FF.
  * a reply echoes cmd ^ 0xFF in byte 1 and inverts its payload from byte 2.
  * 0x06 returns: vid:u16, model:u16, firmware:u32, flags:u8, level:u8, charging:u8 and
    then a second level/charging pair with the same values. Charging is 1 while the mouse
    is on the cable, 0 otherwise. The flags byte reads as the radio link state (0x09 on
    the dongle, 0x00 while wired).
  * **it must be re-asked for every attempt.** One request followed by repeated reads only
    ever returns the request itself (raw, then decoded, alternating) - the reference
    driver's "retry the read" advice is what makes this work here, and about one exchange
    in two carries a real answer anyway.
  * **the receiver only answers while the mouse itself is awake.** Asleep, it still answers
    the frame, but with an all-zero payload: measured here as eight seconds of zeros
    followed by the real 100% the moment the mouse was moved. It is the mouse's idle timer
    that decides, so a silent mouse is a normal state - keep the last value on a greyed-out
    icon, exactly like a Razer mouse that has gone idle, rather than calling it a fault.
  * the receiver also pushes a device-info notice on input report 0x13 (01 01 00 <level>
    02 03 06 <..> "M7 Ultra"), which independently reported the same level.

Also measured on the cable: the mouse answers on its own wired PID (5253:0031 - the same
number it reports as the model id) with the same collection and the same command, while
the receiver goes quiet, so the charging source wins and the icon stays single.

The G7 is a different chip on a different vendor id (A8A5:2255, 'YJX-CHIP', while the
0x5253 receivers are RealTek) and speaks a different protocol, worked out by @kek353 from
their own monitor and the HID dump in issue #8: a 65-byte output report starting
``00 55 30 A5 0B 2E 01 01 01``, answered by an input report starting ``AA 30`` whose byte 8
is the level and byte 9 the charging flag. Its vendor collections are 0xFFA5:0x88,
0xFF05:0x88 and 0xFF01:0x10; only the last one is written to, as in @kek353's monitor.
**Confirmed on @kek353's own G7** (issue #8): the probe answers
``aa 30 a5 0b 0a 01 01 01 2e 00 00 00``, so byte 8 = 0x2E = 46% and byte 9 = 0 while the
mouse sits on its dongle, and that level is the one their tool shows. On its cable the same
read answers ``aa 30 a5 3c 0a 01 01 01 2e 01 00 00`` (byte 9 = 1 while charging) with the
PID unchanged, so a G7 keeps a single icon the way the M7 Ultra does.

The generic 0x3837 mouse query is limited to the A7 V2 Ultra identity from issue #4
and PR #20: 3837:100b, configuration collection 0xFF01:0x01 on interface 2.
The A7 V2 Pro (3837:100a) exposes the same collection and answers the same 0x06
status read (measured on one unit: 85 %, not charging, on report 0x11). On its
cable it enumerates as 3837:4018 and the same read reports charging while the
receiver goes quiet, so one icon follows it between the two.
https://github.com/HeyOkay/HaloBattery/issues/4
https://github.com/HeyOkay/HaloBattery/pull/20
The reference documents the same short 0x11 status read, but the vendor id alone
does not identify its protocol: issue #189's 3837:3033 keyboard and 3837:1016
mouse use output report 0x55 instead. Unknown 0x3837 products and unrelated
collections are therefore left unopened by this legacy mouse query; the
separately sourced 6008/600A headset routes below remain enabled.

The first-generation A5 Pro Max (0x2023 - the 1K receiver F013, the 4K receiver F015 and the
wired mouse F019; issue #149) speaks a third exchange, taken from the community web driver
z750sasr/mchose-a5-pro-max-web-driver (MIT, whose transport the independent Klegus/mchose-macos
table agrees with for the same ids): 64-byte *feature* reports on the vendor collection, usage
page 0xFFFF preferred, report id 0. The request carries route 2 (mouse) at byte 2, its own
length 2 at byte 3, page 0 at byte 4 and command 0x83 at byte 5; the reply is the report read
back, starting with the 0xA1 marker and echoing page and command at its bytes 4 and 5, with
charging in byte 6 (non-zero) and the percentage in byte 7. Values above 100 are
refused rather than shown as a full battery. The reference writes once, waits its 34 ms,
then reads back up to eight times with one re-send in the middle; silent means the mouse
is asleep. **Confirmed on the
reporter's hardware** (@Pekish992, #149): his run showed 22 % and then 25 % while charging,
matching the MCHOSE software and its charging animation. His diagnostics pinned the receiver
(2023:f013, product string 'MCHOSE A5 2.4G', vendor collections ffa0:0001 and ffff:0001 on
interface 1, ffff:0000 on interface 2); the 4K receiver and the wired ids are still untried.

The V9 Turbo+ headset (issue #193) is an audio product on the same 0x3837 vendor id, and
M HUB's own driver reads it differently from the mice: for names on its audio list it sends an
AA-framed request on output report 0x55 (cmd 0x0B) and decodes the reply's payload as
``sleepState, bit7 charging | percent``; the non-audio devices get the 63-byte ``65 01`` frame
on the same report reported as level byte 1 / state byte 2 (2 discharging, 3 charging, 4 full,
26 asleep) in issue #189. A community driver for a V9 headset (github.com/JoaoKSS/
MCHOSE_v9_PRO_Controller) reads its battery with that same ``65 01`` frame, one 64-byte write
and one read, so both are tried here, the AA frame first as in M HUB. The headset presents as
two devices over one product - the headset itself (3837:600A, C-Media strings) and its dongle
(3837:6008, WCH strings) - so both sides of that pair share one icon.

The K7 V2 Ultra+ receiver (3837:1018, interface 2 FF01:0001) uses M HUB's
64-byte output report 4D / command 0900 instead of the legacy feature frame.
Source: MCHOSE M HUB assets/main-DQZs6ZzX.js (SHA256 pinned beside MC_TARGETS),
getCommandBuffer, readDeviceInfo and deviceInfoToObj; PR #403 at c4eb9eeb
records the 85% input reply and confirms chargeStatus 0/1. Only this captured
USB target is enabled. Other newer PIDs receive neither this request nor a
guessed legacy fallback.

The X9 headset (3837:6045, C-Media strings) is listed in the same M HUB web bundle
(www.mchose.com.cn, ``assets/main-DQZs6ZzX.js``, fetched 2026-11-17) as
``createAudioHidProduct({modelKey:"MCHOSE X9", vendorId:14391, productId:24645,
connectionType:WIRELESS, protocolProfile:AUDIO_PROTOCOL_PROFILE.BLK})``. The BLK profile's
``audio.blk.readBattery`` is the ``65 01`` frame above: output report 0x55
(``BLK_BATTERY_REPORT_ID=85``), 63 data bytes, ``[0]=101`` (``BLK_BATTERY_COMMAND_ID``) and
``[1]=1``. Its ``parseBatteryResponse`` reads the level from data byte 1 and the state from
byte 2 (3 charging, 4 full, 2 discharge, 26 asleep), the decode ``parse_status_55`` already
does. The AA frame (cmd 0x0B) is M HUB's MC profile (``audio.mc.readBattery``), which the
X9 is not on, so the X9 is only sent ``65 01``.
Measured on an X9 on its dongle: report 0x55 lives on its ff90:0001 collection, the only one
written to, and the read answers ``55 65 28 02`` (40 %, discharging) within 30 ms. On its
cable the headset drops its radio link - the dongle stops answering - and shows up as a
second USB device, 3837:6044 'MCHOSE X9 HEADSET', which M HUB does not list: nothing is
written to it, so a charging X9 keeps its last level greyed out (ASLEEP_KEEP) and no
charging state is invented. State 3 is decoded as charging the way M HUB decodes it, but it
has never been seen: M HUB loses the cabled headset the same way. The level is confirmed on
Windows too, by @Kolomhon with test builds carrying this code (#458).

Not verified: the legacy 0x3837 family (the device in issue #4 is not here), other models, and the
meaning of the second level/charging pair in the 0x5253 reply (it has matched the first pair
in every reading so far). The V9 Turbo+ is confirmed on @IsonanDough's hardware (#193): the
level showed 100 %, matching M HUB. Its charging state cannot be read on any of its
channels - on its cable it answers neither read, and on its dock it reports as on battery -
so a charging headset keeps its last level greyed out and no charging state is invented.
"""
from __future__ import annotations

import time
from collections import deque
from statistics import median
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

# 0x5253 is the family measured here and keeps the plain icon key. Only the
# individually sourced newer-vendor targets below are allowed active requests.
MEASURED_VID = 0x5253
NEW_VID = 0x3837
MCHOSE_VIDS = (MEASURED_VID, NEW_VID)

# model ids seen in the 0x06 reply (the receiver's own PID does not identify the mouse:
# 0x1020 is used by the M7 Ultra and by the L7 Pro)
MODEL_NAMES = {
    0x0031: "MCHOSE M7 Ultra",
}

# The G7: another chip (0xA8A5, 'YJX-CHIP') and another protocol, from @kek353's monitor
# and the HID dump in issue #8 - see the module docstring. Unverified: no device here.
G7_VID = 0xA8A5
G7_PID = 0x2255
G7_REQUEST = bytes([0x00, 0x55, 0x30, 0xA5, 0x0B, 0x2E, 0x01, 0x01, 0x01]).ljust(65, b"\x00")
G7_HEADER = b"\xaa\x30"
G7_LEVEL_BYTE = 8
G7_CHARGE_BYTE = 9
G7_READS = 25                 # a non-blocking read loop, ~0.5 s at G7_READ_GAP
G7_READ_GAP = 0.02

# The first-generation A5 Pro Max: a third exchange on vendor id 0x2023 (see the module
# docstring). One icon for the mouse whether it sits on a dongle or on its cable.
A5_VID = 0x2023
A5_PIDS = {
    0xF013: "MCHOSE A5 Pro Max (1K receiver)",
    0xF015: "MCHOSE A5 Pro Max (4K receiver)",
    0xF019: "MCHOSE A5 Pro Max (wired)",
}
A5_USAGE_PAGE = 0xFFFF        # the page the reference prefers
A5_REPORT_ID = 0x00           # what both references report on Windows
A5_PAGE = 0x00
A5_CMD_BATTERY = 0x83
A5_ROUTE = 0x02               # 2 = mouse-level commands (1 = pairing, 0 = receiver-local)
A5_LEN = 2                    # the request's own length announcement
A5_READS = 8                  # the reference's bounded response loop
A5_RESEND_AT = 3              # it re-sends once per loop
A5_WRITE_PAUSE = 0.034        # ... and waits its own 34 ms after a write
A5_READ_GAP = 0.03

# The V9 Turbo+ headset (issue #193): one product over two USB devices.
HEADSET_DONGLE_PID = 0x6008
HEADSET_BODY_PID = 0x600A
# The X9 headset: M HUB's BLK audio profile on one USB device (see the module docstring).
X9_PID = 0x6045
X9_CABLE_PID = 0x6044         # the X9 on its cable: not in M HUB, never written to
HEADSET_PIDS = (HEADSET_DONGLE_PID, HEADSET_BODY_PID, X9_PID)
HEADSET_PAIR = {0x6008: 0x6008, 0x600A: 0x6008,   # a dongle and a headset, one icon
                X9_PID: X9_PID}
AUDIO_REPORT = 0x55
AUDIO_LEN = 63                # payload bytes; the report id rides in front of them
AUDIO_CMD_BATTERY = 0x0B
# M HUB's own request for the battery: start 0xAA, version 1, checksum off (flags 0),
# length (payload + 1) 1, cmdType Request [0, 1], cmd 0x0B, checksum slot 0
AUDIO_REQUEST = bytes([0xAA, 0x01, 0x00, 0x01, 0x00, 0x01, AUDIO_CMD_BATTERY, 0x00]).ljust(AUDIO_LEN, b"\x00")
# the status read the sister-model community driver uses, same report
STATUS_REQUEST = bytes([0x65, 0x01]).ljust(AUDIO_LEN, b"\x00")
STATUS_DISCHARGING = 2
STATUS_CHARGING = 3
STATUS_FULL = 4
STATUS_ASLEEP = 26
HEADSET_ATTEMPTS = 2
HEADSET_READ_MS = 400         # a tucked-in device answers in milliseconds

CONFIG_PAGE = 0xFF01          # the only collection that answers
# #4's diagnostics and #20 name this exact A7 V2 Ultra collection. Neither
# another PID nor a vendor page is permission to send the same mouse frame.
# The A7 V2 Pro (3837:100A) is measured on one unit: the same interface 2
# FF01:0001 collection answers the 0x06 status read on report 0x11 with
# 11 f9 c8 c7 e7 bf fa d5 fd fb f6 aa ff a8 ff ff -> 85 %, not charging.
# On its cable the mouse is 3837:4018 (the model id it reports) and the same
# read answers 11 f9 c8 c7 e7 bf fa d5 fd fb ff aa fe aa ff ff -> 85 %,
# charging; the receiver goes quiet then, as the M7 Ultra's does. Both share
# the family key, so one icon follows the mouse between the two.
NEW_MOUSE_TARGETS = {0x100B: (2, CONFIG_PAGE, 0x0001),
                     0x100A: (2, CONFIG_PAGE, 0x0001),
                     0x4018: (2, CONFIG_PAGE, 0x0001)}
# K7 V2 Ultra+ / MagDock, captured in PR #403 (head c4eb9eeba7ea9663).
# M HUB's getCommandBuffer / readDeviceInfo / deviceInfoToObj in
# https://www.mchose.com.cn/assets/main-DQZs6ZzX.js, SHA256
# 52b25bf9cdb449b84c69f50970c1612a2e33b3c98b7caffd03c1c8e9dca1ce1a.
# Only the captured 3837:1018 interface 2 FF01:0001 gets output report 4D.
# Neither other newMouseWirelessPids nor the legacy 06 frame is inferred here.
MC_TARGETS = {0x1018: (2, CONFIG_PAGE, 0x0001)}
MC_LEN, MC_HEADER = 64, 8
MC_ATTEMPTS, MC_READS, MC_READ_MS = 3, 5, 200
SHORT_REPORT = 0x11
LONG_REPORT = 0x12
SHORT_LEN = 20                # payload bytes on 0x11, 64 on 0x12 (the frame adds the id)
LONG_LEN = 64
CMD_STATUS = 0x06
CMD_BOND = 0x03

# The status read is documented on the short report and was also captured there on the M7
# Ultra, so it is tried first (cheaper: 21 bytes on the wire instead of 65); the long
# report is the channel this provider polled on before, kept as the fallback.
CHANNELS = ((SHORT_REPORT, SHORT_LEN), (LONG_REPORT, LONG_LEN))

# Re-ask rather than re-read (see the module docstring): the real answer normally arrives
# on the second exchange. An awake mouse answers within a couple of exchanges; a sleeping
# one answers nothing at all, so a long budget would only slow the poll down.
ATTEMPTS = 4
ATTEMPT_GAP = 0.12

# how long a silent mouse keeps its (greyed-out) icon before it is hidden, s
ASLEEP_KEEP = 300


def _invert(data: bytes) -> bytes:
    return bytes(b ^ 0xFF for b in data)


def make_request(cmd: int, report: int = LONG_REPORT, length: int = 64) -> bytes:
    """A request frame: report id, then cmd and a zero tail, all payload bytes inverted."""
    body = bytes([cmd]).ljust(length, b"\x00")[:length]
    return bytes([report]) + _invert(body)


def parse_status(resp) -> Optional[Tuple[int, int, int, int]]:
    """(level, charging, model, flags) from a status reply, or None if it is not one.

    The same buffer answers with all zeros, or with the request echoed back, between real
    answers, so a reply counts only when it echoes cmd ^ 0xFF *and* its payload carries a
    MCHOSE vid *and* the level is in range.
    """
    if not resp or len(resp) < 13:
        return None
    r = list(resp)
    if r[0] not in (SHORT_REPORT, LONG_REPORT) or r[1] != (CMD_STATUS ^ 0xFF):
        return None
    pay = _invert(bytes(r[2:]))
    if len(pay) < 11:
        return None
    vid = int.from_bytes(pay[0:2], "little")
    if vid not in MCHOSE_VIDS:
        return None
    model = int.from_bytes(pay[2:4], "little")
    flags, level, charge = pay[8], pay[9], pay[10]
    if level > 100:
        return None
    return level, charge, model, flags


def _mc_xor(data) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


def make_mc_request() -> bytes:
    """M HUB's no-data 0900 request, report ID included, with XOR enabled."""
    return bytes.fromhex("4d 01 01 00 00 09 00 00 08") + bytes(55)


def mc_payload(resp) -> Optional[bytes]:
    """Accept only a bounded, checksum-enabled 0900 response."""
    if (not resp or not 10 <= len(resp) <= MC_LEN
            or any(type(byte) is not int or not 0 <= byte <= 255 for byte in resp)):
        return None
    data = bytes(resp)
    if data[:3] != b"\x4d\x01\x01" or data[4:6] != b"\x00\x09":
        return None
    end = MC_HEADER + data[3]
    if data[3] == 0 or end >= len(data):
        return None
    if data[end] != _mc_xor(data[2:end]) or any(data[end + 1:]):
        return None
    return data[MC_HEADER:end]


def parse_mc_info(resp) -> Optional[Tuple[int, bool]]:
    """Captured device-info payload: 0 is unlinked, 1/0 are known charge states."""
    payload = mc_payload(resp)
    if (payload is None or len(payload) < 13 or payload[0] == 0xFF
            or payload[:2] != b"\x37\x38"
            or payload[11] not in (0, 1) or not 0 < payload[12] <= 100):
        return None
    return payload[12], payload[11] == 1


def parse_g7(resp) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a G7 reply, or None if it is not one.

    Layout from @kek353's monitor and the HID dump in issue #8: the frame starts AA 30,
    the level is byte 8 and the charging flag byte 9. Confirmed on @kek353's G7, on the
    dongle and on its cable: `aa 30 a5 0b 0a 01 01 01 2e 00 00 00` (46%, not charging) and
    `aa 30 a5 3c 0a 01 01 01 2e 01 00 00` (46%, charging). Only the two-byte AA 30 header is
    relied on - byte 3 differs between those two (0x0b against 0x3c) - and the same PID
    (A8A5:2255) on the same 0xFF01 collection answers either way, which is what keeps one
    icon for a G7 on its dongle and on its cable. An out-of-range level is refused rather
    than reported as a made-up number.
    """
    if not resp or len(resp) <= G7_CHARGE_BYTE:
        return None
    r = bytes(resp)
    if not r.startswith(G7_HEADER):
        return None
    level = r[G7_LEVEL_BYTE]
    if level > 100:
        return None
    return level, bool(r[G7_CHARGE_BYTE])


def make_a5_request() -> List[int]:
    """The reference's frame: its report id 0, then route 2 / length 2 / page 0 / 0x83.

    hidapi wants the report id as the first byte, so the reference's own payload - route
    at its byte 2, length at 3, page at 4, command at 5 - sits one byte in.
    """
    frame = [0x00, 0x00, 0x00, A5_ROUTE, A5_LEN, A5_PAGE, A5_CMD_BATTERY]
    return frame + [0x00] * 58


def parse_a5(resp) -> Optional[Tuple[int, bool]]:
    """(level, charging) from the A5's reply, or None.

    The reply starts with the 0xA1 marker (hidapi may keep the report id in front of it)
    and echoes page and command; charging is byte 6, the percentage byte 7.
    A percentage above 100 is not a usable reading.
    """
    if not resp or len(resp) < 9:
        return None
    r = bytes(resp)
    at = 0 if r[0] == 0xA1 else (1 if r[1] == 0xA1 else None)
    if at is None or len(r) < at + 8:
        return None
    if r[at + 4] != A5_PAGE or r[at + 5] != A5_CMD_BATTERY:
        return None
    level = r[at + 7]
    if level > 100:
        return None
    return level, r[at + 6] > 0


def audio_checksum_ok(frame: bytes) -> bool:
    """M HUB's frame check: flags, length, cmdType, cmd and payload XORed together.

    The reference computes the checksum only when the frame's flags say it is on
    (flags == 1); a frame that says it is off must carry 0 there.
    """
    if len(frame) < 8:
        return False
    flags, length = frame[2], frame[3]
    if length < 1 or len(frame) < 7 + length:
        return False
    acc = flags ^ length ^ frame[4] ^ frame[5] ^ frame[6]
    for b in frame[7:7 + length - 1]:
        acc ^= b
    expected = acc if flags == 1 else 0
    return frame[7 + length - 1] == expected


def parse_audio(resp) -> Optional[Tuple[Optional[int], bool]]:
    """(level, charging) from an AA-framed battery reply on report 0x55, or None.

    Layout from M HUB's own senders (cmdType Response [0, 1], cmd 0x0B) and its
    battery parser: the payload is ``sleepState`` then ``bit7 charging | percent``.
    A sleeping headset answers with sleepState 0, which comes back as a level of
    None so the caller can grey the icon out instead of showing a number.
    """
    if not resp or len(resp) < 11:
        return None
    data = bytes(resp)
    if data[0] != AUDIO_REPORT or data[1] != 0xAA:
        return None
    if data[5] != 0x00 or data[6] != 0x01 or data[7] != AUDIO_CMD_BATTERY:
        return None
    if not audio_checksum_ok(data[1:]):
        return None
    length = data[4]
    if length < 3:
        return None
    payload = data[8:8 + length - 1]
    if payload[0] == 0:
        return None, False
    percent = payload[1] & 0x7F
    if percent > 100:
        return None
    return percent, (payload[1] >> 7) == 1


def parse_status_55(resp) -> Optional[Tuple[Optional[int], bool]]:
    """(level, charging) from the '65 01' status reply on report 0x55, or None.

    The reply echoes 0x65 in its first data byte; level is byte 2 and the state
    byte 3 - 2 discharging, 3 charging, 4 full, 26 asleep - exactly what M HUB's
    charge-status listener reads on the non-audio 0x3837 devices (issue #189),
    and what github.com/JoaoKSS/MCHOSE_v9_PRO_Controller reads on a V9 headset.
    An asleep headset comes back as a level of None.
    """
    if not resp or len(resp) < 4:
        return None
    data = bytes(resp)
    if data[0] != AUDIO_REPORT or data[1] != 0x65:
        return None
    level, state = data[2], data[3]
    if state == STATUS_ASLEEP:
        return None, False
    if state not in (STATUS_DISCHARGING, STATUS_CHARGING, STATUS_FULL):
        return None
    if level > 100:
        return None
    return level, state == STATUS_CHARGING



def device_key(vid: int, pid: int) -> str:
    """One icon per device: the dongle and the cable of the same product share a key.

    The keys main has always produced are kept: 0x5253 is plain "mchose", everything
    else "mchose:{vid}" - the G7's 0xA8A5 and the 0x3837 receivers included - because
    custom names, hidden icons and battery history live on them (#215 review,
    @ahmedkhursheed23). The first-generation A5 Pro Max (0x2023) and its three
    identities share their vendor id, so they stay on one key too.

    The headsets need keys of their own: the V9 Turbo+ headset (0x600A) and its dongle
    (0x6008) are one product, and both sides deliberately map to the same per-product key
    so they share an icon; the X9 (0x6045) is one device and keeps its own key, apart
    from the 0x3837 mice.
    """
    if vid == MEASURED_VID:
        return "mchose"
    if pid in HEADSET_PAIR:
        return f"mchose:{vid:04x}:{HEADSET_PAIR[pid]:04x}"
    return f"mchose:{vid:04x}"


class MchoseProvider(Provider):
    name = "mchose"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[Optional[int], bool, float]] = {}
        self._names: Dict[str, str] = {}
        self._models: Dict[str, int] = {}
        self._kinds: Dict[str, str] = {}

        self._g7_samples = deque(maxlen=5)
        self._g7_seen_at: Optional[float] = None
        self._g7_sample_at: Optional[float] = None
        self._g7_startup_80 = 0
        self._g7_80_at: Optional[float] = None
        self._g7_low: Optional[Tuple[int, float]] = None

    def _smooth_g7(self, level: int, charging: bool) -> Optional[int]:
        """Median-filter jitter, with bounded startup and confirmed large drops."""
        now = time.monotonic()
        if charging or self._g7_seen_at is None or not 0 <= now - self._g7_seen_at < 600:
            self._g7_samples.clear()
            self._g7_sample_at = None
            self._g7_startup_80 = 0
            self._g7_80_at = None
            self._g7_low = None
        self._g7_seen_at = now
        if charging:
            self._g7_samples.append(level)
            self._g7_sample_at = now
        elif level == 80 and not self._g7_samples:
            # Repeated 80s can be stale for many minutes. Five spaced readings
            # are an availability fallback, not proof of the true charge.
            if self._g7_80_at is None or now - self._g7_80_at >= 5:
                self._g7_startup_80 += 1
                self._g7_80_at = now
                if self._g7_startup_80 >= 5:
                    self._g7_samples.append(80)
                    self._g7_sample_at = now
        else:
            self._g7_startup_80 = 0
            self._g7_80_at = None
            centre = median(self._g7_samples) if self._g7_samples else None
            if level == 80 and centre is not None and abs(centre - 80) >= 10:
                # Keep rejecting out-of-trend 80s once there is an estimate.
                self._g7_low = None
            elif self._g7_sample_at is None or now - self._g7_sample_at >= 5:
                if centre is not None and centre - level > 20:
                    pending = self._g7_low
                    if pending is not None and abs(pending[0] - level) <= 5 and now - pending[1] >= 5:
                        # Two similar, spaced low replies can replace the high
                        # history one poll sooner; a single spike cannot.
                        self._g7_samples.clear()
                        self._g7_samples.append(pending[0])
                        self._g7_low = None
                    else:
                        self._g7_low = (level, now)
                else:
                    self._g7_low = None
                self._g7_samples.append(level)
                self._g7_sample_at = now
            elif self._g7_low is not None and (centre is None or centre - level <= 20
                                             or abs(self._g7_low[0] - level) > 5):
                # Even a fast contradictory refresh cancels confirmation.
                self._g7_low = None
        return round(median(self._g7_samples)) if self._g7_samples else None

    def _read_guarded(self, reader, *args):
        """A device that vanishes mid-read must not fail the whole provider.

        Unplugging a mouse leaves its path in the enumerated list for one cycle, and
        a native read can then raise beyond OSError (ERROR_DEVICE_NOT_CONNECTED /
        WinError 2). Without this guard one gone device greys out every MCHOSE icon
        until the next successful poll.
        """
        try:
            return reader(*args)
        except Exception as e:      # a dead device is a reading gap, not a provider fault
            self._diag.append(f"    {type(e).__name__}: {e}")
            return None

    def _read_collection(self, path: bytes) -> Optional[Tuple[int, int, int, int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for report, length in CHANNELS:
                # re-ask before every read (see the module docstring): one request
                # followed by repeated reads only ever returns the request itself.
                req = make_request(CMD_STATUS, report=report, length=length)
                for attempt in range(ATTEMPTS):
                    try:
                        dev.send_feature_report(req)
                    except (OSError, ValueError) as e:
                        self._diag.append(f"    send on report {report:#04x}: {e}")
                        break
                    time.sleep(ATTEMPT_GAP)
                    try:
                        resp = dev.get_feature_report(report, length + 1)
                    except (OSError, ValueError):
                        continue                  # the receiver answers with "read error"
                    got = parse_status(resp)      # until it has the value from the mouse
                    if got:
                        self._diag.append(f"    answered on report {report:#04x}, attempt "
                                          f"{attempt + 1}: {hexdump(resp, 16)}")
                        return got
                self._diag.append(f"    no fresh reply to 0x06 on report {report:#04x}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _read_g7(self, path: bytes) -> Optional[Tuple[int, bool]]:
        """The G7's own protocol: one output report, then wait for an AA 30 input report.

        @kek353's monitor writes the request once and reads until the answer turns up
        (non-blocking, in a loop), so that is what this does - with a bounded budget and
        a sleep between reads instead of a spin. Unverified against the hardware.
        """
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.set_nonblocking(True)
            except Exception:                       # pragma: no cover
                pass
            try:
                dev.write(G7_REQUEST)
            except (OSError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            for _ in range(G7_READS):
                time.sleep(G7_READ_GAP)
                try:
                    resp = dev.read(64)
                except (OSError, ValueError):
                    continue
                got = parse_g7(resp)
                if got:
                    self._diag.append(f"    AA 30 answer: {hexdump(resp, 12)}")
                    return got
            self._diag.append("    no AA 30 answer")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _read_a5(self, path: bytes) -> Optional[Tuple[int, bool]]:
        """The A5 Pro Max's feature-report exchange, run as the reference runs it: write
        once, wait, then read back up to eight times with one re-send in the middle. The
        mouse only answers while awake, so a silent run reads as no level.
        """
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.send_feature_report(make_a5_request())
            except (OSError, ValueError) as e:
                self._diag.append(f"    send: {e}")
                return None
            time.sleep(A5_WRITE_PAUSE)
            for attempt in range(A5_READS):
                if attempt == A5_RESEND_AT:
                    try:
                        dev.send_feature_report(make_a5_request())
                    except (OSError, ValueError) as e:
                        self._diag.append(f"    send: {e}")
                        return None
                    time.sleep(A5_WRITE_PAUSE)
                try:
                    resp = dev.get_feature_report(A5_REPORT_ID, 65)
                except (OSError, ValueError):
                    resp = None
                got = parse_a5(resp) if resp else None
                if got:
                    self._diag.append(f"    answer (attempt {attempt + 1}): "
                                      f"{hexdump(resp, 12)}")
                    return got
                time.sleep(A5_READ_GAP)
            self._diag.append("    no A1 answer (mouse asleep?)")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass


    def _read_mc(self, path: bytes) -> Optional[Tuple[int, bool]]:
        """Bounded M HUB read; only a busy or zero-level echo requests a retry."""
        dev = hid.device()
        try:
            dev.open_path(path)
            for _ in range(MC_ATTEMPTS):
                if dev.write(make_mc_request()) != MC_LEN:
                    self._diag.append("    0x0900 request was not written in full")
                    return None
                retry = False
                for _ in range(MC_READS):
                    resp = dev.read(MC_LEN, MC_READ_MS)
                    got = parse_mc_info(resp)
                    if got is not None:
                        self._diag.append(f"    0x0900: {hexdump(resp, 25)}")
                        return got
                    payload = mc_payload(resp)
                    if payload is not None:
                        retry = (payload[0] == 0xFF
                                 or (len(payload) >= 13 and payload[:2] == b"\x37\x38"
                                     and payload[11] in (0, 1) and payload[12] == 0))
                        break
                if not retry:
                    break
            self._diag.append("    no usable 0x0900 status")
        except (OSError, ValueError) as error:
            self._diag.append(f"    0x0900: {error}")
        finally:
            try:
                dev.close()
            except (OSError, ValueError):
                pass
        return None

    def _read_headset(self, path: bytes, pid: int) -> Optional[Tuple[Optional[int], bool]]:
        """Ask the headset for its status the way M HUB does, on output report 0x55.

        The AA frame (cmd 0x0B) is the audio path in M HUB's own driver; the
        '65 01' frame is what the sister-model community driver uses and what the
        non-audio family answers to. Both are status reads - nothing else is ever
        sent - and a collection that cannot take report 0x55 refuses the write at
        once, which just moves on to the next candidate. The X9 gets the '65 01'
        frame only: that is its BLK profile's battery read, and BLK has no AA frame.
        """
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            frames = (("AA frame", AUDIO_REQUEST, parse_audio),
                      ("65 01 frame", STATUS_REQUEST, parse_status_55))
            if pid == X9_PID:
                frames = frames[1:]       # BLK has no AA frame: the X9 never gets it
            for label, request, parser in frames:
                for attempt in range(HEADSET_ATTEMPTS):
                    try:
                        wrote = dev.write(bytes([AUDIO_REPORT]) + request)
                    except (OSError, ValueError) as e:
                        self._diag.append(f"    {label} write: {e}")
                        break
                    if wrote is None or wrote < 0:
                        self._diag.append(f"    {label} write refused ({wrote})")
                        break
                    try:
                        resp = dev.read(64, HEADSET_READ_MS)
                    except (OSError, ValueError) as e:
                        # unplugged mid-poll: the other MCHOSE devices must still be read
                        self._diag.append(f"    {label} read: {e}")
                        return None
                    if resp:
                        got = parser(resp)
                        if got is not None:
                            self._diag.append(f"    answered on {label}: {hexdump(resp, 12)}")
                            return got
                        self._diag.append(f"    {label}: {hexdump(resp, 12)} (not a reply)")
                    elif attempt + 1 < HEADSET_ATTEMPTS:
                        self._diag.append(f"    {label}: nothing, asking again")
            self._diag.append("    no reply")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        """One icon per device, whether it is on the dongle, on the cable or on radio."""
        self._diag = []
        infos: List[dict] = []
        for vid in MCHOSE_VIDS + (G7_VID, A5_VID):
            try:
                infos += hidlist.enumerate(vid)
            except Exception as e:  # pragma: no cover
                log.warning("hid.enumerate(mchose): %s", e)
        if not infos:
            return []

        # group the collections of one receiver (or one mouse) together, per vendor id:
        # a G7 and an M7 Ultra on one machine are two devices and get two icons
        groups: Dict[Tuple[int, int], List[dict]] = {}
        for d in infos:
            groups.setdefault((d["vendor_id"], d["product_id"]), []).append(d)

        found: Dict[str, List[Tuple[int, bool, int, int]]] = {}   # key -> readings
        for (vid, pid), ifaces in groups.items():
            if (vid, pid) == (0x3837, X9_CABLE_PID):
                # before the naming below: it must not lend its name to the 0x3837 mice
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x}: the X9 on its "
                                  "cable, which M HUB does not read; leaving it alone")
                continue
            key = device_key(vid, pid)
            self._kinds[key] = "headset" if pid in HEADSET_PIDS else "mouse"
            product = (ifaces[0].get("product_string") or "").strip()
            if (vid == NEW_VID and pid not in NEW_MOUSE_TARGETS
                    and pid not in MC_TARGETS and pid not in HEADSET_PIDS):
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x} product='{product}': "
                                  "legacy mouse query held; no sourced target for this product")
                continue
            if product and (key not in self._names or "+" in product):
                # over one product the more informative string wins: the dongle
                # says "V9 Turbo", the headset itself says "MCHOSE V9 Turbo+"
                self._names[key] = product
            if vid == G7_VID and pid != G7_PID:
                # 0xA8A5 is a chip maker's vendor id ("YJX-CHIP"), not a model, so other
                # devices can sit behind it. The G7 request was captured from the G7 only,
                # and nothing is written to a device that is not it - not even the
                # collection scan, because that is what produced the capture's traffic.
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x} product='{product}': "
                                  f"not the G7 ({G7_PID:04x}), leaving it alone")
                continue
            if vid == A5_VID and pid not in A5_PIDS:
                # only the three A5 Pro Max identities are claimed; another 0x2023 model
                # does not get this request written to it
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x} product='{product}': "
                                  f"not an A5 Pro Max identity, leaving it alone")
                continue
            cols = [d for d in ifaces if (d.get("usage_page") or 0) >= 0xFF00]
            if vid == G7_VID:
                # the G7 answers on 0xFF01 only; the other vendor collections are left
                # alone (nothing off the documented path is written to)
                cols = [d for d in cols if (d.get("usage_page") or 0) == CONFIG_PAGE]
            elif vid == A5_VID:
                # The A5 reference opens its 0xFFFF page only; 0xFFA0 next to it is
                # never where the answer comes from, so it is not written to at all
                # (the G7's rule; review by @ahmedkhursheed23).
                cols = [d for d in cols if (d.get("usage_page") or 0) == A5_USAGE_PAGE]
            elif pid == X9_PID:
                # the X9 declares report 0x55 on ff90:0001 only (measured), so nothing off
                # that path is written to (the G7's rule)
                cols = [d for d in cols
                        if (d.get("usage_page"), d.get("usage")) == (0xFF90, 0x0001)]
            elif pid in HEADSET_PIDS:
                # the dongle's 0xFF00 page first; on the headset none of its pages is
                # known to carry the report yet, so the dump order is kept otherwise
                cols.sort(key=lambda d: (d.get("usage_page") or 0) != 0xFF00)
            elif vid == NEW_VID:
                expected = MC_TARGETS[pid] if pid in MC_TARGETS else NEW_MOUSE_TARGETS[pid]
                cols = [d for d in cols if (d.get("interface_number"), d.get("usage_page"),
                                           d.get("usage")) == expected]
            else:
                # the configuration collection first; 0xFF0B is dead on the M7 Ultra
                cols.sort(key=lambda d: (d.get("usage_page") != CONFIG_PAGE, d.get("usage") != 1))
            if not cols:
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x} product='{product}': "
                                  + ("legacy mouse query held; no sourced control collection"
                                     if vid == NEW_VID else "no vendor collection"))
                continue
            for d in cols:
                self._diag.append(f"[MCHOSE] vid={vid:04x} pid={pid:04x} product='{product}' "
                                  f"iface={d.get('interface_number')} "
                                  f"usage={(d.get('usage_page') or 0):04x}:"
                                  f"{(d.get('usage') or 0):04x}")
                if vid == G7_VID:
                    got_g7 = self._read_guarded(self._read_g7, d["path"])
                    if got_g7:
                        found.setdefault(key, []).append((got_g7[0], got_g7[1], pid, 0))
                elif vid == A5_VID:
                    got_a5 = self._read_guarded(self._read_a5, d["path"])
                    if got_a5:
                        found.setdefault(key, []).append((got_a5[0], got_a5[1], pid, 0))
                elif vid == NEW_VID and pid in MC_TARGETS:
                    got_mc = self._read_guarded(self._read_mc, d["path"])
                    if got_mc is not None:
                        found.setdefault(key, []).append((got_mc[0], got_mc[1], pid, 0))
                elif pid in HEADSET_PIDS:
                    got = self._read_guarded(self._read_headset, d["path"], pid)
                    if got is not None:
                        if got[0] is None:
                            self._diag.append("    heard: asleep")
                        else:
                            found.setdefault(key, []).append((got[0], bool(got[1]), pid, 0))
                        break     # one answer per product; the pair shares an icon
                else:
                    got = self._read_guarded(self._read_collection, d["path"])
                    if got:
                        self._models[key] = got[2]
                        found.setdefault(key, []).append((got[0], bool(got[1]), pid, got[2]))
                if found.get(key):
                    break

        out: List[DeviceStatus] = []
        for key, readings in found.items():
            # a wired mouse may be silent on the radio: prefer whichever source reports
            # charging, and one icon either way
            readings.sort(key=lambda r: (not r[1],))
            level, charge, pid, model = readings[0]
            if key == device_key(G7_VID, G7_PID):
                raw = level
                level = self._smooth_g7(raw, charge)
                self._diag.append(f"    G7 raw={raw}% smoothed="
                                  + (f"{level}%" if level is not None else "waiting for a usable reading"))
            self._diag.append(f"  -> {key} pid={pid:04x}"
                              + (f" model=0x{model:04x}" if model else "")
                              + (f": {level}%" if level is not None else ": battery pending")
                              + (" (charging)" if charge else ""))
            self._last[key] = (level, charge, time.time())
            out.append(DeviceStatus(key, self._display_name(key), level, charge, True,
                                    "mchose", kind=self._kinds.get(key, "mouse")))

        # silent: a receiver cannot tell a switched-off mouse from one that went to sleep
        # a few seconds ago, so keep the last value greyed out for a while
        now = time.time()
        for key, last in self._last.items():
            if key in found or now - last[2] >= ASLEEP_KEEP:
                continue
            out.append(DeviceStatus(key, self._display_name(key), last[0], last[1],
                                    False, "mchose", kind=self._kinds.get(key, "mouse")))
        return out

    def _display_name(self, key: str) -> str:
        """A measured model id first, then the device's own product string.

        The id in the reply names the model on the family measured here (0x0031 is the M7
        Ultra, the same number it uses as its wired PID), but it is a per-model number
        that is not listed per device anywhere, so a model this table does not know is
        named from the receiver's product string: for the A7 V2 Ultra in issue #4 that
        reads "MCHOSE A7 V2 Ultra", the name on the box.
        """
        model = self._models.get(key)
        if model and model in MODEL_NAMES:
            return MODEL_NAMES[model]
        name = (self._names.get(key) or "").strip()
        if name:
            return name
        if model:
            return f"MCHOSE mouse (0x{model:04x})"
        return "MCHOSE mouse"

    def diagnostics(self) -> List[str]:
        return list(self._diag)
