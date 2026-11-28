"""ATK N9 Pro serial battery reader (373b:11cb receiver).

Source: ibroxiim/atk-n9-mac at d95a2d36f673456d7cada9aa2db102d2b01064e6,
tools/diagnose.py lines 36-78 (run, status and battery) and native/native_host.py
lines 68-82 (9600 baud, 8N1). Only category 0 GETs 4 and 3 are sent; no EQ,
microphone, firmware or configuration writes. N9 Pro is untested in that source.
Windows uses the original 64-byte command, without the macOS CDC workaround.
"""
from __future__ import annotations

import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = list_ports = None

from .base import DeviceStatus, Provider, hexdump

VID = 0x373B
PID = 0x11CB
FRAME_SIZE = 64


def make_request(command: int) -> bytes:
    if command not in (3, 4):
        raise ValueError("Only battery and link queries are supported")
    return bytes([2, 0, command]).ljust(FRAME_SIZE, b"\x00")


def parse_reply(frame: bytes, command: int):
    """Return the second payload byte of a complete, matching GET reply."""
    if not frame or len(frame) != FRAME_SIZE or not 4 <= frame[0] <= 63:
        return None
    if frame[1:3] != bytes([0, command]):
        return None
    value = frame[4]
    if command == 4:
        return bool(value) if value in (0, 1) else None
    if command == 3 and (value & 0x7F) <= 100:
        return value & 0x7F, bool(value & 0x80)
    return None


class AtkN9Provider(Provider):
    name = "atk_n9"

    def __init__(self):
        self._diag = []
        self.refresh_interval = None

    def _query(self, port, command):
        port.reset_input_buffer()
        request = make_request(command)
        if port.write(request) != len(request):
            self._diag.append("  incomplete serial write")
            return None
        buffer = bytearray()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            buffer.extend(port.read(FRAME_SIZE - len(buffer)))
            if len(buffer) < FRAME_SIZE:
                continue
            frame = bytes(buffer)
            buffer.clear()
            self._diag.append(f"  command {command}: {hexdump(frame, 12)}")
            result = parse_reply(frame, command)
            if result is not None:
                return result
        self._diag.append(f"  no matching reply to command {command}")
        return None

    def poll(self):
        self._diag = []
        self.refresh_interval = None
        if serial is None or list_ports is None:
            self._diag.append("[ATK N9] pyserial is not installed")
            return []
        out = []
        try:
            ports = list(list_ports.comports())
        except OSError as error:
            self._diag.append(f"[ATK N9] enumeration: {error}")
            return []
        for info in ports:
            if (info.vid, info.pid) != (VID, PID):
                continue
            # Power cycling the headset leaves the USB receiver present, so
            # hotplug cannot wake the ordinary battery polling interval.
            self.refresh_interval = 5.0
            self._diag.append(f"[ATK N9 Pro] {VID:04x}:{PID:04x} {info.device}")
            try:
                with serial.Serial(info.device, 9600, timeout=0.1, write_timeout=0.5) as port:
                    connected = self._query(port, 4)
                    if connected is None:
                        continue
                    key = f"atk_n9:{info.serial_number or info.device}"
                    if not connected:
                        out.append(DeviceStatus(key, "ATK N9 Pro", None, False, False,
                                                self.name, kind="headset", charging_known=False))
                        continue
                    power = self._query(port, 3)
                    if power is not None:
                        level, charging = power
                        out.append(DeviceStatus(key, "ATK N9 Pro", level, charging, True,
                                                self.name, kind="headset"))
            except (OSError, ValueError) as error:
                self._diag.append(f"  serial: {error}")
        return out

    def diagnostics(self):
        return list(self._diag)
