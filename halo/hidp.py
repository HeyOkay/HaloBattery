"""Read the standard HID battery fields, through Windows' HID parser.

USB HID defines a battery collection of its own: page 0x06 usage 0x20 (Battery
Strength) or page 0x85 usage 0x65 (Relative State of Charge) carry the level, and
page 0x85 usages 0x44/0x45/0x46/0xD0 say whether the device is charging,
discharging, full or on AC. A device that implements them can be read without a
vendor protocol at all - this is the fallback for exactly those devices.

The report layout is never guessed: Windows' own parser (HidP_*) is asked which
report and bit field hold the usage, and only the level and the four charge usages
are read. Nothing is written to the device.
"""
from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from typing import Optional, Set, Tuple

# HID usage pages and usages we understand. Battery Strength and Relative State of
# Charge are the two standard level usages; a device may declare either.
LEVEL_USAGES: Tuple[Tuple[int, int], ...] = ((0x06, 0x20), (0x85, 0x65))
CHARGE_PAGE = 0x85
CHARGE_USAGES = (0x44, 0x45, 0x46, 0xD0)   # charging, discharging, full, AC present

HIDP_SUCCESS = 0x00110000
HIDP_INPUT, HIDP_OUTPUT, HIDP_FEATURE = 0, 1, 2

FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


@dataclass(frozen=True)
class StandardBattery:
    level: int
    charging: bool
    full: bool
    charging_known: bool   # the device declares at least one page-0x85 charge usage


class _HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", ctypes.c_uint16),
                ("UsagePage", ctypes.c_uint16),
                ("InputReportByteLength", ctypes.c_uint16),
                ("OutputReportByteLength", ctypes.c_uint16),
                ("FeatureReportByteLength", ctypes.c_uint16),
                ("Reserved", ctypes.c_uint16 * 17),
                ("NumberLinkCollectionNodes", ctypes.c_uint16),
                ("NumberInputButtonCaps", ctypes.c_uint16),
                ("NumberInputValueCaps", ctypes.c_uint16),
                ("NumberInputDataIndices", ctypes.c_uint16),
                ("NumberOutputButtonCaps", ctypes.c_uint16),
                ("NumberOutputValueCaps", ctypes.c_uint16),
                ("NumberOutputDataIndices", ctypes.c_uint16),
                ("NumberFeatureButtonCaps", ctypes.c_uint16),
                ("NumberFeatureValueCaps", ctypes.c_uint16),
                ("NumberFeatureDataIndices", ctypes.c_uint16)]


class _VALUE_CAPS(ctypes.Structure):
    """HIDP_VALUE_CAPS, laid out as hidpi.h declares it on Windows.

    The two union members ("Range"/"NotRange") share their first two USAGEs, so the
    NotRange usage is read from ``UsageMin``; both give the usage of interest.
    """
    _fields_ = [("UsagePage", ctypes.c_uint16),
                ("ReportID", ctypes.c_uint8),
                ("IsAlias", ctypes.c_uint8),
                ("BitField", ctypes.c_uint16),
                ("LinkCollection", ctypes.c_uint16),
                ("LinkUsage", ctypes.c_uint16),
                ("LinkUsagePage", ctypes.c_uint16),
                ("IsRange", ctypes.c_uint8),
                ("IsStringRange", ctypes.c_uint8),
                ("IsDesignatorRange", ctypes.c_uint8),
                ("IsAbsolute", ctypes.c_uint8),
                ("HasNull", ctypes.c_uint8),
                ("Reserved", ctypes.c_uint8),
                ("BitSize", ctypes.c_uint16),
                ("ReportCount", ctypes.c_uint16),
                ("Reserved2", ctypes.c_uint16 * 5),
                ("UnitsExp", ctypes.c_uint32),
                ("Units", ctypes.c_uint32),
                ("LogicalMin", ctypes.c_int32),
                ("LogicalMax", ctypes.c_int32),
                ("PhysicalMin", ctypes.c_int32),
                ("PhysicalMax", ctypes.c_int32),
                ("UsageMin", ctypes.c_uint16),
                ("UsageMax", ctypes.c_uint16),
                ("StringMin", ctypes.c_uint16),
                ("StringMax", ctypes.c_uint16),
                ("DesignatorMin", ctypes.c_uint16),
                ("DesignatorMax", ctypes.c_uint16),
                ("DataIndexMin", ctypes.c_uint16),
                ("DataIndexMax", ctypes.c_uint16)]


