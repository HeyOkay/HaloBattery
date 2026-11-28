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


# --------------------------------------------------------------- SET_REPORT path
#
# Some 8K dongles of this family reject WriteFile on the vendor collection (the
# ATK 373B:1278 does), and only answer the same 17-byte frame when it is sent as
# HID SET_REPORT. hidapi has no call for that, so this path opens the collection
# with CreateFileW, sends it with HidD_SetOutputReport and waits for the reply on
# the same handle. The frame is the same make_request() the interrupt-OUT path
# uses; only the transport differs. Used by providers/pulsar.py for that id.

class _OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", ctypes.c_uint32), ("OffsetHigh", ctypes.c_uint32),
                ("hEvent", ctypes.c_void_p)]


_GENERIC_READ_WRITE = 0x80000000 | 0x40000000
_FILE_FLAG_OVERLAPPED = 0x40000000
_ERROR_IO_PENDING = 997


def set_output_query(path, timeout_ms: int = 700) -> Optional[bytes]:
    """Send the power query with HID SET_REPORT and return the reply, if any."""
    if sys.platform != "win32":
        return None
    try:
        name = path.decode("utf-8", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hidd = ctypes.WinDLL("hid.dll")
        k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                    ctypes.c_void_p]
        k32.CreateFileW.restype = ctypes.c_void_p
        k32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                 ctypes.POINTER(ctypes.c_uint32),
                                 ctypes.POINTER(_OVERLAPPED)]
        k32.ReadFile.restype = ctypes.c_bool
        k32.CreateEventW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_bool,
                                     ctypes.c_wchar_p]
        k32.CreateEventW.restype = ctypes.c_void_p
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.WaitForSingleObject.restype = ctypes.c_uint32
        k32.GetOverlappedResult.argtypes = [ctypes.c_void_p, ctypes.POINTER(_OVERLAPPED),
                                            ctypes.POINTER(ctypes.c_uint32), ctypes.c_bool]
        k32.GetOverlappedResult.restype = ctypes.c_bool
        k32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(_OVERLAPPED)]
        k32.CancelIoEx.restype = ctypes.c_bool
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = ctypes.c_bool
        hidd.HidD_SetOutputReport.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        hidd.HidD_SetOutputReport.restype = ctypes.c_bool

        handle = k32.CreateFileW(name, _GENERIC_READ_WRITE, 3, None, 3,
                                 _FILE_FLAG_OVERLAPPED, None)
        if not handle or handle == ctypes.c_void_p(-1).value:
            return None
    except Exception:
        return None
    event = None
    try:
        frame = bytes(make_request())
        buf = (ctypes.c_ubyte * len(frame)).from_buffer_copy(frame)
        if not hidd.HidD_SetOutputReport(ctypes.c_void_p(handle), buf, len(frame)):
            return None
        # The interrupt-IN side also carries mouse movement, so the first report
        # after the query is not necessarily the answer. Read until a frame with
        # the power command shows up (the reference tool tries twelve times).
        for attempt in range(12):
            event = k32.CreateEventW(None, True, False, None)
            if not event:
                return None
            try:
                ov = _OVERLAPPED()
                ov.hEvent = ctypes.c_void_p(event)
                out = (ctypes.c_ubyte * PAYLOAD_LEN)()
                read = ctypes.c_uint32()
                wait_ms = timeout_ms if attempt == 0 else 60
                if not k32.ReadFile(ctypes.c_void_p(handle), out, PAYLOAD_LEN,
                                    ctypes.byref(read), ctypes.byref(ov)):
                    if ctypes.get_last_error() != _ERROR_IO_PENDING:
                        return None
                    if k32.WaitForSingleObject(ctypes.c_void_p(event), wait_ms) != 0:
                        k32.CancelIoEx(ctypes.c_void_p(handle), ctypes.byref(ov))
                        # The cancelled read still completes into `ov`, `out` and the
                        # event; wait for it, or the kernel writes into freed memory
                        # and signals a handle that has been closed and reused.
                        k32.GetOverlappedResult(ctypes.c_void_p(handle), ctypes.byref(ov),
                                                ctypes.byref(read), True)
                        continue
                    if not k32.GetOverlappedResult(ctypes.c_void_p(handle), ctypes.byref(ov),
                                                   ctypes.byref(read), False):
                        continue
                data = bytes(out[:read.value])
                if len(data) >= 2 and data[0] == PAYLOAD_HEADER and data[1] == CMD_POWER:
                    return data
            finally:
                try:
                    k32.CloseHandle(ctypes.c_void_p(event))
                except Exception:
                    pass
        return None
    except Exception:
        return None
    finally:
        try:
            k32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass
