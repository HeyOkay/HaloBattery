"""ASUS ROG Raikiri II controller, vendor battery query (#269).

The controller enumerates as 0b05:1c92 and exposes a vendor collection on
interface 4, usage page 0xFF03 / usage 0x0001. Its read-only query is

    B0 12 04 00 ...        (64-byte feature report)

answered with the same header; the level is zero-based byte 5 (counting the
report id) and byte 10 is the charging state according to ASUS GearLink's
SDK. The reporter confirmed the level on hardware (65 % while Armoury Crate
showed 65 %); the charging byte is not yet confirmed.

The app must not show the generic Windows "Xbox 360 Controller" icon next to
this reading, so this module is called from the XInput provider's one-slot
pairing rather than polled as a second provider.
"""
from __future__ import annotations

from typing import List, NamedTuple, Optional

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover
    hid = None

from . import hidlist
from .base import hexdump, log

VID = 0x0B05
PID = 0x1C92
NAME = "ROG Raikiri II"
USAGE = (0xFF03, 0x0001)
INTERFACE = 4
QUERY = bytes([0xB0, 0x12, 0x04]) + bytes(61)
REPORT_LEN = 64
LEVEL_INDEX = 5
CHARGING_INDEX = 10


class Reading(NamedTuple):
    level: int
    charging: bool
    name: str
    approx: str = ""


def parse_reply(frame) -> Optional[Reading]:
    data = list(frame or [])
    if len(data) <= CHARGING_INDEX:
        return None
    if data[0] != 0xB0 or data[1] != 0x12 or data[2] != 0x04:
        return None
    level = data[LEVEL_INDEX]
    if not 0 <= level <= 100:
        return None
    return Reading(level, data[CHARGING_INDEX] != 0x00, NAME)


def collection(infos: List[dict]) -> Optional[dict]:
    for d in infos:
        if d.get("product_id") != PID:
            continue
        if (d.get("usage_page"), d.get("usage")) != USAGE:
            continue
        if d.get("interface_number") != INTERFACE:
            continue
        return d
    return None


def read_connected(diag: List[str]) -> Optional[Reading]:
    """Read the vendor collection; the caller owns the XInput slot."""
    if hid is None:
        return None
    try:
        infos = hidlist.enumerate(VID)
    except Exception as e:  # pragma: no cover
        log.warning("hid.enumerate(raikiri): %s", e)
        return None
    d = collection(infos)
    if d is None:
        diag.append("[Raikiri] no 0b05:1c92 ff03:0001 collection")
        return None
    dev = hid.device()  # type: ignore[union-attr]
    try:
        dev.open_path(d["path"])
        sent = dev.send_feature_report(QUERY)
        if sent is not None and sent < 0:
            diag.append(f"[Raikiri] feature write refused ({sent})")
            return None
        r = dev.get_feature_report(0, REPORT_LEN)
        if r:
            diag.append(f"[Raikiri] reply: {hexdump(r, 12)}")
        reading = parse_reply(r)
        if reading is not None:
            diag.append(f"[Raikiri] {reading.name}: {reading.level}%"
                        f"{' charging' if reading.charging else ''}")
        return reading
    except (OSError, IOError, ValueError) as e:
        diag.append(f"[Raikiri] {e}")
        return None
    finally:
        try:
            dev.close()
        except Exception:
            pass
