"""MSI wireless mice and keyboards (vendor 0DB0).

Existing read-only protocols:
  * VERSA 300 ELITE / GM31: output request 00 0F 20 ...
  * GM41: passive A0 <raw> 07 ... reports.

New read-only protocol for VERSA PRO WIRELESS (1FC6) and STRIKE PRO
WIRELESS (1620):
  * HID interface 1, usage page 00FF, usage 0001.
  * 65-byte output request: 00 0D B0 01 00 00 00 05 followed by 57 zeroes.
  * 65-byte input reply: byte 7 = 05, byte 9 = battery percentage,
    byte 10 = 01 (external power present) or 02 (external power absent).

Source for the new request/reply format: reporter's direct hardware tests
on MSI VERSA PRO WIRELESS and MSI STRIKE PRO WIRELESS using HidSharp 2.6.4.
The request is sent with HID output write(), not a feature report.

MSI Center's 41 80 remote-control request is deliberately not sent.
"""
from __future__ import annotations

import time
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x0DB0
MODELS = {
    0x9E22: ("msi:9e22", "MSI VERSA 300 ELITE WIRELESS", False, "query", 0xFF0D, 0x00B0),
    0x0D3C: ("msi:0d31", "MSI Clutch GM31 Lightweight Wireless", False, "query", 0xFF00, 0x0001),
    0x0D3B: ("msi:0d31", "MSI Clutch GM31 Lightweight Wireless", True, "query", 0xFF00, 0x0001),
    0x0D4C: ("msi:0d41", "MSI Clutch GM41 Lightweight Wireless", False, "passive", 0xFF10, 0x0006),
    0x0D4B: ("msi:0d41", "MSI Clutch GM41 Lightweight Wireless", True, "passive", 0xFF10, 0x0006),
}

# These receivers use a different, actively queried protocol.
PRO_MODELS = {
    0x1FC6: ("msi:1fc6", "MSI VERSA PRO WIRELESS", "mouse"),
    0x1620: ("msi:1620", "MSI STRIKE PRO WIRELESS", "keyboard"),
}
PRO_PAGE = 0x00FF
PRO_USAGE = 0x0001
PRO_KEEP = 300.0
PRO_REQUEST = bytes([0x00, 0x0D, 0xB0, 0x01, 0x00, 0x00, 0x00, 0x05]) + bytes(57)

INTERFACE = 1
REQUEST = bytes([0x00, 0x0F, 0x20])
READ_TIMEOUT_MS = 250
QUERY_LISTEN_MS = 1000
PASSIVE_LISTEN_MS = 2000


def parse_query(r) -> Optional[Tuple[int, bool]]:
    if not r or len(r) < 15:
        return None
    b = list(r)
    if len(b) > 1 and b[0] == 0x00 and b[1] == 0x0F:
        b = b[1:]
    if b[0] != 0x0F or b[1] != 0x20:
        return None
    level = b[14] & 0x7F
    if level > 100:
        return None
    return level, bool(b[14] & 0x80)


