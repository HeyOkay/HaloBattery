"""Ninjutso Sora V2 and Sprime PM1 (Nordic vendor 1915).

Both answer the NinjaForce status feature report 0x05, command 0x15:

    request  05 15 00 00 01 00 ... (32 bytes)
    reply    05 15 ... [9] percent (0 means 100), [10] charging,
             [11] fully charged (Sprime), [12] online

The Sora V2 receiver is AE1C, its cable AE11. The Sprime PM1 uses AC11 /
AC12 / AC13 on the cable and AC1C / AD8C on the receiver. Only this read is
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

VID = 0x1915
SORA_RECEIVER = 0xAE1C
SORA_CABLE = 0xAE11
SPRIME_CABLES = (0xAC11, 0xAC12, 0xAC13)
SPRIME_RECEIVERS = (0xAC1C, 0xAD8C)
PAGE, USAGE = 0xFFA0, 0x0001
REPORT = 0x05
COMMAND = 0x15
FEATURE_LEN = 32
READ_TIMEOUT_MS = 250
LISTEN_MS = 500


def is_cable(pid: int) -> bool:
    return pid in (SORA_CABLE,) + SPRIME_CABLES


def model(pid: int):
    if pid in (SORA_RECEIVER, SORA_CABLE):
        return "ninjutso:sora_v2", "Ninjutso Sora V2", pid == SORA_CABLE
    if pid in SPRIME_CABLES + SPRIME_RECEIVERS:
        return "sprime:pm1", "Sprime PM1", pid in SPRIME_CABLES
    return None


def parse_reply(r, cable: bool, allow_full: bool = False) -> Optional[Tuple[int, bool, bool]]:
    """(level, charging, connected). A reply's byte 9 == 0 means 100 %."""
    if not r or len(r) < 13:
        return None
    b = list(r)
    if b[0] != REPORT or b[1] != COMMAND or b[9] > 100:
        return None
    connected = cable or b[12] != 0
    level = 100 if b[9] == 0 else b[9]
    charging = connected and b[10] != 0
    if allow_full and b[11]:
        level, charging = 100, False
    return level, charging, connected


class NinjutsoSprimeProvider(Provider):
    name = "ninjutso_sprime"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(ninjutso): %s", e)
            return []

        out: List[DeviceStatus] = []
        seen = set()
        for d in infos:
            pid = d.get("product_id")
            if not isinstance(pid, int):
                continue
            m = model(pid)
            if m is None:
                continue
            key, name, cable = m
            if key in seen:
                continue
            if ((d.get("usage_page"), d.get("usage")) != (PAGE, USAGE)
                    or (d.get("feature_report_byte_length") or 0) < FEATURE_LEN):
                continue
            got = self._read(d["path"], cable, key == "sprime:pm1")
            if got is None:
                continue
            level, charging, connected = got
            seen.add(key)
            out.append(DeviceStatus(key, name, level if connected else None,
                                    charging, connected, "ninjutso_sprime",
                                    kind="mouse",
                                    via="USB cable" if cable else "2.4 GHz receiver"))
        return out

    def _read(self, path, cable: bool, allow_full: bool) -> Optional[Tuple[int, bool, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        req = bytearray(FEATURE_LEN)
        req[0] = REPORT
        req[1] = COMMAND
        req[4] = 0x01
        try:
            for attempt in range(4):
                if attempt:
                    time.sleep(0.09)
                if dev.send_feature_report(bytes(req)) < 0:
                    break
                time.sleep(0.015)
                r = dev.get_feature_report(REPORT, 32)
                if not r:
                    continue
                self._diag.append(f"  {hexdump(r, 16)}")
                parsed = parse_reply(r, cable, allow_full)
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
