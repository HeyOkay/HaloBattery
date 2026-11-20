"""Machenike L7 Arctic / L7 Mix (vendor 248A).

Official L7 Mix app protocol: feature report 0x51, command 0x06, 21 bytes.
The reply is another 0x51/0x06 frame with the status marker `8A 24 4E FF`
at bytes 4-7, link bits in byte 10, level in byte 11 and charging in byte 12.

The receiver is `248A:FF4C` on interface 1, the cable `248A:FF4E` on
interface 2; both use usage page 0x008C, usage 1. Only this read is sent.
The 248A vendor id is shared with Ajazz, so the product string must be an L7.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x248A
RECEIVER = 0xFF4C
CABLE = 0xFF4E
PIDS = (CABLE, RECEIVER)
PAGE, USAGE = 0x008C, 0x0001
REPORT = 0x51
COMMAND = 0x06
STATUS_LEN = 21
KEY = "machenike:l7"
NAME = "Machenike L7 Arctic / L7 Mix"
READ_TIMEOUT_MS = 20
LISTEN_MS = 200


def parse_status(r, cable: bool) -> Optional[Tuple[bool, Optional[int], bool]]:
    """(connected, level, charging)."""
    if not r or len(r) != STATUS_LEN:
        return None
    b = list(r)
    if b[0] != REPORT or b[1] != COMMAND:
        return None
    if b[4:8] != [0x8A, 0x24, 0x4E, 0xFF]:
        return None
    if (b[10] & 7) != (0 if cable else 1):
        return None
    connected = cable or bool(b[10] & 8)
    if not connected:
        return False, None, False
    if b[11] > 100 or b[12] > 1:
        return None
    return True, b[11], b[12] == 1


class MachenikeProvider(Provider):
    name = "machenike"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(machenike): %s", e)
            return []

        # cable first: a live cable reading is better than the receiver's cached state
        for pid in (CABLE, RECEIVER):
            cable = pid == CABLE
            for d in infos:
                if d.get("product_id") != pid:
                    continue
                product = d.get("product_string") or ""
                if "L7" not in product:
                    continue
                if ((d.get("interface_number") != (2 if cable else 1))
                        or (d.get("usage_page"), d.get("usage")) != (PAGE, USAGE)
                        or (d.get("input_report_byte_length") or 0) != 33
                        or (d.get("output_report_byte_length") or 0) != 33):
                    continue
                got = self._read(d["path"], cable)
                if got is None:
                    continue
                connected, level, charging = got
                if not connected:
                    return [DeviceStatus(KEY, NAME, None, False, False,
                                         "machenike", kind="mouse")]
                self._diag.append(f"  -> {level}% charging={charging}")
                return [DeviceStatus(KEY, NAME, level, charging, True,
                                     "machenike", kind="mouse",
                                     via="USB cable" if cable else "2.4 GHz receiver")]
        return []

    def _read(self, path, cable: bool) -> Optional[Tuple[bool, Optional[int], bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        req = bytearray(STATUS_LEN)
        req[0] = REPORT
        req[1] = COMMAND
        try:
            if dev.send_feature_report(bytes(req)) < 0:
                return None
            end = time.time() + LISTEN_MS / 1000.0
            while time.time() < end:
                time.sleep(0.01)
                r = dev.get_feature_report(REPORT, STATUS_LEN)
                if not r:
                    continue
                self._diag.append(f"  {hexdump(r, 21)}")
                parsed = parse_status(r, cable)
                if parsed is not None:
                    return parsed
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
