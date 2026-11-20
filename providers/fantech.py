"""Fantech Helios Go XD5 (25A7:FA7B / FA7C) and the Redragon Invader Pro (25A7:FA7E).

The vendor uses 17-byte feature reports with report id 0x08 and a checksum
such that the whole frame sums to 0x55. Command 0x04 returns the battery:

    [08][04][0 ...][checksum]

The reply can arrive on an input report of a different HID collection, or on
the feature report itself. The level is byte 6 with byte 7 charging when the
report id is present, with two older layouts accepted (level 5/4). The
Redragon id reports the level in tenths.

Protocol from Fantech's OemDrv / jarjut/xd5-go-web; the dongle layout was
confirmed by libratbag issue #1893.
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

VID = 0x25A7
WIRED = 0xFA7B
DONGLE = 0xFA7C
REDRAGON = 0xFA7E
PIDS = (WIRED, DONGLE, REDRAGON)
REPORT = 0x08
CMD = 0x04
LEN = 17
LISTEN_MS = 500
READ_TIMEOUT_MS = 250


def build_request() -> bytes:
    p = bytearray(LEN)
    p[0] = REPORT
    p[1] = CMD
    p[16] = (0x55 - sum(p[:16])) & 0xFF
    return bytes(p)


def parse_reply(r, tenths: bool = False) -> Optional[Tuple[int, bool, bool]]:
    """(level, charging, checksum_ok) from a battery reply in any layout."""
    if not r:
        return None
    b = list(r)
    checksum_ok = len(b) >= LEN and (sum(b[:LEN]) & 0xFF) == 0x55

    def plausible(power, charge):
        if len(b) <= charge or b[power] < 1 or b[power] > 100 or b[charge] > 1:
            return None
        level = b[power] * 10 if tenths else b[power]
        if level > 100:
            return None
        return level, b[charge] == 1, checksum_ok

    if len(b) >= 7 and b[0] == 0x04:
        return plausible(5, 6) or plausible(4, 5)
    if len(b) >= 8 and b[1] == 0x04:
        return plausible(6, 7)
    return None


class FantechProvider(Provider):
    name = "fantech"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(fantech): %s", e)
            return []

        for pid in PIDS:
            entries = [d for d in infos if d.get("product_id") == pid]
            cmd_entry = next((d for d in entries
                              if (d.get("feature_report_byte_length") or 0) >= LEN), None)
            if cmd_entry is None:
                continue
            got = self._read(cmd_entry, entries, pid == REDRAGON)
            if got is None:
                continue
            level, charging, _ = got
            name = "Redragon Invader Pro" if pid == REDRAGON else "Fantech Helios Go XD5"
            self._diag.append(f"  -> {level}% charging={charging}")
            return [DeviceStatus(f"fantech:{pid:04x}", name, level, charging, True,
                                 "fantech", kind="mouse",
                                 via="USB cable" if pid == WIRED else "2.4 GHz receiver")]
        return []

    def _read(self, cmd_entry, entries, tenths: bool) -> Optional[Tuple[int, bool, bool]]:
        cmd = hid.device()  # type: ignore[union-attr]
        readers = []
        try:
            cmd.open_path(cmd_entry["path"])
            if (cmd_entry.get("input_report_byte_length") or 0) >= 6:
                readers.append(cmd)
            for d in entries:
                if d is cmd_entry or (d.get("input_report_byte_length") or 0) < 6:
                    continue
                try:
                    dev = hid.device()  # type: ignore[union-attr]
                    dev.open_path(d["path"])
                    readers.append(dev)
                except (OSError, IOError):
                    pass
            req = build_request()
            if cmd.send_feature_report(req) < 0:
                return None
            end = time.time() + LISTEN_MS / 1000.0
            found = None
            while time.time() < end:
                for dev in readers:
                    r = dev.read(64, READ_TIMEOUT_MS)
                    if not r:
                        continue
                    self._diag.append(f"  {hexdump(r, 16)}")
                    parsed = parse_reply(r, tenths)
                    if parsed is not None and (found is None or parsed[2]):
                        found = parsed
                    if found is not None and found[2]:
                        return found
            if found is not None:
                return found
            try:
                r = cmd.get_feature_report(REPORT, LEN)
                parsed = parse_reply(r, tenths) if r else None
                if parsed is not None:
                    return parsed
            except (OSError, IOError, ValueError):
                pass
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            for dev in readers:
                try:
                    dev.close()
                except Exception:
                    pass
            if cmd not in readers:
                try:
                    cmd.close()
                except Exception:
                    pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
