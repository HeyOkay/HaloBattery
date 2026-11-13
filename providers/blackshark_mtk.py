"""Razer BlackShark V2 HyperSpeed on its receiver or USB-C cable (#85, #86).

The 2.4 GHz receiver (1532:0565, "Razer BlackShark V2 HS 2.4", manufacturer
"MediaTek Inc") and the headset on its cable (1532:056E) share a MediaTek
command channel: report id 2 on usage page 0xFF14, 64-byte frames. While the
cable is plugged in the dongle stops answering, so the cable is read first.

Protocol reverse-engineered by justik13/razer-blackshark-v2-hyperspeed-webhid
(MIT), and implemented read-only by arkady-emelyanov/blackshark-battery and by
openrazer PR #2974 (the kernel driver). The frame layout, including the XOR
checksum, is the one those three agree on:

    [0]  02                 report id
    [2]  60 | seq           5-bit sequence
    [6]  04                 payload length of a query
    [9]  80 / 00            domain: 0x80 through the dongle, 0x00 on the cable
    [10] 21 / 2A            GET battery / GET charging state
    [62] XOR of bytes 0..61 the dongle drops a frame with a bad checksum

A reply repeats the sequence and command, carries the headset address 0x80,
the domain, an acknowledgement byte 0x01, a payload count and then the value:
the percent for 0x21, the charging flag for 0x2A. Both are GET requests and
nothing else is ever sent.

**Unverified** until the reporters of #85 and #86 run the test build: the
reference implementations were validated on this hardware, but this provider
has not seen one yet.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x1532
PIDS: Dict[int, int] = {
    0x0565: 0x80,                # receiver: frames are relayed to domain 0x80
    0x056E: 0x00,                # headset on the USB-C cable: local domain
}
NAME = "Razer BlackShark V2 HyperSpeed"
KEY = "blackshark_hs"

USAGE_PAGE, USAGE = 0xFF14, 0x0001
REPORT_ID = 0x02
REPORT_SIZE = 64
SEQ_BASE = 0x60
CMD_BATTERY = 0x21
CMD_CHARGING = 0x2A

READ_ATTEMPTS = 6
READ_TIMEOUT_MS = 200
ASLEEP_KEEP = 180                # s; the headset is either on or off


def make_frame(seq: int, domain: int, cmd: int) -> List[int]:
    """The 64-byte GET frame, checksum included."""
    frame = [0x00] * REPORT_SIZE
    frame[0] = REPORT_ID
    frame[2] = SEQ_BASE | (seq & 0x1F)
    frame[6] = 0x04
    frame[9] = domain
    frame[10] = cmd
    checksum = 0
    for b in frame[:62]:
        checksum ^= b
    frame[62] = checksum
    return frame


def reply_checksum_ok(payload: List[int]) -> bool:
    """The driver accepts the checksum with or without the report id folded in."""
    checksum = 0
    for b in payload[:61]:
        checksum ^= b
    return payload[61] in (checksum, checksum ^ REPORT_ID)


def parse_reply(frame, seq: int, domain: int, cmd: int) -> Optional[int]:
    """The reply's first payload byte, or None when it is not our answer."""
    if not frame:
        return None
    payload = list(frame)
    if payload and payload[0] == REPORT_ID:      # hidapi keeps it on Windows
        payload = payload[1:]
    if len(payload) < 13:
        return None
    if (payload[1] & 0x1F) != (seq & 0x1F):
        return None
    if payload[7] != 0x80:                       # the headset's address
        return None
    if payload[8] != domain and not (domain == 0 and payload[8] == 0x80):
        return None
    if payload[9] != cmd or payload[10] != 0x01:
        return None
    if not 1 <= payload[11] <= 48:               # payload count
        return None
    if not reply_checksum_ok(payload):
        return None
    return payload[12]


def pick(infos: List[dict]) -> Optional[dict]:
    for d in infos:
        if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
            return d
    return None


class BlackSharkMtkProvider(Provider):
    name = "blackshark_mtk"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None
        self._seq = 0

    def _query(self, dev, domain: int, cmd: int) -> Optional[int]:
        seq = self._seq & 0x1F
        self._seq = (self._seq + 1) & 0x1F
        try:
            sent = dev.write(bytes(make_frame(seq, domain, cmd)))
        except (OSError, ValueError) as e:
            self._diag.append(f"    write 0x{cmd:02x}: {e}")
            return None
        if sent is not None and sent < 0:
            self._diag.append(f"    write 0x{cmd:02x} refused ({sent})")
            return None
        for _ in range(READ_ATTEMPTS):
            try:
                r = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
            except (OSError, ValueError) as e:
                self._diag.append(f"    read 0x{cmd:02x}: {e}")
                return None
            if not r:
                continue
            self._diag.append(f"    0x{cmd:02x} reply: {hexdump(r, 16)}")
            value = parse_reply(r, seq, domain, cmd)
            if value is not None:
                return value
        return None

    def _read(self, path: bytes, domain: int) -> Optional[Tuple[int, bool, bool]]:
        """(level, charging, charging_known) or None when the headset is silent."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            level = self._query(dev, domain, CMD_BATTERY)
            if level is None:
                return None
            if level > 100:
                self._diag.append(f"    refused battery value {level}")
                return None
            charging_value = self._query(dev, domain, CMD_CHARGING)
            if charging_value is None:
                return level, False, False
            return level, charging_value > 0, True
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
            log.warning("hid.enumerate(blackshark_mtk): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") in PIDS]
        if not mine:
            return []
        self._diag.append("[BlackShark] " + ", ".join(
            f"{d.get('product_id', 0):04x} if={d.get('interface_number')}"
            for d in mine[:4]))

        parsed = None
        for pid in (0x056E, 0x0565):            # cable first: the dongle goes quiet
            group = [d for d in mine if d.get("product_id") == pid]
            if not group:
                continue
            d = pick(group)
            if d is None:
                self._diag.append(f"  pid={pid:04x}: no {USAGE_PAGE:04x}:{USAGE:04x} collection")
                continue
            domain = PIDS[pid]
            self._diag.append(f"  pid={pid:04x} domain={domain:02x} "
                              f"iface={d.get('interface_number')}")
            parsed = self._read(d["path"], domain)
            if parsed is not None:
                break
            self._diag.append("  no answer (headset off, or the link is not up)")

        if parsed is not None:
            level, charging, charging_known = parsed
            self._last = (level, charging, time.time())
            return [DeviceStatus(KEY, NAME, level, charging, True, "blackshark_mtk",
                                 kind="headset", charging_known=charging_known)]

        if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, self._last[0], False, False, "blackshark_mtk",
                                 kind="headset", charging_known=False)]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