class _BUTTON_CAPS(ctypes.Structure):
    """HIDP_BUTTON_CAPS, laid out as hidpi.h declares it on Windows (72 bytes).

    Needed to find which report carries a charge usage: a device may put its
    charging bit in a different report than its level.
    """
    _fields_ = [("UsagePage", ctypes.c_uint16),
                ("ReportID", ctypes.c_uint8),
                ("IsAlias", ctypes.c_uint8),
                ("BitField", ctypes.c_uint16),
                ("LinkCollection", ctypes.c_uint16),
                ("LinkUsage", ctypes.c_uint16),
                ("LinkUsagePage", ctypes.c_uint16),
                ("IsRange", ctypes.c_uint8),
                ("IsStringRange", ctypes.c_uint8),
                ("IsDesignatorRange", ctypes.c_uint8),
                ("IsAbsolute", ctypes.c_uint8),
                ("ReportCount", ctypes.c_uint16),
                ("Reserved2", ctypes.c_uint16),
                ("Reserved", ctypes.c_uint32 * 9),
                ("UsageMin", ctypes.c_uint16),
                ("UsageMax", ctypes.c_uint16),
                ("StringMin", ctypes.c_uint16),
                ("StringMax", ctypes.c_uint16),
                ("DesignatorMin", ctypes.c_uint16),
                ("DesignatorMax", ctypes.c_uint16),
                ("DataIndexMin", ctypes.c_uint16),
                ("DataIndexMax", ctypes.c_uint16)]


# Windows' parser writes fixed-size native structures into the array we pass; a
# wrong stride would turn every following entry into garbage, so refuse to run at
# all if this build's layout differs from the documented one.
if (ctypes.sizeof(_VALUE_CAPS) != 72 or ctypes.sizeof(_BUTTON_CAPS) != 72
        or ctypes.sizeof(_HIDP_CAPS) != 64):  # pragma: no cover
    _LAYOUT_OK = False
else:
    _LAYOUT_OK = True

_dll = {}          # module name -> library, filled on first use
_load_failed = False


def _load():
    global _load_failed
    if _dll:
        return _dll
    if _load_failed or sys.platform != "win32" or not _LAYOUT_OK:
        return {}
    try:
        hid = ctypes.WinDLL("hid.dll")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        hid.HidD_GetPreparsedData.restype = ctypes.c_bool
        hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
        hid.HidD_FreePreparsedData.restype = ctypes.c_bool
        hid.HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        hid.HidD_GetFeature.restype = ctypes.c_bool
        hid.HidD_GetInputReport.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        hid.HidD_GetInputReport.restype = ctypes.c_bool
        hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(_HIDP_CAPS)]
        hid.HidP_GetCaps.restype = ctypes.c_long
        hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.POINTER(_VALUE_CAPS),
                                          ctypes.POINTER(ctypes.c_uint16), ctypes.c_void_p]
        hid.HidP_GetValueCaps.restype = ctypes.c_long
        hid.HidP_GetButtonCaps.argtypes = [ctypes.c_int, ctypes.POINTER(_BUTTON_CAPS),
                                           ctypes.POINTER(ctypes.c_uint16), ctypes.c_void_p]
        hid.HidP_GetButtonCaps.restype = ctypes.c_long
        hid.HidP_GetUsages.argtypes = [ctypes.c_int, ctypes.c_uint16, ctypes.c_uint16,
                                       ctypes.POINTER(ctypes.c_uint16),
                                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
                                       ctypes.c_char_p, ctypes.c_uint32]
        hid.HidP_GetUsages.restype = ctypes.c_long
        hid.HidP_GetUsageValue.argtypes = [ctypes.c_int, ctypes.c_uint16, ctypes.c_uint16,
                                           ctypes.c_uint16, ctypes.POINTER(ctypes.c_uint32),
                                           ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        hid.HidP_GetUsageValue.restype = ctypes.c_long
        hid.HidP_MaxUsageListLength.argtypes = [ctypes.c_int, ctypes.c_uint16, ctypes.c_void_p]
        hid.HidP_MaxUsageListLength.restype = ctypes.c_uint32
        kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                                         ctypes.c_void_p]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool
    except (AttributeError, OSError):        # pragma: no cover - no hid.dll
        _load_failed = True
        return {}
    _dll.update(hid=hid, kernel32=kernel32)
    return _dll


