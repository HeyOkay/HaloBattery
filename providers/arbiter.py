"""Arbiter Studio Akitsu wireless mice (vendor 373E).

DriverCore protocol on the vendor collection `FFFF:0000` (65-byte feature
report 0). A feature report with bytes 3=02, 4=02, 6=83 is followed by a
feature read of report 0:

    status A1/A2 = answered, A0 = mouse asleep
    byte 7 charging, byte 8 percentage (or shifted when hidapi returns the
    report id; both shapes are accepted)

Read-only: no settings or mode writes are sent.
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

VID = 0x373E
MODELS = {
    0x0009: ("arbiter:000a", "Arbiter Studio Akitsu", False, 0x000A),
    0x000A: ("arbiter:000a", "Arbiter Studio Akitsu", True, 0),
    0x0038: ("arbiter:0039", "Arbiter Studio Akitsu 3950", False, 0x0039),
    0x0039: ("arbiter:0039", "Arbiter Studio Akitsu 3950", True, 0),
    0x0066: ("arbiter:0067", "Arbiter Studio Akitsu Medium", False, 0x0067),
    0x0067: ("arbiter:0067", "Arbiter Studio Akitsu Medium", True, 0),
}
PAGE = 0xFFFF
FEATURE_LEN = 65
READ_WAIT_MS = 100


def parse_reply(r) -> Optional[Tuple[ Optional[int], bool, str]]:
    """(level or None, charging, state) where state is battery/asleep/none."""
    if not r:
        return None
    b = list(r)
    if len(b) >= 9 and b[4] == 0x02 and b[6] == 0x83:
        status, chg, level = b[1], b[7], b[8]
    elif len(b) >= 8 and b[3] == 0x02 and b[5] == 0x83:
        status, chg, level = b[0], b[6], b[7]
    else:
        return None
    if status == 0xA0:
        return None, False, "asleep"
    if status not in (0xA1, 0xA2):
        return None
    if level > 100:
        return None
    return level, chg == 1, "battery"


class ArbiterProvider(Provider):
    name = "arbiter"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(arbiter): %s", e)
            return []

        out: List[DeviceStatus] = []
        handled = set()
        for pid, (key, name, receiver, pair) in MODELS.items():
            if key in handled:
                continue
            got = None
            for d in infos:
                if d.get("product_id") != pid or d.get("usage_page") != PAGE \
                        or (d.get("feature_report_byte_length") or 0) != FEATURE_LEN:
                    continue
                got = self._read(d["path"])
                if got is not None:
                    break
            if got is None:
                continue
            level, charging, state = got
            handled.add(key)
            if state == "asleep":
                out.append(DeviceStatus(key, name, None, False, False, "arbiter",
                                        kind="mouse", via="2.4 GHz receiver" if receiver else "USB cable"))
                continue
            via = "2.4 GHz receiver" if receiver else "USB cable"
            connected = (level or 0) > 0 or not receiver
            out.append(DeviceStatus(key, name, level if connected else None,
                                    charging, connected, "arbiter", kind="mouse",
                                    via=via))
        return out

    def _read(self, path) -> Optional[Tuple[Optional[int], bool, str]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        req = bytearray(FEATURE_LEN)
        req[3] = 0x02
        req[4] = 0x02
        req[6] = 0x83
        try:
            for attempt in range(2):
                if dev.send_feature_report(bytes(req)) < 0:
                    return None
                time.sleep(READ_WAIT_MS / 1000.0)
                r = dev.get_feature_report(0, FEATURE_LEN)
                if r:
                    self._diag.append(f"  {hexdump(r, 16)}")
                    parsed = parse_reply(r)
                    if parsed is not None:
                        return parsed
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
