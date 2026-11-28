"""Niceboy MW400 wireless mouse on its 2.4 GHz receiver `248a:fa02`.

The receiver's interface 0 exposes a 33-byte vendor input/output report on
usage `0001:0000`. A read-only request `05 24 fa 48` is answered with

    05 .. fa 48 <lo> <hi> <charging> ...

`raw = lo | hi << 8` and `millivolts = raw * 990 / 240`. The configuration app
maps that voltage to 10 % steps; the same table is used here. All three payload
bytes zero mean the receiver answered but the mouse is asleep or off; its last
reading then stays greyed for ``ASLEEP_KEEP_S`` and refreshes on movement.

The 248A vendor id is shared with other Telink receivers, so only `fa02` is
claimed. Protocol source: BatteryHub 1.1.1 src/drivers/niceboy.cpp
(GPL-3.0-or-later; protocol reference only), from the Niceboy MW400 app and
hardware replies.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x248A
PID = 0xFA02
REPORT_ID = 0x05
REPORT_LEN = 33
TIMEOUT_S = 0.5
ASLEEP_KEEP_S = 1800.0             # the receiver stays plugged in while the mouse sleeps
KEY = "niceboy:fa02"
NAME = "Niceboy MW400"

MV = (4200, 4100, 4000, 3900, 3800, 3700, 3500, 3400, 3200)
PCT = (100, 90, 80, 70, 60, 50, 40, 30, 20)


def percent_from_mv(mv: int) -> int:
    for threshold, level in zip(MV, PCT):
        if mv >= threshold:
            return level
    return 10


def make_request() -> List[int]:
    request = [0x00] * REPORT_LEN
    request[:4] = [REPORT_ID, 0x24, 0xFA, 0x48]
    return request


def _payload(report) -> bytes:
    b = bytes(report or b"")
    if len(b) > 1 and b[0] == 0x00 and b[1] == REPORT_ID:
        b = b[1:]
    return b


def is_asleep_reply(report) -> bool:
    """True for a battery reply whose payload is all zero (mouse asleep or off)."""
    b = _payload(report)
    return (len(b) >= 7 and b[0] == REPORT_ID and b[2] == 0xFA and b[3] == 0x48
            and (b[4] | b[5] | b[6]) == 0)


def parse_reply(report) -> Optional[Tuple[int, bool]]:
    """(percent, charging) from a battery reply, or None for another report."""
    b = _payload(report)
    if len(b) < 7 or b[0] != REPORT_ID or b[2] != 0xFA or b[3] != 0x48:
        return None
    if (b[4] | b[5] | b[6]) == 0:
        return None
    mv = (b[4] | (b[5] << 8)) * 990 // 240
    return percent_from_mv(mv), b[6] != 0


class NiceboyProvider(Provider):
    name = "niceboy"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and d.get("interface_number") == 0
                    and (d.get("usage_page"), d.get("usage")) == (0x0001, 0x0000)
                    and (d.get("input_report_byte_length") or REPORT_LEN) == REPORT_LEN
                    and (d.get("output_report_byte_length") or REPORT_LEN) == REPORT_LEN):
                return d
        return None

    def _query(self, path) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            for _ in range(4):
                if not dev.read(REPORT_LEN, 30):
                    break
            dev.write(make_request())
            for _ in range(6):
                reply = dev.read(REPORT_LEN, 80)
                if not reply:
                    continue
                self._diag.append(f"  reply: {hexdump(reply, 10)}")
                parsed = parse_reply(reply)
                if parsed is not None:
                    return parsed
                if is_asleep_reply(reply):
                    break                  # the receiver answered for a sleeping mouse
            self._diag.append("  no battery reply (mouse asleep or off)")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(niceboy): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            self._last = None
            return []
        self._diag.append(f"[Niceboy] pid={PID:04x} iface={d.get('interface_number')}")
        parsed = self._query(d["path"])
        if parsed is not None:
            level, charging = parsed
            self._last = (level, charging, time.monotonic())
            return [DeviceStatus(KEY, NAME, level, charging, True, "niceboy", kind="mouse")]
        if self._last is not None and time.monotonic() - self._last[2] <= ASLEEP_KEEP_S:
            level, charging, _seen = self._last
            return [DeviceStatus(KEY, NAME, level, charging, False, "niceboy",
                                 kind="mouse", wake_on_input=True)]
        self._last = None
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
