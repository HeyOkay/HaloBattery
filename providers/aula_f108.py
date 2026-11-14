"""AULA F108 Pro on its 2.4 GHz receiver, using a 33-byte Windows HID report.

Battery request and additive checksum from meitar/aula-driver,
Sources/AulaKit/Hardware/WirelessAulaDevice.swift lines 82-95 and 174-185:
https://github.com/meitar/aula-driver/blob/985df39f8224d6f031aeafdacf1a6328f458600f/Sources/AulaKit/Hardware/WirelessAulaDevice.swift#L82-L95

On Windows, interface 3 (FF60:0061) answered `00 20 01 00 21 ... 42`
when the keyboard screen showed 33%. The capture puts the level at index 4
including report ID 0, followed by padding and the additive checksum. The
charging field is unknown.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID, PID = 0x05AC, 0x024F
PRODUCT = "F108Pro Dongle"
NAME = "AULA F108 Pro"
INTERFACE = 3
USAGE_PAGE, USAGE = 0xFF60, 0x0061
REPORT_LEN = 33
COMMAND = (0x20, 0x01)
READ_ATTEMPTS, READ_MS = 4, 150


def make_request() -> bytes:
    """Report ID 0, the battery command, zero padding and its checksum."""
    return bytes((0, *COMMAND)) + bytes(REPORT_LEN - 4) + bytes((sum(COMMAND),))


def parse_reply(report: Optional[Sequence[int]]) -> Optional[int]:
    if not report:
        return None
    # hidapi can strip the zero report ID from the input buffer.
    if len(report) == REPORT_LEN and report[0] == 0:
        report = report[1:]
    if len(report) != REPORT_LEN - 1 or tuple(report[:3]) != (*COMMAND, 0):
        return None
    level = report[3]
    if 0 <= level <= 100 and (sum(report[:-1]) & 0xFF) == report[-1]:
        return level
    return None


class AulaF108Provider(Provider):
    name = "aula_f108"

    def __init__(self):
        self._diag: List[str] = []

    def _read(self, path) -> Optional[int]:
        dev = hid.device()
        try:
            dev.open_path(path)
            if dev.write(make_request()) != REPORT_LEN:
                self._diag.append("  battery request was not written in full")
                return None
            for _ in range(READ_ATTEMPTS):
                reply = dev.read(REPORT_LEN, READ_MS)
                level = parse_reply(reply)
                if level is not None:
                    self._diag.append(f"  {hexdump(reply, REPORT_LEN)} -> {level}%")
                    return level
                if reply:
                    self._diag.append(f"  ignored reply: {hexdump(reply, REPORT_LEN)}")
            self._diag.append("  no valid battery reply")
        except (OSError, ValueError) as e:
            self._diag.append(f"  HID I/O: {e}")
        finally:
            try:
                dev.close()
            except (OSError, ValueError):
                pass
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except (OSError, ValueError) as e:  # pragma: no cover
            log.warning("hid.enumerate(aula_f108): %s", e)
            return []

        for d in infos:
            if (d.get("product_id") != PID
                    or (d.get("product_string") or "").strip().casefold() != PRODUCT.casefold()
                    or d.get("interface_number") != INTERFACE
                    or (d.get("usage_page"), d.get("usage")) != (USAGE_PAGE, USAGE)):
                continue
            self._diag.append(f"[AULA] {NAME} {VID:04x}:{PID:04x} iface={INTERFACE} "
                              f"{USAGE_PAGE:04x}:{USAGE:04x}")
            level = self._read(d["path"])
            if level is not None:
                return [DeviceStatus(f"aula_f108:{PID:04x}", NAME, level, source=self.name,
                                     kind="keyboard", charging_known=False)]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
