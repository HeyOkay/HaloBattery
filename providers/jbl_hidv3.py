"""JBL Quantum "HIDV3" headsets (Quantum 520/650/850/950 and X variants).

The receiver or the USB cable exposes a vendor collection with usage page
0xFF12. A feature report 0x88 asks for the two-byte feature id 0x000D
(battery); the headset answers on input report 0x86:

    88 00 DD 01 00 01 00 02 00 0D 00       request
    86 00 DD 01 00 01 ... 0D 00 ... <v>    reply, v = bit 7 charging | level

The status bit 15 rejects an error reply, and a zero-sized/empty feature is
not a level. Only this read-only query is sent.
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

VID = 0x0ECB
PAGE = 0xFF12
FEATURE = 0x000D
REQUEST_ID = 0x88
REPLY_ID = 0x86
REQUEST = bytes([REQUEST_ID, 0x00, 0xDD, 0x01, 0x00, 0x01, 0x00, 0x02,
                 0x00, FEATURE & 0xFF, FEATURE >> 8])
READ_TIMEOUT_MS = 250
LISTEN_MS = 800

# pid -> (model key, display name, via)
MODELS = {
    0x2125: ("jbl:650", "JBL Quantum 650 Wireless", "2.4 GHz receiver"),
    0x2126: ("jbl:650", "JBL Quantum 650 Wireless", "USB cable"),
    0x213B: ("jbl:650x", "JBL Quantum 650X Wireless", "2.4 GHz receiver"),
    0x213D: ("jbl:650x", "JBL Quantum 650X Wireless", "USB cable"),
    0x2127: ("jbl:950", "JBL Quantum 950 Wireless", "2.4 GHz receiver"),
    0x2128: ("jbl:950", "JBL Quantum 950 Wireless", "USB cable"),
    0x213E: ("jbl:950x", "JBL Quantum 950X Wireless", "2.4 GHz receiver"),
    0x2140: ("jbl:950x", "JBL Quantum 950X Wireless", "USB cable"),
    0x218D: ("jbl:520", "JBL Quantum 520 Wireless", "2.4 GHz receiver"),
    0x218E: ("jbl:520", "JBL Quantum 520 Wireless", "USB cable"),
    0x21A7: ("jbl:520x", "JBL Quantum 520X Wireless", "2.4 GHz receiver"),
    0x21A9: ("jbl:520x", "JBL Quantum 520X Wireless", "USB cable"),
    0x219A: ("jbl:850", "JBL Quantum 850 Wireless", "2.4 GHz receiver"),
    0x219B: ("jbl:850", "JBL Quantum 850 Wireless", "USB cable"),
}


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from a HIDV3 device-info reply."""
    if not r:
        return None
    b = list(r)
    if len(b) < 16 or b[0] != REPLY_ID:
        return None
    if b[1:6] != [0x00, 0xDD, 0x01, 0x00, 0x01]:
        return None
    if b[10] & 0x80:              # error status, bit 15
        return None
    if b[11] != (FEATURE & 0xFF) or b[12] != (FEATURE >> 8):
        return None
    if b[13] == 0 and b[14] == 0:
        return None
    raw = b[15]
    level = raw & 0x7F
    if level > 100:
        return None
    return level, bool(raw & 0x80)


class JblHidV3Provider(Provider):
    name = "jbl"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[int, bool]] = {}

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(jbl_hidv3): %s", e)
            return []

        found: Dict[str, List[Tuple[str, str, dict]]] = {}
        for d in infos:
            pid = d.get("product_id")
            if not isinstance(pid, int):
                continue
            model = MODELS.get(pid)
            if model is None:
                continue
            if d.get("usage_page") != PAGE:
                continue
            if (d.get("feature_report_byte_length") or 0) < len(REQUEST):
                continue
            key, name, via = model
            found.setdefault(key, []).append((name, via, d))

        out: List[DeviceStatus] = []
        for key, entries in found.items():
            name = entries[0][0]
            got: Optional[Tuple[int, bool]] = None
            used = ""
            for name, via, d in entries:
                level = self._read(d["path"], name, d.get("feature_report_byte_length") or len(REQUEST))
                if level is not None:
                    got = level
                    used = via
                    if level[1]:
                        break
            if got is None:
                self._diag.append(f"[{key}] no answer (headset off or asleep)")
                if key in self._last:
                    level, charging = self._last[key]
                    out.append(DeviceStatus(key, name, level, charging, False, "jbl",
                                            kind="headset", via=used))
                else:
                    out.append(DeviceStatus(key, name, None, False, False, "jbl",
                                            kind="headset", via=used))
                continue
            self._last[key] = got
            out.append(DeviceStatus(key, name, got[0], got[1], True, "jbl",
                                    kind="headset", via=used))
        return out

    def _read(self, path, name: str, feature_len: int = len(REQUEST)) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"[{name}] open: {e}")
            return None
        try:
            try:
                req = REQUEST + bytes(max(0, feature_len - len(REQUEST)))
                dev.send_feature_report(req)
            except (OSError, ValueError) as e:
                self._diag.append(f"[{name}] feature write: {e}")
                return None
            deadline = time.time() + LISTEN_MS / 1000.0
            while time.time() < deadline:
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"[{name}] {hexdump(r, 24)}")
                parsed = parse_reply(r)
                if parsed is not None:
                    return parsed
            self._diag.append(f"[{name}] no HIDV3 battery reply")
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"[{name}] error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
