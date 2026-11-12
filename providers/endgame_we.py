"""XM2we receiver battery reader (3367:1961), not the newer XM2w protocol.

Protocol source: okms/xm2we-linux, PROTOCOL.md (Framing / Battery) and
xm2we, frame/query/battery: https://github.com/okms/xm2we-linux
Query 0x04 returns percent and charging; 0x03 returns the radio link state.
Send feature report 0x08; receive input report 0x09, 17 bytes including
the 0x55-complement checksum. No configuration or EEPROM writes are used.
Windows descriptors on the owner's receiver confirm separate collections:
ff02:0002 has feature report 0x08; ff01:0000 has input report 0x09.
Both are opened for the same physical receiver; no other models are enabled.
The receiver can cache battery values, and percentage is a voltage estimate.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import List

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump

VID = 0x3367
PID = 0x1961
BATTERY = 0x04
LINK = 0x03
TIMEOUT = 0.4


def receiver_path(path):
    """Keep the receiver instance, removing both Windows collection identifiers.

    HID paths carry ColXX in the hardware field AND a collection-specific final
    &000N in the instance field. Preserve the rest to avoid pairing two receivers.
    """
    parts = path.lower().split(b"#")
    if len(parts) >= 4 and re.search(br"&col[0-9a-f]{2}$", parts[1]):
        parts[1] = re.sub(br"&col[0-9a-f]{2}$", b"", parts[1])
        parts[2] = re.sub(br"&[0-9a-f]{4}$", b"", parts[2])
        return b"#".join(parts)
    return re.sub(br"&col[0-9a-f]{2}", b"", path.lower())


def query_frame(opcode):
    """Only documented status queries may be constructed here."""
    if opcode not in (BATTERY, LINK):
        raise ValueError("Only battery and link queries are allowed")
    data = [0x08, opcode] + [0] * 14
    return data + [(0x55 - sum(data)) & 0xFF]


def reply_payload(data, opcode):
    if len(data) != 17 or data[0] != 0x09 or data[1] != opcode:
        return None
    if sum(data) & 0xFF != 0x55 or data[2:5] != [0, 0, 0]:
        return None
    size = data[5]
    if not 1 <= size <= 10:
        return None
    return data[6:6 + size]


class EndgameWEProvider(Provider):
    name = "endgame_we"

    def __init__(self):
        self._diag: List[str] = []

    def _query(self, dev, reader, opcode):
        frame = query_frame(opcode)
        self._diag.append(f"  query {opcode:02x}: sending status request")
        try:
            sent = dev.send_feature_report(frame)
        except OSError as e:
            self._diag.append(f"  query {opcode:02x}: send failed: {e}")
            return None
        if sent != len(frame):
            self._diag.append(f"  query {opcode:02x}: short send")
            return None
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                data = list(reader.read(64, remaining))
            except OSError as e:
                self._diag.append(f"  query {opcode:02x}: input read failed: {e}")
                return None
            if not data:
                break
            payload = reply_payload(data, opcode)
            if payload is not None:
                self._diag.append(f"  query {opcode:02x}: {hexdump(data)}")
                return payload
        self._diag.append(f"  query {opcode:02x}: no valid reply")
        return None

    def poll(self):
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except OSError as e:
            self._diag.append(f"[Endgame WE] enumerate: {e}")
            return []
        results = []
        seen = set()
        for info in infos:
            if (info.get("product_id") != PID or
                    info.get("usage_page") != 0xFF02 or info.get("usage") != 0x02):
                continue
            path = info["path"]
            if path in seen:
                continue
            seen.add(path)
            self._diag.append("[Endgame WE] XM2we receiver 3367:1961 ff02:0002")
            incoming = next((entry for entry in infos
                             if entry.get("product_id") == PID
                             and entry.get("usage_page") == 0xFF01
                             and entry.get("usage") == 0
                             and receiver_path(entry["path"]) == receiver_path(path)), None)
            if incoming is None:
                self._diag.append("  no matching ff01:0000 input collection; nothing sent")
                continue
            dev = hid.device()
            reader = hid.device()
            try:
                reader.open_path(incoming["path"])
                dev.open_path(path)
                # Read battery first: link can briefly drop on charger insertion.
                battery = self._query(dev, reader, BATTERY)
                link = self._query(dev, reader, LINK)
                if battery is None or battery[0] > 100:
                    continue
                charging_known = len(battery) > 1 and battery[1] in (0, 1)
                charging = len(battery) > 1 and battery[1] == 1
                online = charging or (link is not None and link[0] == 1)
                # Path distinguishes receivers without publishing their serial numbers.
                key = "endgame_we:" + hashlib.sha256(path).hexdigest()[:16]
                results.append(DeviceStatus(key, "Endgame Gear XM2we", battery[0],
                                            charging, online, self.name, kind="mouse",
                                            wake_on_input=not online,
                                            charging_known=charging_known))
            except (OSError, ValueError) as e:
                self._diag.append(f"  HID error: {e}")
            finally:
                for handle in (dev, reader):
                    try:
                        handle.close()
                    except OSError:
                        pass
        return results

    def diagnostics(self):
        return list(self._diag)
