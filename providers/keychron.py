"""Keychron wireless devices over their receiver (Ultra-Link 8K) or cable, without
Keychron's own software.

Protocol from csutcliff/keychron-battery-dkms, which implements it for these ids:

  * 3434:d048  Keychron M5, over the cable
  * 3434:d028  the Ultra-Link 8K receiver

Keychron does not expose a standard HID battery report, so the level comes from the
vendor protocol:

  * the vendor collection on interface 4 - the interface number is the only handle
    that module uses to find it, and a probe logs what is actually present.
  * request: `b3 06 00 ...` (report id 0xB3, command 0x06 status), normally as a
    64-byte *feature* report. Some 8K receivers reject the feature form with
    `-1`; the same frame is then written as an output report (#373).
  * reply: a 64-byte input report on the interrupt endpoint: `b4 06 ... <level> ...`.
    Byte 20 holds the level in its low seven bits and the charging flag in bit 7
    (#373).
  * up to three attempts, 100 ms apart, with a 500 ms wait for each reply.

A single receiver id can appear twice (two Ultra-Link 8K dongles); every
candidate collection is queried, so each receiver gets its own icon.
"""
from __future__ import annotations

import hashlib
import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

KEYCHRON_VID = 0x3434

USAGE_PAGE = 0
USAGE = 0
VENDOR_INTERFACE = 4            # the interface the module binds to

REPORT_ID_CMD = 0xB3
REPORT_ID_RESP = 0xB4
CMD_STATUS = 0x06
BATTERY_OFFSET = 20
REPORT_SIZE = 64

ATTEMPTS = 3
RETRY_DELAY = 0.1               # 100 ms between attempts
READ_TIMEOUT_MS = 500           # the module waits 500 ms for the reply
FLUSH_TIMEOUT_MS = 30

PIDS = {
    0xD028: "Keychron Ultra-Link 8K",
    0xD048: "Keychron M5",
    0xD026: "Keychron Nape Pro",      # "Keychron Link-KM" (#270): vendor channel on iface 1
}


def make_request() -> List[int]:
    return [REPORT_ID_CMD, CMD_STATUS] + [0x00] * (REPORT_SIZE - 2)


def parse_level(r) -> Optional[Tuple[int, bool]]:
    """(percent, charging) from a status reply, or None when this is not one.

    Bit 7 of byte 20 is the charging flag on the Ultra-Link 8K (#373); the
    low seven bits are the level.
    """
    if not r or len(r) <= BATTERY_OFFSET:
        return None
    if r[0] != REPORT_ID_RESP or r[1] != CMD_STATUS:
        return None
    raw = r[BATTERY_OFFSET]
    level = raw & 0x7F
    if not 0 <= level <= 100:
        return None
    return level, bool(raw & 0x80)


class KeychronProvider(Provider):
    name = "keychron"

    def __init__(self):
        self._diag: List[str] = []

    def _picks(self, infos: List[dict]) -> List[dict]:
        """Every candidate collection, not just the first.

        Two Ultra-Link 8K receivers share the same id; both interface-4
        collections are queried so each has its own icon (#373).
        """
        ifaces = [d for d in infos if d.get("interface_number") == VENDOR_INTERFACE]
        if ifaces:
            return ifaces
        # the Nape Pro's Link-KM receiver puts its vendor collection on interface 1
        # (ffc1:0001), not interface 4: pick the vendor page wherever it is (#270)
        vendor = [d for d in infos if (d.get("usage_page") or 0) >= 0xFF00]
        if vendor:
            # Keychron's battery page is ffc1; the Nape Pro also exposes a VIA
            # channel (ff60) that is not a battery channel.
            battery = [d for d in vendor if d.get("usage_page") == 0xFFC1]
            chosen = battery or vendor[:1]
            self._diag.append(f"  no interface {VENDOR_INTERFACE} collection; using "
                              f"iface={chosen[0].get('interface_number')} "
                              f"{chosen[0].get('usage_page', 0):04x}:"
                              f"{chosen[0].get('usage', 0):04x}")
            return chosen
        self._diag.append(f"  no interface {VENDOR_INTERFACE} collection; "
                          f"falling back to the first of {len(infos)}")
        return infos[:1]

    def _query(self, path: bytes) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            for attempt in range(ATTEMPTS):
                if attempt:
                    time.sleep(RETRY_DELAY)
                sent = None
                try:
                    sent = dev.send_feature_report(make_request())
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"  attempt {attempt + 1} feature send: {e}")
                if sent is None or sent < 0:
                    # Some Ultra-Link 8K receivers reject SET_FEATURE with -1;
                    # the same request as an output report is accepted (#373).
                    if sent is not None:
                        self._diag.append(f"  attempt {attempt + 1} feature refused "
                                          f"({sent}); trying output")
                    try:
                        sent = dev.write(make_request())
                    except (OSError, IOError, ValueError) as e:
                        self._diag.append(f"  attempt {attempt + 1} output send: {e}")
                        continue
                    if sent is None or sent < 0:
                        self._diag.append(f"  attempt {attempt + 1} output refused ({sent})")
                        continue
                r = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
                if not r:
                    self._diag.append(f"  attempt {attempt + 1}: no reply")
                    continue
                self._diag.append(f"  attempt {attempt + 1} reply: {hexdump(r)}")
                parsed = parse_level(r)
                if parsed is not None:
                    return parsed
            self._diag.append("  no usable level in the replies")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(KEYCHRON_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(keychron): %s", e)
            return []
        out = []
        for pid in PIDS:
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            name = PIDS[pid]
            found = []
            for d in self._picks(mine):
                self._diag.append(f"[Keychron] pid={pid:04x} '{name}' "
                                  f"iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                got = self._query(d["path"])
                if got is not None:
                    found.append((d["path"], got))
            for path, (level, charging) in found:
                if len(found) == 1:
                    key = f"keychron:{pid:04x}"
                else:
                    key = f"keychron:{pid:04x}:{hashlib.sha1(path).hexdigest()[:8]}"
                out.append(DeviceStatus(key, name, level, charging, True, "keychron"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
