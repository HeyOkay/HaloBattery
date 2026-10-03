"""Corsair Virtuoso SE and Virtuoso XT headsets, over the Slipstream receiver or the
USB cable, without iCUE.

Protocol from Sapd/HeadsetControl, lib/devices/corsair_virtuoso_xt.hpp at commit
8292ac41 ("added support for Virtuoso SE used wired or with receiver", #567):

  * the vendor collection with usage page 0xFF42, usage 0x0001 (HeadsetControl
    names it as interface 3).
  * write the two bytes 02 00 (0x02 is the report id).
  * the reply is 64 bytes: [0] 0x01, [1] status, [2] battery percent 0..100.
    Status 0xF0 is normal. Status 0x00 means the headset is not connected to the
    receiver, so the provider shows nothing (the icon leaves the tray).
  * the headset also sends volume reports (first byte 0x0E) without being asked.
    They can sit in the queue ahead of the reply, so they are skipped; HeadsetControl
    reads at most 8 reports before it gives up.
  * charging is not decoded in the reference ("TBD"), so charging is always False.

Product ids from that file: 0A3E (SE, receiver), 0A3D (SE, cable), 0A64 (XT,
receiver), 0A62 (XT, cable). 0A40 (the "Slipstream Multi-Device Receiver" of
issue #204) is not included: it answers 02 00 with 01 f0 00, so byte 2 is not
the level there. It probably needs the newer property exchange (HeadsetControl
PR #570); it waits for the reporter's probe.
"""
from __future__ import annotations

from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

CORSAIR_VID = 0x1B1C

USAGE_PAGE = 0xFF42
USAGE = 0x0001

REQUEST = bytes([0x02, 0x00])
REPORT_BATTERY = 0x01
STATUS_NORMAL = 0xF0
STATUS_OFFLINE = 0x00
MSG_SIZE_READ = 64
MAX_READS = 8                   # HeadsetControl's MAX_READ_ATTEMPTS
READ_TIMEOUT_MS = 500

# pid -> (model, connection). The model is the key of the tray icon, so a headset
# that is on the receiver and on the cable at once is still one icon.
PIDS = {
    0x0A3E: ("Corsair Virtuoso SE", "receiver"),
    0x0A3D: ("Corsair Virtuoso SE", "cable"),
    0x0A64: ("Corsair Virtuoso XT", "receiver"),
    0x0A62: ("Corsair Virtuoso XT", "cable"),
}
KEYS = {"Corsair Virtuoso SE": "corsair_virtuoso:se",
        "Corsair Virtuoso XT": "corsair_virtuoso:xt"}


def parse_reply(r) -> Optional[int]:
    """Percent from a battery reply, or None (not a battery reply, headset offline,
    level out of range)."""
    if not r or len(r) < 3:
        return None
    if r[0] != REPORT_BATTERY or r[1] == STATUS_OFFLINE:
        return None
    return r[2] if 0 <= r[2] <= 100 else None


class CorsairVirtuosoProvider(Provider):
    name = "corsair_virtuoso"

    def __init__(self):
        self._diag: List[str] = []

    def _query(self, path: bytes) -> Optional[List[int]]:
        """The battery report, or None."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            if dev.write(REQUEST) < 0:      # hidapi returns -1 on failure, it does not raise
                self._diag.append("  write refused")
                return None
            for _ in range(MAX_READS):
                r = dev.read(MSG_SIZE_READ, READ_TIMEOUT_MS)
                if not r:
                    self._diag.append("  no reply")
                    return None
                self._diag.append(f"  reply: {hexdump(r, 8)}")
                if r[0] == REPORT_BATTERY:
                    return list(r)
                # anything else, such as a 0x0E volume report: not ours, read on
            self._diag.append(f"  no battery report in {MAX_READS} reads")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(CORSAIR_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(corsair virtuoso): %s", e)
            return []
        out: List[DeviceStatus] = []
        done = set()
        for pid, (model, via) in PIDS.items():
            key = KEYS[model]
            if key in done:
                continue
            mine = [d for d in infos if d["product_id"] == pid
                    and d.get("usage_page") == USAGE_PAGE and d.get("usage") == USAGE]
            if not mine:
                continue
            d = mine[0]
            self._diag.append(f"[Corsair Virtuoso] pid={pid:04x} '{model}' {via} "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            reply = self._query(d["path"])
            if reply is None:
                continue
            if reply[1] == STATUS_OFFLINE:
                self._diag.append("  headset not connected to the receiver")
                continue
            level = parse_reply(reply)
            if level is None:
                self._diag.append(f"  level out of range: {reply[2] if len(reply) > 2 else None}")
                continue
            if reply[1] != STATUS_NORMAL:
                self._diag.append(f"  status {reply[1]:02x}: not one the reference names")
            done.add(key)
            out.append(DeviceStatus(key, model, level, False, True,
                                    "corsair_virtuoso", kind="headset"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
