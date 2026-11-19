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

import hashlib
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
    if any(type(value) is not int or not 0 <= value <= 255 for value in report):
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


def receiver_path(path) -> Optional[bytes]:
    """Normalize a Windows collection path, without using an optional serial."""
    if isinstance(path, bytes):
        try:
            path = path.decode("utf-8")
        except UnicodeError:
            return None
    if not isinstance(path, str) or not path or "\0" in path:
        return None
    try:
        return path.casefold().encode("utf-8")
    except UnicodeError:
        return None


class AulaF108Provider(Provider):
    name = "aula_f108"

    def __init__(self):
        self._diag: List[str] = []
        self._legacy_aliases = {}

    def legacy_key_aliases(self):
        """Cached metadata only; never guess which of two receivers had the old key."""
        return dict(self._legacy_aliases)

    def _read(self, path) -> Optional[int]:
        dev = hid.device()
        try:
            dev.open_path(path)
            metadata = getattr(dev, "get_report_lengths", None)
            expected_write = REPORT_LEN
            if metadata is not None and not callable(metadata):
                self._diag.append("  invalid report metadata; not querying")
                return None
            if callable(metadata):
                caps = metadata()
                if (not isinstance(caps, dict)
                        or any(type(caps.get(kind)) is not int or caps[kind] < REPORT_LEN
                               for kind in ("input", "output"))):
                    self._diag.append("  incompatible report contract; not querying")
                    return None
                # Native write pads to the collection's largest output report;
                # its successful byte count can exceed this canonical 33-byte query.
                expected_write = caps["output"]
            sent = dev.write(make_request())
            if type(sent) is not int or sent != expected_write:
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
        self._legacy_aliases = {}
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except (OSError, ValueError) as e:  # pragma: no cover
            log.warning("hid.enumerate(aula_f108): %s", e)
            return []

        eligible = {}
        for d in infos:
            product = d.get("product_string")
            if (d.get("vendor_id") != VID or d.get("product_id") != PID
                    or not isinstance(product, str)
                    or product.strip().casefold() != PRODUCT.casefold()
                    or d.get("interface_number") != INTERFACE
                    or (d.get("usage_page"), d.get("usage")) != (USAGE_PAGE, USAGE)):
                continue
            path = receiver_path(d.get("path"))
            if path is not None:
                eligible.setdefault(path, d)
        out = []
        for path, d in sorted(eligible.items()):
            self._diag.append(f"[AULA] {NAME} {VID:04x}:{PID:04x} iface={INTERFACE} "
                              f"{USAGE_PAGE:04x}:{USAGE:04x}")
            level = self._read(d["path"])
            if level is not None:
                key = f"aula_f108:{PID:04x}:" + hashlib.sha256(b"path:" + path).hexdigest()
                out.append(DeviceStatus(key, NAME, level, source=self.name,
                                        kind="keyboard", charging_known=False))
        # Count eligible receivers, not just successful reads: a silent sibling
        # makes the historical shared key ambiguous too.
        if len(eligible) == 1 and len(out) == 1:
            self._legacy_aliases[f"aula_f108:{PID:04x}"] = out[0].key
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
