"""Keychron wireless devices over their receiver (Ultra-Link 8K) or cable, without
Keychron's own software.

Protocol from csutcliff/keychron-battery-dkms, which implements it for these ids:

  * 3434:d048  Keychron M5, over the cable
  * 3434:d028  the Ultra-Link 8K receiver

Keychron does not expose a standard HID battery report, so the level comes from the
vendor protocol:

  * the vendor collection on interface 4 - the interface number is the only handle
    that module uses to find it, and a probe logs what is actually present.
  * request: a 64-byte `b3 06 00 ...` frame (report id 0xB3, command 0x06
    status), sent as a feature report on the Ultra-Link 8K.
  * the Nape Pro's Link-KM receiver publishes the same vendor collection on
    interface 1 as an interrupt-OUT endpoint; when SET_FEATURE is refused or
    unanswered, the identical frame is retried with write() (#270, confirmed by
    a Keychron Launcher USB capture).
  * reply: a 64-byte input report on the interrupt endpoint: `b4 06 ... <level> ...`.
    Byte 20 holds the level in its low seven bits and the charging flag in bit 7.
  * up to three attempts, 100 ms apart, with a 500 ms wait for each reply.
"""
from __future__ import annotations

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

    Byte 20 holds the level in its low seven bits and the charging flag in bit 7
    (csutcliff/keychron-battery-dkms, and the Keychron Launcher capture in #270).
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

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        # Interface 4 is where the Ultra-Link 8K and M5 put their vendor
        # collection, but the Link-KM receiver also exposes a plain keyboard
        # collection on interface 4 (0001:0006). Choosing by the number alone
        # sent the b3 06 query to that silent keyboard page (#270).
        vendor4 = [d for d in infos
                   if d.get("interface_number") == VENDOR_INTERFACE
                   and (d.get("usage_page") or 0) >= 0xFF00]
        if vendor4:
            return vendor4[0]
        # The Nape Pro's Link-KM receiver puts its battery vendor collection on
        # interface 1 (ffc1:0001), not interface 4; it also exposes a VIA page
        # (ff60) on interface 3 that accepts writes but never answers (#270).
        vendor = [d for d in infos if (d.get("usage_page") or 0) >= 0xFF00]
        battery = [d for d in vendor
                   if (d.get("usage_page"), d.get("usage")) == (0xFFC1, 0x0001)]
        chosen = battery or vendor[:1]
        if chosen:
            self._diag.append(f"  no interface {VENDOR_INTERFACE} vendor collection; "
                              f"using iface={chosen[0].get('interface_number')} "
                              f"{chosen[0].get('usage_page', 0):04x}:"
                              f"{chosen[0].get('usage', 0):04x}")
            return chosen[0]
        self._diag.append(f"  no interface {VENDOR_INTERFACE} vendor collection; "
                          f"falling back to the first of {len(infos)}")
        return infos[0] if infos else None

    def _query(self, path: bytes) -> Optional[List[int]]:
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
                feature_ok = sent is not None and sent >= 0
                if not feature_ok:
                    self._diag.append(f"  attempt {attempt + 1} feature refused "
                                      f"({sent}); trying output")
                r = None
                if feature_ok:
                    try:
                        r = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
                    except (OSError, IOError, ValueError) as e:
                        self._diag.append(f"  attempt {attempt + 1} feature read: {e}")
                        feature_ok = False
                if feature_ok and not r:
                    # The Nape Pro's Link-KM receiver (#270) publishes its vendor
                    # collection as an interrupt-OUT endpoint. The capture of
                    # Keychron Launcher shows the same b3 06 frame written as a
                    # 64-byte output report; SET_FEATURE is refused or ignored,
                    # so retry the identical frame on the output endpoint.
                    self._diag.append(f"  attempt {attempt + 1}: feature accepted, "
                                      "no reply; trying output")
                    feature_ok = False
                if not feature_ok:
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
                        self._diag.append(f"  attempt {attempt + 1} output read: {e}")
                        continue
                if not r:
                    self._diag.append(f"  attempt {attempt + 1}: no reply")
                    continue
                self._diag.append(f"  attempt {attempt + 1} reply: {hexdump(r)}")
                if parse_level(r) is not None:
                    return list(r)
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
        seen = set()
        for pid in PIDS:
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            d = self._pick(mine)
            if d is None:
                continue
            seen.add(d["path"])
            name = PIDS[pid]
            self._diag.append(f"[Keychron] pid={pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            reply = self._query(d["path"])
            parsed = parse_level(reply)
            if parsed is None:
                continue
            level, charging = parsed
            out.append(DeviceStatus(f"keychron:{pid:04x}", name, level, charging, True,
                                    "keychron"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
