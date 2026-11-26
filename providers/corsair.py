"""Corsair wireless receivers (Void v2 Wireless and its siblings) over USB/HID,
without iCUE.

Protocol from Sapd/HeadsetControl's corsair_void_v2w device:

  * the vendor collection on the receiver, which HeadsetControl identifies by
    interface 4. Windows captures in #28 and PR #176 identify FF42:0001;
    the sibling FF42:0002 notice channel is never written.
  * commands are 64 bytes with no leading report id: [0] 0x02, [1] endpoint,
    [2] 0x02, [3] command. Endpoint 0x08 is the receiver itself, 0x09 the
    headset behind it. Replies are 64 bytes. HeadsetControl writes the same
    message with a 0x00 report id in front (65 bytes), which Windows refuses
    on this family before it reaches the device (0x57, headsetcontrol#521) -
    so the 64-byte shape goes out first and the rid-prefixed one is only a
    fallback (see _write_frame).
  * talking to a sleeping headset needs the same minimal handshake
    HeadsetControl uses - receiver firmware query, receiver heartbeat, then a
    headset heartbeat - which is enough to read the battery without switching
    the headset into software mode (that switch is audible as a pop).
  * battery: command 0x0f to endpoint 0x09 -> reply[4] | reply[5] << 8 is a
    0..1000 value, ten times the percent. The receiver sometimes answers with
    the paired headset id instead of a level, so a reading of 0 or above 1000 is
    retried a few times and then refused rather than shown. **Confirmed on
    hardware**: #28's reporter's run of the fixed build shows the level (his
    probe's accepted-write reply reads `01 01 02 00 2a 03` = 81 %).

The reply carries no charging flag: HeadsetControl reports the level as
available (not charging) for this family, and so does this provider.

The Dark Core RGB Pro SE (dongle 1b1c:1b7f) speaks the newer Corsair protocol
that ckb-next calls "bragi" (USES_BRAGI in src/daemon/usb.h; bragi_proto.h,
device_bragi.c) and OpenLinkHub "slipstream" (src/devices/slipstream/): 64-byte
routed frames behind report id 0, 65 bytes to hidapi. Byte 1 is the route (0x08
the receiver itself, 0x08 | child the device behind it; 0x09 = the paired
mouse), byte 2 the command (0x02 = get) and byte 3 the property (battery level
0x0F). An answer is `[route][0x02][err][value]...`: err 0 means OK, and the
battery sits little-endian in bytes 3-4 in tenths of a percent - both drivers
divide by 10. The 1.13.0 build asked this dongle with ckb-next's "nxp" packet;
the reporter's runs in #56 showed it never answers that one, and that one of
its *other* frames parsed as a level was the 0 % flash. The request above and
its `01 02 00 26 02 ...` answer (55 %) were captured from the reporter's
dongle, and the exchange is confirmed on hardware: KKiruano's test build run
showed the same level SignalRGB does. The answer echoes the command, not the
property, so a second app on the same dongle (iCUE, SignalRGB) can put an
answer for another property on the channel that parses as a level; the ask is
therefore repeated and only two equal answers are taken, the wait is a time
window (traffic frames end a 500 ms read early), and a per-dongle budget
bounds reads across matching control collections (review by @ahmedkhursheed23). The
Virtuoso RGB Wireless SE receiver (1b1c:0a40) runs the same exchange with two
measured differences (#204, jeffpeng3's Linux proof-of-concept and the Windows
probe): Windows refuses every report-id-prefixed write on it and takes the bare
64-byte frame - which also carries a leading 0x02 the Dark Core's frame does not
have (`02 09 02 0f` against `09 02 0f`) - and its headset reports the charge
state on property 0x10 (1 charging, 2 on battery). It is read with the full
session that was measured on it - firmware query, receiver heartbeat, headset
heartbeat, then the properties.

The rest of the Virtuoso family is the same exchange on the ids HeadsetControl's
reworked Virtuoso XT/SE device covers (#570): the SE's own receiver 1b1c:0a3e and
its cable 1b1c:0a3d, the XT's receiver 1b1c:0a64 and its cable 1b1c:0a62. The wired
ids read with target 0x08 first and the receivers with 0x09, each with the other as
the fallback, and reads need neither the session nor software mode (HeadsetControl
notes only writes need software mode). Those four ran on HeadsetControl's author's
hardware (#568/#570 discussion) and have not answered a build here; the 0a40 is
measured. The Dark Core's wired id 1b1c:1b7e stays out - nothing can prove it
answers.

The `--probe` run (#28) digs deeper than the tray ever should: every write's
return value, every frame with the time it took to arrive, a listen-only pass
before writing anything (with the vendor app open it catches the vendor's own
exchange), and - when the usual sequence yields nothing - the vendor app's
fuller software-mode path. It also prints every collection's declared report
lengths and tries only the sourced 64/65-byte framings on FF42:0001/interface 4,
because a write Windows refuses locally looks exactly like a switched-off
device. Normal polling keeps the short windows; a provider runs on the tray's
timer and must never block it.

The Dark Core / Ironclaw mice and their dongles speak a second, unrelated
protocol ("nxp" in ckb-next, which reads them): a single-field 64-byte packet
`{CMD_GET 0x0e, FIELD_BATTERY 0x50}` answered with the level as an index into a
five-step table `{0, 15, 30, 50, 100}` at byte 4 and a status byte at byte 5.
ckb-next's source is the reference (src/daemon/nxp_proto.h and device.c,
repo ckb-next/ckb-next; its protocol notes live in ckb-next/corsair-protocol).
The status byte's meaning is not written down in either, so no charging state
is reported, and the level is coarse, so it is shown as "about N%".

The K65 PLUS Wireless keyboard's receiver (1b1c:2b07, #154) runs the same routed
exchange as the Dark Core's dongle: route 0x09 asks the paired keyboard for property
0x0F on the ff42:0001 collection, and the answer carries the level in tenths in bytes
3-4. OpenLinkHub's own K65 Plus dongle handler (src/devices/k65plusWdongle) sends
``cmdBatteryLevel {0x02, 0x0F}`` on the 65-byte report-id-0 frame and reads the value
as a little-endian uint16 from the reply's bytes 3-4, so this id mirrors that; no
charge property is read, because that handler reports none. Confirmed on the
reporter's own unit (#154): his test build run showed 100 % (`01 02 00 e8 03`).

The M65 RGB Ultra Wireless's SLIPSTREAM receiver (1b1c:1ba6, #411) publishes the
same ff42:0001/0002 pair and answers the same routed get-property exchange as
the Dark Core's dongle. Its level is property 0x0F and its charge state is
property 0x10, matching OpenLinkHub's m65rgbultraW package (state 1 charging,
2 on battery, 3 full). When the mouse is plugged in, it moves to its cable
identity 1b1c:1bb5, which publishes the same pair and answers level on target
0x08 plus charge property 0x10 (the exchange OpenLinkHub's m65rgbultraWU
package uses). The cable and receiver share one icon; the cable's charging
reading wins while it is connected. Confirmed on the reporter's mouse in #411
(test builds 4 and 5): receiver replies 01 02 00 d4 03 (98 %) and charge state 2; cable
replies 00 02 00 3e 03 (83 %) with charge state 1, and 00 02 00 e8 03 (100 %)
with state 3 (full). Both identities answered on ff42:0001 only, so the M65 ids
are asked there and nowhere else, and the cable is asked on target 0x08 only.
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

CORSAIR_VID = 0x1B1C

USAGE_PAGE = 0xFF42
USAGE = 0x0001
CONTROL_INTERFACE = 4           # the collection that carries the protocol

RECEIVER_ENDPOINT = 0x08
HEADSET_ENDPOINT = 0x09
MSG_SIZE_WRITE = 65
MSG_SIZE_READ = 64

CMD_FIRMWARE = 0x13
CMD_HEARTBEAT = 0x12
CMD_BATTERY = 0x0F

FW_SUB = 0x02
HB_SUB = 0x02
BATTERY_SUB = 0x02

LEVEL_INDEX = 4                 # little-endian 16-bit, hundredths
LEVEL_MAX = 1000
ATTEMPTS = 3
READ_TIMEOUT_MS = 500
FLUSH_TIMEOUT_MS = 30

# The --probe run (#28): this family's answers can arrive late, and the vendor
# app's own traffic is part of the picture, so the probe listens first and then
# talks with longer windows. The tray keeps the short ones above.
PROBE_LISTEN_MS = 6000
PROBE_HB_TIMEOUT_MS = 3000
PROBE_ATTEMPT_TIMEOUT_MS = (2000, 4000, 4000)
PROBE_SOFTWARE_TIMEOUT_MS = (2000, 4000)


def in_probe() -> bool:
    """True inside `halo_battery.pyw --probe` (which sets HALO_PROBE)."""
    return os.environ.get("HALO_PROBE") == "1"


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort),
                ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort * 17),
                ("NumberLinkCollectionNodes", ctypes.c_ushort),
                ("NumberInputButtonCaps", ctypes.c_ushort),
                ("NumberInputValueCaps", ctypes.c_ushort),
                ("NumberInputDataIndices", ctypes.c_ushort),
                ("NumberOutputButtonCaps", ctypes.c_ushort),
                ("NumberOutputValueCaps", ctypes.c_ushort),
                ("NumberOutputDataIndices", ctypes.c_ushort),
                ("NumberFeatureButtonCaps", ctypes.c_ushort),
                ("NumberFeatureValueCaps", ctypes.c_ushort),
                ("NumberFeatureDataIndices", ctypes.c_ushort)]


def caps_for(path) -> Optional[Tuple[int, int, int]]:
    """(input, output, feature) report byte lengths of one HID collection.

    The handle is opened with no access rights (query only), so nothing is sent
    to the device. Windows-only, like the rest of the probe machinery.
    """
    if sys.platform != "win32":
        return None
    try:
        p = path.decode("utf-8", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        k32, hidd = ctypes.windll.kernel32, ctypes.windll.hid
        k32.CreateFileW.restype = ctypes.c_void_p
        handle = k32.CreateFileW(p, 0, 3, None, 3, 0, None)   # no access, share r/w, open existing
        if handle in (None, ctypes.c_void_p(-1).value):
            return None
        try:
            pp = ctypes.c_void_p()
            if not hidd.HidD_GetPreparsedData(ctypes.c_void_p(handle), ctypes.byref(pp)):
                return None
            try:
                caps = _HIDP_CAPS()
                if hidd.HidP_GetCaps(pp, ctypes.byref(caps)) != 0x00110000:   # HIDP_STATUS_SUCCESS
                    return None
                return (caps.InputReportByteLength, caps.OutputReportByteLength,
                        caps.FeatureReportByteLength)
            finally:
                hidd.HidD_FreePreparsedData(pp)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:            # a probe must never take the provider down
        return None


def _error_note(dev, n) -> str:
    """The OS reason behind a refused write, when hidapi reports one."""
    if not isinstance(n, int) or n >= 0:
        return ""
    try:
        err = dev.error()
    except Exception:
        return ""
    return f" ({err})" if err else ""

# Direct USB mode of the original HS80 RGB Wireless (1b1c:0a69). This is
# distinct from the 0a6b receiver exchange above: it answers command 02 0f on
# target 08. Protocol/layout: ST1CKEL/HS80_Control docs/PROTOCOL.md and PR #2,
# measured on real hardware: https://github.com/ST1CKEL/HS80_Control/pull/2
HS80_USB_PID = 0x0A69
HS80_CHARGING_CABLE_PID = 0x0A6A  # switched-off headset while connected to USB power
HS80_USB_INTERFACE = 3
HS80_USB_USAGE_PAGE = 0xFF42
HS80_USB_TARGET = 0x08
HS80_USB_COMMAND_BATTERY = (0x02, 0x0F)
HS80_USB_REPORT_SIZE = 64
HS80_USB_READ_ATTEMPTS = 3
HS80_USB_READ_TIMEOUT_MS = 500


def hs80_usb_request(target: int = HS80_USB_TARGET) -> bytes:
    """Build a 64-byte battery-query report for the HS80 control interface."""
    return bytes([0x02, target, *HS80_USB_COMMAND_BATTERY]
                 + [0x00] * (HS80_USB_REPORT_SIZE - 4))


def parse_hs80_usb_battery(report, target: int = HS80_USB_TARGET) -> Optional[int]:
    """Decode a matched HS80 response for target 0x08 (USB) or 0x09 (receiver)."""
    if not report or len(report) < 6:
        return None
    r = list(report)
    # Report 0x01; response channel is target minus 0x08; command family 0x02, OK.
    if r[0] != 0x01 or r[1] != target - 0x08 or r[2] != 0x02 or r[3] != 0x00:
        return None
    tenths = r[4] | (r[5] << 8)
    if tenths > 1000:
        return None
    return tenths // 10


PIDS = {
    0x2A08: "Corsair Void v2 Wireless",
    0x2A02: "Corsair Virtuoso Max Wireless",
    0x0A97: "Corsair HS80 Max Wireless",
}


def make_request(endpoint: int, sub: int, command: int) -> List[int]:
    frame = [0x00, 0x02, endpoint, sub, command]
    return frame + [0x00] * (MSG_SIZE_WRITE - len(frame))


def make_software_mode(endpoint: int) -> List[int]:
    """`01 03 00 02` to an endpoint - the vendor app's software-mode switch.

    HeadsetControl's initializeDevice() and OpenLinkHub's Connect() both send it
    before commands work on a cold device. The headset can pop audibly when the
    mode changes, which is why normal polling avoids it.
    """
    frame = [0x00, 0x02, endpoint, 0x01, 0x03, 0x00, 0x02]
    return frame + [0x00] * (MSG_SIZE_WRITE - len(frame))


# --- second family: the "bragi" / "slipstream" exchange of the Dark Core RGB Pro SE dongle ---
# A wired mouse (1b1c:1b7e) exists too; nothing here can prove it answers, so only the
# dongle is read.
BRAGI_PIDS = {
    0x1B7F: "Corsair Dark Core RGB Pro SE",
    # The M65's cable identity is listed before the receiver: while charging both
    # can be present, and the receiver then has no battery answer. Asking the
    # cable first avoids waiting out the receiver's read budget before the level.
    0x1BB5: "Corsair M65 RGB Ultra Wireless",   # #411 the same mouse on its cable
    0x1BA6: "Corsair M65 RGB Ultra Wireless",   # #411 SLIPSTREAM receiver
    0x2B07: "Corsair K65 PLUS Wireless",
    0x1BDC: "Corsair M75 Wireless",
    0x0A40: "Corsair Virtuoso RGB Wireless SE",
    0x0A3E: "Corsair Virtuoso RGB Wireless SE",
    0x0A3D: "Corsair Virtuoso RGB Wireless SE",
    0x0A64: "Corsair Virtuoso RGB Wireless XT",
    0x0A62: "Corsair Virtuoso RGB Wireless XT",
}
# the pictogram each family member draws: the Dark Core is a mouse, the K65 PLUS a
# keyboard; the Virtuoso ids are headsets
BRAGI_KINDS = {0x1B7F: "mouse", 0x1BA6: "mouse", 0x1BB5: "mouse",
               0x2B07: "keyboard", 0x1BDC: "mouse"}
# one icon per product: a headset's receiver and cable ids share the key
BRAGI_KEYS = {
    0x1B7F: "corsair:1b7f",
    0x1BA6: "corsair:m65",
    0x1BB5: "corsair:m65",
    0x2B07: "corsair:2b07",
    0x1BDC: "corsair:1bdc",
    0x0A40: "corsair:virtuoso-se",
    0x0A3E: "corsair:virtuoso-se",
    0x0A3D: "corsair:virtuoso-se",
    0x0A64: "corsair:virtuoso-xt",
    0x0A62: "corsair:virtuoso-xt",
}
BRAGI_VENDOR_PAGE = 0xFF42       # the dongle's two vendor collections, from the #56 dump

# --- third family: the older Void headsets (HeadsetControl's "rich" Void, 0x0A55) ---
# The dongle's battery collection is the vendor page 0xFFC5 on interface 3 (HeadsetControl's
# RichVoidDevice capability detail). The exchange is short: an output report C9 with the
# byte 64, and a five-byte input report 64 00 <level> B1 <status>. No session, no framing.
VOID_VENDOR_PAGE = 0xFFC5
VOID_USAGE = 0x0001
VOID_REPORT_ID = 0xC9
VOID_ASK = 0x64                  # the byte after the report id
VOID_REPLY_ID = 0x64             # the input report the answer arrives in
VOID_MARKER = 0xB1               # byte 3 of a battery reply
VOID_STATUS_OFF = 0              # the receiver has no headset on it
VOID_CHARGING = (4, 5)           # 1 normal, 2 low, 4/5 charging (HeadsetControl)
VOID_PIDS = {0x0A55: "Corsair Void Elite Wireless"}
BRAGI_USAGE = 0x0001             # the one that answers; 0x0002 is its notice channel
BRAGI_MSG_SIZE = 64              # ckb-next: MSG_SIZE (structures.h)
BRAGI_ROUTE_DONGLE = 0x08        # the receiver itself
BRAGI_ROUTE_MOUSE = 0x09         # 0x08 | child 1: the paired mouse
BRAGI_ROUTE_CHILD = 0x01         # the route the mouse's answers come back with
BRAGI_CMD_GET = 0x02             # ckb-next: CMD_GET
BRAGI_PROP_BATTERY = 0x0F        # ckb-next: BRAGI_BATTERY_LEVEL
BRAGI_PROP_CHARGE = 0x10         # 1 = charging, 2 = on battery, 3 = full (OpenLinkHub)
BRAGI_PROP_HEARTBEAT = 0x12      # the session heartbeats (#204)
BRAGI_PROP_FIRMWARE = 0x13       # the session's firmware query (#204)
BRAGI_LEVEL_MAX = 1000           # tenths of a percent (765 -> 76 %)
BRAGI_WINDOW_S = 6.0             # the answer measured ~4.7 s after the write (#56); a
                                 # time window, not a read count - traffic frames end a
                                 # 500 ms read early and used to eat the old budget
BRAGI_CONFIRM_WINDOW_S = 3.0     # the confirming ask; the mouse is awake by then
BRAGI_READ_ATTEMPTS = 96         # a hard stop so a busy channel cannot spin
BRAGI_POLL_BUDGET_S = 7.0        # total per dongle, across its collections

# The Virtuoso family reads through the bare 64-byte frame with the leading 0x02
# and reports the charge state on property 0x10: measured on the 0A40 receiver
# (#204), and the same exchange on the four XT/SE ids from HeadsetControl's
# reworked device (#570). The Dark Core takes the framed write and reports no
# charge, so each stays scoped to the ids measured with it.
BRAGI_BARE_PIDS = {0x0A40, 0x0A3E, 0x0A3D, 0x0A64, 0x0A62}
# the session was measured on the 0A40 alone; HeadsetControl reads the family's
# properties without one (#570), so the new ids mirror that
BRAGI_SESSION_PIDS = {0x0A40}
# the wired ids resolve their target the other way round: HeadsetControl's
# resolveTarget tries 0x08 first on a wired id and 0x09 on a receiver, with the
# other as the fallback (#570).
BRAGI_WIRED_PIDS = {0x0A3D, 0x0A62}
BRAGI_XT_CABLE_PID = 0x0A62
# The M65 RGB Ultra Wireless on its cable (1b1c:1bb5) uses the same Bragi
# battery/charge properties as OpenLinkHub's m65rgbultraWU package, on target
# 0x08 only: that package sends nothing to 0x09, and in #411 the 0x09 fallback
# only ever got the error 01 02 06. So the cable has no fallback target.
M65_CABLE_PID = 0x1BB5
# The M65 ids answered only on ff42:0001 (#411, receiver and cable); they are
# never asked on ff42:0002 or on any other collection.
BRAGI_EXACT_PIDS = {0x1BA6, M65_CABLE_PID}
# ids that answer the charge property 0x10. The Virtuoso family was measured in
# #204; the wired M65 uses the same property in OpenLinkHub's m65rgbultraWU
# package, and its Slipstream receiver uses m65rgbultraW's charge query (#411).
BRAGI_CHARGE_PIDS = set(BRAGI_BARE_PIDS) | {0x1BB5, 0x1BA6}
# The SABRE v3 PRO (receiver 2b42, mouse on cable 2b43) is the newer
# Slipstream V2 exchange, not Bragi. OpenLinkHub's sabrev3proW package reaches
# the mouse through the receiver with 512-byte reports addressed to the paired
# device (endpoint 1); its sabrev3proWU package talks to the mouse on its cable
# with 64-byte reports and endpoint 0. Measured through the receiver in #543:
# `01 00 01 02 00 84 03` - endpoint, 00, 01, the command, status 0, then the
# value (0x0384 = 900 tenths = 90 %).
# Only the reporter-tested plain property reads are used. A refusal or silence
# never adds a login or a software/hardware-mode write.
SABRE_V3_PIDS = {
    0x2B43: "Corsair SABRE v3 PRO Ultralight",   # the mouse on its cable
    0x2B42: "Corsair SABRE v3 PRO Ultralight",   # its 2.4 GHz receiver
}
SABRE_V3_CABLE_PID = 0x2B43
SABRE_V3_KEY = "corsair:sabre-v3"
SABRE_V3_MSG_SIZE = 512
SABRE_V3_WRITE_SIZE = SABRE_V3_MSG_SIZE + 1
SABRE_V3_CABLE_WRITE_SIZE = 64 + 1
SABRE_V3_ENDPOINT = 0x01
SABRE_V3_CABLE_ENDPOINT = 0x00
SABRE_V3_ROUTES = {                      # (endpoint, output report size)
    0x2B43: (SABRE_V3_CABLE_ENDPOINT, SABRE_V3_CABLE_WRITE_SIZE),
    0x2B42: (SABRE_V3_ENDPOINT, SABRE_V3_WRITE_SIZE),
}
SABRE_V3_CMD_GET = 0x02
SABRE_V3_PROP_BATTERY = 0x0F
SABRE_V3_PROP_CHARGE = 0x10
SABRE_V3_READ_MS = 500
SABRE_V3_REPLY_MS = 600                  # wait for the answer to one request


def bragi_request(prop: int = BRAGI_PROP_BATTERY,
                  route: int = BRAGI_ROUTE_MOUSE) -> bytes:
    """The Dark Core's get-property frame: report id 0, the route, cmd, property."""
    payload = bytearray(BRAGI_MSG_SIZE)
    payload[0] = route
    payload[1] = BRAGI_CMD_GET
    payload[2] = prop
    return b"\x00" + bytes(payload)


