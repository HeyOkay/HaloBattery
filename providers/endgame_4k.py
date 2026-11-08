"""Endgame Gear 4K mice behind the "HS Dongle" (3367:1970).

One dongle id serves several mice - XM2w 4K, OP1w 4K and their v2 revisions - and the
mouse behind it is what the vendor software resolves; the adapter itself is neutral.

The exchange is the family's 17-byte frame, documented for the XM2we by
okms/xm2we-linux (PROTOCOL.md) and by Janlhin/Endgame_Gear_Web_Configurator
(src/xm2we.ts: buildFrame, frameValid, readBattery):

    request  feature report 0x08:  08 <opcode> 00 ... <checksum>   (17 bytes,
                                   checksum = 0x55 - sum of the first 16 bytes)
    reply    input report 0x09:    09 <opcode> 00 <addr> <size> <data...> <checksum>
                                   the data starts at byte 6, the whole frame sums to 0x55

Battery is opcode 0x04: data[0] = percent, data[1] = charging. It is read before the
link state because plugging the cable briefly drops the link while the level stays
valid. Only these two documented status reads are ever built.

The two collections of one dongle are paired by the receiver part of their HID paths:
the feature collection is 0xff02:0002, the replies arrive on an 0xff01 collection.

Unverified on this dongle: the frame and the battery opcode are documented for the
XM2we, and the 4K dongle exposes the same two collections. If it does not answer, the
diagnostics says so and nothing is shown.
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x3367
PIDS = {0x1970: "Endgame Gear 4K mouse"}

BATTERY = 0x04
LINK = 0x03
REPORT_OUT = 0x08
REPORT_IN = 0x09
FRAME_LEN = 17
CHECKSUM = 0x55
TIMEOUT = 0.4
MIN_PERCENT, MAX_PERCENT = 0, 100


def receiver_path(path: bytes) -> bytes:
    """The receiver part of a HID path, without the per-collection identifiers.

    Windows paths carry ColXX in the hardware field and a collection-specific final
    &000N in the instance field; both are removed so the feature and input collections
    of one dongle pair up, and two dongles do not.
    """
    low = path.lower()
    parts = low.split(b"#")
    if len(parts) >= 4 and re.search(br"&col[0-9a-f]{2}$", parts[1]):
        parts[1] = re.sub(br"&col[0-9a-f]{2}$", b"", parts[1])
        parts[2] = re.sub(br"&[0-9a-f]{4}$", b"", parts[2])
        return b"#".join(parts)
    return re.sub(br"&col[0-9a-f]{2}", b"", low)


def query_frame(opcode: int) -> List[int]:
    """The 17-byte status request. Only the documented reads can be built."""
    if opcode not in (BATTERY, LINK):
        raise ValueError("only the battery and link queries are allowed")
    body = [REPORT_OUT, opcode] + [0x00] * 14
    return body + [(CHECKSUM - sum(body)) & 0xFF]


def reply_data(data, opcode: int) -> Optional[List[int]]:
    """The data bytes of a status reply, or None when this is not one."""
    if len(data) != FRAME_LEN or data[0] != REPORT_IN or data[1] != opcode:
        return None
    if sum(data) & 0xFF != CHECKSUM or list(data[2:5]) != [0, 0, 0]:
        return None
    size = data[5]
    if not 1 <= size <= 10:
        return None
    return list(data[6:6 + size])


def parse_battery(data) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a battery reply's data, or None."""
    if not data or len(data) < 1:
        return None
    level = data[0]
    if not MIN_PERCENT <= level <= MAX_PERCENT:
        return None
    return level, (len(data) > 1 and data[1] != 0)


class Endgame4kProvider(Provider):
    name = "endgame_4k"

    def __init__(self):
        self._diag: List[str] = []

    def _ask(self, dev, reader, opcode: int) -> Optional[List[int]]:
        frame = query_frame(opcode)
        try:
            sent = dev.send_feature_report(frame)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query {opcode:02x}: send failed: {e}")
            return None
        if sent != len(frame):
            self._diag.append(f"  query {opcode:02x}: short send ({sent})")
            return None
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            try:
                r = reader.read(64, max(1, int((deadline - time.monotonic()) * 1000)))
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  query {opcode:02x}: read failed: {e}")
                return None
            if not r:
                continue
            data = reply_data(r, opcode)
            if data is None:
                self._diag.append(f"  query {opcode:02x}: other frame {hexdump(r, FRAME_LEN)}")
                continue
            self._diag.append(f"  query {opcode:02x}: {hexdump(r, FRAME_LEN)}")
            return data
        self._diag.append(f"  query {opcode:02x}: no reply")
        return None

    def _read_dongle(self, feature: dict, input_: dict) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        reader = hid.device()
        try:
            dev.open_path(feature["path"])
            reader.open_path(input_["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            # the level first: on the cable the link drops for a moment, the level does not
            data = self._ask(dev, reader, BATTERY)
            got = parse_battery(data)
            if got is not None:
                return got
            self._diag.append("  no battery reading")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            for d in (dev, reader):
                try:
                    d.close()
                except Exception:
                    pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:  # pragma: no cover
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(endgame): %s", e)
            return []
        out: List[DeviceStatus] = []
        for pid, name in PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            features = [d for d in mine
                        if (d.get("usage_page"), d.get("usage")) == (0xFF02, 0x0002)]
            inputs = [d for d in mine if d.get("usage_page") == 0xFF01]
            self._diag.append(f"[Endgame] pid={pid:04x} '{name}' "
                              f"collections={len(mine)} feature={len(features)} "
                              f"input={len(inputs)}")
            if not features or not inputs:
                self._diag.append("  no useable collection pair (0xff02:0002 + 0xff01)")
                continue
            pairs: List[Tuple[dict, dict]] = []
            for f in features:
                key = receiver_path(f["path"])
                match = next((i for i in inputs if receiver_path(i["path"]) == key), None)
                if match is not None:
                    pairs.append((f, match))
            if not pairs:
                self._diag.append("  the feature and input collections do not pair up")
                continue
            for f, i in pairs:
                self._diag.append(f"  dongle iface={f.get('interface_number')} "
                                  f"{f.get('usage_page', 0):04x}:{f.get('usage', 0):04x}"
                                  f" -> input iface={i.get('interface_number')}")
                got = self._read_dongle(f, i)
                if got is None:
                    continue
                level, charging = got
                # the key carries a digest of the receiver path: two dongles of this id
                # are two mice, and the path is what tells them apart
                instance = hashlib.sha1(receiver_path(f["path"])).hexdigest()[:8]
                out.append(DeviceStatus(f"endgame:{pid:04x}:{instance}", name, level,
                                        charging, True, "endgame_4k", kind="mouse"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
