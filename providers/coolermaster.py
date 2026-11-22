"""Cooler Master MM712 / MM731 wireless mice (vendor 2516).

The dongle (01B7) and the two cable ids expose a vendor collection on
interface 3, usage page FF0A. A 4-byte output request `00 46 81 00` is
answered by a status report `47 81 <model> ...`; byte 10 is the level,
byte 11 charging, byte 12 the transport (0 cable, 1 dongle). Model byte 50
identifies the MM712 and 70 the MM731 on the shared dongle.

Protocol from syntactic-spice/coolermaster-usb-protocol and
riVFerd/MM731-Controller. Only the status request is sent.
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

VID = 0x2516
DONGLE = 0x01B7
MM712_CABLE = 0x0169
MM731_CABLE = 0x0167
PIDS = (DONGLE, MM712_CABLE, MM731_CABLE)
PAGE = 0xFF0A
INTERFACE = 3
REQUEST = bytes([0x00, 0x46, 0x81, 0x00])
READ_TIMEOUT_MS = 250
LISTEN_MS = 500
ATTEMPTS = 2


def model_name(pid: int, model: int) -> Tuple[str, str]:
    if pid == MM712_CABLE or (pid == DONGLE and model == 0x50):
        return "coolermaster:mm712", "Cooler Master MM712"
    if pid == MM731_CABLE or (pid == DONGLE and model == 0x70):
        return "coolermaster:mm731", "Cooler Master MM731"
    return f"coolermaster:{pid:04x}", "Cooler Master wireless mouse"


def parse_reply(r) -> Optional[Tuple[int, bool, int, int]]:
    """(level, charging, transport, model) from a 47 81 status report."""
    if not r or len(r) < 13:
        return None
    b = list(r)
    if b[0] != 0x47 or b[1] != 0x81:
        return None
    level = b[10]
    if not 1 <= level <= 100:
        return None
    return level, b[11] != 0, b[12], b[2]


class CoolerMasterProvider(Provider):
    name = "coolermaster"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(coolermaster): %s", e)
            return []

        found: Dict[str, List[Tuple[str, dict]]] = {}
        for d in infos:
            pid = d.get("product_id")
            if not isinstance(pid, int) or pid not in PIDS or d.get("interface_number") != INTERFACE \
                    or d.get("usage_page") != PAGE:
                continue
            found.setdefault(f"pid:{pid}", []).append((d.get("product_string") or "", d))

        merged: Dict[str, DeviceStatus] = {}
        for _, entries in found.items():
            for product, d in entries:
                got = self._read(d["path"], product, d.get("product_id"),
                                 d.get("output_report_byte_length") or 32)
                if got is None:
                    continue
                level, charging, transport, model = got
                key, name = model_name(d.get("product_id"), model)
                via = "USB cable" if transport == 0 else "2.4 GHz receiver"
                st = merged.get(key)
                if st is None:
                    merged[key] = DeviceStatus(key, name, level, charging, True,
                                               "coolermaster", kind="mouse", via=via)
                else:
                    st.charging = st.charging or charging
                    if charging:
                        st.via = via
                    if level > (st.level or 0):
                        st.level = level
        if not merged:
            self._diag.append("  no answer (mouse off or asleep)")
        return list(merged.values())

    def _read(self, path, product: str, pid, out_len: int = 32) -> Optional[Tuple[int, bool, int, int]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open {hex(pid or 0)}: {e}")
            return None
        try:
            for _ in range(ATTEMPTS):
                try:
                    dev.write(REQUEST + bytes(max(0, out_len - len(REQUEST))))
                except (OSError, ValueError) as e:
                    self._diag.append(f"  write: {e}")
                    continue
                deadline = time.time() + LISTEN_MS / 1000.0
                while time.time() < deadline:
                    r = dev.read(64, READ_TIMEOUT_MS)
                    if not r:
                        continue
                    self._diag.append(f"  {hexdump(r, 16)}")
                    parsed = parse_reply(r)
                    if parsed is not None:
                        return parsed
            self._diag.append(f"  {product or hex(pid or 0)}: no status reply")
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
