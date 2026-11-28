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
    `-1`; the same frame is then written as an output report (#373). The Link-KM
    capture in #270 also documents that output form when the feature request is
    accepted but unanswered. Both retries are limited to `ffc1:0001`.
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
    0xD029: "Keychron Link-KM mouse",
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
    this provider retains its existing feature-only requests (#421 review).

    The Linux source supplies the frame and interface, but its current
    keychron_battery.c uses HID_OUTPUT_REPORT, not a feature report.
    """
    return ((d.get("usage_page") or 0), (d.get("usage") or 0)) == BATTERY_COLLECTION


# --- Keychron models added from BatteryHub 1.1.1's keychron.cpp -------------
# The provider above reads the Ultra-Link 8K/Link-KM mice with B3 06. The
# remaining families use separate, documented exchanges:
#   D038 4K Link       FF0A:0001, 41 00 81 01 -> level byte 6, state byte 5
#   D030/D031 1K Link  page 008C, feature 51/06 -> level byte 11, charge byte 12
#   D029 Link-KM mouse FFC1:0001, B5/B3 06 (the existing B3 06 path)
#   0A50 K5 Max        FF60:0061, B2 slots then A4 -> level byte 1, state byte 4
#   1260 Q6 Ultra 8K   FF60:0061, B2 slots then A7 31 -> level byte 2, charge byte 3
# All are read-only. Protocol and hardware evidence: BatteryHub 1.1.1
# src/drivers/keychron.cpp (GPL-3.0-or-later; protocol reference only).

FOUR_K = 0xD038
LEGACY_PIDS = (0xD030, 0xD031)
LINK_KM_MOUSE = 0xD029
KEYBOARD_PIDS = (0x0A50, 0x1260)
RAW_USAGE = (0xFF60, 0x0061)


def _strip_id(report):
    b = bytes(report or b"")
    if len(b) == 65 and b[0] == 0x00:
        b = b[1:]
    return b


def make_4k_request(cmd: int, sub: int = 0x01) -> List[int]:
    payload = [0x00] * REPORT_SIZE
    payload[0] = cmd
    payload[2] = 0x81
    payload[3] = sub
    payload[63] = (0xA1 - sum(payload[:63])) & 0xFF
    return [0x00] + payload


def parse_4k(reply) -> Optional[Tuple[int, bool]]:
    b = _strip_id(reply)
    if len(b) < 13 or b[0] != 0x41 or b[3] != 0x01:
        return None
    if not 1 <= b[6] <= 100:
        return None
    return b[6], b[5] != 0


def parse_legacy(reply) -> Optional[Tuple[int, int, bool]]:
    """(mouse pid, percent, charging) from the D030/D031 feature reply."""
    b = _strip_id(reply)
    if len(b) < 13 or b[0] != 0x51 or b[1] != 0x06 or b[2] != 0x01:
        return None
    if (b[4] | (b[5] << 8)) != 0x3434 or b[11] > 100:
        return None
    return b[6] | (b[7] << 8), b[11], b[12] == 0x01


def _raw_query(dev, cmd: bytes, timeout: float = 0.6) -> Optional[bytes]:
    for _ in range(4):
        if not dev.read(33, 30):
            break
    request = bytes([0x00]) + cmd + bytes(32 - len(cmd))
    dev.write(request)
    for _ in range(max(2, int(timeout / 0.1))):
        reply = dev.read(33, 100)
        if not reply:
            continue
        b = bytes(reply)
        if b[:len(cmd)] == cmd or (len(b) > len(cmd) and b[1:1 + len(cmd)] == cmd):
            return b
    return None


def parse_raw_keyboard(reply, pid: int) -> Optional[Tuple[int, bool]]:
    b = bytes(reply or b"")
    if b and b[0] == 0x00:
        b = b[1:]
    if pid == 0x1260:
        if len(b) < 4 or b[0:2] != b"\xA7\x31" or b[2] > 100:
            return None
        return b[2], b[3] != 0
    if len(b) < 5:
        return None
    if b[0] != 0xA4 or b[1] > 100:
        return None
    return b[1], b[4] == 1


def keyboard_name(pid: int) -> str:
    return "Keychron Q6 Ultra 8K" if pid == 0x1260 else "Keychron K5 Max"


def legacy_name(pid: int) -> str:
    return {0xD032: "Keychron M3", 0xD033: "Keychron M3",
            0xD035: "Keychron M1", 0xD036: "Keychron M3 mini",
            0xD03E: "Keychron M3 mini", 0xD03B: "Keychron M2",
            0xD03D: "Keychron M2 mini", 0xD03F: "Keychron M6",
            0xD044: "Keychron M7"}.get(pid, "Keychron mouse")


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
                feature_ok = sent is not None and sent >= 0
                if not feature_ok:
                    self._diag.append(f"  attempt {attempt + 1} feature refused ({sent})"
                                      + ("; trying output" if output_ok else ""))
                r = None
                if feature_ok:
                    try:
                        r = dev.read(REPORT_SIZE, READ_TIMEOUT_MS)
                    except (OSError, IOError, ValueError) as e:
                        self._diag.append(f"  attempt {attempt + 1} feature read: {e}")
                        return None
                if feature_ok and not r and output_ok:
                    # #270: the Link-KM accepts SET_FEATURE but can answer only
                    # the identical captured B3/06 frame on its output endpoint.
                    self._diag.append(f"  attempt {attempt + 1}: feature accepted, "
                                      "no reply; trying output")
                    feature_ok = False
                if not feature_ok:
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
                        self._diag.append(f"  attempt {attempt + 1} output read: {e}")
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

    def _read_4k(self, d: dict) -> Optional[Tuple[int, bool, str]]:
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"  [4K] open: {e}")
            return None
        try:
            for _ in range(4):
                if not dev.read(REPORT_SIZE, 30):
                    break
            dev.write(make_4k_request(0x01))
            first = b""
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                first = dev.read(REPORT_SIZE, 100)
                if first:
                    break
            b = _strip_id(first)
            if len(b) < 8 or b[0] != 0x01 or b[3] != 0x01:
                self._diag.append("  [4K] no 01 reply")
                return None
            tag = b[6] | (b[7] << 8)
            if tag in (0x3434, 0x362D):
                self._diag.append("  [4K] receiver answered as 8K; not the 4K layout")
                return None
            dev.write(make_4k_request(0x41))
            deadline = time.monotonic() + 0.6
            while time.monotonic() < deadline:
                reply = dev.read(REPORT_SIZE, 100)
                parsed = parse_4k(reply) if reply else None
                if parsed is not None:
                    return parsed[0], parsed[1], "Keychron 4K mouse"
            self._diag.append("  [4K] no 41 battery reply")
            return None
        except (OSError, ValueError) as e:
            self._diag.append(f"  [4K] query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _read_legacy(self, d: dict) -> Optional[Tuple[int, int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"  [1K] open: {e}")
            return None
        try:
            try:
                dev.send_feature_report([0x51, 0x06])
            except (OSError, ValueError) as e:
                self._diag.append(f"  [1K] feature send: {e}")
                return None
            reply = b""
            for _ in range(5):
                time.sleep(0.04)
                for length in (1001, 65):
                    try:
                        reply = dev.get_feature_report(0x51, length)
                    except (OSError, ValueError):
                        reply = b""
                        continue
                    if reply:
                        break
                if reply:
                    break
            parsed = parse_legacy(reply) if reply else None
            if parsed is None:
                self._diag.append("  [1K] no 51/06 reply")
                return None
            return parsed
        except (OSError, ValueError) as e:
            self._diag.append(f"  [1K] query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    @staticmethod
    def _slot_pid(reply, wanted) -> Optional[int]:
        b = bytes(reply or b"")
        if b and b[0] == 0x00:
            b = b[1:]
        if len(b) < 2 or b[0] != 0xB2:
            return None
        slots = min(b[1], 5)
        for i in range(slots):
            off = 2 + 5 * i
            if off + 4 >= len(b):
                break
            vid = b[off] | (b[off + 1] << 8)
            pid = b[off + 2] | (b[off + 3] << 8)
            if vid == 0x3434 and pid in wanted and b[off + 4] == 1:
                return pid
        return None

    def _read_keyboard(self, d: dict, pid: Optional[int], via_receiver: bool) -> Optional[Tuple[int, int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"  [keyboard] open: {e}")
            return None
        try:
            kbpid = pid
            if via_receiver:
                slots = _raw_query(dev, b"\xB2", 0.3)
                wanted = set(range(0x0A50, 0x0A59)) | {0x1260}
                kbpid = self._slot_pid(slots, wanted) if slots else None
                if kbpid is None:
                    self._diag.append("  [keyboard] no keyboard in the receiver slots")
                    return None
            if kbpid is None:
                return None
            cmd = b"\xA7\x31" if kbpid == 0x1260 else b"\xA4"
            reply = _raw_query(dev, cmd, 0.4)
            if reply is None and kbpid != 0x1260:
                reply = _raw_query(dev, cmd, 0.4)   # first one wakes the link
            parsed = parse_raw_keyboard(reply, kbpid) if reply else None
            if parsed is None:
                self._diag.append(f"  [keyboard] no reply for {kbpid:04x}")
                return None
            return kbpid, parsed[0], parsed[1]
        except (OSError, ValueError) as e:
            self._diag.append(f"  [keyboard] query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _special(self, infos: List[dict]) -> List[DeviceStatus]:
        out: List[DeviceStatus] = []
        seen = set()
        # 4K Link receiver
        for d in [x for x in infos if x.get("product_id") == FOUR_K
                  and (x.get("usage_page"), x.get("usage")) == (0xFF0A, 0x0001)]:
            got = self._read_4k(d)
            if got is not None:
                level, charging, name = got
                out.append(DeviceStatus(f"keychron:{FOUR_K:04x}:{_identity(d)}", name,
                                        level, charging, True, "keychron", kind="mouse"))
            break
        # 1K receivers: mouse via feature 51/06 and K5 Max via raw A4
        for pid in LEGACY_PIDS:
            for d in [x for x in infos if x.get("product_id") == pid
                      and (x.get("usage_page") or 0) == 0x008C]:
                got = self._read_legacy(d)
                if got is not None:
                    mouse_pid, level, charging = got
                    out.append(DeviceStatus(f"keychron:{pid:04x}:{mouse_pid:04x}",
                                            legacy_name(mouse_pid), level, charging,
                                            True, "keychron", kind="mouse"))
                break
            for d in [x for x in infos if x.get("product_id") == pid
                      and (x.get("usage_page"), x.get("usage")) == RAW_USAGE]:
                got = self._read_keyboard(d, None, True)
                if got is not None:
                    kbpid, level, charging = got
                    key = f"keychron-kb:{kbpid:04x}"
                    if key not in seen:
                        seen.add(key)
                        out.append(DeviceStatus(key, keyboard_name(kbpid), level, charging,
                                                True, "keychron", kind="keyboard"))
                break
        # Q6 Ultra 8K behind the Ultra-Link 8K receiver
        for d in [x for x in infos if x.get("product_id") == 0xD028
                  and (x.get("usage_page"), x.get("usage")) == RAW_USAGE
                  and "keychron" in (x.get("product_string") or "").lower()]:
            got = self._read_keyboard(d, None, True)
            if got is not None:
                kbpid, level, charging = got
                key = f"keychron-kb:{kbpid:04x}"
                if key not in seen:
                    seen.add(key)
                    out.append(DeviceStatus(key, keyboard_name(kbpid), level, charging,
                                            True, "keychron", kind="keyboard"))
            break
        # Keyboards on their USB cable
        for pid in KEYBOARD_PIDS:
            for d in [x for x in infos if x.get("product_id") == pid
                      and (x.get("usage_page"), x.get("usage")) == RAW_USAGE]:
                got = self._read_keyboard(d, pid, False)
                if got is not None:
                    kbpid, level, charging = got
                    key = f"keychron-kb:{kbpid:04x}"
                    if key not in seen:
                        seen.add(key)
                        out.append(DeviceStatus(key, keyboard_name(kbpid), level, charging,
                                                True, "keychron", kind="keyboard"))
                break
        return out

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        self._legacy_aliases = {}
        try:
            infos = hidlist.enumerate(KEYCHRON_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(keychron): %s", e)
            return []
        out = []
        out += self._special(infos)
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
