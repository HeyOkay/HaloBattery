"""Akko Verge S9 Ultra on its 2.4 GHz dongle (`36eb:1087`).

The dongle is one HID interface (interface 3, report id `0x02`, 64-byte
reports) next to the headset's audio interfaces. The vendor app's exchange was
captured with USBPcap in #505:

    request  02 10 01 00 00 ... (64 bytes)      link status
    reply    02 10 01 00 01 ...                  headset on the dongle
             02 10 01 00 00 ...                  headset off or out of range

    request  02 12 01 00 00 ...                 battery
    reply    02 12 01 00 64 ...                 100 % (capture 1)
             02 12 01 00 63 ...                 99 %  (capture 2, the value the
                                                   vendor app showed then)
             02 12 01 01 00 ...                 no level reported (byte 3 = 1)

Byte 4 is the level, a plain 0-100 percentage - the two captures read 100 and
99 while the vendor app showed the same numbers. Byte 3 is 0 whenever a level
came back and 1 in the one phase where the vendor app had no value either; its
meaning is not established, so it is logged only. The exchange carries no
charging flag, and the vendor app did not show a charging state on the dongle
either (#505), so `charging_known` is False rather than a guess.

Every frame ends with a two-byte trailer over its first 62 bytes. It was
fitted to all 16 distinct frames in the two captures - the all-zero `02 10 01 00`
request carries `4B 43`, the all-zero `02 12 01 00` request `71 12`, the
`02 10 01 00 01` reply `71 E1` - so the request is checksummed and a reply whose
trailer does not match is not used.

Only these two read commands are sent. The vendor app's 0x03 frame (a 16-byte
value at startup) was not needed by any later battery read in the capture and
is not sent.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

try:
    import hid  # type: ignore
except Exception:                       # pragma: no cover - only without the package
    hid = None

VID = 0x36EB
PID = 0x1087
NAME = "Akko S9 Ultra"
KEY = f"akko:{PID:04x}"

REPORT = 0x02
LENGTH = 64                             # the report id plus 63 data bytes
LINK_CMD = 0x10
BATTERY_CMD = 0x12
ACK = 0x01                              # byte 2 of every request and reply
READ_TIMEOUT_MS = 400
ATTEMPTS = 2
ASLEEP_KEEP = 300                       # s, keep the last level greyed

# the same interface lists a consumer-control collection and the headset page;
# the vendor exchange rides the interface either way, so both are tried
USAGES = ((0x008C, 0x0001), (0x000C, 0x0001))


CHECKSUM_INIT = 0xFFFF
CHECKSUM_POLY = 0x8005


def frame_checksum(payload) -> int:
    """The two-byte trailer every frame carries, over the first 62 bytes.

    Fitted to all 16 distinct frames in the #505 captures (the all-zero 0x10
    request ends in 0x4B43, the all-zero 0x12 request in 0x7112, and the
    `02 10 01 00 01` reply in 0x71E1), so a frame without it is not one this
    dongle sent or would accept.
    """
    crc = CHECKSUM_INIT
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ CHECKSUM_POLY if crc & 1 else crc >> 1
    return crc & 0xFFFF


def make_request(command: int) -> List[int]:
    """The 64-byte report: id, command, ack, zero payload, checksum."""
    body = [REPORT, command, ACK, 0x00] + [0x00] * (LENGTH - 6)
    checksum = frame_checksum(body)
    return body + [checksum >> 8, checksum & 0xFF]


def parse_reply(reply, command: int) -> Optional[List[int]]:
    """The reply to `command`, or None when this is not a valid one."""
    if not reply or len(reply) < LENGTH:
        return None
    r = list(reply)[:LENGTH]
    if r[0] != REPORT or r[1] != command or r[2] != ACK:
        return None
    if (r[62] << 8 | r[63]) != frame_checksum(r[:62]):
        return None
    return r


def parse_link(reply) -> Optional[bool]:
    """True while the headset is linked, False when it is off, None if unreadable."""
    r = parse_reply(reply, LINK_CMD)
    if r is None:
        return None
    if r[4] == 0x01:
        return True
    if r[4] == 0x00:
        return False
    return None


def parse_battery(reply) -> Optional[int]:
    """Level 1-100 from a battery reply; 0 and out-of-range are no reading."""
    r = parse_reply(reply, BATTERY_CMD)
    if r is None:
        return None
    return r[4] if 1 <= r[4] <= 100 else None


class AkkoS9Provider(Provider):
    name = "akko_s9"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, float]] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:          # pragma: no cover - the package misbehaving
            log.warning("hid.enumerate(akko): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        self._diag.append(f"[Akko] pid={PID:04x} '{NAME}'")
        try:
            return self._poll_dongle(mine)
        except Exception as e:          # a provider must never take the poll down
            self._diag.append(f"  poll error: {e}")
            return [self._status(self._kept_level(), online=False)]

    def _poll_dongle(self, infos: List[dict]) -> List[DeviceStatus]:
        seen = set()
        candidates = []
        for page, usage in USAGES:
            for d in infos:
                if ((d.get("usage_page") or 0) == page and (d.get("usage") or 0) == usage
                        and d.get("path") not in seen):
                    seen.add(d.get("path"))
                    candidates.append(d)
        if not candidates:
            self._diag.append(f"  no headset or consumer collection among {len(infos)}")
            return [self._status(self._kept_level(), online=False)]
        for d in candidates:
            self._diag.append(f"  trying iface={d.get('interface_number')} "
                              f"{(d.get('usage_page') or 0):04x}:{(d.get('usage') or 0):04x}")
            result = self._probe(d["path"])
            if result is None:
                continue
            state, level = result
            if state == "asleep":
                self._diag.append("  link down: headset off or out of range")
                return [self._status(self._kept_level(), online=False)]
            if level is not None:
                self._last = (level, time.time())
            self._diag.append(f"  -> {level if level is not None else '?'}%")
            return [self._status(level, online=True)]
        self._diag.append("  no reply on any collection")
        return [self._status(self._kept_level(), online=False)]

    def _probe(self, path: bytes):
        """None when this collection does not answer, else (state, level).

        state is "asleep" when the headset is off, "online" when it answered -
        and the level may still be None then (the reply carried no number), which
        is different from silence: the icon stays, only the number is missing.
        """
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            link = self._ask(dev, LINK_CMD)
            linked = parse_link(link)
            if linked is None:
                return None
            if not linked:
                return ("asleep", None)
            return ("online", parse_battery(self._ask(dev, BATTERY_CMD)))
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _ask(self, dev, command: int) -> Optional[List[int]]:
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(0.05)
            try:
                sent = dev.write(bytes(make_request(command)))
                if sent is not None and sent < 0:
                    self._diag.append(f"    {command:02x}: write refused ({sent})")
                    continue
                reply = dev.read(LENGTH, READ_TIMEOUT_MS)
            except (OSError, IOError, ValueError, AttributeError) as e:
                self._diag.append(f"    {command:02x}: {e}")
                continue
            if reply:
                self._diag.append(f"    {command:02x}: {hexdump(reply, 12)}")
                if parse_reply(reply, command) is not None:
                    return list(reply)
        return None

    def _kept_level(self) -> Optional[int]:
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return self._last[0]
        return None

    @staticmethod
    def _status(level: Optional[int], online: bool) -> DeviceStatus:
        return DeviceStatus(KEY, NAME, level, False, online, "akko_s9",
                            kind="headset", charging_known=False)

    def diagnostics(self) -> List[str]:
        return list(self._diag)
