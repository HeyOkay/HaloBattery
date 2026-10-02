"""FlyDigi pads' own battery report, over their 0xFFA0 vendor collection.

The Vader 5 Pro (37d7:2401) shows up as an Xbox 360 pad to XInput; over its 2.4 GHz
dongle XInput reports the battery type as "wired" (the dongle looks like a cable), and
its Windows.Gaming.Input battery report is the constant remain=full=1000 placeholder -
so neither API has a level for it (#191; the same placeholder the #110 dock showed).
The pad answers on its own vendor channel: SDL's hidapi driver keeps exactly the 0xFFA0
usage-page collection of these ids (SDL_hidapi.c) and speaks a 32-byte report protocol
on it (SDL_hidapi_flydigi.c) that needs no driver and no libusb - plain HID, so the
same exchange works from here.

The exchange: a write of ``00 5A A5 01 02 00`` - the first byte zeroed, because the
Vader 5 uses unnumbered reports - is answered by 32-byte reports starting
``5A A5 01`` (numbered reports carry their report id first; both are accepted). In the
reply, byte 5 is the device id (130 for the Vader 5 Pro), byte 6 the connection, bytes
15-16 the firmware, and byte 11 the battery: its high nibble is the state (0 on
battery, 1 charging, 2 charged) and its low nibble the level step, x20 for the Vader
family (SDL shows the same number through SDL_JoystickCurrentPowerLevel). The reads are
pure HID input reports, so this never writes anything but the request, and a reply
whose state or level step is out of range is refused rather than shown.

The pad's own input stream shares the collection, so the reads drain reports until the
answer shows up; the first write shape that provokes one is remembered in the
diagnostics, and the other shapes are only tried after a variant produced nothing (the
ZEROED one is what SDL sends for this model). Unverified on hardware here: a test build
for the reporters of #191 settles the exact write shape and the level mapping.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional, Tuple

import hid

from . import hidlist
from .base import hexdump

VENDOR_VID = 0x37D7

# the vendor collection every one of these pads keeps its own protocol on
VENDOR_USAGE_PAGE = 0xFFA0

# PID -> name, from the ids SDL's driver knows with this channel
KNOWN = {
    0x2401: "FlyDigi Vader 5 Pro",
}

# SDL's request for the device info record (``02`` asks for the Vader's record shape),
# with the first byte zeroed: the Vader 5 uses unnumbered reports. The numbered and the
# full-length shapes are fallbacks for a collection that keeps its report id or refuses
# a short write; the first shape that provokes an answer is the one the diagnostics name.
INFO_REQUEST = bytes([0x00, 0x5A, 0xA5, 0x01, 0x02, 0x00])
INFO_REQUEST_PADDED = INFO_REQUEST + bytes(32 - len(INFO_REQUEST))
INFO_REQUEST_NUMBERED = bytes([0x03]) + INFO_REQUEST[1:]
INFO_REQUEST_NUMBERED_PADDED = bytes([0x03]) + INFO_REQUEST_PADDED[1:]

DRAIN_READS = 40          # queued input-stream reports dropped before the request
DRAIN_TIMEOUT_MS = 2
READS = 20                # reads per write shape while the answer is awaited
READ_TIMEOUT_MS = 25
REPORT_SIZE = 32

STATE_TEXT = {0: "", 1: "charging", 2: "fully charged"}


class Reading(NamedTuple):
    level: int
    charging: bool
    approx: str
    name: str


def collections() -> List[dict]:
    """The pad's 0xFFA0 vendor collections, as hidapi describes them."""
    out = []
    try:
        infos = hidlist.enumerate(VENDOR_VID)
    except Exception:
        return []
    for d in infos:
        if d.get("product_id") in KNOWN and d.get("usage_page") == VENDOR_USAGE_PAGE:
            out.append(d)
    return out