def available() -> bool:
    return bool(_load())


def scale_value(value: int, logical_min: int, logical_max: int, vendor_id: int = 0) -> Optional[int]:
    """A raw HID value as a percentage, or None when it is not a sane level.

    Apple's Magic accessories report a plain percentage while declaring a 0-255
    range (Linux carries the same exception as HID_BATTERY_QUIRK_PERCENT).
    """
    if value < 0 or value > 0xFFFFFFFF:
        return None
    if vendor_id == 0x05AC and value <= 100:
        return int(value)
    if logical_max > logical_min:
        return max(0, min(100, int((value - logical_min) * 100 / (logical_max - logical_min))))
    return int(value) if value <= 100 else None


def charge_flags(usages: Set[int]) -> Tuple[bool, bool]:
    """(charging, full) from the page-0x85 usages a report presents.

    AC present without "discharging" means the device is on external power, which
    is what every vendor tool shows as charging; "full" is the device's own word.
    """
    charging = 0x44 in usages or 0x46 in usages or (0xD0 in usages and 0x45 not in usages)
    return charging, 0x46 in usages


def read(path: str, vendor_id: int = 0) -> Optional[StandardBattery]:
    """Read the standard battery fields of one HID collection, or None.

    Never raises: a device that does not answer, or answers nonsense, is simply
    not shown by this fallback.
    """
    libs = _load()
    if not libs or not path:
        return None
    if isinstance(path, (bytes, bytearray)):
        path = bytes(path).decode("utf-8", "replace")
    hid, kernel32 = libs["hid"], libs["kernel32"]
    handle = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                  None, OPEN_EXISTING, 0, None)
    if not handle or handle == INVALID_HANDLE_VALUE:
        return None
    pp = ctypes.c_void_p()
    try:
        if not hid.HidD_GetPreparsedData(handle, ctypes.byref(pp)) or not pp:
            return None
        caps = _HIDP_CAPS()
        if hid.HidP_GetCaps(pp, ctypes.byref(caps)) != HIDP_SUCCESS:
            return None
        for report_type, length, count, buttons in (
                (HIDP_FEATURE, caps.FeatureReportByteLength, caps.NumberFeatureValueCaps,
                 caps.NumberFeatureButtonCaps),
                (HIDP_INPUT, caps.InputReportByteLength, caps.NumberInputValueCaps,
                 caps.NumberInputButtonCaps)):
            if not length or not count:
                continue
            found = _read_report(hid, handle, pp, report_type, length, count, vendor_id,
                                 buttons)
            if found is not None:
                return found
        return None
    except Exception:                        # pragma: no cover - defensive boundary
        return None
    finally:
        try:
            if pp:
                hid.HidD_FreePreparsedData(pp)
        finally:
            kernel32.CloseHandle(handle)


def _read_report(hid, handle, pp, report_type: int, length: int, count: int,
                 vendor_id: int, button_count: int = 0) -> Optional[StandardBattery]:
    caps = (_VALUE_CAPS * count)()
    used = ctypes.c_uint16(count)
    if hid.HidP_GetValueCaps(report_type, caps, ctypes.byref(used), pp) != HIDP_SUCCESS:
        return None
    got = hid.HidD_GetFeature if report_type == HIDP_FEATURE else hid.HidD_GetInputReport
    for index in range(min(count, used.value)):
        cap = caps[index]
        usage = int(cap.UsageMin)
        if (int(cap.UsagePage), usage) not in LEVEL_USAGES:
            continue
        report = _fetch(got, handle, int(cap.ReportID), length)
        if report is None:
            continue
        value = ctypes.c_uint32()
        if hid.HidP_GetUsageValue(report_type, cap.UsagePage, cap.LinkCollection, usage,
                                  ctypes.byref(value), pp, report, length) != HIDP_SUCCESS:
            continue
        raw = _signed(int(value.value), int(cap.BitSize), int(cap.LogicalMin))
        level = scale_value(raw, int(cap.LogicalMin), int(cap.LogicalMax), vendor_id)
        if level is None:
            continue
        usages, known = _charge_usages(hid, handle, pp, report_type, count, button_count,
                                       int(cap.ReportID), report, length)
        charging, full = charge_flags(usages)
        return StandardBattery(level, charging, full, known)
    return None


