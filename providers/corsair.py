"""Corsair wireless receivers (Void v2 Wireless and its siblings) over USB/HID,
without iCUE.

Protocol from Sapd/HeadsetControl's corsair_void_v2w device:

  * the vendor collection on the receiver, which HeadsetControl identifies by
    interface 4 (it gives no usage page/usage for it, so the interface number is
    the only handle on that collection; a probe logs whatever it actually finds).
  * commands are 65 bytes: [0] 0x00 report id, [1] 0x02, [2] endpoint,
    [3] 0x02, [4] command. Endpoint 0x08 is the receiver itself, 0x09 the
    headset behind it. Replies are 64 bytes.
  * talking to a sleeping headset needs the same minimal handshake
    HeadsetControl uses - receiver firmware query, receiver heartbeat, then a
    headset heartbeat - which is enough to read the battery without switching
    the headset into software mode (that switch is audible as a pop).
  * battery: command 0x0f to endpoint 0x09 -> reply[4] | reply[5] << 8 is a
    0..1000 value, ten times the percent. The receiver sometimes answers with
    the paired headset id instead of a level, so a reading of 0 or above 1000 is
    retried a few times and then refused rather than shown.

The reply carries no charging flag: HeadsetControl reports the level as
available (not charging) for this family, and so does this provider.
"""
from __future__ import annotations

import time
from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

CORSAIR_VID = 0x1B1C

USAGE_PAGE = 0
USAGE = 0
CONTROL_INTERFACE = 4           # the collection that carries the protocol

RECEIVER_ENDPOINT = 0x08
HEADSET_ENDPOINT = 0x09
MSG_SIZE_WRITE = 65
MSG_SIZE_READ = 64

CMD_FIRMWARE = 0x13
CMD_HEARTBEAT = 0x12
CMD_BATTERY = 0x0F

FW_SUB = 0x02
HB_SUB = 0x02
BATTERY_SUB = 0x02

LEVEL_INDEX = 4                 # little-endian 16-bit, hundredths
LEVEL_MAX = 1000
ATTEMPTS = 3
READ_TIMEOUT_MS = 500
FLUSH_TIMEOUT_MS = 30

PIDS = {
    0x2A08: "Corsair Void v2 Wireless",
    0x2A02: "Corsair Virtuoso Max Wireless",
    0x0A97: "Corsair HS80 Max Wireless",
}


def make_request(endpoint: int, sub: int, command: int) -> List[int]:
    frame = [0x00, 0x02, endpoint, sub, command]
    return frame + [0x00] * (MSG_SIZE_WRITE - len(frame))


def parse_level(r) -> Optional[int]:
    """Percent from a battery reply, or None when there is no usable value."""
    if not r or len(r) <= LEVEL_INDEX + 1:
        return None
    vendor = r[LEVEL_INDEX] | (r[LEVEL_INDEX + 1] << 8)
    if vendor == 0 or vendor > LEVEL_MAX:
        return None
    return vendor // 10


class CorsairProvider(Provider):
    name = "corsair"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if d.get("interface_number") == CONTROL_INTERFACE:
                return d
        self._diag.append(f"  no interface {CONTROL_INTERFACE} collection; "
                          f"falling back to the first of {len(infos)}")
        return infos[0] if infos else None

    def _write(self, dev, endpoint: int, sub: int, command: int) -> bool:
        dev.write(make_request(endpoint, sub, command))
        return True

    def _drain(self, dev) -> None:
        """Drop replies that are still queued, so the next read is ours."""
        for _ in range(4):
            if not dev.read(MSG_SIZE_READ, FLUSH_TIMEOUT_MS):
                return

    def _query(self, path: bytes) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            # The handshake that wakes a sleeping headset without an audible pop.
            self._write(dev, RECEIVER_ENDPOINT, FW_SUB, CMD_FIRMWARE)
            self._write(dev, RECEIVER_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            self._drain(dev)
            self._write(dev, HEADSET_ENDPOINT, HB_SUB, CMD_HEARTBEAT)
            if not dev.read(MSG_SIZE_READ, READ_TIMEOUT_MS):
                self._diag.append("  headset heartbeat: no reply "
                                  "(headset off or asleep)")
                return None
            self._drain(dev)

            for attempt in range(ATTEMPTS):
                self._write(dev, HEADSET_ENDPOINT, BATTERY_SUB, CMD_BATTERY)
                r = dev.read(MSG_SIZE_READ, READ_TIMEOUT_MS)
                if not r:
                    self._diag.append(f"  attempt {attempt + 1}: no reply")
                    continue
                self._diag.append(f"  attempt {attempt + 1} reply: {hexdump(r)}")
                if parse_level(r) is not None:
                    return list(r)
            self._diag.append("  no usable level in the replies")
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
            log.warning("hid.enumerate(corsair): %s", e)
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
            self._diag.append(f"[Corsair] pid={pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            reply = self._query(d["path"])
            level = parse_level(reply)
            if level is None:
                continue
            out.append(DeviceStatus(f"corsair:{pid:04x}", name, level, False, True,
                                    "corsair", kind="headset"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
