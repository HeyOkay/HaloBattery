"""Shared CompX report-8 battery protocol used by several mouse vendors.

The wire format is common to a number of CompX ODM devices; product IDs,
names and provider ownership belong in each vendor's own provider module.
"""
from __future__ import annotations

import ctypes
import sys
from typing import List, Optional, Tuple

PAYLOAD_HEADER = 0x08
PAYLOAD_LEN = 17
CHECKSUM_BASE = 0x55
CMD_POWER = 0x04
LEVEL_INDEX = 6
POWER_INDEX = 7
VOLTAGE_SLICE = (8, 10)
CONTROL_USAGE = (0xFF02, 0x0002)
READ_ATTEMPTS = 4
READ_TIMEOUT_MS = 250


class _HIDP_CAPS(ctypes.Structure):
    """The part of HIDP_CAPS needed to query a collection's output report size."""
    _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                ("InputReportByteLength", ctypes.c_ushort),
                ("OutputReportByteLength", ctypes.c_ushort),
                ("FeatureReportByteLength", ctypes.c_ushort),
                ("Reserved", ctypes.c_ushort * 17),
                ("NumberLinkCollectionNodes", ctypes.c_ushort),
                ("NumberInputButtonCaps", ctypes.c_ushort),
                ("NumberInputValueCaps", ctypes.c_ushort),
                ("NumberInputDataIndices", ctypes.c_ushort),
                ("NumberOutputButtonCaps", ctypes.c_ushort),
                ("NumberOutputValueCaps", ctypes.c_ushort),
                ("NumberOutputDataIndices", ctypes.c_ushort),
                ("NumberFeatureButtonCaps", ctypes.c_ushort),
                ("NumberFeatureValueCaps", ctypes.c_ushort),
                ("NumberFeatureDataIndices", ctypes.c_ushort)]


def _query_output_length(path) -> Optional[int]:
    """Read one HID collection's output length without opening it for I/O."""
    if sys.platform != "win32":
        return None
    try:
        p = path.decode("utf-8", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        k32, hidd = ctypes.windll.kernel32, ctypes.windll.hid
        k32.CreateFileW.restype = ctypes.c_void_p
        handle = k32.CreateFileW(p, 0, 3, None, 3, 0, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            return None
        try:
            pp = ctypes.c_void_p()
            if not hidd.HidD_GetPreparsedData(ctypes.c_void_p(handle), ctypes.byref(pp)):
                return None
            try:
                caps = _HIDP_CAPS()
                if hidd.HidP_GetCaps(pp, ctypes.byref(caps)) != 0x00110000:
                    return None
                return caps.OutputReportByteLength
            finally:
                hidd.HidD_FreePreparsedData(pp)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:
        return None


_CAPS: dict = {}


def output_length(path) -> Optional[int]:
    """Cached output report length; paths change when devices are replugged."""
    if path not in _CAPS:
        _CAPS[path] = _query_output_length(path)
    return _CAPS[path]


def checksum(payload: List[int]) -> int:
    return (CHECKSUM_BASE - sum(payload)) % 256


def make_request() -> List[int]:
    frame = [PAYLOAD_HEADER, CMD_POWER] + [0x00] * (PAYLOAD_LEN - 3)
    frame.append(checksum(frame))
    return frame


def parse_power(r) -> Optional[Tuple[int, bool]]:
    """Return battery percent and charging/cable flag from a valid reply."""
    if not r or len(r) < PAYLOAD_LEN:
        return None
    frame = list(r[:PAYLOAD_LEN])
    if frame[0] != PAYLOAD_HEADER or frame[1] != CMD_POWER:
        return None
    if frame[PAYLOAD_LEN - 1] != checksum(frame[:PAYLOAD_LEN - 1]):
        return None
    level = frame[LEVEL_INDEX]
    if not 0 <= level <= 100:
        return None
    return level, frame[POWER_INDEX] != 0


def voltage_mv(r) -> Optional[int]:
    if not r or len(r) < VOLTAGE_SLICE[1]:
        return None
    return int.from_bytes(bytes(r[VOLTAGE_SLICE[0]:VOLTAGE_SLICE[1]]), "big")
