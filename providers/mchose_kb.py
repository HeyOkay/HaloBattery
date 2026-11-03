"""MCHOSE K87 keyboards on the 2.4 GHz receiver (41E4:2001).

Protocol taken from MCHOSE's own web driver (M HUB): the site www.mchose.com.cn ships the
configurator as ``assets/purify.es-*.js``, whose command table has

    ["getBattery", {default: {
        wirelessCommand: "13 4a 01 00 00 ... 00",          # 19 bytes
        wirelessParser: batteryLevel:u8, fullStatus:bit4, chargeStatus:bit4,
        wiredCommand: "06 87 00 00 01 00 02 00 ..." }}]

0x13 is the report id, so the wire frame is ``13 4a 01`` + zeros. On the hardware here
(``41E4:2001``, vendor collection usage page 0xFF02, 20-byte in/out) the answer is

    13 4A 01 00 02 64 01 00 00 ... 00 C5
                     ^^  ^^           ^^ checksum = sum(bytes 0..18) & 0xFF

which decodes as: command echo, 0x02 (link/mode), **0x64 = 100 %**, 0x01 (full/charge
nibble). The checksum matches the frame, so the layout is confirmed against the vendor
parser: after the 5-byte header the payload is ``batteryLevel`` then the full/charge nibble.

The 8-byte feature report on the same interface (usage page 0xFF04, report id 6) is a second
status channel; it reports the same 100 in its own layout, but the output channel above is
the one the vendor driver reads the level from.

Only the 0xFF02 collection is written to, and only for this vendor/product pair.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VENDOR = 0x41E4
PRODUCTS = {0x2001: "MCHOSE K87"}
USAGE_PAGE = 0xFF02

REPORT_ID = 0x13
CMD = 0x4A
REQUEST = bytes([REPORT_ID, CMD, 0x01, 0x00, 0x00, 0x02] + [0] * 13)

ATTEMPTS = 3
ATTEMPT_GAP = 0.15
READ_TIMEOUT_MS = 100

ASLEEP_KEEP = 300


def parse_reply(resp) -> Optional[Tuple[int, int]]:
    """(level, flags) from the getBattery answer, or None.

    ``flags`` is the byte after the level: the vendor schema splits it into
    ``fullStatus`` (high nibble) and ``chargeStatus`` (low nibble). Measured on the hardware
    here, a full keyboard that is **not** on the cable answers ``0x01``, so the low nibble is
    not "charging right now" - it reads 1 while the pack is full, and the tray must not show
    the breathing-charging animation for it. The flag is kept for diagnostics only.
    """
    if not resp or len(resp) < 7:
        return None
    r = bytes(resp)
    if r[0] != REPORT_ID or r[1] != CMD:
        return None
    level = r[5]
    if level > 100:
        return None
    return level, r[6]


class MchoseKbProvider(Provider):
    name = "mchose_kb"

    def __init__(self):
        self._diag: List[str] = []
        self._last = {}
        self._name = "MCHOSE K87"

    def _read(self, path: bytes) -> Optional[Tuple[int, int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.set_nonblocking(True)
            except Exception:
                pass
            for attempt in range(ATTEMPTS):
                try:
                    dev.write(REQUEST)
                except (OSError, ValueError) as e:
                    self._diag.append(f"    write: {e}")
                    return None
                deadline = time.time() + ATTEMPT_GAP
                while time.time() < deadline:
                    try:
                        resp = dev.read(20, timeout_ms=READ_TIMEOUT_MS)
                    except (OSError, ValueError):
                        break
                    if not resp:
                        continue
                    got = parse_reply(resp)
                    if got:
                        self._diag.append(f"    answer (attempt {attempt + 1}): {hexdump(resp, 8)}")
                        return got
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VENDOR)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(mchose_kb): %s", e)
            return []

        out: List[DeviceStatus] = []
        seen = set()
        for d in infos:
            pid = d.get("product_id")
            if d.get("vendor_id") != VENDOR or pid not in PRODUCTS:
                continue
            if (d.get("usage_page") or 0) != USAGE_PAGE:
                continue
            key = f"mchose_kb:{pid:04x}"
            if key in seen:
                continue
            product = (d.get("product_string") or "").strip()
            name = PRODUCTS[pid] if PRODUCTS[pid] else product
            self._diag.append(f"[MCHOSE-KB] pid={pid:04x} product='{product}' "
                              f"iface={d.get('interface_number')} usage={USAGE_PAGE:04x}")
            got = self._read(d["path"])
            if not got:
                self._diag.append("    no answer (keyboard asleep?)")
                continue
            level, flags = got
            seen.add(key)
            self._last[key] = (level, name, time.time())
            self._diag.append(f"  -> {key}: {level}% (flags 0x{flags:02x})")
            out.append(DeviceStatus(key, name, level, False, True,
                                    "mchose_kb", kind="keyboard", wake_on_input=True))

        if not seen:
            last = self._last.get("mchose_kb:2001")
            if last:
                out.append(DeviceStatus("mchose_kb:2001", last[1], last[0], False, False,
                                        "mchose_kb", kind="keyboard", wake_on_input=True))
            elif any(d.get("vendor_id") == VENDOR and d.get("product_id") in PRODUCTS for d in infos):
                out.append(DeviceStatus("mchose_kb:2001", self._name, None, False, False,
                                        "mchose_kb", kind="keyboard", wake_on_input=True))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