def bragi_se_request(prop: int = BRAGI_PROP_BATTERY,
                     route: int = BRAGI_ROUTE_MOUSE) -> bytes:
    """The Virtuoso SE receiver's get-property frame: `02 <route> 02 <property>`.

    Its data begins with the extra 0x02 (`02 09 02 0f` against the Dark Core's
    `09 02 0f`), and the whole 64 bytes go out bare - Windows refuses every
    report-id-prefixed form on this receiver (#204, measured).
    """
    head = bytes([0x02, route, BRAGI_CMD_GET, prop])
    return head + bytes(BRAGI_MSG_SIZE - len(head))


def parse_bragi_raw(reply) -> Optional[int]:
    """The raw 16-bit value of a get-property answer, or None when it is not one.

    Two answer shapes carry it, both routed to `0x01` with command `0x02` and
    err `0x00`: the Dark Core's `01 02 00 <v LE>` (measured in #56) and the
    Virtuoso receivers' `01 01 02 00 <v LE>`, whose extra byte is the source
    (`01` = the headset; probe-measured on the 0A40, #204 - the value sits one
    byte further along). The dongle sends other frames as well (device-list
    records on the same channel, notices on its sibling collection, the session
    heartbeats with values in the thousands), so a frame counts only when every
    field above matches - one of those frames parsed as a level was the 0 %
    flash the earlier release showed.
    """
    if not reply:
        return None
    data = list(reply)
    if len(data) >= BRAGI_MSG_SIZE + 1:      # hidapi may hand the report id back
        data = data[1:]
    if len(data) < 5:
        return None
    if data[0] != BRAGI_ROUTE_CHILD:
        return None
    if data[1] == BRAGI_CMD_GET and data[2] == 0x00:            # the Dark Core
        return data[3] | (data[4] << 8)
    if (len(data) >= 6 and data[1] == BRAGI_ROUTE_CHILD         # the Virtuoso
            and data[2] == BRAGI_CMD_GET and data[3] == 0x00):
        return data[4] | (data[5] << 8)
    return None


