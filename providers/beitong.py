"""BEITONG KP40D's battery over its 2.4 GHz receiver (20bc:515c).

Source: owner's USBPcap capture, beitong-battery-20261110.pcapng, 2026-11-10,
SHA-256 ac71c6a2f6e4b012f97bb130ab4f05ca2ca2fc5b2b3415c143a063859536e9ec.
iControl 1.0.5 sends report 02 15 followed by 62 zero bytes on endpoint 04;
endpoint 83 answers with a 64-byte report starting 02 15 (e.g. frame 713).
Byte 2 is the reported level; byte 4 was 50 on battery and 51 on the charger
(frames 7189 and 8821). The reported level rises while charging and settles
after unplugging: it is the controller's estimate, not a calibrated capacity.
Only this status request is sent, never iControl's configuration commands.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional

import hid

from . import hidlist
from .base import hexdump

VENDOR_VID = 0x20BC
PRODUCT_PID = 0x515C
STATUS_REQUEST = bytes([0x02, 0x15]) + bytes(62)


class Reading(NamedTuple):
    level: int
    charging: bool
    approx: str
    name: str


def parse_reply(frame: bytes) -> Optional[Reading]:
    if len(frame) != 64 or frame[:2] != b"\x02\x15":
        return None
    if frame[2] > 100 or frame[4] not in (0x50, 0x51):
        return None
    return Reading(frame[2], frame[4] == 0x51, "", "BEITONG KP40D")


def read_connected(diag: List[str]) -> Optional[Reading]:
    """Read only the vendor collection; the caller identifies the XInput slot."""
    cols = [d for d in hidlist.enumerate(VENDOR_VID)
            if d.get("product_id") == PRODUCT_PID
            and d.get("interface_number") == 1
            and d.get("usage_page") == 0x00FF and d.get("usage") == 0x0003]
    # More than one receiver cannot be matched to the one XInput slot reliably.
    if len(cols) != 1:
        diag.append(f"[Beitong] {len(cols)} vendor collection(s): not read")
        return None
    dev = hid.device()
    try:
        dev.open_path(cols[0]["path"])
        for _ in range(10):
            if not dev.read(64, 2):
                break
        sent = dev.write(STATUS_REQUEST)
        if sent is not None and sent < len(STATUS_REQUEST):
            diag.append(f"[Beitong] status write failed ({sent})")
            return None
        for _ in range(20):
            frame = bytes(dev.read(64, 25))
            if not frame:
                continue
            reading = parse_reply(frame)
            if reading is not None:
                diag.append(f"[Beitong] status: {hexdump(frame, 10)} -> "
                            f"{reading.level}%, charging={reading.charging}")
                return reading
        diag.append("[Beitong] no valid status reply")
    except (OSError, ValueError) as e:
        diag.append(f"[Beitong] {e}")
    finally:
        try:
            dev.close()
        except Exception:
            pass
    return None
