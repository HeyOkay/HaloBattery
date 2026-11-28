"""PlayStation PULSE Explore earbuds on the PlayStation Link adapter `054c:0ecc`.

The adapter's vendor collection is usage `ff01:0020` on interface 3, with
64-byte feature reports. Read-only:

    82  link state; byte 1 is 1 while earbuds are connected
    A0  ASCII name of the connected device, from byte 1
    B0  device status; byte 55 holds the two levels as nibbles:
        low nibble = left, high nibble = right, each on a 0-15 scale

The reader publishes the lower earbud as the device level and shows both in the
text when they differ. Charging and the case level are not decoded. Other
PlayStation Link devices use unverified status layouts and are ignored.

Protocol source: BatteryHub 1.1.1 src/drivers/playstation_link.cpp
(GPL-3.0-or-later; protocol reference only), from PlayStation Link app captures
and one PULSE Explore pair.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x054C
PID = 0x0ECC
USAGE = (0xFF01, 0x0020)
REPORT_LEN = 64
LINK_ID = 0x82
NAME_ID = 0xA0
STATUS_ID = 0xB0
LEVELS_AT = 55
DEVICE_NAME = "PULSE Explore"
KEY = "pslink:0ecc"
NAME = "PULSE Explore"


def nibble_to_percent(n: int) -> int:
    return (n * 100 + 7) // 15


def _feature(report, report_id: int) -> bytes:
    b = bytes(report or b"")
    if b and b[0] == report_id:
        return b
    return bytes([report_id]) + b


def ascii_name(report) -> str:
    b = _feature(report, NAME_ID)
    return "".join(chr(value) for value in b[1:] if 0x20 <= value < 0x7F)


def parse_levels(report) -> Optional[Tuple[int, int]]:
    """(left, right) percentages from a B0 status report, or None."""
    b = _feature(report, STATUS_ID)
    if len(b) <= LEVELS_AT or b[0] != STATUS_ID:
        return None
    levels = b[LEVELS_AT]
    return nibble_to_percent(levels & 0x0F), nibble_to_percent(levels >> 4)


class PsLinkProvider(Provider):
    name = "pslink"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("product_id") == PID
                    and d.get("interface_number") == 3
                    and (d.get("usage_page"), d.get("usage")) == USAGE
                    and (d.get("feature_report_byte_length") or REPORT_LEN) >= REPORT_LEN):
                return d
        return None

    def _get(self, dev, report_id: int) -> Optional[bytes]:
        try:
            report = dev.get_feature_report(report_id, REPORT_LEN)
        except (OSError, ValueError) as e:
            self._diag.append(f"  feature {report_id:02x}: {e}")
            return None
        if not report:
            return None
        return _feature(report, report_id)

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(pslink): %s", e)
            return []
        d = self._pick(infos)
        if d is None:
            return []
        self._diag.append(f"[PULSE Explore] pid={PID:04x} iface={d.get('interface_number')}")
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return []
        try:
            link = self._get(dev, LINK_ID)
            if link is None or len(link) < 2:
                self._diag.append("  no link-state report")
                return []
            if link[1] == 0:
                self._diag.append("  adapter present, earbuds off or in the case")
                return [DeviceStatus(KEY, NAME, None, False, False, "pslink",
                                     kind="headset", charging_known=False)]
            name = self._get(dev, NAME_ID)
            status = self._get(dev, STATUS_ID)
            if name is None or status is None:
                return []
            self._diag.append(f"  name: {ascii_name(name)!r}")
            if ascii_name(name) != DEVICE_NAME:
                self._diag.append("  another PlayStation Link device; layout unknown")
                return []
            levels = parse_levels(status)
            if levels is None:
                self._diag.append(f"  status: {hexdump(status, 8)}")
                return []
            left, right = levels
            self._diag.append(f"  left {left}%, right {right}% -> {min(left, right)}%")
            return [DeviceStatus(KEY, NAME, min(left, right), False, True, "pslink",
                                 kind="headset", charging_known=False)]
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def diagnostics(self) -> List[str]:
        return list(self._diag)