def parse_bragi(reply) -> Optional[int]:
    """The battery level in percent from a get-property answer, or None."""
    value = parse_bragi_raw(reply)
    if value is None or value == 0 or value > BRAGI_LEVEL_MAX:
        return None
    return value // 10


def parse_bragi_xt_cable(reply, *, charging: bool = False):
    """The wired XT's own GET reply; only used for its target-08 requests.

    HeadsetControl identifies 0A62 as the self-addressed XT, and maps target 08
    to reply source 00 while checking report ID, command and status:
    https://github.com/Sapd/HeadsetControl/blob/163c6edf744c28804d95e3653e5ea7bc9486b46c/lib/devices/corsair_virtuoso_xt.hpp#L370-L405
    https://github.com/Sapd/HeadsetControl/blob/163c6edf744c28804d95e3653e5ea7bc9486b46c/lib/devices/corsair_virtuoso_xt.hpp#L440-L554
    Receiver parsing deliberately keeps rejecting source 00.
    """
    if not reply:
        return None
    data = list(reply)
    if len(data) == BRAGI_MSG_SIZE + 1 and data[0] == 0:
        data = data[1:]
    if len(data) != BRAGI_MSG_SIZE or data[:4] != [0x01, 0x00, 0x02, 0x00]:
        return None
    if any(type(byte) is not int or not 0 <= byte <= 255 for byte in data):
        return None
    value = int.from_bytes(bytes(data[4:8]), "little")
    if charging:
        return {1: True, 2: False}.get(value)
    return value // 10 if value <= BRAGI_LEVEL_MAX else None