def gm41_percent(raw: int) -> int:
    return max(0, min(100, (raw * 51 + 1760 + 50) // 100))


def parse_passive(r) -> Optional[Tuple[int, int]]:
    if not r or len(r) < 4:
        return None
    b = list(r)
    if b[0] != 0xA0 or b[2] != 0x07:
        return None
    return gm41_percent(b[1]), False


def parse_pro_reply(r) -> Optional[Tuple[int, bool]]:
    """Parse the 65-byte VERSA PRO / STRIKE PRO battery reply."""
    if r is None:
        return None
    # hidapi on Windows strips the leading zero Report ID from input reports.
    if len(r) == 64:
        r = bytes([0]) + bytes(r)
    if len(r) < 65:
        return None
    if r[7] != 0x05:
        return None

    level = r[9]
    charging_state = r[10]
    if level == 0 or level > 100 or charging_state not in (0x01, 0x02):
        return None

    return level, charging_state == 0x01


class MsiProvider(Provider):
    name = "msi"

    def __init__(self):
        self._diag: List[str] = []
        # key -> (HID path, monotonic timestamp, last valid status)
        self._pro_cache: Dict[str, Tuple[object, float, DeviceStatus]] = {}

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            self._diag.append("HIDAPI module is unavailable")
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(msi): %s", e)
            self._diag.append(f"HIDAPI enumeration error: {e}")
            return []

        pro_infos = [info for info in infos if info.get("product_id") in PRO_MODELS]

        out: List[DeviceStatus] = []
        seen = set()

        # Existing MSI models retain their original polling behaviour.
        for pid, (key, name, cable, mode, page, usage) in MODELS.items():
            if key in seen:
                continue
            for d in infos:
                if d.get("product_id") != pid or d.get("interface_number") != INTERFACE \
                        or d.get("usage_page") != page or d.get("usage") != usage:
                    continue
                if mode == "query":
                    if (d.get("output_report_byte_length") or 0) < 65 \
                            or (d.get("input_report_byte_length") or 0) < 65:
                        continue
                    got = self._query(d)
                else:
                    if (d.get("input_report_byte_length") or 0) < 7:
                        continue
                    got = self._passive(d)
                if got is None:
                    continue
                level, charging = got
                seen.add(key)
                out.append(DeviceStatus(
                    key, name, level, charging,
                    level > 0 or charging or not cable,
                    "msi", kind="mouse",
                    via="USB cable" if cable else "2.4 GHz receiver",
                ))
                break

        # New receiver models: query each device on every provider poll.
        present_pro_keys = set()
        for pid, (key, name, kind) in PRO_MODELS.items():
            candidates = [info for info in pro_infos
                          if info.get("product_id") == pid]
            d = next((
                info for info in candidates
                if info.get("interface_number") == INTERFACE
                and info.get("usage_page") == PRO_PAGE
                and info.get("usage") == PRO_USAGE
            ), None)

            if d is None:
                self._diag.append(f"{name}: no matching HID interface")
                if candidates:
                    present_pro_keys.add(key)
                    cached = self._pro_cache.get(key)
                    if cached is not None:
                        _, timestamp, status = cached
                        if time.monotonic() - timestamp <= PRO_KEEP:
                            out.append(replace(
                                status, online=False, polling_stale=True
                            ))
                        else:
                            self._pro_cache.pop(key, None)
                else:
                    self._pro_cache.pop(key, None)
                continue

            present_pro_keys.add(key)
            path = d.get("path")
            cached = self._pro_cache.get(key)

            # A reconnected/re-enumerated HID interface must be queried again.
            if cached is not None and cached[0] != path:
                self._pro_cache.pop(key, None)
                cached = None


            got = self._query_pro(d)
            if got is None:
                if cached is not None:
                    _, timestamp, status = cached
                    if time.monotonic() - timestamp <= PRO_KEEP:
                        out.append(replace(
                            status, online=False, polling_stale=True
                        ))
                    else:
                        self._pro_cache.pop(key, None)
                continue

            level, charging = got
            status = DeviceStatus(
                key, name, level, charging, True,
                "msi", kind=kind, via="2.4 GHz receiver",
            )
            self._pro_cache[key] = (path, time.monotonic(), status)
            out.append(status)

        # Also discard entries for devices no longer enumerated.
        for key in list(self._pro_cache):
            if key not in present_pro_keys:
                self._pro_cache.pop(key, None)

        return out

    def _query_pro(self, d) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        name = d.get("product_string") or "MSI PRO"
        try:
            dev.open_path(d["path"])
            written = dev.write(PRO_REQUEST)
            if written is None or written < 0:
                self._diag.append(f"{name}: HID write failed ({written})")
                return None
            if written != len(PRO_REQUEST):
                self._diag.append(
                    f"{name}: HID write returned {written} bytes "
                    f"(expected {len(PRO_REQUEST)}); continuing"
                )

            end = time.monotonic() + QUERY_LISTEN_MS / 1000.0
            dumped_unknown = False
            while time.monotonic() < end:
                r = dev.read(65, READ_TIMEOUT_MS)
                if not r:
                    continue
                got = parse_pro_reply(r)
                if got is None and not dumped_unknown:
                    self._diag.append(
                        f"{name}: unknown reply: {hexdump(r, 32)}"
                    )
                    dumped_unknown = True
                if got is not None:
                    return got
            self._diag.append(
                f"{name}: no valid reply within {QUERY_LISTEN_MS} ms"
            )
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"{name}: HID error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _query(self, d) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
            out_len = d.get("output_report_byte_length") or 65
            dev.write(REQUEST + bytes(max(0, out_len - len(REQUEST))))
            end = time.time() + QUERY_LISTEN_MS / 1000.0
            while time.time() < end:
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  reply: {hexdump(r, 32)}")
                got = parse_query(r)
                if got is not None:
                    return got
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def _passive(self, d) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
            end = time.time() + PASSIVE_LISTEN_MS / 1000.0
            while time.time() < end:
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  reply: {hexdump(r, 32)}")
                got = parse_passive(r)
                if got is not None:
                    return got
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