def _parse_reply(frame: bytes) -> Optional[Tuple[bytes, int, int, Tuple[int, int], int]]:
    """-> (body, device id, connection, firmware, battery byte) or None.

    A report that is not the device info answer (the pad's input stream shares the
    collection) is None, not an error.
    """
    if len(frame) < 17:
        return None
    body = frame[1:] if frame[0] != 0x5A else frame
    if len(body) < 17 or body[0] != 0x5A or body[1] != 0xA5 or body[2] != 0x01:
        return None
    return bytes(body), body[5], body[6], (body[15], body[16]), body[11]


def _battery(state: int, step: int) -> Optional[Tuple[int, bool, str]]:
    """-> (level, charging, approx text); None when the reply's shape is unexpected."""
    if state not in STATE_TEXT or step > 5:
        return None
    level = 100 if state == 2 else step * 20
    return level, state in (1, 2), STATE_TEXT[state]


def read_battery(path, diag: List[str], name: str) -> Optional[Reading]:
    """Ask one collection for the device info record -> the pad's reading, or None."""
    dev = hid.device()
    try:
        dev.open_path(path)
    except (OSError, IOError, ValueError) as e:
        diag.append(f"[Flydigi] open: {e}")
        return None
    try:
        # the input stream shares this collection: drop what is already queued, or the
        # answer can sit behind a long burst of input reports
        for _ in range(DRAIN_READS):
            try:
                if not dev.read(REPORT_SIZE, DRAIN_TIMEOUT_MS):
                    break
            except (OSError, ValueError):
                break
        seen: List[bytes] = []
        wrote = ""
        for shape, request in (("", INFO_REQUEST), (" (padded)", INFO_REQUEST_PADDED),
                               (" (numbered)", INFO_REQUEST_NUMBERED),
                               (" (numbered padded)", INFO_REQUEST_NUMBERED_PADDED)):
            try:
                sent = dev.write(request)
            except (OSError, ValueError) as e:
                diag.append(f"[Flydigi] write: {e}")
                return None
            if sent != len(request):
                diag.append(f"[Flydigi] write{shape} returned {sent} of {len(request)} bytes")
                continue
            wrote = shape
            for _ in range(READS):
                try:
                    frame = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
                except (OSError, ValueError) as e:
                    diag.append(f"[Flydigi] read: {e}")
                    return None
                if not frame:
                    continue
                frame = bytes(frame)
                if len(seen) < 3 and frame not in seen:
                    seen.append(frame)
                parsed = _parse_reply(frame)
                if parsed is None:
                    continue
                body, device, connection, firmware, byte = parsed
                diag.append(f"[Flydigi] info reply{shape}: device={device} "
                            f"connection={connection} firmware={firmware[0]}.{firmware[1]} "
                            f"battery byte=0x{byte:02x}")
                got = _battery((byte >> 4) & 0x0F, byte & 0x0F)
                if got is None:
                    diag.append(f"[Flydigi] unexpected battery byte 0x{byte:02x}: not shown")
                    return None
                level, charging, approx = got
                return Reading(level, charging, approx, name)
        diag.append("[Flydigi] no info reply"
                    + (f" (last shape tried:{wrote or ' none answered'})" if wrote else ""))
        for frame in seen:
            diag.append(f"[Flydigi]   saw: {hexdump(frame)}")
        return None
    finally:
        try:
            dev.close()
        except Exception:
            pass


def read_connected(diag: List[str]) -> Optional[Reading]:
    """Read the one connected FlyDigi pad, if there is one.

    Called only when exactly one XInput slot is connected, so a reading cannot be
    attributed to the wrong controller.
    """
    cols = collections()
    if not cols:
        return None
    name = KNOWN.get(cols[0].get("product_id"), "FlyDigi controller")
    diag.append(f"[Flydigi] {name} (pid={cols[0].get('product_id'):04x}), "
                f"{len(cols)} vendor collection(s)")
    for d in cols:
        reading = read_battery(d["path"], diag, name)
        if reading is not None:
            return reading
    return None
