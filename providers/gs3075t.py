"""GANSS GS3075T and other Sonix 0C45:FEFE 2.4 GHz keyboards.

The receiver exposes the vendor collection ``FF60:0061`` on interface 3 and
speaks the 32-byte Sonix keyboard exchange captured by the reporter in #379:

    host    AA 10 18 00 ...                         32 bytes on the wire
    kbd     55 10 18 ... ... <level> <charge> ...   32 bytes

Byte 25 is the level in percent (0-based, with ``55`` at byte 0); byte 26 is
``0x02`` while the charging cable is plugged in, ``0x01`` when the cable is in
and the battery is full, and ``0x00`` on battery. Both cable states are reported
as charging: the tray's charging ring means "on external power" here.

On Windows, hidapi treats the first byte passed to ``write()`` as the report id.
The keyboard has no report id on this collection, so the host frame must start
with a zero report-id byte: ``write([0x00] + request())`` passes 33 bytes to
hidapi and puts the 32-byte query above on the wire. Without it the receiver
answers only a short ``55 18`` acknowledgement and the level never arrives.

The first request after the link wakes up can be lost, and the receiver answers
more readily while the 2.4 GHz link is active. The query is still sent once per
poll and never re-sent after silence or a refused write: a lost first request is
answered on the next poll.

The reporter measured both the level and the charging byte on a GS3075T. The
same 0C45:FEFE receiver and FF60:0061 collection ship with other Sonix boards
(other GANSS T-series models, the ATTACKSHARK X66, which takes a different
command), so the query is only sent to a receiver whose product string names
the GS3075T (the reporter's says "GS3075T 2.4G", #379).
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

VID = 0x0C45
PID = 0xFEFE
PAGE, USAGE = 0xFF60, 0x0061
REPORT_LEN = 32
HEADER = 0x55
CMD_BATTERY = 0x10
FRAME_TYPE = 0x18
LEVEL_AT = 25
CHARGE_AT = 26
CHARGE_ON = (0x01, 0x02)         # 02 charging up; 01 cable in and full
READ_MS = 100
REPORT_ID = 0x00
PRODUCT_MARK = "GS3075T"         # product string of the confirmed receiver (#379)


def request() -> List[int]:
    """The 32-byte query on the wire: ``AA 10 18`` and zeros.

    hidapi's ``write`` expects a report-id byte first; use ``write_frame()``
    to send it. The report id is 0x00 on this collection, so 33 bytes go to
    hidapi and the device sees the 32-byte frame above.
    """
    return [0xAA, CMD_BATTERY, FRAME_TYPE] + [0x00] * (REPORT_LEN - 3)


def write_frame() -> List[int]:
    """The query as passed to hidapi, including the zero report id."""
    return [REPORT_ID] + request()


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a reply, or None when this is not one.

    hidapi may or may not include the report-id byte 0, so accept ``55`` at
    byte 0 (level 25) or byte 1 (level 26).
    """
    if not r:
        return None
    for lead, level_at, charge_at in ((0, LEVEL_AT, CHARGE_AT),
                                      (1, LEVEL_AT + 1, CHARGE_AT + 1)):
        if len(r) <= charge_at or r[lead] != HEADER or r[lead + 1] != CMD_BATTERY:
            continue
        level = r[level_at]
        if not 1 <= level <= 100:
            continue
        return level, r[charge_at] in CHARGE_ON
    return None


class Gs3075tProvider(Provider):
    name = "gs3075t"

    def __init__(self):
        self._diag: List[str] = []

    def _read(self, path) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            # One write per poll: never re-sent after silence or a refused write.
            try:
                sent = dev.write(write_frame())
            except (OSError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            if sent is not None and sent < 0:
                self._diag.append("    the write was refused")
                return None
            deadline = time.time() + 0.75
            while time.time() < deadline:
                try:
                    r = dev.read(REPORT_LEN, READ_MS)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    read: {e}")
                    return None
                parsed = parse_reply(r)
                if parsed is not None:
                    self._diag.append(f"    answer: {hexdump(r, 12)} -> "
                                      f"{parsed[0]}% charging={parsed[1]}")
                    return parsed
            self._diag.append("    no answer (keyboard off or asleep)")
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
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(gs3075t): %s", e)
            return []

        out: List[DeviceStatus] = []
        seen = set()
        for d in infos:
            if d.get("product_id") != PID:
                continue
            if (d.get("usage_page"), d.get("usage")) != (PAGE, USAGE):
                continue
            path = d.get("path")
            if path in seen:
                continue
            seen.add(path)
            product = (d.get("product_string") or "").strip()
            if PRODUCT_MARK not in product.upper():
                # Shared Sonix receiver of another board: send nothing to it.
                self._diag.append(f"[GS3075T] {product or '(no product string)'} "
                                  f"vid={VID:04x} pid={PID:04x}: not a GS3075T, not asked")
                continue
            name = product
            self._diag.append(f"[GS3075T] {name} vid={VID:04x} pid={PID:04x} "
                              f"iface={d.get('interface_number')}")
            got = self._read(path)
            if got is None:
                continue
            level, charging = got
            out.append(DeviceStatus(f"gs3075t:{PID:04x}", name, level, charging, True,
                                    "gs3075t", kind="keyboard"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
