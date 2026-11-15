"""HyperX Cloud Core Wireless over its 2.4 GHz dongle, without NGENUITY.

Protocol from LennardKittner/HyperHeadset's `cloud-core` branch and the
reporter's reverse-engineering in its issue #59:

  * the dongle's battery collection is usage page 0xFF00 / usage 0x0001 on
    interface 3. The same interface also carries consumer-control (000c) and
    telephony (000b) collections; they are not battery endpoints.
  * request: one 64-byte FEATURE report, report id 0xFF:
        FF 07 00 FD 04 0C F1 02 01 04 F0 0C + zeroes
  * answer: a short-lived FEATURE report starting FF 12. The battery voltage is
    little-endian at bytes 11..12; byte 9 is 0x03 on battery and 0x05 wired
    charging. The reference polls the feature report for about 100-150 ms,
    because the answer expires if it waits once.
  * the headset has no percentage field. The reference converts the voltage
    with a 20-point curve and averages the last 20 voltages; that curve is
    approximate and was measured on a unit whose battery later failed, so the
    status is marked approximate here rather than presented as an exact number.

The Cloud Core Wireless dongle is 0951:173F (Kingston); its USB-C identity is
0951:1740. HyperHeadset also lists the HP vendor id (03F0), so both vendor ids
are enumerated. The cable and dongle are the same product and share one icon.

Still unverified on HaloBattery hardware: the request, the voltage curve and the
charging byte are the reference's, not a local measurement.
"""
from __future__ import annotations

import bisect
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

HP_VID = 0x03F0
KINGSTON_VID = 0x0951
VENDOR_IDS = (HP_VID, KINGSTON_VID)

PIDS = {
    0x173F: "HyperX Cloud Core Wireless",
    0x1740: "HyperX Cloud Core Wireless",
}
CANONICAL_PID = 0x173F

USAGE_PAGE = 0xFF00
USAGE = 0x0001
REPORT_ID = 0xFF
PACKET_LEN = 64

BATTERY_REQUEST = bytes([0xFF, 0x07, 0x00, 0xFD, 0x04, 0x0C, 0xF1, 0x02,
                         0x01, 0x04, 0xF0, 0x0C] + [0] * (PACKET_LEN - 12))

# LennardKittner/HyperHeadset's cloud-core curve. It is deliberately kept
# approximate: the reference's own measurements came from a failing battery.
VOLTAGE_THRESHOLDS = (3380, 3430, 3440, 3450, 3460, 3470, 3480, 3490, 3500,
                      3510, 3520, 3530, 3540, 3550, 3560, 3570, 3580, 3590,
                      3600, 3610)
VOLTAGE_PERCENTAGES = (5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70,
                       75, 80, 85, 90, 95, 100)

# The feature answer is short-lived; the reference polls it instead of sleeping
# once. Keep this run bounded.
READ_WINDOW_S = 0.25
READ_POLL_S = 0.01
VOLTAGE_AVERAGE_WINDOW = 20


def voltage_to_percent(voltage: int) -> int:
    """The reference's floor lookup on its 20-point voltage curve."""
    if voltage <= VOLTAGE_THRESHOLDS[0]:
        return VOLTAGE_PERCENTAGES[0]
    index = bisect.bisect_right(VOLTAGE_THRESHOLDS, voltage) - 1
    return VOLTAGE_PERCENTAGES[index]


def parse_response(reply) -> Optional[Tuple[int, Optional[bool]]]:
    """(voltage in mV, charging or None) from a Cloud Core feature answer."""
    if not reply or len(reply) < 13:
        return None
    data = list(reply)
    if data[0] != REPORT_ID or data[1] != 0x12:
        return None
    voltage = data[11] | (data[12] << 8)
    if not 2500 <= voltage <= 4500:
        return None
    if data[9] == 0x05:
        charging = True
    elif data[9] == 0x03:
        charging = False
    else:
        charging = None
    return voltage, charging


class HyperXCloudCoreProvider(Provider):
    name = "hyperx_cloud_core"

    def __init__(self):
        self._diag: List[str] = []
        self._history: Dict[str, Deque[int]] = {}

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if (d.get("usage_page"), d.get("usage")) == (USAGE_PAGE, USAGE):
                return d
        offered = ", ".join(f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                            for d in infos)
        self._diag.append(f"  no usage {USAGE_PAGE:04x}:{USAGE:04x} collection "
                          f"(found: {offered})")
        return None

    def _query(self, path: bytes) -> Optional[Tuple[int, Optional[bool]]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                wrote = dev.send_feature_report(list(BATTERY_REQUEST))
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  feature write: {e}")
                return None
            if isinstance(wrote, int) and wrote < 0:
                self._diag.append("  feature write refused")
                return None

            deadline = time.monotonic() + READ_WINDOW_S
            while time.monotonic() < deadline:
                try:
                    reply = dev.get_feature_report(REPORT_ID, PACKET_LEN)
                except (OSError, IOError, ValueError) as e:
                    self._diag.append(f"  feature read: {e}")
                    return None
                parsed = parse_response(reply)
                if parsed is not None:
                    self._diag.append(f"  reply: {hexdump(reply)}")
                    return parsed
                time.sleep(READ_POLL_S)
            self._diag.append("  no battery answer in the feature-report window")
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
        found: Dict[str, DeviceStatus] = {}
        for vid in VENDOR_IDS:
            try:
                infos = hidlist.enumerate(vid)
            except Exception as e:  # pragma: no cover
                log.warning("hid.enumerate(hyperx cloud core %04x): %s", vid, e)
                continue
            for pid in PIDS:
                mine = [d for d in infos if d.get("product_id") == pid]
                if not mine:
                    continue
                d = self._pick(mine)
                if d is None:
                    continue
                key_pid = CANONICAL_PID
                key = f"hyperx:{key_pid:04x}"
                name = PIDS[pid]
                self._diag.append(
                    f"[HyperX Cloud Core] pid={vid:04x}:{pid:04x} '{name}' "
                    f"iface={d.get('interface_number')} "
                    f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                parsed = self._query(d.get("path"))
                if parsed is None:
                    continue
                voltage, charging = parsed
                history = self._history.setdefault(key, deque(maxlen=VOLTAGE_AVERAGE_WINDOW))
                history.append(voltage)
                average = sum(history) // len(history)
                level = voltage_to_percent(average)
                approx = f"about {level}%"
                self._diag.append(f"  voltage {average} mV -> {approx}")
                status = DeviceStatus(
                    key, name, level, bool(charging), True, "hyperx_cloud_core",
                    approx=approx, kind="headset",
                    charging_known=charging is not None,
                    ui_message="state.approx", ui_params={"level": level})
                old = found.get(key)
                if old is None or (status.charging and not old.charging):
                    found[key] = status
        return list(found.values())

    def diagnostics(self) -> List[str]:
        return list(self._diag)