def _parse_bragi_cable_raw(reply) -> Optional[int]:
    """The wired M65 answer: report id 0, then ``02 00 <value LE16>``.

    OpenLinkHub's m65rgbultraWU transfer reads the level at bytes 3..4 of
    this buffer; the same shape is kept separate from the routed replies so
    a receiver's own answer cannot be mistaken for the mouse's.
    """
    if not reply:
        return None
    data = list(reply)
    if len(data) >= 5 and data[0] == 0x00 and data[1] == BRAGI_CMD_GET and data[2] == 0x00:
        return data[3] | (data[4] << 8)
    if len(data) >= 4 and data[0] == BRAGI_CMD_GET and data[1] == 0x00:
        # Some Windows reads hand the report id back as a separate first byte.
        return data[2] | (data[3] << 8)
    return None


def parse_bragi_cable(reply) -> Optional[int]:
    """The battery level from a wired-M65 answer, or None."""
    value = _parse_bragi_cable_raw(reply)
    if value is None or value == 0 or value > BRAGI_LEVEL_MAX:
        return None
    return value // 10


def parse_bragi_charge_cable_state(reply) -> Optional[int]:
    """The wired M65's property 0x10 state, or None.

    OpenLinkHub's m65rgbultraWU package treats byte 3 as a state code:
    1 charging, 2 on battery, 3 full, 0 not charging. The value is kept
    separate from the level shape because it is a state, not a percentage.
    """
    value = _parse_bragi_cable_raw(reply)
    if value in (0, 1, 2, 3):
        return value
    return None


def parse_bragi_charge_cable(reply) -> Optional[bool]:
    """The charge state from the wired M65's property 0x10 answer, or None."""
    state = parse_bragi_charge_cable_state(reply)
    if state is None:
        return None
    return state == 1


def void_request() -> List[int]:
    """The battery ask: an output report 0xC9 whose first byte is 0x64."""
    return [VOID_REPORT_ID, VOID_ASK]


