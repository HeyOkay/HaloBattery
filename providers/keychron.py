"""Keychron wireless devices over their receiver (Ultra-Link 8K) or cable, without
Keychron's own software.

Protocol from csutcliff/keychron-battery-dkms, which implements it for these ids:

  * 3434:d048  Keychron M5, over the cable
  * 3434:d028  the Ultra-Link 8K receiver

Source: csutcliff/keychron-battery-dkms, keychron_battery.c lines 185-228
(output exchange), 267-289 (byte 20: seven-bit level and charging bit 7).
Windows captures: HaloBattery #270 (Link-KM, ffc1:0001 on interface 1),
#373 (Ultra-Link 8K, ffc1:0001 on interface 4). Request remains the same
64-byte b3 06 + zero padding; feature refusal/silence retries it as output.

Only known products and their exact battery collections are queried. The Link-KM
keyboard/VIA pages and arbitrary vendor fallbacks receive no command. Every
eligible receiver is read independently and keyed by its canonical HID endpoint
path, not enumeration order or duplicate USB serials. A path change creates a
new endpoint identity; this is not a universal hardware UID. Cached legacy-key
aliases are provided only when one receiver for the product is unambiguous.
"""
from __future__ import annotations

import hashlib
import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

KEYCHRON_VID = 0x3434

USAGE_PAGE = 0xFFC1
USAGE = 0x0001
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
    """(percent, charging) from the sourced B4/06 status reply."""
    if not isinstance(r, (bytes, bytearray, list, tuple)) or len(r) <= BATTERY_OFFSET:
        return None
    if r[0] != REPORT_ID_RESP or r[1] != CMD_STATUS:
        return None
    raw = r[BATTERY_OFFSET]
    if type(raw) is not int or not 0 <= raw <= 255:
        return None
    level = raw & 0x7F
    return (level, bool(raw & 0x80)) if level <= 100 else None


class KeychronProvider(Provider):
    name = "keychron"

    def __init__(self):
        self._diag: List[str] = []
        self._legacy_aliases = {}

    def legacy_key_aliases(self):
        """Cached migration hints only when one physical receiver is eligible."""
        return dict(self._legacy_aliases)

    @staticmethod
    def _path(info):
        path = info.get("path")
        if isinstance(path, str):
            path = path.encode("utf-8", errors="surrogatepass")
        return path.lower() if isinstance(path, bytes) and path else None

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            pid = d.get("product_id")
            interface = 1 if pid == 0xD026 else VENDOR_INTERFACE
            if (pid in PIDS and d.get("vendor_id", KEYCHRON_VID) == KEYCHRON_VID
                    and d.get("interface_number") == interface
                    and (d.get("usage_page"), d.get("usage")) == (USAGE_PAGE, USAGE)):
                return d
        self._diag.append("  no sourced ffc1:0001 battery collection; not queried")
        return None

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
                # Both forms carry exactly the captured read-only B3/06 frame.
                # Some Windows receivers reject SET_FEATURE without raising;
                # others accept it but only answer the output endpoint.
                for method, label in ((dev.send_feature_report, "feature"), (dev.write, "output")):
                    try:
                        sent = method(make_request())
                    except (OSError, ValueError) as e:
                        self._diag.append(f"  attempt {attempt + 1} {label} send: {e}")
                        continue
                    if type(sent) is not int or sent < REPORT_SIZE:
                        self._diag.append(f"  attempt {attempt + 1} {label} refused/short ({sent})")
                        continue
                    try:
                        r = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
                    except (OSError, ValueError) as e:
                        self._diag.append(f"  attempt {attempt + 1} {label} read: {e}")
                        continue
                    if not r:
                        self._diag.append(f"  attempt {attempt + 1} {label}: no reply")
                        continue
                    self._diag.append(f"  attempt {attempt + 1} {label} reply: {hexdump(r)}")
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
        self._legacy_aliases = {}
        try:
            infos = hidlist.enumerate(KEYCHRON_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(keychron): %s", e)
            return []
        out = []
        for pid in PIDS:
            mine = [d for d in infos if d.get("product_id") == pid]
            eligible = {}
            for d in mine:
                path = self._path(d)
                if path is not None and self._pick([d]) is not None:
                    eligible.setdefault(path, d)
            keys = []
            for path, d in sorted(eligible.items()):
                # A collection path is physical identity; enumeration order is
                # never an assignment of one receiver's level to another.
                identity = b"path:" + path
                key = f"keychron:{pid:04x}:" + hashlib.sha256(identity).hexdigest()
                keys.append(key)
                name = PIDS[pid]
                self._diag.append(f"[Keychron] pid={pid:04x} '{name}' "
                                  f"iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                parsed = parse_level(self._query(d["path"]))
                if parsed is None:
                    continue
                level, charging = parsed
                out.append(DeviceStatus(key, name, level, charging, True, "keychron"))
            if len(keys) == 1:
                self._legacy_aliases[f"keychron:{pid:04x}"] = keys[0]
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
