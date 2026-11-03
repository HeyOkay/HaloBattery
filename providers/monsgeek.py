"""MonsGeek 2.4 GHz keyboard dongles (MonsGeek FUN60 Pro and siblings).

Protocol recovered from MonsGeek's own Windows driver (MonsGeek Driver v4,
``resources/app/dist/js/index.bc699543.js``, ``___dongleCheck24GStatusByF7``) and verified on
the hardware of the reporter: a ``3151:5026`` receiver ("MonsGeek 2.4G Wireless Keyboard")
answering 54 %, later 52 %, on repeated polls.

  * the receiver exposes one vendor collection per interface; only the one on usage page
    0xFFFF (usage 0x0002, interface 2) carries the configuration exchange, and it is a
    *feature* report of 65 bytes (report id 0, then 64 payload bytes).
  * the status request is a single byte, 0xF7, at payload byte 0 - the rest zero.
  * the answer comes back through the same feature report:
        payload[0]  status
        payload[1]  keyboard level, 0..100
        payload[2]  mouse level (the dongle's own default, 100, when no mouse is paired)
        payload[3]  keyboard online flag
        payload[4]  mouse online flag
        payload[5]  may send
        payload[6]  device type: 1 keyboard, 2 mouse, 3 both
        payload[8]  RF boot flag
    With the report id in front of it (what hidapi returns) that is r[2] for the level and
    r[7] for the device type.

The driver re-asks rather than re-reads: one request followed by repeated reads keeps
returning the same frame, so every poll sends 0xF7 again before reading.

Only the 0xFFFF collection is written to, and only for the vendor ids below - nothing is
sent to a device that is not one of these dongles.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

MEASURED_VID = 0x3151

# Only the 2.4 GHz receiver is claimed. The wired id of the same board (3151:502F) exposes
# the same 0xFFFF:0x0002 feature collection, but it answers the 0xF7 request with the request
# echoed back and no level (`00 f7 00 ...`, read three times on the hardware), so it is left
# alone rather than shown as 0 %.
DONGLES = {
    0x5026: ("MonsGeek FUN60 Pro", "keyboard"),
}

CONFIG_PAGE = 0xFFFF
CONFIG_USAGE = 0x0002      # the configuration collection on interface 2
STATUS_CMD = 0xF7
REPORT_ID = 0x00
PAYLOAD_LEN = 64
REPORT_LEN = PAYLOAD_LEN + 1        # what hidapi wants: report id + payload

ATTEMPTS = 3
ATTEMPT_GAP = 0.02

# how long a keyboard that stopped answering keeps its greyed-out icon, s
ASLEEP_KEEP = 300


def make_request() -> List[int]:
    """The status frame: report id 0, then 0xF7 and zeros."""
    return [REPORT_ID, STATUS_CMD] + [0] * (PAYLOAD_LEN - 1)


def parse_status(resp) -> Optional[Tuple[int, int, int]]:
    """(keyboard level, mouse level, device type) from a status reply, or None.

    A reply counts only when the device type is one the driver knows (1, 2 or 3) and the
    keyboard level is in range - an asleep receiver answers with zeros, which would
    otherwise show as a real 0 %.
    """
    if not resp or len(resp) < 9:
        return None
    r = bytes(resp)
    kind = r[7]
    level = r[2]
    mouse = r[3]
    if kind not in (1, 2, 3) or level > 100:
        return None
    return level, mouse, kind


class MonsGeekProvider(Provider):
    name = "monsgeek"

    def __init__(self):
        self._diag: List[str] = []
        self._last = {}          # key -> (level, name, when)

    def _read_dongle(self, path: bytes) -> Optional[Tuple[int, int, int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for attempt in range(ATTEMPTS):
                try:
                    dev.send_feature_report(make_request())
                except (OSError, ValueError) as e:
                    self._diag.append(f"    send: {e}")
                    return None
                time.sleep(ATTEMPT_GAP)
                try:
                    resp = dev.get_feature_report(REPORT_ID, REPORT_LEN)
                except (OSError, ValueError):
                    continue
                got = parse_status(resp)
                if got:
                    self._diag.append(f"    answer (attempt {attempt + 1}): {hexdump(resp, 12)}")
                    return got
            self._diag.append("    no 0xF7 answer")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(MEASURED_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(monsgeek): %s", e)
            return []

        out: List[DeviceStatus] = []
        seen = set()
        for d in infos:
            vid = d.get("vendor_id")
            pid = d.get("product_id")
            if vid != MEASURED_VID or pid not in DONGLES:
                continue
            product = (d.get("product_string") or "").strip()
            name = product or DONGLES[pid][0]
            key = f"monsgeek:{pid:04x}"
            if key in seen:
                continue
            if (d.get("usage_page") or 0) != CONFIG_PAGE:
                continue               # the keyboard/mouse collections are left alone
            if (d.get("usage") or 0) != CONFIG_USAGE:
                continue               # 0xFFFF:0x0001 is the same page on another interface and never answers
            if key in seen:
                continue               # already read from the collection that answers
            self._diag.append(f"[MonsGeek] pid={pid:04x} product='{product}' "
                              f"iface={d.get('interface_number')} usage=ffff:{d.get('usage') or 0:04x}")
            got = self._read_dongle(d["path"])
            if not got:
                continue
            level, _mouse, kind = got
            seen.add(key)
            self._diag.append(f"  -> {key}: {level}% (device type {kind})")
            self._last[key] = (level, name, time.time())
            out.append(DeviceStatus(key, name, level, False, True,
                                    "monsgeek", kind="keyboard"))

        now = time.time()
        for key, (level, name, when) in self._last.items():
            if key in seen or now - when >= ASLEEP_KEEP:
                continue
            out.append(DeviceStatus(key, name, level, False, False,
                                    "monsgeek", kind="keyboard"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
