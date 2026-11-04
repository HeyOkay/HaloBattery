"""Plantronics / Poly headsets behind the BT600 USB adapter (Voyager 8200 UC).

The BT600 is a Bluetooth relay, not a vendor dongle: the headset talks to it over
Bluetooth and Poly's software reads the headset through it, with a get/set protocol
tunnelled in HID report 0x07 on the vendor collection 0xffa2:0003 (interface 3).
From HeadsetControl's plantronics_bt600.hpp, whose class covers this headset.

Frame (64 bytes, zero padded):

    07 01 01 10 <len> <addr> 00 00 <op> <grp> <item> <value...>

<addr> 0x00 is the adapter itself, 0x20 the headset; replies carry 0x00 (adapter) or
0x02 (headset). Before anything answers, the adapter needs the Windows attach sequence:
a two-byte output report 06 01, a HELLO to the adapter, then a HELLO to the headset.
It is idempotent, so it is replayed before every read.

Battery: GET group 0x0A item 0x1A. The value is <level> <level_count> <charging>
<minutes hi> <minutes lo> <marker>, and the percentage is Poly's own
level / (level_count - 1) * 100 - level_count includes 0, so 0x0B means ten steps.
The minutes are the remaining talk time and are not used for the level.

Only the battery is read here: no setting is ever written.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x047F

# 0x02EE is the id HeadsetControl's own class covers; 0x02F7 is the adapter in the
# #328 report, with the same collections and the same product string.
PIDS = {0x02EE: "Plantronics headset", 0x02F7: "Plantronics headset"}

VENDOR_PAGE = 0xFFA2
VENDOR_USAGE = 0x0003
INTERFACE = 3

REPORT_ID = 0x07
MSG_SIZE = 64
HEADER = (0x01, 0x01, 0x10)

OFF_ADDR, OFF_OP, OFF_GRP, OFF_ITEM = 5, 8, 9, 10
OFF_LEVEL, OFF_COUNT, OFF_CHARGING, OFF_MIN_HI, OFF_MIN_LO = 11, 12, 13, 14, 15

ADDR_DONGLE = 0x00
ADDR_DONGLE_REPLY = 0x00
ADDR_HEADSET = 0x20
ADDR_HEADSET_REPLY = 0x02

OP_HELLO = 0x01
OP_GET = 0x02
OP_HELLO_ACK = 0x08
OP_GET_REPLY = 0x03

GRP_HELLO, ITEM_HELLO = 0x01, 0x02
GRP_BATTERY, ITEM_BATTERY = 0x0A, 0x1A

SESSION_NUDGE = [0x06, 0x01]
MAX_READS = 16
READ_TIMEOUT_MS = 1000


def frame(op: int, grp: int, item: int, addr: int = ADDR_HEADSET) -> List[int]:
    """A 64-byte request. The length byte is 0x06 for a GET and 0x07 for the rest."""
    f = [REPORT_ID, HEADER[0], HEADER[1], HEADER[2],
         0x06 if op == OP_GET else 0x07, addr, 0x00, 0x00, op, grp, item]
    return f + [0x00] * (MSG_SIZE - len(f))


def is_reply_for(r, grp: int, item: int, addr: int) -> bool:
    return (len(r) > OFF_ITEM and r[0] == REPORT_ID
            and (r[1], r[2], r[3]) == HEADER
            and r[OFF_ADDR] == addr and r[OFF_GRP] == grp and r[OFF_ITEM] == item)


def parse_battery(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a battery reply, or None when it is not that shape."""
    if not is_reply_for(r, GRP_BATTERY, ITEM_BATTERY, ADDR_HEADSET_REPLY):
        return None
    if r[OFF_OP] != OP_GET_REPLY:
        return None
    count = r[OFF_COUNT]
    if count < 2:                       # no denominator: not a battery structure
        return None
    level = r[OFF_LEVEL]
    if level >= count:                  # out of the adapter's own scale
        return None
    return min(100, level * 100 // (count - 1)), r[OFF_CHARGING] != 0


class PlantronicsProvider(Provider):
    name = "plantronics"

    def __init__(self):
        self._diag: List[str] = []

    def _await(self, dev, op: int, grp: int, item: int, addr: int):
        """The next reply for (grp, item), skipping events and catalogue frames."""
        for _ in range(MAX_READS):
            r = dev.read(MSG_SIZE, READ_TIMEOUT_MS)
            if not r:
                return None
            if is_reply_for(r, grp, item, addr) and (op is None or r[OFF_OP] == op):
                return list(r)
        return None

    def _query(self, path: bytes) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                dev.write(SESSION_NUDGE)
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  nudge: {e}")
            for addr, reply, label in ((ADDR_DONGLE, ADDR_DONGLE_REPLY, "dongle"),
                                       (ADDR_HEADSET, ADDR_HEADSET_REPLY, "headset")):
                dev.write(frame(OP_HELLO, GRP_HELLO, ITEM_HELLO, addr))
                if self._await(dev, OP_HELLO_ACK, GRP_HELLO, ITEM_HELLO, reply) is None:
                    self._diag.append(f"  hello {label}: no answer")
                    if addr == ADDR_HEADSET:
                        self._diag.append("  (the headset is not linked: off, out of range "
                                          "or not paired with the adapter)")
                        return None
                else:
                    self._diag.append(f"  hello {label}: ok")

            dev.write(frame(OP_GET, GRP_BATTERY, ITEM_BATTERY))
            r = self._await(dev, OP_GET_REPLY, GRP_BATTERY, ITEM_BATTERY, ADDR_HEADSET_REPLY)
            if r is None:
                self._diag.append("  no battery reply")
                return None
            got = parse_battery(r)
            if got is None:
                self._diag.append(f"  battery reply out of shape: {hexdump(r, 16)}")
                return None
            level, charging = got
            minutes = (r[OFF_MIN_HI] << 8) | r[OFF_MIN_LO]
            self._diag.append(f"  battery: {r[OFF_LEVEL]}/{max(1, r[OFF_COUNT] - 1)} -> {level}%"
                              f"{' (charging)' if charging else ''}, {minutes} min left")
            return level, charging
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
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(plantronics): %s", e)
            return []
        out: List[DeviceStatus] = []
        for pid, name in PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            vend = [d for d in mine
                    if (d.get("usage_page"), d.get("usage")) == (VENDOR_PAGE, VENDOR_USAGE)]
            self._diag.append(f"[Plantronics] pid={pid:04x} '{name}'")
            if not vend:
                self._diag.append(f"  no {VENDOR_PAGE:04x}:{VENDOR_USAGE:04x} collection")
                continue
            for d in sorted(vend, key=lambda x: (x.get("interface_number") or 0, str(x.get("path")))):
                self._diag.append(f"  iface={d.get('interface_number')} "
                                  f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                got = self._query(d["path"])
                if got is None:
                    continue
                level, charging = got
                out.append(DeviceStatus(f"plantronics:{pid:04x}", name, level, charging,
                                        True, "plantronics", kind="headset"))
                break
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
