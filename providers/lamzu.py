"""LAMZU Maya X on its 8K dongle (373E:001E) or on the cable (373E:001C).

Source: Sheroune/lamzu-battery-monitory (MIT), and its Linux port in
Quitsoon/gnome-shell-extension-lamzu-battery (MIT); anany1702/lamzu-ctl (MIT) reads
it the same way ("Aurora", the newer revision). The exchange is the one the WLmouse
and G-Wolves providers use - the same firmware family - on the vendor collection
(usage page 0xFFFF) of interface 2:

    request   feature report 0, 64 bytes: 00 00 02 02 00 83 00 ...
    reply     feature report 0:           a1 00 02 02 00 83 <charging> <battery %>

Nothing else is ever written to the mouse. The request and the reply parser are
the ones in wlmouse.py. The dongle cannot tell a sleeping mouse from one that is
switched off, so a silent mouse keeps its last level, greyed out, for a while.

The Maya X uses a feature-report protocol on VID 0x373E. The Thorn / Atlantis
generation is a separate LAMZU family on CompX VID 0x3554; it uses report-8
interrupt frames, shared at the protocol level with some Pulsar/ATK/VXE devices.
Both families are handled here under LAMZU device ownership. Source for the
Atlantis family and its CompX protocol: OpenMouse-Project/mouse-protocol
[PR #85](https://github.com/OpenMouse-Project/mouse-protocol/pull/85), cross-checked
against the Thorn's HID diagnostics and live readings.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .wlmouse import QUERY, parse_feature
from .compx import (CONTROL_USAGE as COMPX_CONTROL_USAGE, PAYLOAD_LEN as COMPX_PAYLOAD_LEN,
                    READ_ATTEMPTS as COMPX_READ_ATTEMPTS, READ_TIMEOUT_MS as COMPX_READ_TIMEOUT_MS,
                    make_request as compx_make_request, output_length as compx_output_length,
                    parse_power as compx_parse_power, voltage_mv as compx_voltage_mv)

LAMZU_VID = 0x373E
DONGLE = 0x001E
WIRED = 0x001C
PIDS = {DONGLE: "dongle", WIRED: "cable"}
NAME = "LAMZU Maya X"

INTERFACE = 2                    # the vendor collection is on interface 2 ...
USAGE_PAGE = 0xFFFF              # ... with usage page 0xFFFF

REPLY_TRIES = 10                 # get the reply up to 10 times, 50 ms apart
ASLEEP_KEEP = 300                # s, as in the WLmouse and G-Wolves providers

Reading = Tuple[int, bool]


def vendor_collection(ifaces: List[dict]) -> Optional[dict]:
    """The one collection the request may go to: interface 2, usage page 0xFFFF."""
    for d in ifaces:
        if d.get("interface_number") == INTERFACE and d.get("usage_page") == USAGE_PAGE:
            return d
    return None


class LamzuProvider(Provider):
    name = "lamzu"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None

    def _read(self, path: bytes) -> Optional[Reading]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.send_feature_report([0x00] + QUERY + [0x00] * (64 - len(QUERY)))
            except (OSError, ValueError) as e:
                self._diag.append(f"    send: {e}")
                return None
            for _ in range(REPLY_TRIES):
                time.sleep(0.05)
                try:
                    resp = dev.get_feature_report(0, 65)
                except (OSError, ValueError):
                    resp = None
                batt, chg = parse_feature(resp)
                if batt is not None:
                    self._diag.append(f"    reply: {hexdump(resp, 12)}")
                    return batt, bool(chg)
            self._diag.append("    no a1 reply to request 0x83 (mouse asleep or off)")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(LAMZU_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(lamzu): %s", e)
            return []
        groups: Dict[int, List[dict]] = {}
        for d in infos:
            if d["product_id"] in PIDS:
                groups.setdefault(d["product_id"], []).append(d)
        if not groups:
            return []

        readings: List[Reading] = []
        # the cable first: it charges, and the dongle has nothing to add then
        for pid in sorted(groups, key=lambda p: p != WIRED):
            self._diag.append(f"[Lamzu] pid={pid:04x} ({PIDS[pid]}) "
                              f"'{(groups[pid][0].get('product_string') or '').strip()}'")
            d = vendor_collection(groups[pid])
            if d is None:
                self._diag.append(f"  no interface {INTERFACE} collection with usage page "
                                  f"{USAGE_PAGE:04x}: nothing sent")
                continue
            self._diag.append(f"  iface={d.get('interface_number')} usage="
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            res = self._read(d["path"])
            if res is not None:
                readings.append(res)
                break

        key = "lamzu"
        if readings:
            batt, chg = readings[0]
            self._last = (batt, chg, time.time())
            return [DeviceStatus(key, NAME, batt, chg, True, "lamzu", kind="mouse")]
        if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
            return [DeviceStatus(key, NAME, self._last[0], self._last[1], False, "lamzu",
                                 kind="mouse")]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)


# The Thorn / Atlantis family uses CompX's report-8 protocol, not the Maya X's
# feature-report command. Keep its product ownership and lifecycle in the Lamzu
# provider while reusing the shared CompX frame builder/parser.
COMPX_VID = 0x3554
ATLANTIS_PIDS = {
    0xF510: "LAMZU Thorn (4K receiver)",
    0xF50F: "LAMZU Thorn (wired)",
}
THORN_KEY = "lamzu:3554f510"
THORN_ASLEEP_KEEP = 300


class LamzuAtlantisProvider(Provider):
    name = "lamzu"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[str, int, bool, float]] = None

    def _pick_compx(self, infos: List[dict]) -> Optional[dict]:
        control = [d for d in infos if (d.get("usage_page"), d.get("usage")) == COMPX_CONTROL_USAGE]
        for d in control + infos:
            size = compx_output_length(d["path"])
            if size in (None, COMPX_PAYLOAD_LEN):
                return d
            self._diag.append(f"  {d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                              f"output={size} cannot take a {COMPX_PAYLOAD_LEN}-byte frame; skipped")
        return infos[0] if infos else None

    def _query_compx(self, path: bytes):
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            for _ in range(4):
                if not dev.read(COMPX_PAYLOAD_LEN, 30):
                    break
            dev.write(compx_make_request())
            for attempt in range(COMPX_READ_ATTEMPTS):
                r = dev.read(COMPX_PAYLOAD_LEN, COMPX_READ_TIMEOUT_MS)
                if not r:
                    break
                self._diag.append(f"  reply {attempt + 1}: {hexdump(r)}")
                parsed = compx_parse_power(r)
                if parsed is not None:
                    return list(r), parsed
                time.sleep(0.02)
            self._diag.append("  no power reply (mouse idle or no response)")
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = [d for d in hidlist.enumerate(COMPX_VID)
                     if d.get("product_id") in ATLANTIS_PIDS]
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(lamzu atlantis): %s", e)
            return []
        groups: Dict[int, List[dict]] = {}
        for d in infos:
            groups.setdefault(d["product_id"], []).append(d)
        now = time.time()
        present = bool(groups)
        # Ask the cable first; its power flag is how the report signals charging.
        for pid in sorted(groups, key=lambda p: p != 0xF50F):
            group = groups[pid]
            name = ATLANTIS_PIDS[pid]
            self._diag.append(f"[Lamzu Thorn] pid=3554:{pid:04x} '{name}'")
            d = self._pick_compx(group)
            if d is None:
                continue
            self._diag.append(f"  iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                              f"output={compx_output_length(d['path'])}")
            result = self._query_compx(d["path"])
            if result is None:
                continue
            raw, (level, charging) = result
            mv = compx_voltage_mv(raw)
            if mv:
                self._diag.append(f"  {mv} mV")
            self._last = (name, level, charging, now)
            return [DeviceStatus(THORN_KEY, name, level, charging, True, "lamzu", kind="mouse")]

        if present and self._last and now - self._last[3] < THORN_ASLEEP_KEEP:
            name, level, charging, _seen = self._last
            return [DeviceStatus(THORN_KEY, name, level, charging, False, "lamzu",
                                 kind="mouse", wake_on_input=True)]
        if not present:
            self._last = None
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
