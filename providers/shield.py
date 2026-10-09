"""NVIDIA SHIELD Controller 2017 (0955:7214), over USB or Bluetooth HID.

Protocol: Linux drivers/hid/hid-nvidia-shield.c, hostcmd request/response
structures and thunderstrike_parse_battery_payload/charger_payload:
https://github.com/torvalds/linux/blob/master/drivers/hid/hid-nvidia-shield.c

Only battery (7), charger (58) and board-info (16) queries are sent. Reports are 33 bytes:
output [4, command, 0, ...]; input [3, command, 0, payload...]. Battery
capacity is byte 14. Charger payload is connected, type, state; state 2
means charging. Board-info supplies the same serial across both transports.
Tested on NVIDIA Controller v01.04 over Bluetooth and USB on Windows 11.
"""
from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional, Sequence, Tuple, Union

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x0955
PID = 0x7214
TIMEOUT = 1.0
REQUEST_REPORT = 0x04
RESPONSE_REPORT = 0x03
CMD_BATTERY = 0x07
CMD_CHARGER = 0x3A
CMD_BOARD_INFO = 0x10
TRANSACTION = 0
REQUEST_LENGTH = 33
INPUT_LENGTHS = (33, 65)  # Windows pads input to the collection's maximum.
HEADER_LENGTH = 3
BATTERY_BYTE = 14
SERIAL_START, SERIAL_END = 5, 19  # 7 little-endian words after the board revision
CHARGER_CHARGING = 2
CHARGER_STATES = (0, 1, 2, 3, 8)  # unknown, disabled, charging, full, failed
_BT_HID_GUID = "{00001124-0000-1000-8000-00805f9b34fb}"


def is_bluetooth(path: Union[str, bytes]) -> bool:
    """Windows Bluetooth HID paths use VID& or the classic HID service GUID."""
    text = path.decode("ascii", "ignore") if isinstance(path, bytes) else path
    text = text.lower()
    return "vid&" in text or _BT_HID_GUID in text


def parse_battery(report: Sequence[int]) -> Optional[int]:
    # Windows pads input reports to the collection's 65-byte maximum.
    expected = [RESPONSE_REPORT, CMD_BATTERY, TRANSACTION]
    if len(report) not in INPUT_LENGTHS or list(report[:HEADER_LENGTH]) != expected:
        return None
    return report[BATTERY_BYTE] if 0 <= report[BATTERY_BYTE] <= 100 else None


def parse_charger(report: Sequence[int]) -> Optional[bool]:
    expected = [RESPONSE_REPORT, CMD_CHARGER, TRANSACTION]
    if len(report) not in INPUT_LENGTHS or list(report[:HEADER_LENGTH]) != expected:
        return None
    connected, charger_type, state = report[3:6]
    if connected not in (0, 1) or charger_type not in (0, 1, 2) or state not in CHARGER_STATES:
        return None
    if connected and state == 0:
        return None  # Firmware explicitly reports an unknown charger state.
    return bool(connected and state == CHARGER_CHARGING)


def parse_serial(report: Sequence[int]) -> Optional[str]:
    """Board serial bytes form a transport-independent identity; zero means unavailable."""
    expected = [RESPONSE_REPORT, CMD_BOARD_INFO, TRANSACTION]
    if len(report) not in INPUT_LENGTHS or list(report[:HEADER_LENGTH]) != expected:
        return None
    serial = bytes(report[SERIAL_START:SERIAL_END])
    return serial.hex() if any(serial) else None


class ShieldProvider(Provider):
    name = "shield"

    def __init__(self) -> None:
        self._diag: List[str] = []

    def _query(self, device, command: int) -> List[int]:
        packet = [REQUEST_REPORT, command, TRANSACTION] + [0] * (REQUEST_LENGTH - HEADER_LENGTH)
        if device.write(packet) != len(packet):
            raise OSError("Incomplete HID write")
        deadline = time.monotonic() + TIMEOUT
        expected = [RESPONSE_REPORT, command, TRANSACTION]
        while time.monotonic() < deadline:
            data = device.read(max(INPUT_LENGTHS), max(1, int((deadline - time.monotonic()) * 1000)))
            if len(data) >= HEADER_LENGTH and list(data[:HEADER_LENGTH]) == expected:
                self._diag.append(f"    reply cmd={command:#04x} len={len(data)}: {hexdump(data)}")
                return data
        self._diag.append(f"    cmd={command:#04x}: no matching reply within {TIMEOUT:.1f} s")
        return []

    def _read(self, path: Union[str, bytes]) -> Optional[Tuple[int, Optional[bool], Optional[str]]]:
        device = hid.device()
        try:
            device.open_path(path)
            level = parse_battery(self._query(device, CMD_BATTERY))
            if level is None:
                self._diag.append("    no valid battery reply")
                return None
            self._diag.append(f"    battery: {level}%")
            charging = None
            try:
                charging = parse_charger(self._query(device, CMD_CHARGER))
            except (OSError, ValueError) as exc:
                self._diag.append(f"    charger: {exc}")
            self._diag.append(f'    charging: {charging if charging is not None else "unknown"}')
            # Re-read identity: a different USB pad can reuse the same port/path.
            serial = None
            try:
                serial = parse_serial(self._query(device, CMD_BOARD_INFO))
            except (OSError, ValueError) as exc:
                self._diag.append(f"    board info: {exc}")
            return level, charging, serial
        except (OSError, ValueError) as exc:
            self._diag.append(f"    HID: {exc}")
            return None
        finally:
            try:
                device.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            devices = hidlist.enumerate(VID)
        except Exception as exc:
            log.warning("hid.enumerate(shield): %s", exc)
            return []
        results: Dict[str, DeviceStatus] = {}
        for info in devices:
            if info["product_id"] != PID:
                continue
            path = info["path"]
            bluetooth = is_bluetooth(path)
            # HID serial is a Bluetooth address on BT but empty on the tested USB pad.
            # Keep a per-interface fallback instead of collapsing unidentified pads.
            fallback = info.get("serial_number") or hashlib.sha256(
                path if isinstance(path, bytes) else path.encode()).hexdigest()[:16]
            self._diag.append(f"[SHIELD] NVIDIA SHIELD Controller (2017), "
                              f"{'Bluetooth' if bluetooth else 'USB'} pid={PID:04x}")
            self._diag.append(f"  iface={info.get('interface_number')} "
                              f"usage={info.get('usage_page', 0):04x}:{info.get('usage', 0):04x}")
            result = self._read(path)
            if result is not None:
                level, charging, serial = result
                key = f"shield:board:{serial}" if serial else f"shield:{str(fallback).lower()}"
                status = DeviceStatus(key, "NVIDIA SHIELD Controller", level,
                                      bool(charging), source=self.name,
                                      kind="gamepad", via="bluetooth" if bluetooth else "",
                                      charging_known=charging is not None)
                # Same physical pad through two paths: a valid USB reading wins.
                previous = results.get(key)
                if previous is None or (not bluetooth and previous.via == "bluetooth"):
                    results[key] = status
        return list(results.values())

    def diagnostics(self) -> List[str]:
        return list(self._diag)
