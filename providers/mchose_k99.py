"""MCHOSE K99 V3 on its 2.4 GHz receiver (3837:3033), #458.

The V3 uses M HUB's "GLW" func-config exchange on the id-less 64-byte
generic-desktop collection (usage 0x0001:0x0000, with 0xFF31:0x0074 as the
fallback collection that answers the same frames):

    request  00 | 55 05 00 38 38 00 00 00 ...     read 56 bytes at offset 0
    reply    AA 05 00 <checksum> 38 00 00 00 ...  payload starts at byte 8

The request header is flag 0x55, command 0x05, reserved 0, checksum, size,
offset (little endian), reserved.  For a read with no payload the checksum is
size + offset = 0x38.  The reply header carries the same fields; its checksum
covers size + offset + payload.  The battery byte is payload offset 32: bit 7
is charging, bits 0..6 are the percentage.

This is the protocol from krisgfx9/Wireless-Device-Battery-Monitor PR #1957,
recovered from M HUB's own web driver and confirmed on a G75 V2 on the same
3837:3033 receiver.  It replaces the earlier 55 03 00 38 38 request, which
returned a different info frame (52% in the reporter's diagnostics while M HUB
showed 90% and no charging state).  Only read-only frames are sent.
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

VID = 0x3837
PID = 0x3033
PAGE, USAGE = 0x0001, 0x0000
ALT_PAGE, ALT_USAGE = 0xFF31, 0x0074

REPORT_ID = 0x00
REPORT_LEN = 64
FLAG = 0x55
MAGIC = 0xAA
CMD = 0x05
SIZE = 56
PAYLOAD_AT = 8
BATTERY_AT = 32
CHECKSUM_AT = 3

ATTEMPTS = 3
READ_TIMEOUT_MS = 250
ATTEMPT_GAP = 0.15
ASLEEP_KEEP = 300

NAME = "MCHOSE K99 V3"
KEY = "mchose_k99:3033"


def _body() -> bytes:
    """The 64-byte GLW frame without hidapi's report id byte."""
    p = bytearray(REPORT_LEN)
    p[0:8] = bytes([FLAG, CMD, 0x00, SIZE, SIZE, 0x00, 0x00, 0x00])
    return bytes(p)


def make_request() -> bytes:
    """The 64-byte GLW request body."""
    return _body()


def wire_request() -> bytes:
    """The frame as hidapi expects it: report id 0, then the 64-byte body."""
    return bytes([REPORT_ID]) + _body()


def parse_reply(r) -> Optional[Tuple[int, bool, int]]:
    """(level, charging, raw battery byte) from a GLW func-config reply."""
    if not r:
        return None
    b = bytes(r)
    if len(b) >= REPORT_LEN + 1 and b[0] == REPORT_ID and b[1] == MAGIC:
        b = b[1:]
    if len(b) < PAYLOAD_AT + SIZE or b[0] != MAGIC or b[1] != CMD:
        return None
    size = b[4]
    offset = b[5] | (b[6] << 8)
    if size < BATTERY_AT + 1 or offset != 0 or PAYLOAD_AT + size > len(b):
        return None
    checksum = (b[4] + b[5] + b[6] + b[7] + sum(b[PAYLOAD_AT:PAYLOAD_AT + size])) & 0xFF
    if checksum != b[CHECKSUM_AT]:
        return None
    raw = b[PAYLOAD_AT + BATTERY_AT]
    level = raw & 0x7F
    if level > 100:
        return None
    return level, bool(raw & 0x80), raw


class MchoseK99Provider(Provider):
    name = "mchose_k99"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(mchose_k99): %s", e)
            return []

        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[MCHOSE K99 V3] pid=3837:{PID:04x} "
                          f"'{d.get('product_string') or ''}' "
                          f"iface={d.get('interface_number')} "
                          f"{d.get('usage_page'):04x}:{d.get('usage'):04x}")
        got = self._read(d["path"])
        if got is None:
            self._diag.append("  no answer (keyboard asleep or off)")
            if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
                level, charging, _ = self._last
                return [DeviceStatus(KEY, NAME, level, charging, False,
                                     "mchose_k99", kind="keyboard",
                                     wake_on_input=True, charging_known=False)]
            return [DeviceStatus(KEY, NAME, None, False, False, "mchose_k99",
                                 kind="keyboard", wake_on_input=True,
                                 charging_known=False)]

        level, charging, raw = got
        self._diag.append(f"  -> {level}% charging={charging} raw={raw:02x}")
        self._last = (level, charging, time.time())
        return [DeviceStatus(KEY, NAME, level, charging, True, "mchose_k99",
                             kind="keyboard", wake_on_input=True,
                             charging_known=True)]

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for page, usage in ((PAGE, USAGE), (ALT_PAGE, ALT_USAGE)):
            for d in infos:
                if (d.get("product_id") == PID
                        and (d.get("usage_page"), d.get("usage")) == (page, usage)):
                    return d
        return None

    def _read(self, path) -> Optional[Tuple[int, bool, int]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                dev.set_nonblocking(True)
            except Exception:
                pass
            for attempt in range(ATTEMPTS):
                request = wire_request()
                try:
                    dev.write(request)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  write ({len(request)}B): {e}")
                    continue
                deadline = time.time() + ATTEMPT_GAP
                while time.time() < deadline:
                    r = dev.read(REPORT_LEN + 1, READ_TIMEOUT_MS)
                    if not r:
                        continue
                    self._diag.append(f"  reply: {hexdump(r, 16)}")
                    parsed = parse_reply(r)
                    if parsed is not None:
                        return parsed
                time.sleep(0.03)
            return None
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def diagnostics(self) -> List[str]:
        return list(self._diag)
