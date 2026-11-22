from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

try:
    import hid
except ImportError:          # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

# Protocol sequence and response signature from janit88/InZoneH9Tray,
# inzone_tray.py at 4780303dbee4bf1a86acf37c2cbc96b1774eee00:
# https://github.com/janit88/InZoneH9Tray/blob/4780303dbee4bf1a86acf37c2cbc96b1774eee00/inzone_tray.py
VID = 0x054C
PID = 0x0E53
PIDS = {0x0E53: "Sony INZONE H9",
        0x0FA8: "Sony INZONE H9 II"}
KEYS = {0x0E53: "sony_inzone_h9", 0x0FA8: "sony_inzone_h9ii"}
# H9 II (#294, fork #30) has no confirmed source for the H9 init sequence.
# Keep the protocol fixture without opening its serial port in stable builds.
ENABLED_PIDS = frozenset((0x0E53,))
NAME = "Sony INZONE H9"
INIT_1 = bytes.fromhex("01 00 FC 08 96 C3 21 01 01 01 00 7D")
INIT_2 = bytes.fromhex("01 00 FC 08 96 C3 41 02 01 01 00 9E")
QUERY = bytes.fromhex("01 00 FC 08 96 C3 41 04 01 01 00 A0")
SIGNATURE = bytes.fromhex("04 FF 0B 00 96 C3 14 04")
FRAME_LENGTH = 14

# INZONE H5 (054c:0ebf): the same Sony frames, carried over HID instead of a COM port.
# The dongle has no serial port; its vendor collection (usage page 0xFF04) takes 64-byte
# reports: report id 0x02, the frame length, then the same frame as QUERY above but with
# our own transaction id. The reply echoes that id and ends in a checksum.
# Request and reply layout from two independent sources:
#   Cinnabarz/InzoneBattery src/InzoneHid.cs at 0a48f8b03fd91f26803756b28b3af486eb0ad642
#   (BuildCommand, WriteCommand, Parse, GetBattery; verified there on the H5):
#   https://github.com/Cinnabarz/InzoneBattery/blob/0a48f8b03fd91f26803756b28b3af486eb0ad642/src/InzoneHid.cs
#   RobotRoss/linzone-hub kernel/hid-inzone-battery-dkms/hid_inzone_battery.c at
#   d12fe2376193aecf9caec1a438369c75b1aa173a (inzone_send_hci_get, inzone_parse_battery_event,
#   device table entry 0x0ebf):
#   https://github.com/RobotRoss/linzone-hub/blob/d12fe2376193aecf9caec1a438369c75b1aa173a/kernel/hid-inzone-battery-dkms/hid_inzone_battery.c
HID_PIDS = {0x0EBF: "Sony INZONE H5"}
HID_KEYS = {0x0EBF: "sony_inzone_h5"}
HID_USAGE_PAGE = 0xFF04
HID_REPORT_ID = 0x02
HID_REPORT_SIZE = 64
HID_TID = 0x0102             # echoed back; INZONE Hub shares the channel with its own ids
HID_TYPE_RET = 0x10          # reply type (InzoneBattery TypeRet)
HID_HEADSET_OFF = 0xFF       # level byte while the dongle is in but the headset is off
HID_READ_ATTEMPTS = 8        # other frames (INZONE Hub's replies) can arrive first
HID_READ_TIMEOUT_MS = 250


def find_frame(data: bytes) -> Optional[bytes]:
    for offset in range(max(0, len(data) - FRAME_LENGTH + 1)):
        frame = data[offset:offset + FRAME_LENGTH]
        if frame.startswith(SIGNATURE) and frame[11] in (0, 1) and frame[12] <= 100:
            return frame
    return None


def make_query(tid: int) -> bytes:
    """The battery GET frame for transaction `tid`; make_query(1) == QUERY."""
    body = bytes([0x96, 0xC3, 0x41, 0x04, 0x01, tid & 0xFF, (tid >> 8) & 0xFF])
    return bytes([0x01, 0x00, 0xFC, 0x08]) + body + bytes([sum(body) & 0xFF])


def make_hid_report(tid: int) -> List[int]:
    frame = make_query(tid)
    report = [HID_REPORT_ID, len(frame)] + list(frame)
    return report + [0x00] * (HID_REPORT_SIZE - len(report))


