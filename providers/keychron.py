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


def _identity(info: dict) -> str:
    """Stable per-receiver suffix: the dongle's serial when Windows exposes one,
    else a hash of its collection path - never how many receivers answered."""
    serial = str(info.get("serial_number") or "").strip()
    seed = serial.encode("utf-8") if serial else bytes(info.get("path") or b"")
    return hashlib.sha1(seed).hexdigest()[:8]


class KeychronProvider(Provider):
    name = "keychron"

    def __init__(self):
        self._diag: List[str] = []

    def _picks(self, infos: List[dict]) -> List[dict]:
        """Every battery collection, not just the first.

        Interface 4 is where the Ultra-Link 8K and M5 put their vendor
        collection, but the Link-KM receiver also exposes a plain keyboard
        collection there (0001:0006): choosing by the number alone sent the
        b3 06 query to that silent keyboard page (#270). So interface 4 only
        counts when it is a vendor page, and otherwise the battery page
        ffc1:0001 is chosen explicitly - never the VIA channel (ff60) (#421).

        Two Ultra-Link 8K receivers share the same id; every matching
        collection is returned so each has its own icon (#373).
        """
        vendor4 = [d for d in infos
                   if d.get("interface_number") == VENDOR_INTERFACE
                   and (d.get("usage_page") or 0) >= 0xFF00]
        if vendor4:
            return vendor4
        vendor = [d for d in infos if (d.get("usage_page") or 0) >= 0xFF00]
        battery = [d for d in vendor
                   if (d.get("usage_page"), d.get("usage")) == (0xFFC1, 0x0001)]
        chosen = battery or vendor[:1]
        if chosen:
            self._diag.append(f"  no interface {VENDOR_INTERFACE} vendor collection; "
                              f"using iface={chosen[0].get('interface_number')} "
                              f"{chosen[0].get('usage_page', 0):04x}:"
                              f"{chosen[0].get('usage', 0):04x}")
            return chosen
        self._diag.append(f"  no vendor collection; falling back to the first of {len(infos)}")
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
                try:
                    r = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
                except (OSError, IOError, ValueError) as e:
                    # the receiver was unplugged or went away mid-read: this is
                    # this receiver's failure, not the whole poll's (#373 review)
                    self._diag.append(f"  attempt {attempt + 1} read: {e}")
                    return None
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
            candidates = self._picks(mine)
            # Identity follows what is plugged in, not who answered this time:
            # with two receivers present, the quieter one must not inherit the
            # other's key (and its settings) when it finally answers (#373 review).
            # One receiver keeps the original keychron:<pid> key, so a single
            # dongle's history is not reset by this change.
            multi = len(candidates) > 1
            found = []
            for d in candidates:
                self._diag.append(f"[Keychron] pid={pid:04x} '{name}' "
                                  f"iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                try:
                    got = self._query(d["path"])
                except Exception as e:  # one receiver must not take the poll down
                    self._diag.append(f"  iface={d.get('interface_number')} "
                                      f"{(d.get('usage_page') or 0):04x}:"
                                      f"{(d.get('usage') or 0):04x}: {e}")
                    continue
                if got is not None:
                    found.append((d, got))
            for d, (level, charging) in found:
                key = f"keychron:{pid:04x}"
                if multi:
                    key += ":" + _identity(d)
                out.append(DeviceStatus(key, name, level, charging, True, "keychron"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
