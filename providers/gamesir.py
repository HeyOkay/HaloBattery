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
collection, so the read-only request GameSir Connect sends when it loads the
pad's profile, "read current profile" (0F 0B, getReadCurrentProfileCommand), is
sent and report 0x12 follows (0.00-0.01 s). Only this request is sent, never a
configuration command. (0F 09, "read firmware version", was not tried.)

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
USAGE = 0x40
STATE_REPORT = 0x12
CHARGE_BYTE = 35
LEVEL_BYTE = 36
STATUS_REQUEST = bytes([0x0F, 0x0B])     # "read current profile": read-only

MODEL = "GameSir G7 Pro 8K"
# GameSir Connect's other pads on the same fff0:0040 gamepad-state report (proxyC2's
# parseGamepadState reads charge/level from bytes 35-36 just as proxyG7ProCE does).
# The Cyclone 2's receiver is 3537:100b (#221); its own app shows 85 % while Windows
# reports the 1000/1000 placeholder.
OTHER_MODELS: Dict[int, str] = {
    0x100B: "GameSir Cyclone 2",     # 2.4 GHz receiver
    0x0575: "GameSir Cyclone 2",     # cable / charging dock (#221)
}
# Windows exposes the charging identity as a generic Xbox 360 pad; pair it to the
# GameSir HID collection instead of trusting the Xbox PID alone.
XINPUT_WIRED_PID = 0x1053
CYCLONE_CHARGING_PID = 0x0575
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
    return pid in EDITIONS or pid in OTHER_MODELS


def parse_state(frame, pid: int = 0) -> Optional[Reading]:
    if len(frame) <= LEVEL_BYTE or frame[0] != STATE_REPORT:
        return None
    if frame[LEVEL_BYTE] > 100:
        return None
    # GameSir Connect only tests the charge state against 1; any other value is
    # "not charging" there, so the level is kept and no charging state is invented.
    if pid in OTHER_MODELS:
        name = OTHER_MODELS[pid]
    else:
        edition = EDITIONS.get(pid, "")
        name = f"{MODEL} {edition}" if edition else MODEL
    return Reading(frame[LEVEL_BYTE], frame[CHARGE_BYTE] == 1, "", name)


def supported_collections() -> List[dict]:
    """The vendor collections of a supported GameSir pad, if any."""
    try:
        infos = hidlist.enumerate(VENDOR_VID)
        out = []
        cyclone_hid = False
        for d in infos:
            pid = d.get("product_id") or 0
            if pid not in OTHER_MODELS and pid not in EDITIONS:
                continue
            if d.get("usage_page") != USAGE_PAGE or d.get("usage") != USAGE:
                continue
            if (pid == CYCLONE_CHARGING_PID
                    and "cyclone" not in (d.get("product_string") or "").casefold()):
                # 0x0575 is also the idle G7 dongle; only the Cyclone 2's
                # "GameSir-Cyclone 2" identity is the charging collection (#221).
                continue
            out.append(d)
            if pid == CYCLONE_CHARGING_PID:
                cyclone_hid = True
        if cyclone_hid:
            # While charging, Windows also exposes the same pad on the generic
            # Xbox id. Its fff0:0040 collection can carry the state the 0575
            # collection answers only as zeros (#221 follow-up).
            out += [d for d in infos
                    if d.get("product_id") == XINPUT_WIRED_PID
                    and d.get("usage_page") == USAGE_PAGE
                    and d not in out]
        return out
    except (OSError, ValueError):
        return []


def has_supported_collection() -> bool:
    """True when a supported GameSir HID collection is present."""
    return bool(supported_collections())


def read_connected(diag: List[str]) -> Optional[Reading]:
    """Read only the vendor collection; the caller identifies the XInput slot."""
    cols = supported_collections()
    # One pad cannot be matched to the one XInput slot reliably. The charging
    # identity adds the generic 1053 collection as a fallback, so two entries
    # are expected exactly when 0575 + 1053 belong to the same pad.
    pids = {d.get("product_id") for d in cols}
    if len(cols) == 0 or len(cols) > 2 or (
            len(cols) == 2 and pids != {CYCLONE_CHARGING_PID, XINPUT_WIRED_PID}):
        diag.append(f"[GameSir] {len(cols)} vendor collection(s): not read")
        return None
    has_cyclone = any(d.get("product_id") == CYCLONE_CHARGING_PID for d in cols)
    for d in cols:
        pid = d["product_id"]
        dev = hid.device()
        try:
            dev.open_path(d["path"])
            sent = dev.write(STATUS_REQUEST)
            if sent is not None and sent < len(STATUS_REQUEST):
                diag.append(f"[GameSir] pid={pid:04x} request write failed ({sent})")
                continue
            skipped_zero = False
            for _ in range(40):
                frame = bytes(dev.read(64, 25))
                if not frame:
                    continue
                # The charging identity answers the request with an all-zero state
                # report before its real one (#221, 3537:0575). Treating that as
                # 0 % froze the icon at zero while the pad was charging; wait for
                # the next report instead.
                if (len(frame) > LEVEL_BYTE and frame[0] == STATE_REPORT
                        and frame[CHARGE_BYTE] == 0 and frame[LEVEL_BYTE] == 0):
                    skipped_zero = True
                    diag.append(f"[GameSir] pid={pid:04x}: zero state report; waiting for the real one")
                    continue
                reading = parse_state(frame, pid)
                if reading is not None:
                    if pid == XINPUT_WIRED_PID and has_cyclone:
                        # The generic 1053 collection carries the Cyclone's state;
                        # its pid alone maps to the G7 Pro name (#221).
                        reading = reading._replace(name=OTHER_MODELS[CYCLONE_CHARGING_PID])
                    diag.append(f"[GameSir] {reading.name} pid={pid:04x} report 0x12 bytes 35-36: "
                                f"{hexdump(frame[CHARGE_BYTE:LEVEL_BYTE + 1], 2)} -> "
                                f"{reading.level}%, charging={reading.charging}")
                    return reading
            if skipped_zero:
                diag.append(f"[GameSir] pid={pid:04x}: only zero state reports after the request")
            diag.append(f"[GameSir] pid={pid:04x}: no state report after the request")
        except (OSError, ValueError) as e:
            diag.append(f"[GameSir] pid={pid:04x}: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
    return None
