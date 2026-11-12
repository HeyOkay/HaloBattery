"""VAXEE wireless mice (3057:..., vendor collection 0xFF05), without VAXEE Control Center.

Protocol from stuffz/mouse-battery-monitor and the VAXEE Control Center web
driver (vcc.vaxee.co), ported from ryanlewis/mousebatt's src/protocol.rs, whose
VAXEE support was tested on an XE-S behind a VXD02 4K dongle.  Confirmed in Halo
Battery on an E1 Wireless over its receiver:

    request  feature report 0x0e, 64 bytes:
             0e a5 <cmd> 01 01 00 ...
             0e = report id, a5 = header, 01 = read, 01 = one data byte
    reply    feature report 0x0e:
             0e a5 <cmd> <rw> <len> <data...>

The reply is valid only when it echoes the command we asked for; its value is
byte 5.  Command 0x0b is the battery in 5 % steps (byte 5 * 5, refusing values above
20) and 0x10 is the charging flag (non-zero = the mouse is on its cable).

The receiver leaves the answer in the feature buffer about 100 ms after the
request.  A sleeping mouse can ignore the first exchange, so the pair is tried
twice, 300 ms apart.

Devices are found by vendor id and the HID usage page rather than by product
id: the same mouse is 3057:2001 (or another receiver id) on its dongle and an
id of its own on the cable.  The cable is asked first, they share one icon,
and a reading that reports charging wins.  While any 0xFF05 collection is
still in the USB tree, a silent mouse keeps its last level on a greyed icon
for ASLEEP_KEEP, as in the other receiver providers - a sleeping mouse is not
a removed one.  Losing the receiver, or the cap expiring, drops it.

The cable and its dongle share one KEY ("vaxee") and one icon, which is what
one mouse on a cable or a dongle needs.  It also means two different VAXEE
mice plugged in at once are merged into a single icon; give the cable path a
per-pid key if that ever matters.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VAXEE_VID = 0x3057
USAGE_PAGE = 0xFF05

REPORT_ID = 0x0E
HEADER = 0xA5
READ = 0x01
CMD_BATTERY = 0x0B
CMD_CHARGING = 0x10

REPORT_LEN = 64
REPLY_LENGTHS = (REPORT_LEN, REPORT_LEN + 1)
SETTLE_S = 0.10                  # the web driver's read delay
QUERY_TRIES = 2
RETRY_SLEEP_S = 0.30             # the link ignores requests for a while after a wake
ASLEEP_KEEP = 300                # s, as in the other receiver providers

# Receiver product ids from the web driver's DefWireless table; anything else
# on the vendor page is the mouse itself on a cable.
DONGLE_PIDS = frozenset({0x1001, 0x1002, 0x2001, 0x2002})

KEY = "vaxee"

Reading = Tuple[int, bool]       # percent, charging


def parse_reply(resp, cmd: int) -> Optional[int]:
    """Byte 5 of a feature reply that answers `cmd`, or None if it is not one."""
    r = list(resp or [])
    if len(r) < 6 or r[0] != REPORT_ID or r[1] != HEADER or r[2] != cmd:
        return None
    return r[5]


def display_name(product: str, is_dongle: bool = False) -> str:
    """The mouse model, or a plain 'VAXEE mouse'.

    A mouse on its cable reports its own model in the product string.  A
    receiver reports its own part number (a VXD02 reads 'VXD02 4K Dongle'),
    which says nothing about the mouse paired to it, so a dongle reading falls
    back to a plain 'VAXEE mouse' rather than a part number.
    """
    product = (product or "").strip()
    if is_dongle or not product:
        return "VAXEE mouse"
    if "vaxee" in product.lower():
        return product
    return f"VAXEE {product}"


class VaxeeProvider(Provider):
    name = "vaxee"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[str, int, bool, float]] = None

    def _query(self, dev, cmd: int) -> Optional[int]:
        """Send one read command and return its value from the feature buffer."""
        request = [REPORT_ID, HEADER, cmd, READ, 0x01] + [0x00] * (REPORT_LEN - 5)
        try:
            sent = dev.send_feature_report(request)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    cmd 0x{cmd:02x}: send: {e}")
            return None
        if isinstance(sent, int) and sent < 0:
            self._diag.append(f"    cmd 0x{cmd:02x}: Windows refused the request")
            return None
        time.sleep(SETTLE_S)
        for length in REPLY_LENGTHS:
            try:
                resp = dev.get_feature_report(REPORT_ID, length)
            except (OSError, IOError, ValueError):
                resp = None
            value = parse_reply(resp, cmd)
            if value is not None:
                self._diag.append(f"    cmd 0x{cmd:02x} reply: {hexdump(resp, 8)}")
                return value
        self._diag.append(f"    cmd 0x{cmd:02x}: no valid reply")
        return None

    def _read(self, path) -> Optional[Reading]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for attempt in range(QUERY_TRIES):
                battery = self._query(dev, CMD_BATTERY)
                charging = self._query(dev, CMD_CHARGING)
                if battery is not None and not 0 <= battery <= 20:
                    self._diag.append(f"    invalid battery value: {battery}")
                    battery = None
                if battery is not None and charging is not None:
                    level = battery * 5
                    self._diag.append(f"    -> {level}%"
                                      + (" (charging)" if charging else ""))
                    return level, charging != 0
                if attempt + 1 < QUERY_TRIES:
                    time.sleep(RETRY_SLEEP_S)
            self._diag.append("    no battery reply after two attempts "
                              "(mouse asleep, off, or out of range)")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VAXEE_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(vaxee): %s", e)
            return []

        seen = set()
        candidates: List[dict] = []
        for d in infos:
            if d.get("usage_page") != USAGE_PAGE or d.get("path") in seen:
                continue
            seen.add(d.get("path"))
            candidates.append(d)
        if not candidates:
            self._last = None       # the receiver left: forget the old mouse too
            return []

        # The cable first: it charges, and the dongle has nothing to add then.
        candidates.sort(key=lambda d: d.get("product_id") in DONGLE_PIDS)

        best: Optional[Tuple[str, int, bool]] = None
        for d in candidates:
            pid = d.get("product_id", 0)
            product = str(d.get("product_string") or "").strip()
            name = display_name(product, is_dongle=pid in DONGLE_PIDS)
            self._diag.append(f"[VAXEE] pid={pid:04x} '{product}' "
                              f"iface={d.get('interface_number')} "
                              f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            got = self._read(d["path"])
            if got is not None:
                level, charging = got
                if best is None or charging:
                    best = (name, level, charging)
                if charging:
                    break           # a charging reading is the one to show

        if best is not None:
            name, level, charging = best
            self._last = (name, level, charging, time.time())
            return [DeviceStatus(KEY, name, level, charging, True, "vaxee",
                                 kind="mouse")]

        # A silent collection is a sleeping mouse, not a removed one: keep the
        # last reading greyed while it is fresh, then drop it so a mouse left
        # off does not sit greyed forever.
        if self._last is not None:
            name, level, charging, when = self._last
            if time.time() - when < ASLEEP_KEEP:
                self._diag.append(f"  keeping {level}%"
                                  + (" (charging)" if charging else "")
                                  + " greyed: the receiver is still plugged in")
                return [DeviceStatus(KEY, name, level, charging, False, "vaxee",
                                     kind="mouse")]
            self._diag.append(f"  dropping {level}%: greyed for more than "
                              f"{ASLEEP_KEEP} s")
            self._last = None
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