def _fetch(got, handle, report_id: int, length: int):
    """One report from the device, or None. Read-only: Get Feature / Get Input Report."""
    report = ctypes.create_string_buffer(length)
    report[0] = bytes((report_id,))
    if not got(handle, ctypes.cast(report, ctypes.c_void_p), length):
        return None
    return report


def _signed(raw: int, bits: int, logical_min: int) -> int:
    """HidP_GetUsageValue returns the field's raw bits; a field with a negative
    logical minimum is two's complement in its own width, not in 32 bits."""
    if logical_min < 0 and 0 < bits < 32 and raw & (1 << (bits - 1)):
        return raw - (1 << bits)
    if logical_min < 0 and bits == 32:
        return ctypes.c_int32(raw).value
    return raw


def _charge_declarations(hid, pp, report_type: int, value_count: int,
                         button_count: int) -> Tuple[Set[int], list]:
    """Which reports carry a charge usage: (report IDs with charge buttons,
    [(report ID, link collection, usage)] for charge values)."""
    button_ids: Set[int] = set()
    values = []
    if button_count:
        bcaps = (_BUTTON_CAPS * button_count)()
        used = ctypes.c_uint16(button_count)
        if hid.HidP_GetButtonCaps(report_type, bcaps, ctypes.byref(used), pp) == HIDP_SUCCESS:
            for index in range(min(button_count, used.value)):
                cap = bcaps[index]
                if int(cap.UsagePage) != CHARGE_PAGE:
                    continue
                low = int(cap.UsageMin)
                high = int(cap.UsageMax) if cap.IsRange else low
                if any(low <= u <= high for u in CHARGE_USAGES):
                    button_ids.add(int(cap.ReportID))
    if value_count:
        vcaps = (_VALUE_CAPS * value_count)()
        used = ctypes.c_uint16(value_count)
        if hid.HidP_GetValueCaps(report_type, vcaps, ctypes.byref(used), pp) == HIDP_SUCCESS:
            for index in range(min(value_count, used.value)):
                cap = vcaps[index]
                usage = int(cap.UsageMin)
                if int(cap.UsagePage) == CHARGE_PAGE and usage in CHARGE_USAGES:
                    values.append((int(cap.ReportID), int(cap.LinkCollection), usage))
    return button_ids, values


def _charge_usages(hid, handle, pp, report_type: int, value_count: int, button_count: int,
                   level_id: int, level_report, length: int) -> Tuple[Set[int], bool]:
    """The page-0x85 charge usages that are set, and whether they could be read.

    Each charge usage is read from the report the parser says carries it - the
    level's own report, or another one fetched the same read-only way. "Known" means
    at least one of the four charge usages is declared and its report answered; a
    device without them, or one whose charge report fails, is "not known" rather
    than "not charging".
    """
    button_ids, values = _charge_declarations(hid, pp, report_type, value_count, button_count)
    wanted = button_ids | {rid for rid, _, _ in values}
    if not wanted:
        return set(), False
    got = hid.HidD_GetFeature if report_type == HIDP_FEATURE else hid.HidD_GetInputReport
    reports = {}
    for rid in sorted(wanted):
        reports[rid] = level_report if rid == level_id else _fetch(got, handle, rid, length)
    found: Set[int] = set()
    known = False
    most = int(hid.HidP_MaxUsageListLength(report_type, CHARGE_PAGE, pp)) if button_ids else 0
    for rid in sorted(button_ids):
        report = reports.get(rid)
        if report is None or not most:
            continue
        buttons = (ctypes.c_uint16 * most)()
        used = ctypes.c_uint32(most)
        if hid.HidP_GetUsages(report_type, CHARGE_PAGE, 0, buttons,
                              ctypes.byref(used), pp, report, length) == HIDP_SUCCESS:
            known = True
            found.update(int(u) for u in buttons[:min(most, used.value)]
                         if int(u) in CHARGE_USAGES)
    for rid, link, usage in values:
        report = reports.get(rid)
        if report is None:
            continue
        value = ctypes.c_uint32()
        if hid.HidP_GetUsageValue(report_type, CHARGE_PAGE, link, usage,
                                  ctypes.byref(value), pp, report, length) == HIDP_SUCCESS:
            known = True
            if value.value:
                found.add(usage)
    return found, known
