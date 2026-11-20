"""MOONDROP GM01 Pro wireless headset (35D8:0133, WalkPlay).

The 2.4 GHz dongle exposes a 64-byte consumer-page control collection on
interface 3, report 0x4B. A read is `[4B][80][cmd][00]`:

  * cmd 0x27 -> [4B][80][27][len] ... [8]=link, [9]=percentage
  * cmd 0x31 -> [4B][80][31][len] [4]=link; the dongle answers this itself

On its USB cable the headset exposes one consumer collection with no control
channel and no level; it is charging, so the last level stays dimmed.
Only these read commands are sent.
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

VID = 0x35D8
PID = 0x0133
PAGE = 0x000C
IFACE = 3
REPORT = 0x4B
READ = 0x80
CMD_BATTERY = 0x27
CMD_LINK = 0x31
READ_TIMEOUT_MS = 250
LISTEN_MS = 400
KEY = "moondrop:gm01"
NAME = "MOONDROP GM01 Pro"


def parse_battery(r) -> Optional[Tuple[bool, int]]:
    if not r or len(r) < 10:
        return None
    b = list(r)
    if b[0] != REPORT or b[1] != READ or b[2] != CMD_BATTERY:
        return None
    if b[3] < 6 or b[9] > 100:
        return None
    return bool(b[8]), b[9]


def parse_link(r) -> Optional[bool]:
    if not r or len(r) < 5:
        return None
    b = list(r)
    if b[0] != REPORT or b[1] != READ or b[2] != CMD_LINK or b[3] < 1:
        return None
    return bool(b[4])


class MoondropProvider(Provider):
    name = "moondrop"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(moondrop): %s", e)
            return []

        found = [d for d in infos if d.get("product_id") == PID
                 and d.get("interface_number") == IFACE and d.get("usage_page") == PAGE]
        if not found:
            return []
        for d in found:
            if (d.get("output_report_byte_length") or 0) < 4 \
                    or (d.get("input_report_byte_length") or 0) < 10:
                continue
            got = self._read(d["path"], d.get("output_report_byte_length") or 64)
            if got is not None:
                link, pct = got
                if not link:
                    return [DeviceStatus(KEY, NAME, None, False, False,
                                         "moondrop", kind="headset",
                                         via="2.4 GHz receiver")]
                if pct is None:
                    return [DeviceStatus(KEY, NAME, None, False, True,
                                         "moondrop", kind="headset",
                                         via="2.4 GHz receiver")]
                self._diag.append(f"  -> {pct}%")
                return [DeviceStatus(KEY, NAME, pct, False, True,
                                     "moondrop", kind="headset",
                                     via="2.4 GHz receiver")]

        # No control answer: the headset on its cable has one consumer collection
        # (its path has no &colNN) and no battery channel.
        on_cable = any("&col" not in str(d.get("path", "")).lower() for d in found)
        if on_cable:
            return [DeviceStatus(KEY, NAME, None, True, True, "moondrop",
                                 kind="headset", via="USB cable")]
        return [DeviceStatus(KEY, NAME, None, False, False, "moondrop", kind="headset")]

    def _read(self, path, out_len: int) -> Optional[Tuple[bool, Optional[int]]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            r = self._query(dev, CMD_BATTERY, out_len)
            if r is not None:
                got = parse_battery(r)
                if got is not None:
                    return got
            r = self._query(dev, CMD_LINK, out_len)
            if r is not None:
                link = parse_link(r)
                if link is not None:
                    return link, None
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _query(self, dev, cmd: int, out_len: int):
        req = bytes([REPORT, READ, cmd, 0x00]) + bytes(max(0, out_len - 4))
        dev.write(req)
        end = time.time() + LISTEN_MS / 1000.0
        while time.time() < end:
            r = dev.read(64, READ_TIMEOUT_MS)
            if not r:
                continue
            self._diag.append(f"  {hexdump(r, 16)}")
            if list(r)[:3] == [REPORT, READ, cmd]:
                return r
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
