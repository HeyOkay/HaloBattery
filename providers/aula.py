"""AULA F87 Pro V2C keyboards on their 2.4 GHz receiver (0C45:FEFC).

Source: AULA's own Windows application, AULA HUB (an Electron app whose HID calls were
hooked over CDP to recover the command), then replayed against the hardware and confirmed
byte for byte. The same exchange is what the board answers to AULA's own web driver
(hev.aulacn.com).

The receiver exposes usage page 0xFF80 usage 0x0001, 33-byte input and output reports and
no report id in the payload:

    host   00 AA 10 00 ...                         33 bytes, 0xAA marks the query
    kbd    00 55 10 <type> ... ... ... <level> ...
                      |                 |
                      |                 byte 26: the level in %
                      byte 3: frame type - 0x18 full, 0x00 incremental

Both frame types carry the level: measured 0x60 (96 %) on a full frame and 0x5F (95 %) on
an incremental one, minutes apart on the same board, so 0x18 must not be required - an
idle keyboard only ever sends 0x00.

There is no charge state anywhere in the protocol. A sweep of commands 0x01..0x1F answered
on 0x10 only, and that answer did not change while the battery went 96 % -> 100 %, so a
keyboard that sits on its cable is not shown as charging here.

The receiver does not cache: with the keyboard switched off, the query gets no reply at all
(while it is on, the answer arrives in ~30 ms). Silence is therefore the offline signal for
this device, and a silent keyboard shows no icon - the same way a Razer mouse that has gone
idle is handled.

The cable identity (38A6:2712, collections 0xFF67/0xFF68, 65-byte reports) is deliberately
not claimed: it reports no battery at all - its feature reports echo only the report id, and
the same query answers a configuration block (`00 55 10 00 ... 0C 11 0A 10 ... 8C`) with no
percentage in it, at any command. Only the 2.4 GHz receiver is readable.
"""
from __future__ import annotations

import time
from typing import List, Optional

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x0C45                   # the 2.4 GHz receiver; the cable id 0x38A6 has no battery
PAGE, USAGE = 0xFF80, 0x0001
REPORT_LEN = 33
QUERY = 0xAA                   # the `AA` in `AA 10`
CMD_BATTERY = 0x10
HEADER = 0x55                  # the reply starts `55 10`
LEVEL_AT = 26
TIMEOUT = 1.5                  # s, two attempts are made inside this
READ_MS = 100
ATTEMPTS = 2


def request() -> List[int]:
    """The 33-byte query: report id 0, then `AA 10` and zeros."""
    frame = [0x00, QUERY, CMD_BATTERY]
    return frame + [0x00] * (REPORT_LEN - len(frame))


def parse_level(r) -> Optional[int]:
    """The level from a reply, or None if this is not one.

    The reply is `55 10 <type> ... <level>` with the level at byte 26 of the 33-byte
    report *including* its report id 0. hidapi drops that leading 0 byte on some backends
    (report id 0 means "no report id" in HID), so both layouts are accepted: `55` at byte
    0 (level at 25) or at byte 1 (level at 26).
    """
    if not r:
        return None
    for lead, level_at in ((0, 25), (1, 26)):
        if len(r) <= level_at or r[lead] != HEADER or r[lead + 1] != CMD_BATTERY:
            continue
        level = r[level_at]
        return level if 1 <= level <= 100 else None
    return None


class AulaProvider(Provider):
    name = "aula"

    def __init__(self):
        self._diag: List[str] = []

    def _read(self, path) -> Optional[int]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            deadline = time.time() + TIMEOUT
            for _ in range(ATTEMPTS):
                try:
                    dev.write(request())
                except (OSError, ValueError) as e:
                    self._diag.append(f"    write: {e}")
                    return None
                while time.time() < deadline:
                    try:
                        r = dev.read(REPORT_LEN, READ_MS)
                    except (OSError, ValueError) as e:
                        self._diag.append(f"    read: {e}")
                        return None
                    level = parse_level(r)
                    if level is not None:
                        self._diag.append(f"    answer: {hexdump(r, 12)} -> {level}%")
                        return level
                if time.time() >= deadline:
                    break
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
            log.warning("hid.enumerate(aula): %s", e)
            return []

        out: List[DeviceStatus] = []
        for d in infos:
            if (d.get("usage_page"), d.get("usage")) != (PAGE, USAGE):
                continue
            name = (d.get("product_string") or "").strip() or "AULA keyboard"
            self._diag.append(f"[AULA] {name} vid={VID:04x} pid={d['product_id']:04x}")
            level = self._read(d["path"])
            if level is not None:
                out.append(DeviceStatus(f"aula:{d['product_id']:04x}", name, level, False,
                                        True, "aula", kind="keyboard"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
