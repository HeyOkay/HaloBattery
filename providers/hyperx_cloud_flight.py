"""HyperX Cloud Flight Wireless over its 2.4 GHz receiver.

The 0951:16C4 and 0951:1723 receivers use the read-only exchange from
HeadsetControl's HyperXCloudFlight implementation: write a 20-byte `21 ff 05`
request, then read 15 or 20 bytes. The battery voltage is big-endian at bytes 3-4;
above 0x100B means charging, otherwise the voltage-to-percent curve estimates the
level.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x0951
PIDS = {
    0x16C4: "HyperX Cloud Flight Wireless",  # older Kingston receiver
    0x1723: "HyperX Cloud Flight Wireless",  # newer Kingston receiver
}
USAGE = 0x0303
USAGE_PAGES = {0xFF53, 0xFF90}
REQUEST = [0x21, 0xFF, 0x05] + [0x00] * 17
REPLY_LENGTHS = {15, 20}
READ_TIMEOUT_MS = 2000
CHARGING_THRESHOLD_MV = 0x100B


def estimate_level(voltage: int) -> int:
    """Estimate the battery percentage using HeadsetControl's Cloud Flight curve."""
    if voltage <= 3648:
        return round(0.00125 * voltage)
    if voltage > 3975:
        return 100
    return round(
        0.00000002547505 * voltage**4
        - 0.0003900299 * voltage**3
        + 2.238321 * voltage**2
        - 5706.256 * voltage
        + 5452299.0
    )


def parse_reply(reply: List[int]) -> Optional[Tuple[Optional[int], bool]]:
    """Return (level, charging) for a supported response length, otherwise None."""
    if len(reply) not in REPLY_LENGTHS:
        return None
    voltage = (reply[3] << 8) | reply[4]
    if voltage > CHARGING_THRESHOLD_MV:
        return None, True
    return estimate_level(voltage), False


class HyperXCloudFlightProvider(Provider):
    name = "hyperx_cloud_flight"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for info in infos:
            if info.get("usage") == USAGE and info.get("usage_page") in USAGE_PAGES:
                return info
        offered = ", ".join(
            f"{info.get('usage_page', 0):04x}:{info.get('usage', 0):04x}" for info in infos
        )
        self._diag.append(f"  no battery collection {USAGE:04x} (found: {offered})")
        return None

    def _query(self, path: bytes) -> Optional[Tuple[Optional[int], bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            if dev.write(REQUEST) < 0:
                self._diag.append("  request write failed")
                return None
            reply = dev.read(20, READ_TIMEOUT_MS)
            if not reply:
                self._diag.append("  no battery reply")
                return None
            parsed = parse_reply(reply)
            if parsed is None:
                self._diag.append(f"  invalid reply ({len(reply)} bytes): {hexdump(reply)}")
                return None
            voltage = (reply[3] << 8) | reply[4]
            self._diag.append(f"  reply ({voltage} mV): {hexdump(reply)}")
            return parsed
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(hyperx cloud flight): %s", e)
            self._diag.append(f"  enumerate: {e}")
            return []

        out = []
        for pid, name in PIDS.items():
            devices = [info for info in infos if info.get("product_id") == pid]
            if not devices:
                continue
            info = self._pick(devices)
            if info is None:
                continue
            self._diag.append(f"[HyperX Cloud Flight] pid={pid:04x} "
                              f"iface={info.get('interface_number')} "
                              f"{info.get('usage_page', 0):04x}:{info.get('usage', 0):04x}")
            parsed = self._query(info["path"])
            if parsed is None:
                continue
            level, charging = parsed
            out.append(DeviceStatus(
                f"hyperx:{pid:04x}", name, level, charging, True,
                "hyperx_cloud_flight",
                approx="charging (level unavailable)" if charging else "",
                kind="headset",
            ))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
