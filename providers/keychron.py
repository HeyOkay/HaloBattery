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
    `-1`; the same frame is then written as an output report (#373). That retry is
    made only on the documented battery collection `ffc1:0001`.
  * nothing else is opened: a receiver whose collections are only keyboard,
    mouse, consumer, VIA or unrelated vendor pages is skipped (#421 review).
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
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

KEYCHRON_VID = 0x3434

USAGE_PAGE = 0
USAGE = 0
VENDOR_INTERFACE = 4            # the interface the module binds to
# The battery collection the exchange is documented for: the Link-KM's on interface 1
# (#270, Keychron Launcher capture, confirmed on an M6) and the Ultra-Link 8K's on
# interface 4 (#373 diagnostics: `if=4 usage=ffc1:0001`).
BATTERY_COLLECTION = (0xFFC1, 0x0001)
VIA_PAGE = 0xFF60               # the VIA channel: accepts writes, never answers b3 06 (#270)

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
    """A receiver's own key suffix, from its HID path alone.

    The path is unique per plugged-in receiver and stays the same across replugging into
    the same port and across reboots (Windows builds it from the dongle's serial number when
    it has one, otherwise from its USB port). It never depends on which other receivers are
    plugged in or answering (#373 review); a serial string could repeat between two units.
    """
    return hashlib.sha256(b"path:" + bytes(info.get("path") or b"")).hexdigest()


def is_battery_collection(d: dict) -> bool:
    """A collection the b3 06 exchange is documented for; nothing else is opened.

    `ffc1:0001` wherever it is (the Link-KM's interface 1, the Ultra-Link 8K's
    interface 4), or a vendor page on interface 4, the interface
    csutcliff/keychron-battery-dkms binds to. Never a keyboard, mouse or consumer
    collection and never the VIA channel: a b3 06 write there is not a battery
    query (#421 review).
    """
    page, usage = d.get("usage_page") or 0, d.get("usage") or 0
    if (page, usage) == BATTERY_COLLECTION:
        return True
    return (d.get("interface_number") == VENDOR_INTERFACE
            and page >= 0xFF00 and page != VIA_PAGE)


def output_fallback_allowed(d: dict) -> bool:
    """The write() retry is documented only for the ffc1:0001 battery collection
    (#373 on the Ultra-Link 8K, the Keychron Launcher capture in #270); elsewhere
    the frame stays a feature report, as the Linux module sends it (#421 review)."""
    return ((d.get("usage_page") or 0), (d.get("usage") or 0)) == BATTERY_COLLECTION


class KeychronProvider(Provider):
    name = "keychron"

    def __init__(self):
        self._diag: List[str] = []
        self._legacy_aliases: Dict[str, str] = {}

    def legacy_key_aliases(self) -> Dict[str, str]:
        """The old shared `keychron:<pid>` key -> the one receiver it can only have meant.

        Cached from the last poll. Reported only when exactly one receiver of that id is
        plugged in and it answered; with two, which of them had the old key is unknowable,
        so nothing moves (the app's migrate_device_keys() does the move, once)."""
        return dict(self._legacy_aliases)

    def _picks(self, infos: List[dict]) -> List[dict]:
        """Every battery collection, not just the first.

        Interface 4 is where the Ultra-Link 8K and M5 put their vendor
        collection, but the Link-KM receiver also exposes a plain keyboard
        collection there (0001:0006): choosing by the number alone sent the
        b3 06 query to that silent keyboard page (#270). So interface 4 only
        counts when it is a vendor page, and otherwise the battery page
        ffc1:0001 is chosen explicitly - never the VIA channel (ff60) (#421).
        Without either, nothing is returned and nothing is opened (#421 review).

        Two Ultra-Link 8K receivers share the same id; every matching
        collection is returned so each has its own icon (#373).
        """
        vendor4 = [d for d in infos
                   if d.get("interface_number") == VENDOR_INTERFACE and is_battery_collection(d)]
        if vendor4:
            return vendor4
        battery = [d for d in infos if is_battery_collection(d)]
        if battery:
            self._diag.append(f"  no interface {VENDOR_INTERFACE} vendor collection; "
                              f"using iface={battery[0].get('interface_number')} "
                              f"{battery[0].get('usage_page', 0):04x}:"
                              f"{battery[0].get('usage', 0):04x}")
            return battery
        # Only keyboard, mouse, consumer, VIA or unrelated vendor collections: none of
        # them is a battery endpoint, so none is opened or written (#421 review).
        return []

    def _query(self, d: dict) -> Optional[Tuple[int, bool]]:
        output_ok = output_fallback_allowed(d)
        dev = hid.device()
        try:
            dev.open_path(d["path"])
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
                        self._diag.append(f"  attempt {attempt + 1} feature refused ({sent})"
                                          + ("; trying output" if output_ok else ""))
                    if not output_ok:
                        continue        # no documented output endpoint here (#421 review)
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
        self._legacy_aliases = {}
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
            if not candidates:
                self._diag.append(f"[Keychron] pid={pid:04x} '{name}': no battery collection "
                                  f"(ffc1:0001, or a vendor page on interface {VENDOR_INTERFACE}) "
                                  f"among {len(mine)}; nothing opened")
            found = []
            for d in candidates:
                self._diag.append(f"[Keychron] pid={pid:04x} '{name}' "
                                  f"iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                try:
                    got = self._query(d)
                except Exception as e:  # one receiver must not take the poll down
                    self._diag.append(f"  iface={d.get('interface_number')} "
                                      f"{(d.get('usage_page') or 0):04x}:"
                                      f"{(d.get('usage') or 0):04x}: {e}")
                    continue
                if got is not None:
                    found.append((d, got))
            # Every receiver is keyed on its own identity, whether or not another one is
            # plugged in or answering: a key that depended on the others would move a
            # receiver's history and settings when a sibling is unplugged (#373 review).
            for d, (level, charging) in found:
                key = f"keychron:{pid:04x}:{_identity(d)}"
                out.append(DeviceStatus(key, name, level, charging, True, "keychron"))
            # The key before this change was the shared keychron:<pid>. Count the receivers
            # that are plugged in, not just the ones that answered: a silent sibling makes
            # the old key just as ambiguous.
            if len(candidates) == 1 and len(found) == 1:
                self._legacy_aliases[f"keychron:{pid:04x}"] = out[-1].key
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
