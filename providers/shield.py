"""NVIDIA SHIELD Controller 2017 (0955:7214), over Bluetooth HID.

Protocol: Linux drivers/hid/hid-nvidia-shield.c, hostcmd request/response
structures and thunderstrike_parse_battery_payload/charger_payload:
https://github.com/torvalds/linux/blob/master/drivers/hid/hid-nvidia-shield.c

Only battery (7) and charger (58) queries are sent. Reports are 33 bytes:
output [4, command, 0, ...]; input [3, command, 0, payload...]. Battery
capacity is byte 14. Charger payload is connected, type, state; state 2
means charging. Tested on a Bluetooth NVIDIA Controller v01.04 on Windows 11.
"""
from __future__ import annotations

import hashlib
import time

try:
    import hid
except ImportError:  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, log
from .nintendo import is_bluetooth

VID = 0x0955
PID = 0x7214
TIMEOUT = 1.0


def parse_battery(report):
    # Windows pads input reports to the collection's 65-byte maximum.
    if len(report) not in (33, 65) or list(report[:3]) != [3, 7, 0]:
        return None
    return report[14] if 0 <= report[14] <= 100 else None


def parse_charger(report):
    if len(report) not in (33, 65) or list(report[:3]) != [3, 58, 0]:
        return None
    connected, charger_type, state = report[3:6]
    if connected not in (0, 1) or charger_type not in (0, 1, 2) or state not in (0, 1, 2, 3, 8):
        return None
    return bool(connected and state == 2)


class ShieldProvider(Provider):
    name = 'shield'

    def __init__(self):
        self._diag = []

    def _query(self, device, command):
        packet = [4, command, 0] + [0] * 30
        if device.write(packet) != len(packet):
            raise OSError('Incomplete HID write')
        deadline = time.monotonic() + TIMEOUT
        while time.monotonic() < deadline:
            data = device.read(65, max(1, int((deadline - time.monotonic()) * 1000)))
            if len(data) >= 3 and list(data[:3]) == [3, command, 0]:
                return data
        return []

    def _read(self, path):
        device = hid.device()
        try:
            device.open_path(path)
            level = parse_battery(self._query(device, 7))
            if level is None:
                self._diag.append('    no valid battery reply')
                return None
            self._diag.append(f'    battery: {level}%')
            charging = None
            try:
                charging = parse_charger(self._query(device, 58))
            except (OSError, ValueError) as exc:
                self._diag.append(f'    charger: {exc}')
            self._diag.append(f'    charging: {charging if charging is not None else "unknown"}')
            return level, bool(charging)
        except (OSError, ValueError) as exc:
            self._diag.append(f'    HID: {exc}')
            return None
        finally:
            try:
                device.close()
            except Exception:
                pass

    def poll(self):
        self._diag = []
        if hid is None:
            return []
        try:
            devices = hidlist.enumerate(VID)
        except Exception as exc:
            log.warning('hid.enumerate(shield): %s', exc)
            return []
        results = []
        seen = set()
        for info in devices:
            if info['product_id'] != PID or not is_bluetooth(info['path']):
                continue
            # Serial is the Bluetooth address; no collision between two pads.
            path = info['path']
            identity = info.get('serial_number') or hashlib.sha256(
                path if isinstance(path, bytes) else path.encode()).hexdigest()[:16]
            key = f'shield:{str(identity).lower()}'
            if key in seen:
                continue
            self._diag.append('[SHIELD] NVIDIA SHIELD Controller (2017), Bluetooth')
            result = self._read(path)
            if result is not None:
                seen.add(key)
                level, charging = result
                results.append(DeviceStatus(key, 'NVIDIA SHIELD Controller', level,
                                            charging, source=self.name,
                                            kind='gamepad', via='bluetooth'))
        return results

    def diagnostics(self):
        return list(self._diag)
