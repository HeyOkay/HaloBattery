"""URX Core75 on its 2.4 GHz dongle (36b0:3002), #245.

The dongle exposes the keyboard's VIA raw HID channel: usage page 0xFF60, usage 0x61 on
interface 1, 32-byte packets, report id 0, custom channel 0. URX HUB reads its battery as
custom value **7** with VIA's custom-value read (command 0x08). The reporter's probe (#245)
settled the framing and the reply:

    request  00 | 08 00 07 00 ...         report id 0, then VIA custom get, channel 0, value 7
    reply    08 00 07 4c 00 ...           echoed header, then the level: 0x4c = 76 %

Without the leading report-id byte the keyboard answers `ff 07 ...` (refused), and VIA's
protocol-version read answers `01 00 0d` = version 13, which is what URX HUB expects.

The reply carries no charging state we could find - every byte after the level was 0 both in
the probe and in a later run - so the charger is reported as unknown rather than guessed.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x36B0
PID = 0x3002
NAME = "URX Core75"
KEY = f"urx:{PID:04x}"

USAGE_PAGE, USAGE = 0xFF60, 0x0061          # the VIA raw HID channel, interface 1
REPORT_SIZE = 32
REPORT_ID = 0x00
VIA_CUSTOM_GET = 0x08
CHANNEL = 0x00
VALUE_BATTERY = 0x07

READ_ATTEMPTS = 6
READ_TIMEOUT_MS = 250
ASLEEP_KEEP = 300


def make_request() -> List[int]:
    """``00 | 08 00 07`` - report id 0, VIA custom get, channel 0, value 7."""
    frame = [0] * REPORT_SIZE
    frame[0], frame[1], frame[2], frame[3] = REPORT_ID, VIA_CUSTOM_GET, CHANNEL, VALUE_BATTERY
    return frame


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from ``[00 |] 08 00 07 <level> ...``, or None.

    The report id is looked for at 0 and 1: hidapi keeps it in front on some Windows
    builds and strips it on others. Charging is unknown on this board, so it is always
    False here; the caller reports it as not known.
    """
    if not r:
        return None
    start = None
    if len(r) > 3 and r[0] == VIA_CUSTOM_GET and r[1] == CHANNEL and r[2] == VALUE_BATTERY:
        start = 0
    elif (len(r) > 4 and r[0] == REPORT_ID and r[1] == VIA_CUSTOM_GET
          and r[2] == CHANNEL and r[3] == VALUE_BATTERY):
        start = 1
    if start is None:
        return None
    level = r[start + 3]
    if not 1 <= level <= 100:
        return None
    return level, False


def pick(infos: List[dict]) -> Optional[dict]:
    for d in infos:
        if d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE:
            return d
    return None


class UrxProvider(Provider):
    name = "urx"

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
            log.warning("hid.enumerate(urx): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        d = pick(mine)
        if d is None:
            self._diag.append(f"[URX] pid={PID:04x} '{NAME}': no 0xFF60:0x61 channel")
            return [DeviceStatus(KEY, NAME, None, False, False, "urx", kind="keyboard")]
        self._diag.append(f"[URX] pid={PID:04x} '{NAME}' iface={d.get('interface_number')} "
                          f"{USAGE_PAGE:04x}:{USAGE:04x}")
        parsed = self._ask(d)
        if parsed is not None:
            level, _charging = parsed
            self._last = (level, time.time())
            self._diag.append(f"  -> {level}%")
            return [DeviceStatus(KEY, NAME, level, False, True, "urx", kind="keyboard",
                                 charging_known=False)]
        self._diag.append("  no answer (keyboard asleep or off)")
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, self._last[0], False, False, "urx",
                                 kind="keyboard", charging_known=False)]
        return [DeviceStatus(KEY, NAME, None, False, False, "urx", kind="keyboard",
                             charging_known=False)]

    def _ask(self, d: dict) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
        except Exception as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            sent = dev.write(bytes(make_request()))
            if sent is not None and sent < 0:
                self._diag.append(f"  write refused ({sent})")
                return None
            for _ in range(READ_ATTEMPTS):
                r = dev.read(REPORT_SIZE + 1, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  reply: {hexdump(r, 8)}")
                parsed = parse_reply(r)
                if parsed is not None:
                    return parsed
        except Exception as e:  # pragma: no cover - hidapi raises OSError
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
