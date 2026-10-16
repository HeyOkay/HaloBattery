from __future__ import annotations

import time
from typing import List, Optional

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

from .base import DeviceStatus, Provider

VID = 0x054C
PID = 0x0E53
NAME = "Sony INZONE H9"
INIT_1 = bytes.fromhex("01 00 FC 08 96 C3 21 01 01 01 00 7D")
INIT_2 = bytes.fromhex("01 00 FC 08 96 C3 41 02 01 01 00 9E")
QUERY = bytes.fromhex("01 00 FC 08 96 C3 41 04 01 01 00 A0")
SIGNATURE = bytes.fromhex("04 FF 0B 00 96 C3 14 04")
FRAME_LENGTH = 14


def find_frame(data: bytes) -> Optional[bytes]:
    for offset in range(max(0, len(data) - FRAME_LENGTH + 1)):
        frame = data[offset:offset + FRAME_LENGTH]
        if frame.startswith(SIGNATURE) and frame[11] in (0, 1) and frame[12] <= 100:
            return frame
    return None


class SonyInzoneProvider(Provider):
    name = "sony_inzone"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if serial is None or list_ports is None:
            return []
        for info in list_ports.comports():
            if info.vid != VID or info.pid != PID:
                continue
            try:
                with serial.Serial(info.device, 115200, timeout=0.2, write_timeout=1) as port:
                    port.dtr = True
                    port.rts = True
                    time.sleep(0.5)
                    port.reset_input_buffer()
                    port.reset_output_buffer()
                    response = b""
                    for command in (INIT_1, INIT_2, QUERY):
                        port.write(command)
                        time.sleep(0.25)
                        response = b""
                        end = time.monotonic() + 0.8
                        while time.monotonic() < end:
                            waiting = port.in_waiting
                            response += port.read(waiting if waiting else 1)
                    frame = find_frame(response)
            except (OSError, serial.SerialException) as exc:
                self._diag.append(f"{info.device}: {exc}")
                continue
            if frame is not None:
                return [DeviceStatus("sony_inzone_h9", NAME, frame[12], frame[11] == 1,
                                     True, self.name, kind="headset")]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
