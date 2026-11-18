"""MCHOSE K99 V3 on its 2.4 GHz receiver (3837:3033), #458.

The V3 does not use the 20-byte M HUB keyboard frame of the K87/K99 legacy.
Its battery channel is the generic-desktop HID collection on interface 1
(usage page 0x0001, usage 0x0000), with a fixed 64-byte report:

    request  55 03 00 38 38 00 00 00 00 ...
    reply    AA 03 00 38 ... <level at byte 8> ...

Protocol from arutkowski00/mchose-hid-bridge (MIT), recovered from the K99 V3
receiver and its M HUB behaviour.  The reply echoes command 0x03 and sub-command
0x38; a reply without that echo is not ours.  Byte 6 is recorded in diagnostics
but its charge semantics are not documented, so the charger is reported as
unknown rather than guessed.

Only this one collection is opened, and only the read-only battery request is
sent.
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
REPORT_ID = 0x55
REPORT_LEN = 64
MAGIC = 0xAA
CMD = 0x03
SUB = 0x38
LEVEL_AT = 8
STATUS_AT = 6

ATTEMPTS = 3
READ_TIMEOUT_MS = 250
ATTEMPT_GAP = 0.15
ASLEEP_KEEP = 300

NAME = "MCHOSE K99 V3"
KEY = "mchose_k99:3033"


def make_request() -> bytes:
    """The 64-byte K99 V3 battery query, exactly as seen on the wire."""
    p = bytearray(REPORT_LEN)
    p[0:8] = bytes([REPORT_ID, CMD, 0x00, SUB, SUB, 0x00, 0x00, 0x00])
    return bytes(p)


def wire_request() -> bytes:
    """The same frame with hidapi's unnumbered-report id in front.

    Windows hidapi interprets byte 0 as a report id; the K99 V3 frame's 0x55 is
    the protocol magic, not a report id, so the frame must be sent behind a
    leading 0x00.  The raw 64-byte form is kept as a fallback for hidapi builds
    that write unnumbered reports directly.
    """
    return b"\x00" + make_request()


def parse_reply(r) -> Optional[Tuple[int, int]]:
    """(level, status byte) from a K99 V3 battery reply, or None.

    The response has no checksum; the echoed command/sub-command pair is the
    framing check.  A level outside 1..100 is refused rather than shown.
    """
    if not r or len(r) < LEVEL_AT + 1:
        return None
    b = bytes(r)
    if len(b) >= REPORT_LEN + 1 and b[0] == 0x00 and b[1] == MAGIC:
        b = b[1:]
    if b[0] != MAGIC or b[1] != CMD or b[2] != 0x00 or b[3] != SUB:
        return None
    level = b[LEVEL_AT]
    if not 1 <= level <= 100:
        return None
    return level, b[STATUS_AT]


class MchoseK99Provider(Provider):
    name = "mchose_k99"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, float]] = None

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
                          f"{PAGE:04x}:{USAGE:04x}")
        got = self._read(d["path"])
        if got is None:
            self._diag.append("  no answer (keyboard asleep or off)")
            if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
                return [DeviceStatus(KEY, NAME, self._last[0], False, False,
                                     "mchose_k99", kind="keyboard",
                                     wake_on_input=True, charging_known=False)]
            return [DeviceStatus(KEY, NAME, None, False, False, "mchose_k99",
                                 kind="keyboard", wake_on_input=True,
                                 charging_known=False)]

        level, status = got
        self._diag.append(f"  -> {level}% raw status=0x{status:02x} "
                          f"(charge semantics undocumented)")
        self._last = (level, time.time())
        return [DeviceStatus(KEY, NAME, level, False, True, "mchose_k99",
                             kind="keyboard", wake_on_input=True,
                             charging_known=False)]

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if d.get("product_id") == PID and (d.get("usage_page"), d.get("usage")) == (PAGE, USAGE):
                return d
        return None

    def _read(self, path) -> Optional[Tuple[int, int]]:
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
                # The K99 V3 frame has no numbered report; Windows hidapi expects
                # an unnumbered-report id (0x00) in front, while the raw 64-byte
                # form is kept as a fallback for hidapi builds that write the
                # report directly.
                request = (wire_request(), make_request())[attempt % 2]
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