def parse_hid_reply(report, tid: int) -> Optional[Tuple[Optional[int], Optional[bool]]]:
    """(level, charging) from the battery reply to `tid`, or None when `report` is not a
    valid one. level None: the headset is off. charging None: unknown status byte."""
    r = bytes(report or b"")
    if len(r) < 2 + FRAME_LENGTH or r[0] != HID_REPORT_ID or r[1] < FRAME_LENGTH:
        return None
    frame = r[2:2 + FRAME_LENGTH]
    if not frame.startswith(SIGNATURE) or frame[8] != HID_TYPE_RET:
        return None
    if frame[9] | (frame[10] << 8) != tid or sum(frame[4:13]) & 0xFF != frame[13]:
        return None
    status, level = frame[11], frame[12]
    if level == HID_HEADSET_OFF:
        return None, None
    if level > 100:
        return None
    return level, {0: False, 1: True}.get(status)


class SonyInzoneProvider(Provider):
    name = "sony_inzone"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        return self._poll_serial() + self._poll_hid()

    def _poll_hid(self) -> List[DeviceStatus]:
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(sony): %s", e)
            return []
        out = []
        for d in infos:
            pid = d.get("product_id")
            if pid not in HID_PIDS or d.get("usage_page") != HID_USAGE_PAGE:
                continue
            if any(s.key == HID_KEYS[pid] for s in out):
                continue
            name = HID_PIDS[pid]
            self._diag.append(f"[Sony] pid={pid:04x} '{name}' iface={d.get('interface_number')} "
                              f"{HID_USAGE_PAGE:04x}:{d.get('usage', 0):04x}")
            result = self._query_hid(d["path"])
            if result is None:
                continue
            level, charging = result
            if level is None:
                out.append(DeviceStatus(HID_KEYS[pid], name, None, False, False, self.name,
                                        kind="headset", charging_known=False))
            else:
                out.append(DeviceStatus(HID_KEYS[pid], name, level, bool(charging), True,
                                        self.name, kind="headset",
                                        charging_known=charging is not None))
        return out

    def _query_hid(self, path) -> Optional[Tuple[Optional[int], Optional[bool]]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            dev.write(make_hid_report(HID_TID))
            for _ in range(HID_READ_ATTEMPTS):
                r = dev.read(HID_REPORT_SIZE, HID_READ_TIMEOUT_MS)
                if not r:
                    continue
                self._diag.append(f"  frame: {hexdump(r, 16)}")
                result = parse_hid_reply(r, HID_TID)
                if result is not None:
                    return result
            self._diag.append("  no battery reply matching the query")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _poll_serial(self) -> List[DeviceStatus]:
        if serial is None or list_ports is None:
            self._diag.append("[Sony] pyserial is not installed - the INZONE H9 provider is "
                              "off (pip install pyserial)")
            return []
        for info in list_ports.comports():
            if info.vid != VID or info.pid not in PIDS:
                continue
            if info.pid not in ENABLED_PIDS:
                self._diag.append(f"[Sony] pid={info.pid:04x}: release hold; not opening")
                continue
            try:
                with serial.Serial(info.device, 115200, timeout=0.2, write_timeout=1) as port:
                    port.dtr = True
                    port.rts = True
                    time.sleep(0.5)
                    port.reset_input_buffer()
                    port.reset_output_buffer()
                    for command in (INIT_1, INIT_2):
                        port.write(command)
                        time.sleep(0.25)
                    port.write(QUERY)
                    time.sleep(0.25)
                    response = b""
                    frame = None
                    end = time.monotonic() + 0.8
                    while time.monotonic() < end:
                        waiting = port.in_waiting
                        chunk = port.read(waiting if waiting else 1)
                        if chunk:
                            response += chunk
                            frame = find_frame(response)
                            if frame is not None:
                                break
            except (OSError, serial.SerialException) as exc:
                self._diag.append(f"{info.device}: {exc}")
                continue
            if frame is not None:
                return [DeviceStatus(KEYS[info.pid], PIDS[info.pid], frame[12],
                                     frame[11] == 1, True, self.name, kind="headset")]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
