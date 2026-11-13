"""GameSir G7 Pro 8K PC family: the battery over the pad's GameSir Connect collection.

On its 2.4 GHz dongle or its cable the pad is an Xbox 360 controller for Windows.
XInput says "wired" and Windows.Gaming.Input returns the remain=full=1000
placeholder whatever the charge, which showed as a permanent 100 %.

Source: GameSir Connect 1.11.9 (proxyG7ProCE). The pad's second HID collection
(usage page 0xFFF0, usage 0x40) carries input report 0x12, "gamepad key state",
which parseGamepadState() reads: byte 35 is the charge state (1 = charging),
byte 36 the level in percent.

The pad does not send that report on its own: an idle pad stayed silent for
minutes, stick moving or not. It answers as soon as it is asked anything on that
collection, so the read-only request GameSir Connect sends on every connection,
"read firmware version" (0F 0B), is sent and report 0x12 follows (0.00-0.01 s).
Only this request is sent, never a configuration command.

Confirmed on hardware by the owner on the Aimlabs edition, 2026-11-13: the dongle
(3537:10c6) and the cable (3537:10c5); 100 / 98 / 97 % matched GameSir Connect;
bytes "01 60" on the cable and on the charging dock = charging, 96 %. Right after
power-on the pad said 100 % once, then 98 % two minutes later: its own estimate
settling, not a parsing error. The other editions come from GameSir Connect's
table and are unverified.
"""
from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

import hid

from . import hidlist
from .base import hexdump

VENDOR_VID = 0x3537
USAGE_PAGE = 0xFFF0
STATE_REPORT = 0x12
CHARGE_BYTE = 35
LEVEL_BYTE = 36
STATUS_REQUEST = bytes([0x0F, 0x0B])     # "read firmware version": read-only

MODEL = "GameSir G7 Pro 8K"
# GameSir Connect's G7ProCE modes: pid -> edition. Each pair is cable, dongle.
EDITIONS: Dict[int, str] = {
    0x10B7: "", 0x10B9: "", 0x10CB: "", 0x10CC: "", 0x10CD: "", 0x10CE: "",
    0x10C5: "Aimlabs", 0x10C6: "Aimlabs",
    0x10C7: "Nioh 3", 0x10C8: "Nioh 3",
    0x10C9: "NBA 2K", 0x10CA: "NBA 2K",
    0x10FD: "Golden Apple", 0x10FE: "Golden Apple",
}


class Reading(NamedTuple):
    level: int
    charging: bool
    approx: str
    name: str


def is_supported(pid: int) -> bool:
    return pid in EDITIONS


def parse_state(frame, pid: int = 0) -> Optional[Reading]:
    if len(frame) <= LEVEL_BYTE or frame[0] != STATE_REPORT:
        return None
    if frame[LEVEL_BYTE] > 100:
        return None
    # GameSir Connect only tests the charge state against 1; any other value is
    # "not charging" there, so the level is kept and no charging state is invented.
    edition = EDITIONS.get(pid, "")
    name = f"{MODEL} {edition}" if edition else MODEL
    return Reading(frame[LEVEL_BYTE], frame[CHARGE_BYTE] == 1, "", name)


def read_connected(diag: List[str]) -> Optional[Reading]:
    """Read only the vendor collection; the caller identifies the XInput slot."""
    cols = [d for d in hidlist.enumerate(VENDOR_VID)
            if is_supported(d.get("product_id") or 0)
            and d.get("usage_page") == USAGE_PAGE]
    # More than one pad cannot be matched to the one XInput slot reliably.
    if len(cols) != 1:
        diag.append(f"[GameSir] {len(cols)} vendor collection(s): not read")
        return None
    pid = cols[0]["product_id"]
    dev = hid.device()
    try:
        dev.open_path(cols[0]["path"])
        sent = dev.write(STATUS_REQUEST)
        if sent is not None and sent < len(STATUS_REQUEST):
            diag.append(f"[GameSir] request write failed ({sent})")
            return None
        for _ in range(40):
            frame = bytes(dev.read(64, 25))
            if not frame:
                continue
            reading = parse_state(frame, pid)
            if reading is not None:
                diag.append(f"[GameSir] {reading.name} pid={pid:04x} report 0x12 bytes 35-36: "
                            f"{hexdump(frame[CHARGE_BYTE:LEVEL_BYTE + 1], 2)} -> "
                            f"{reading.level}%, charging={reading.charging}")
                return reading
        diag.append(f"[GameSir] pid={pid:04x}: no state report after the request")
    except (OSError, ValueError) as e:
        diag.append(f"[GameSir] {e}")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return None