def parse_void(reply) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a Void battery reply, or None.

    The reply is 64 00 <level|mic flag> B1 <status>. A status of 0 means the receiver
    has no headset on it; 1 and 2 are on-battery (normal and low), 4 and 5 charging.
    Bit 7 of the level byte is the microphone-lifted flag, not part of the level.
    """
    if not reply or len(reply) < 5:
        return None
    if reply[0] != VOID_REPLY_ID or reply[3] != VOID_MARKER:
        return None
    status = reply[4]
    if status == VOID_STATUS_OFF:
        return None
    level = reply[2] & 0x7F
    if not 0 <= level <= 100:
        return None
    return level, status in VOID_CHARGING


def parse_bragi_charge(reply) -> Optional[bool]:
    """True while charging, False on battery, None when not a charge answer.

    Property 0x10: 1 = charging, 2 = on battery (measured on the Virtuoso SE
    receiver's headset, #204); anything else is not taken as a state.
    """
    value = parse_bragi_raw(reply)
    if value == 1:
        return True
    if value == 2:
        return False
    return None


def parse_level(r) -> Optional[int]:
    """Percent from a battery reply, or None when there is no usable value."""
    if not r or len(r) <= LEVEL_INDEX + 1:
        return None
    vendor = r[LEVEL_INDEX] | (r[LEVEL_INDEX + 1] << 8)
    if vendor == 0 or vendor > LEVEL_MAX:
        return None
    return vendor // 10


def sabre_v3_frame(prop: int, endpoint: int = SABRE_V3_ENDPOINT,
                   size: int = SABRE_V3_WRITE_SIZE) -> bytes:
    """A plain Slipstream V2 get-property report: report id 0, endpoint, 01 01, 02, property."""
    frame = bytearray(size)
    frame[1] = endpoint
    frame[2] = 0x01
    frame[3] = 0x01
    frame[4] = SABRE_V3_CMD_GET
    frame[5] = prop
    return bytes(frame)


def sabre_v3_reply(r, endpoint: int, cmd: int) -> Optional[Tuple[int, bytes]]:
    """(status, data) when `r` answers `cmd` sent to `endpoint`, otherwise None.

    A reply echoes the endpoint at byte 0 and the command at byte 3; byte 4 is
    the status (0 = done) and the data starts at byte 5. hidapi drops the zero
    report id on Windows; a reply that still carries it is one byte further on.
    The receiver also sends frames of its own on this collection - `00 00 04 0d`
    arrived in place of the charge answer in #543 - so anything else is skipped.
    """
    if not r:
        return None
    data = bytes(r)
    for start in (0, 1):
        if start and data[0] != 0x00:
            break
        if len(data) < start + 5:
            break
        if data[start] == endpoint and data[start + 3] == cmd:
            return data[start + 4], data[start + 5:]
    return None


def parse_sabre_v3_level(data) -> Optional[int]:
    """Property 0x0F's value: tenths of a percent, little-endian."""
    if not data or len(data) < 2:
        return None
    raw = data[0] | (data[1] << 8)
    if not 10 <= raw <= 1000:
        return None
    return raw // 10


def parse_sabre_v3_charge(data) -> Optional[bool]:
    """Property 0x10's value: 1 charging, 2 on battery, 3 full (OpenLinkHub)."""
    if not data:
        return None
    state = data[0]
    if state == 1:
        return True
    if state in (2, 3):
        return False
    return None


class CorsairProvider(Provider):
    name = "corsair"

    def __init__(self):
        self._diag: List[str] = []

    def _query_hs80_usb(self, infos: List[dict], target: int = HS80_USB_TARGET,
                         transport_name: str = "USB") -> Optional[int]:
        """Read battery from the direct USB connection or HS80 wireless receiver."""
        collections = [d for d in infos
                      if d.get("interface_number") == HS80_USB_INTERFACE
                      and d.get("usage_page") == HS80_USB_USAGE_PAGE]
        collections.sort(key=lambda d: (0 if d.get("usage") == 0x0001 else 1,
                                        d.get("usage", 0)))
        if not collections:
            self._diag.append(f"  HS80 {transport_name}: no interface 3 ff42 collection")
            return None
        for d in collections:
            dev = hid.device()
            try:
                dev.open_path(d["path"])
                self._diag.append(f"  HS80 {transport_name} iface={d.get('interface_number')} "
                                  f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                # Discard stale reports so they cannot be mistaken for this query.
                for _ in range(4):
                    if not dev.read(HS80_USB_REPORT_SIZE, FLUSH_TIMEOUT_MS):
                        break
                request = hs80_usb_request(target)
                written = dev.write(request)
                if not isinstance(written, int) or written != len(request):
                    self._diag.append(f"  HS80 {transport_name}: short write ({written!r}/{len(request)})")
                    continue
                for attempt in range(HS80_USB_READ_ATTEMPTS):
                    report = dev.read(HS80_USB_REPORT_SIZE, HS80_USB_READ_TIMEOUT_MS)
                    if not report:
                        continue
                    self._diag.append(f"  HS80 {transport_name} reply {attempt + 1}: {hexdump(report, 12)}")
                    level = parse_hs80_usb_battery(report, target)
                    if level is not None:
                        self._diag.append(f"  HS80 {transport_name} battery: {level}%")
                        return level
                self._diag.append(f"  HS80 {transport_name}: no valid battery reply")
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  HS80 {transport_name} query: {e}")
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
        return None

    @staticmethod
    def _control_collections(infos: List[dict]) -> List[dict]:
        # HeadsetControl corsair_void_v2w.hpp interface 4; Windows capture:
        # https://github.com/HeyOkay/HaloBattery/pull/176#issuecomment-6031065546
        return [d for d in infos if d.get("interface_number") == CONTROL_INTERFACE
                and (d.get("usage_page"), d.get("usage")) == (USAGE_PAGE, USAGE)]

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        collections = self._control_collections(infos)
        if collections:
            return collections[0]
        self._diag.append(f"  no interface {CONTROL_INTERFACE} "
                          f"{USAGE_PAGE:04x}:{USAGE:04x} collection; not opening HID")
        return None

    def _pick_bragi(self, infos: List[dict]) -> List[dict]:
        """Only the ff42:0001 command/reply collection from #56 and #154.

        HeadsetControl's corsair_virtuoso_xt.hpp getCapabilityDetail(),
        lines 97-102, identifies the same pair for the XT/SE devices:
        https://github.com/Sapd/HeadsetControl/blob/master/lib/devices/corsair_virtuoso_xt.hpp#L97-L102
        """
        vend = [d for d in infos
                if (d.get("usage_page"), d.get("usage")) == (BRAGI_VENDOR_PAGE, BRAGI_USAGE)]
        if not vend:
            self._diag.append(f"  no {BRAGI_VENDOR_PAGE:04x}:{BRAGI_USAGE:04x} collection; "
                              "not opening HID")
        return sorted(vend, key=lambda d: d.get("interface_number") or 99)

    def _ask_bragi(self, dev, window: float, parse=parse_bragi) -> Optional[int]:
        """One ask's answer within a time window; the parsed value, or None."""
        deadline = time.monotonic() + window
        for _ in range(BRAGI_READ_ATTEMPTS):
            if time.monotonic() >= deadline:
                return None
            r = dev.read(BRAGI_MSG_SIZE + 1, READ_TIMEOUT_MS)
            if not r:
                continue
            self._diag.append(f"  reply: {hexdump(r)}")
            value = parse(r)
            if value is not None:
                return value
        return None

    def _write_bragi(self, dev, prop: int = BRAGI_PROP_BATTERY,
                     route: int = BRAGI_ROUTE_MOUSE, pid: int = 0x1B7F) -> bool:
        """Send a get-property ask in the framing the receiver takes.

        The Dark Core's dongle takes the 65-byte report-id-0 frame (confirmed on
        hardware), with the bare form as a fallback; the Virtuoso SE receiver
        refuses every report-id-prefixed form with Windows' 0x57 and takes its
        own bare 64-byte frame, extra leading 0x02 included (#204, measured).
        """
        if pid in BRAGI_BARE_PIDS:
            forms = [bragi_se_request(prop, route)]
        else:
            req = bragi_request(prop, route)
            forms = [req, req[1:]]
        framed = True
        for buf in forms:
            try:
                wrote = dev.write(buf)
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  write: {e}")
                wrote = None
            if isinstance(wrote, int) and wrote > 0:
                if not framed:
                    self._diag.append("  (the framed write was refused; "
                                      "the bare form took it)")
                return True
            framed = False
        self._diag.append(f"  prop {prop:02x}: the write was refused")
        return False

    def _read_window(self, dev, window: float, label: str) -> None:
        """Read and log whatever arrives in a short window (session replies)."""
        deadline = time.monotonic() + window
        for _ in range(BRAGI_READ_ATTEMPTS):
            if time.monotonic() >= deadline:
                return
            try:
                r = dev.read(BRAGI_MSG_SIZE + 1, READ_TIMEOUT_MS)
            except (OSError, IOError, ValueError):
                return
            if r:
                self._diag.append(f"  {label}: {hexdump(r)}")

    def _bragi_session(self, dev, deadline: float, pid: int) -> None:
        """The session the SE receiver was measured with (#204): firmware query,
        receiver heartbeat (route 0x08), headset heartbeat (route 0x09)."""
        for prop, route, label in ((BRAGI_PROP_FIRMWARE, BRAGI_ROUTE_DONGLE,
                                    "firmware query"),
                                   (BRAGI_PROP_HEARTBEAT, BRAGI_ROUTE_DONGLE,
                                    "receiver heartbeat"),
                                   (BRAGI_PROP_HEARTBEAT, BRAGI_ROUTE_MOUSE,
                                    "headset heartbeat")):
            if time.monotonic() >= deadline:
                return
            if not self._write_bragi(dev, prop, route, pid):
                self._diag.append(f"  {label}: not sent")
                return
            self._read_window(dev, min(0.8, max(0.0, deadline - time.monotonic())), label)

    @staticmethod
    def _routes(pid: int) -> Tuple[int, ...]:
        """The target order for a pid: the wired ids try themselves first (HSC #570)."""
        if pid == M65_CABLE_PID:
            return (BRAGI_ROUTE_DONGLE,)
        if pid in BRAGI_WIRED_PIDS:
            return (BRAGI_ROUTE_DONGLE, BRAGI_ROUTE_MOUSE)
        if pid in (0x0A3E, 0x0A64):
            return (BRAGI_ROUTE_MOUSE, BRAGI_ROUTE_DONGLE)
        return (BRAGI_ROUTE_MOUSE,)

    def _bragi_charge(self, dev, deadline: float, pid: int,
                      route: int = BRAGI_ROUTE_MOUSE) -> Optional[bool]:
        """The charge state on the receivers that report one (property 0x10),
        or None."""
        if pid not in BRAGI_CHARGE_PIDS or time.monotonic() >= deadline:
            return None
        if not self._write_bragi(dev, BRAGI_PROP_CHARGE, route=route, pid=pid):
            return None
        if pid == 0x1BB5:
            state = self._ask_bragi(dev, min(BRAGI_CONFIRM_WINDOW_S,
                                             deadline - time.monotonic()),
                                    parse=parse_bragi_charge_cable_state)
            if state is None:
                self._diag.append("  no charge-state answer")
                return None
            self._diag.append("  charge state: " +
                              {1: "charging", 2: "on battery", 3: "full"}
                              .get(state, "not charging"))
            return state == 1
        charge_parse = parse_bragi_charge
        if pid == BRAGI_XT_CABLE_PID and route == BRAGI_ROUTE_DONGLE:
            charge_parse = lambda reply: parse_bragi_xt_cable(reply, charging=True)
        value = self._ask_bragi(dev, min(BRAGI_CONFIRM_WINDOW_S,
                                         deadline - time.monotonic()),
                                parse=charge_parse)
        if value is None:
            self._diag.append("  no charge-state answer")
            return None
        self._diag.append("  charge state: " + ("charging" if value else "on battery"))
        return bool(value)

    def _sabre_v3_read(self, dev, timeout_ms: int = SABRE_V3_READ_MS) -> bytes:
        try:
            return bytes(dev.read(SABRE_V3_WRITE_SIZE, max(1, timeout_ms)) or b"")
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  SABRE v3 read: {e}")
            return b""

    def _sabre_v3_ask(self, dev, frame: bytes, endpoint: int, cmd: int,
                      label: str) -> Optional[Tuple[int, bytes]]:
        """Send one request and wait for its own answer, skipping anything else.

        Reports still queued from earlier are dropped first, as OpenLinkHub's
        Slipstream V2 transfer does before every write.
        """
        for n in range(8):
            r = self._sabre_v3_read(dev, FLUSH_TIMEOUT_MS)
            if not r:
                break
            if n < 2:
                self._diag.append(f"  {label}: dropped queued {hexdump(r, 8)}")
        sent = dev.write(frame)
        if sent is not None and sent < 0:
            self._diag.append(f"  {label}: write refused ({sent})")
            return None
        t0 = time.monotonic()
        deadline = t0 + SABRE_V3_REPLY_MS / 1000.0
        skipped = 0
        while True:
            left_ms = int((deadline - time.monotonic()) * 1000)
            if left_ms <= 0:
                break
            r = self._sabre_v3_read(dev, left_ms)
            if not r:
                break
            got = sabre_v3_reply(r, endpoint, cmd)
            if got is None:
                if skipped < 3:
                    self._diag.append(f"  {label}: skipped {hexdump(r, 8)}")
                skipped += 1
                continue
            ms = int((time.monotonic() - t0) * 1000)
            self._diag.append(f"  {label} reply ({ms} ms): {hexdump(r, 8)}")
            return got
        self._diag.append(f"  {label}: no reply")
        return None

    def _sabre_v3_battery(self, dev, endpoint: int, size: int,
                          label: str) -> Tuple[str, Optional[int]]:
        """("ok", level), or ("refused" | "silent" | "empty", None)."""
        got = self._sabre_v3_ask(dev, sabre_v3_frame(SABRE_V3_PROP_BATTERY, endpoint, size),
                                 endpoint, SABRE_V3_CMD_GET, label)
        if got is None:
            return "silent", None
        status, data = got
        if status != 0:
            self._diag.append(f"  {label}: refused, status {status:02x}")
            return "refused", None
        level = parse_sabre_v3_level(data)
        if level is None:
            self._diag.append(f"  {label}: no level in {hexdump(data, 4)}")
            return "empty", None
        return "ok", level

    def _query_sabre_v3(self, path: bytes,
                        pid: int) -> Optional[Tuple[int, Optional[bool]]]:
        """Read the plain properties only; never log in or change the mouse's mode."""
        endpoint, size = SABRE_V3_ROUTES.get(pid, (SABRE_V3_ENDPOINT, SABRE_V3_WRITE_SIZE))
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            self._diag.append(f"  endpoint {endpoint:02x}, {size - 1}-byte reports")
            _state, level = self._sabre_v3_battery(dev, endpoint, size, "battery")
            if level is None:
                self._diag.append("  no SABRE v3 level")
                return None
            charge = None
            got = self._sabre_v3_ask(dev, sabre_v3_frame(SABRE_V3_PROP_CHARGE, endpoint, size),
                                     endpoint, SABRE_V3_CMD_GET, "charge")
            if got is not None:
                status, data = got
                if status == 0:
                    charge = parse_sabre_v3_charge(data)
                    if charge is None:
                        self._diag.append(f"  charge: unknown state {hexdump(data, 2)}")
                else:
                    self._diag.append(f"  charge: refused, status {status:02x}")
            self._diag.append(f"  -> {level}% (plain read), charging="
                              + ("unknown" if charge is None else str(charge)))
            return level, charge
        except (OSError, IOError, ValueError) as e:  # pragma: no cover - hidapi
            self._diag.append(f"  SABRE v3 error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _query_bragi(self, path: bytes, deadline: float,
                     pid: int) -> Optional[Tuple[int, Optional[bool]]]:
        """One battery question to the dongle; (level, charging or None), or None.

        The answer arrives late - measured ~4.7 s on the reporter's dongle with
        the mouse resting (#56) - so the wait is a time window (review by
        @ahmedkhursheed23: traffic frames end a 500 ms read early and used to
        eat a read-count budget). The answer does not echo which property it
        answers, and Windows delivers input reports to every open handle, so a
        second app polling the same dongle could put a foreign answer on the
        channel that parses as a level - the ask is repeated and the two answers
        must agree (a confirming ask that stays silent is noted and the single
        reading taken; a disagreement is refused rather than shown). The SE
        receivers run the measured session first and answer the charge property
        after the level (#204).
        """
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            self._drain(dev)
            if pid in BRAGI_SESSION_PIDS:
                self._bragi_session(dev, deadline, pid)
            routes = self._routes(pid)
            first = None
            route = routes[0]
            for i, route in enumerate(routes):
                if not self._write_bragi(dev, route=route, pid=pid):
                    return None
                parse = (parse_bragi_xt_cable
                         if pid == BRAGI_XT_CABLE_PID and route == BRAGI_ROUTE_DONGLE
                         else parse_bragi_cable if pid == M65_CABLE_PID else parse_bragi)
                first = self._ask_bragi(dev, min(BRAGI_WINDOW_S,
                                                 deadline - time.monotonic()), parse=parse)
                if first is not None:
                    break
                if i + 1 < len(routes):
                    self._diag.append(f"  no answer on target {route:02x}; "
                                      "trying the other")
            if first is None:
                self._diag.append("  no battery answer")
                return None
            if not self._write_bragi(dev, route=route, pid=pid):
                return None
            second = self._ask_bragi(dev, min(BRAGI_CONFIRM_WINDOW_S,
                                              deadline - time.monotonic()), parse=parse)
            if second is None:
                self._diag.append("  (the confirming ask stayed silent; taking the "
                                  "single reading)")
            elif second != first:
                self._diag.append(f"  the two answers disagree ({first} % / {second} %) - "
                                  f"refused")
                return None
            return first, self._bragi_charge(dev, deadline, pid, route)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _write(self, dev, endpoint: int, sub: int, command: int) -> int:
        return self._write_frame(dev, make_request(endpoint, sub, command),
                                 f"{endpoint:02x}/{sub:02x}/{command:02x}")

    def _write_frame(self, dev, frame, label: str) -> int:
        """One write, in the shape this family's receiver accepts on Windows.

        Windows refuses a write whose length is not exactly the collection's
        OutputReportByteLength (STATUS_INVALID_PARAMETER, 0x57) before it ever
        reaches the device - headsetcontrol#521's wall, and #28's, where every
        65-byte write came back -1. The reporter's probe logs show the shape
        that is accepted: 64 bytes with no leading report id. The two shapes
        cannot both be accepted on one collection, so the fallback picks the
        right one by itself.
        """
        n = dev.write(frame[1:])          # 64 bytes: no leading report id
        if isinstance(n, int) and n < 0:
            n = dev.write(frame)          # 65 bytes: the old rid-prefixed form
        if in_probe():
            self._diag.append(f"  [w] {label} -> {n}{_error_note(dev, n)}")
        return n

    def _drain(self, dev, label: str = "") -> None:
        """Drop replies that are still queued, so the next read is ours."""
        for _ in range(4):
            r = dev.read(MSG_SIZE_READ, FLUSH_TIMEOUT_MS)
            if not r:
                return
            if in_probe():
                self._diag.append(f"  [drain{label}] {hexdump(r, MSG_SIZE_READ)}")

    def _listen(self, dev) -> None:
        """Probe only: read whatever arrives, without writing anything.

        With the vendor app open this catches the vendor's own exchange with the
        headset - the ground truth for what a working answer looks like.
        """
        self._diag.append(f"  listening {PROBE_LISTEN_MS // 1000} s without writing anything")
        t0 = time.monotonic()
        while (time.monotonic() - t0) * 1000 < PROBE_LISTEN_MS:
            r = dev.read(MSG_SIZE_READ, 250)
            if r:
                ms = (time.monotonic() - t0) * 1000
                self._diag.append(f"  [{ms / 1000:6.2f}s] {hexdump(r, MSG_SIZE_READ)}")

    def _probe_fallback(self, dev) -> Optional[List[int]]:
        """Probe only: what is left to try when the usual sequence yields nothing.

        The vendor app's Connect() takes the fuller path - software mode on the
        receiver and the headset first - and the desktop software may leave the
        device in a state only that path talks to. The headset can pop audibly
        when the mode changes; that is expected in this run.
        """
        self._diag.append("  probe: trying the vendor app's fuller sequence "
                          "(software mode; the headset may pop)")
        try:
            self._write(dev, RECEIVER_ENDPOINT, FW_SUB, CMD_FIRMWARE)
            self._write_frame(dev, make_software_mode(RECEIVER_ENDPOINT), "08|01/03/00/02")
            self._write(dev, RECEIVER_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            self._write_frame(dev, make_software_mode(HEADSET_ENDPOINT), "09|01/03/00/02")
            self._drain(dev)
            self._write(dev, HEADSET_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            t0 = time.monotonic()
            r = dev.read(MSG_SIZE_READ, PROBE_HB_TIMEOUT_MS)
            if r:
                self._diag.append(f"  probe: heartbeat answered "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ)}")
            else:
                self._diag.append("  probe: no heartbeat after software mode either")
            for i, timeout in enumerate(PROBE_SOFTWARE_TIMEOUT_MS):
                self._write(dev, HEADSET_ENDPOINT, BATTERY_SUB, CMD_BATTERY)
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, timeout)
                if not r:
                    self._diag.append(f"  probe: attempt {i + 1}: no reply after {timeout} ms")
                    continue
                self._diag.append(f"  probe: attempt {i + 1} reply "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ)}")
                if parse_level(r) is not None:
                    return list(r)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  probe: error in the fuller sequence: {e}")
        return None

    def _probe_caps(self, infos: List[dict]) -> Dict[bytes, Optional[Tuple[int, int, int]]]:
        """Probe only: what every collection of this receiver declares.

        #28's logs showed every write refused with -1 before it could reach the
        dongle - a refusal this layer makes locally, indistinguishable from a
        silent device until the caps are on the table.
        """
        caps_by_path = {}
        for d in infos:
            path = d.get("path")
            caps = caps_for(path)
            caps_by_path[path] = caps
            shown = "?" if caps is None else f"in/out/feat={caps[0]}/{caps[1]}/{caps[2]}"
            self._diag.append(f"  caps: iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} {shown}")
        return caps_by_path

    def _probe_matrix(self, infos: List[dict]) -> None:
        """Probe the sourced 64/65-byte shapes on matching control collections.

        #28 and PR #176 establish the two framing variants, not arbitrary
        caps-sized reports or permission to write to sibling notice channels.
        """
        self._diag.append("  probe: write shapes on documented control collections")
        order = self._control_collections(infos)
        frame = make_request(HEADSET_ENDPOINT, BATTERY_SUB, CMD_BATTERY)
        for d in order:
            where = (f"iface={d.get('interface_number')} "
                     f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            shapes = [("write65-rid0", frame), ("write64-norid", frame[1:])]
            dev = hid.device()
            try:
                dev.open_path(d.get("path"))
            except (OSError, IOError) as e:
                self._diag.append(f"  probe: open {where}: {e}")
                continue
            try:
                accepted = False
                accepted_buf = None
                for label, buf in shapes:
                    try:
                        n = dev.write(bytes(buf))
                    except (OSError, IOError, ValueError) as e:
                        n, note = -1, f" ({e})"
                    else:
                        note = _error_note(dev, n)
                    self._diag.append(f"  probe: {label} {where} -> {n}{note}")
                    if isinstance(n, int) and n >= 0:
                        accepted = True
                        accepted_buf = bytes(buf)
                        break
                if not accepted:
                    continue
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, PROBE_HB_TIMEOUT_MS)
                self._diag.append(f"  probe: first answer "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ) if r else 'none'}")
                n2 = dev.write(accepted_buf)
                self._diag.append(f"  probe: re-ask -> {n2}{_error_note(dev, n2)}")
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, PROBE_HB_TIMEOUT_MS)
                self._diag.append(f"  probe: ask again "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(r, MSG_SIZE_READ) if r else 'none'}")
                self._diag.append("  probe: this collection takes the write - build on it")
                break
            finally:
                try:
                    dev.close()
                except Exception:
                    pass

    def _query(self, path: bytes) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            if in_probe():
                self._listen(dev)

            # The handshake that wakes a sleeping headset without an audible pop.
            self._write(dev, RECEIVER_ENDPOINT, FW_SUB, CMD_FIRMWARE)
            self._write(dev, RECEIVER_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            self._drain(dev)
            hb_n = self._write(dev, HEADSET_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            hb_timeout = PROBE_HB_TIMEOUT_MS if in_probe() else READ_TIMEOUT_MS
            t0 = time.monotonic()
            hb = dev.read(MSG_SIZE_READ, hb_timeout)
            if not hb:
                if isinstance(hb_n, int) and hb_n < 0:
                    self._diag.append("  headset heartbeat: the write was refused "
                                      "before it left (nothing was sent)")
                elif in_probe():
                    self._diag.append(f"  headset heartbeat: no reply after "
                                      f"{hb_timeout} ms (headset off or asleep)")
                else:
                    self._diag.append("  headset heartbeat: no reply "
                                      "(headset off or asleep)")
                if in_probe():
                    return self._probe_fallback(dev)
                return None
            if in_probe():
                self._diag.append(f"  headset heartbeat answered "
                                  f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                  f"{hexdump(hb, MSG_SIZE_READ)}")
            self._drain(dev)

            for attempt in range(ATTEMPTS):
                self._write(dev, HEADSET_ENDPOINT, BATTERY_SUB, CMD_BATTERY)
                if in_probe():
                    timeout = PROBE_ATTEMPT_TIMEOUT_MS[min(attempt, len(PROBE_ATTEMPT_TIMEOUT_MS) - 1)]
                else:
                    timeout = READ_TIMEOUT_MS
                t0 = time.monotonic()
                r = dev.read(MSG_SIZE_READ, timeout)
                if not r:
                    if in_probe():
                        self._diag.append(f"  attempt {attempt + 1}: no reply after {timeout} ms")
                    else:
                        self._diag.append(f"  attempt {attempt + 1}: no reply")
                    continue
                if in_probe():
                    self._diag.append(f"  attempt {attempt + 1} reply "
                                      f"({(time.monotonic() - t0) * 1000:.0f} ms): "
                                      f"{hexdump(r, MSG_SIZE_READ)}")
                else:
                    self._diag.append(f"  attempt {attempt + 1} reply: {hexdump(r)}")
                if parse_level(r) is not None:
                    return list(r)
                if in_probe():
                    self._drain(dev, " between attempts")
            self._diag.append("  no usable level in the replies")
            if in_probe():
                return self._probe_fallback(dev)
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _query_void(self, path: bytes) -> Optional[Tuple[int, bool]]:
        """The older Void exchange: write C9 64, read 64 00 <level> B1 <status>."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            # HeadsetControl writes the two bytes as they are; on Windows a short write
            # is refused when the descriptor's output report is longer, so the padded
            # form is tried after it. Both carry the same command.
            for form in (void_request(), void_request() + [0] * (MSG_SIZE_WRITE - 2)):
                try:
                    n = dev.write(form)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"  write ({len(form)} bytes): {e}")
                    continue
                self._diag.append(f"  [w] ({len(form)} bytes) -> {n}")
                for _ in range(ATTEMPTS):
                    r = dev.read(MSG_SIZE_READ, READ_TIMEOUT_MS)
                    if not r:
                        break
                    self._diag.append(f"  reply: {hexdump(r, 6)}")
                    got = parse_void(r)
                    if got is not None:
                        return got
            self._diag.append("  no battery answer")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(CORSAIR_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(corsair): %s", e)
            return []
        out = []
        seen = set()
        hs80_usb = [d for d in infos if d.get("product_id") == HS80_USB_PID]
        hs80_cable_present = any(d.get("product_id") in
                                 (HS80_USB_PID, HS80_CHARGING_CABLE_PID) for d in infos)
        hs80_found = False
        if hs80_usb:
            self._diag.append("[Corsair HS80 USB] original HS80 RGB Wireless, direct USB")
            level = self._query_hs80_usb(hs80_usb)
            if level is not None:
                # The 0a69 identity is the powered-on direct USB mode; the
                # protocol exposes no charge flag, so cable presence signals charging.
                out.append(DeviceStatus("corsair:hs80", "Corsair HS80 RGB Wireless",
                                        level, True, True, "corsair", kind="headset"))
                hs80_found = True
        hs80_receiver = [d for d in infos if d.get("product_id") == 0x0A6B]
        if hs80_receiver and not hs80_found:
            self._diag.append("[Corsair HS80 receiver] original HS80 RGB Wireless, wireless")
            level = self._query_hs80_usb(hs80_receiver, target=0x09,
                                         transport_name="wireless")
            if level is not None:
                out.append(DeviceStatus("corsair:hs80", "Corsair HS80 RGB Wireless",
                                        level, hs80_cable_present, True, "corsair", kind="headset"))
                hs80_found = True
        if hs80_cable_present and not hs80_found:
            # With the headset switched off, USB enumerates as 0a6a and exposes
            # no battery control collection. Keep the device visible and animate
            # charging; the charge percentage is unavailable in this state.
            out.append(DeviceStatus("corsair:hs80", "Corsair HS80 RGB Wireless",
                                    None, True, True, "corsair", kind="headset"))
        for pid in PIDS:
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            d = self._pick(mine)
            if d is None:
                continue
            seen.add(d["path"])
            name = PIDS[pid]
            self._diag.append(f"[Corsair] pid={pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            if in_probe():
                self._diag.append(f"  path: {d.get('path', b'')!r}")
                self._probe_caps(mine)
            reply = self._query(d["path"])
            if reply is None and in_probe():
                self._probe_matrix(mine)
            level = parse_level(reply)
            if level is None:
                continue
            out.append(DeviceStatus(f"corsair:{pid:04x}", name, level, False, True,
                                    "corsair", kind="headset"))
        # The SABRE v3 PRO is the Slipstream V2 exchange. The mouse's own
        # identity (2b43) appears while its cable connects it to this PC and is
        # asked first; the receiver (2b42) is asked as well, so a diagnostics
        # shows both paths. A cable reading wins. When the mouse gives no charge
        # state, its cable identity being present means it is charging from
        # this PC (the HS80 rule above).
        sabre: Dict[int, Tuple[int, Optional[bool]]] = {}
        sabre_cable = any(d.get("product_id") == SABRE_V3_CABLE_PID for d in infos)
        for pid in SABRE_V3_PIDS:
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            d = next((i for i in mine
                      if (i.get("usage_page"), i.get("usage")) == (BRAGI_VENDOR_PAGE,
                                                                   BRAGI_USAGE)), None)
            if d is None:
                self._diag.append(f"[Corsair SABRE v3] pid={pid:04x}: no "
                                  f"{BRAGI_VENDOR_PAGE:04x}:{BRAGI_USAGE:04x} collection")
                continue
            self._diag.append(f"[Corsair SABRE v3] pid={pid:04x} "
                              f"'{SABRE_V3_PIDS[pid]}' iface={d.get('interface_number')}")
            seen.add(d["path"])
            got = self._query_sabre_v3(d["path"], pid)
            if got is not None:
                sabre[pid] = got
        if sabre:
            pid = SABRE_V3_CABLE_PID if SABRE_V3_CABLE_PID in sabre else next(iter(sabre))
            level, charging = sabre[pid]
            if charging is None and sabre_cable:
                charging = True
                self._diag.append("[Corsair SABRE v3] charging: the mouse's cable "
                                  "identity is present")
            out.append(DeviceStatus(
                SABRE_V3_KEY, SABRE_V3_PIDS[pid], level, bool(charging), True,
                "corsair", kind="mouse",
                via="USB cable" if pid == SABRE_V3_CABLE_PID else "2.4 GHz receiver",
                charging_known=charging is not None,
            ))

        found: Dict[str, Tuple[int, bool, str, str]] = {}
        for pid, name in BRAGI_PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            # The M65's cable and receiver can both be present while charging. The
            # cable is asked first; when it answered, the receiver has no level to
            # add and asking it would only spend its read budget.
            if pid == 0x1BA6 and BRAGI_KEYS[0x1BB5] in found:
                self._diag.append("[Corsair bragi] pid=1ba6 'Corsair M65 RGB Ultra "
                                  "Wireless' - cable identity already read; receiver skipped")
                continue
            self._diag.append(f"[Corsair bragi] pid={pid:04x} '{name}'")
            deadline = time.monotonic() + BRAGI_POLL_BUDGET_S
            for d in self._pick_bragi(mine):
                if time.monotonic() >= deadline:
                    self._diag.append("  (the read budget is spent; the remaining "
                                      "collections are skipped)")
                    break
                self._diag.append(f"  iface={d.get('interface_number')} "
                                  f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                seen.add(d["path"])
                got = self._query_bragi(d["path"], deadline, pid)
                if got is None:
                    continue
                level, charging = got
                key = BRAGI_KEYS[pid]
                kind = BRAGI_KINDS.get(pid, "headset")
                kept = found.get(key)
                # a receiver and a cable of one headset are one icon; when both
                # answer, the one that reports charging wins (the cable state)
                if kept is None or (charging and not kept[1]):
                    found[key] = (level, bool(charging), name, kind)
                break
        for key, (level, charging, name, kind) in found.items():
            out.append(DeviceStatus(key, name, level, charging, True, "corsair",
                                    kind=kind))
        for pid, name in VOID_PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            # HeadsetControl corsair_void_rich.hpp getCapabilityDetail: FFC5:0001.
            vend = [d for d in mine
                    if (d.get("usage_page"), d.get("usage")) == (VOID_VENDOR_PAGE, VOID_USAGE)]
            self._diag.append(f"[Corsair void] pid={pid:04x} '{name}'"
                              + ("" if vend else f" (no {VOID_VENDOR_PAGE:04x} collection)"))
            for d in sorted(vend,
                            key=lambda x: (0 if x.get("usage") == VOID_USAGE else 1,
                                           x.get("interface_number") or 0)):
                self._diag.append(f"  iface={d.get('interface_number')} "
                                  f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                seen.add(d["path"])
                got = self._query_void(d["path"])
                if got is None:
                    continue
                level, charging = got
                out.append(DeviceStatus(f"corsair:void:{pid:04x}", name, level,
                                        charging, True, "corsair", kind="headset"))
                break
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
