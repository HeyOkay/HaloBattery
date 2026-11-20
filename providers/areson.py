"""Redragon Areson/CompX mice (vendor 25A7).

Only the FA07/FA08 receivers use this GetPower exchange: a 17-byte feature
request on FF02:0002, answered on the FF01:0000 input collection:

    request [08][04][00 ...][checksum], checksum = 0x55 - sum(bytes 0..15)
    reply   [09][04][00] ... [6] level 0..10, [7] charging, whole frame sums to 0x55

Level is in 10 % steps. The FA7B/FA7C family uses a different scale and is
not claimed here. Only the read-only request is sent.
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
PIDS = (0xFA07, 0xFA08)
IFACE = 1
CMD_PAGE, CMD_USAGE = 0xFF02, 0x0002
REP_PAGE, REP_USAGE = 0xFF01, 0x0000
LEN = 17
READ_TIMEOUT_MS = 250
LISTEN_MS = 1800
KEY = "areson"
NAME = "Redragon wireless mouse"


def build_request() -> bytes:
    r = bytearray(LEN)
    r[0] = 0x08
    r[1] = 0x04
    r[16] = (0x55 - sum(r[:16])) & 0xFF
    return bytes(r)


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    if not r or len(r) < LEN:
        return None
    b = list(r[:LEN])
    if (sum(b) & 0xFF) != 0x55:
        return None
    if b[0] != 0x09 or b[1] != 0x04 or b[2] != 0x00 or b[6] > 10:
        return None
    return b[6] * 10, b[7] != 0


class AresonProvider(Provider):
    name = "areson"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(areson): %s", e)
            return []

        for pid in PIDS:
            cmd = next((d for d in infos if d.get("product_id") == pid
                        and d.get("interface_number") == IFACE
                        and (d.get("usage_page"), d.get("usage")) == (CMD_PAGE, CMD_USAGE)
                        and (d.get("feature_report_byte_length") or 0) >= LEN), None)
            rep = next((d for d in infos if d.get("product_id") == pid
                        and d.get("interface_number") == IFACE
                        and (d.get("usage_page"), d.get("usage")) == (REP_PAGE, REP_USAGE)
                        and (d.get("input_report_byte_length") or 0) >= LEN), None)
            if cmd is None or rep is None:
                continue
            got = self._read(cmd["path"], rep["path"])
            if got is None:
                continue
            level, charging = got
            self._diag.append(f"  -> {level}% charging={charging}")
            return [DeviceStatus(f"{KEY}:{pid:04x}", NAME, level, charging, True,
                                 "areson", kind="mouse", via="2.4 GHz receiver")]
        return []

    def _read(self, cmd_path, rep_path) -> Optional[Tuple[int, bool]]:
        cmd = hid.device()  # type: ignore[union-attr]
        rep = hid.device()  # type: ignore[union-attr]
        try:
            rep.open_path(rep_path)
            cmd.open_path(cmd_path)
            req = build_request()
            for attempt in range(2):
                if attempt:
                    time.sleep(0.2)
                try:
                    cmd.send_feature_report(req)
                except (OSError, ValueError) as e:
                    self._diag.append(f"  feature write: {e}")
                    break
                end = time.time() + LISTEN_MS / 1000.0
                while time.time() < end:
                    r = rep.read(64, READ_TIMEOUT_MS)
                    if not r:
                        continue
                    self._diag.append(f"  {hexdump(r, 16)}")
                    parsed = parse_reply(r)
                    if parsed is not None:
                        return parsed
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            for dev in (cmd, rep):
                try:
                    dev.close()
                except Exception:
                    pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
