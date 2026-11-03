"""ATK X1 Ultimate and other X1 Nearlink mice, read straight from their receiver.

The 8K Nearlink receiver (product string "ATK 8k Dongle Nearlink Version",
373b:108c) ships with the ATK X1 Ultimate and the other X1 Nearlink mice; the
same mouse on its cable enumerates as 373b:108b ("ATK X1 Nearlink 8k"). Neither
is read by ATK's own older Compx panel, and neither answers the 17-byte
command-0x04 frame of providers/pulsar.py: the Nearlink receiver is new
silicon with its own layout.

The protocol was taken from ATK's own current web driver (ATK HUB,
hub.atkgear.com, build 3.2.28), where the "Evision" mouse controller reads the
battery of exactly these two product ids. Its mouse-protocol frame is a
63-byte payload sent as an output report (64 bytes on the wire, report id
first) on a vendor collection of interface 1:

    [0..1]  requestCode  little-endian, increments with every query, from 1
    [2]     commandId    0x1a = power info
    [3..62] zero

The reply echoes the header and carries the answer in the same frame:

    [0..1]  requestCode echo
    [2]     commandId echo (0x1a)
    [7]     battery percent, 0..100
    [8]     charging flag (0 = on battery, non-zero = charging)

A reply whose commandId does not echo is not an answer - the receiver sends
one of those (commandId 0xff) while the mouse behind it is asleep or out of
range, which is a normal state for an idle mouse and not a device that went
away. As in providers/pulsar.py, the last reading is then kept greyed out for
as long as the receiver stays in the USB tree, and only a receiver that leaves
the tree drops the device.

Which collection and report id depends on the product id, and the driver is
explicit about it: its controller sets report id 5 for the wireless connect
types and keeps its constructor default of 4 otherwise. On the receiver
(373b:108c) that means the 0xffb3 collection with report id 5; on the cable
(373b:108b) it means the 0xffb2 collection with report id 4 - the only vendor
collection of the wired mouse that declares an output report long enough for
the frame. Both are confirmed on hardware: an X1 Ultimate reads its battery
over the receiver (report 5, 0xffb3) and again over the cable (report 4,
0xffb2, charging flag set while the cable supplies power). A mouse on its
cable never sleeps - its own power switch does nothing there - so the wired
reading stays live.
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

CMD_POWER = 0x1A
PAYLOAD_LEN = 63                      # frame payload, without the report id
WIRE_LEN = PAYLOAD_LEN + 1            # what actually goes on the wire

LEVEL_INDEX = 8                       # reply[8] = battery percent ...
POWER_INDEX = 9                       # ... reply[9] = charging flag
CMD_INDEX = 3                         # reply[3] = commandId echo (report id is [0])

CONTROL_USAGE = (0xFFB3, 0x0001)      # the collection the driver binds 108c to

READ_ATTEMPTS = 4
READ_TIMEOUT_MS = 250
FLUSH_TIMEOUT_MS = 30

# product id -> (display name, output report id, usage page of the collection)
PIDS: Dict[int, Tuple[str, int, int]] = {
    0x108C: ("ATK X1 Ultimate (2.4 GHz)", 0x05, 0xFFB3),
    0x108B: ("ATK X1 Ultimate (wired)", 0x04, 0xFFB2),
}


def make_request(request_code: int, report_id: int) -> List[int]:
    """The 64-byte wire frame: report id, then the driver's 63-byte payload."""
    frame = [report_id,
             request_code & 0xFF, (request_code >> 8) & 0xFF,
             CMD_POWER] + [0x00] * (PAYLOAD_LEN - 3)
    return frame


def parse_power(r, report_id: int) -> Optional[Tuple[int, bool]]:
    """(percent, charging) from a power reply, or None when this is not one."""
    if not r or len(r) < WIRE_LEN:
        return None
    if r[0] != report_id or r[CMD_INDEX] != CMD_POWER:
        return None
    level = r[LEVEL_INDEX]
    if not 0 <= level <= 100:
        return None
    return level, r[POWER_INDEX] != 0


class AtkNearlinkProvider(Provider):
    name = "atk_nearlink"

    def __init__(self):
        self._diag: List[str] = []
        self._request_code = 0
        # key -> (name, level, charging, when it was last read)
        self._last: Dict[str, Tuple[str, int, bool, float]] = {}

    def _next_code(self) -> int:
        self._request_code = (self._request_code + 1) % 65536 or 1
        return self._request_code

    def _pick(self, infos: List[dict], usage_page: int) -> Optional[dict]:
        """The collection to open, and the only one: the vendor collection the
        driver's device table binds this product id to. When it is missing, or
        when it declares an output report that cannot take the 64-byte frame,
        the device is not queried at all - writing a guessed frame to some
        other collection is worse than showing no reading."""
        preferred = [d for d in infos
                     if (d.get("usage_page"), d.get("usage")) == (usage_page, 0x0001)]
        if not preferred:
            self._diag.append(f"  no {usage_page:04x}:0001 collection; not queried")
            return None
        for d in preferred:
            length = _output_length(d["path"])
            if length in (None, WIRE_LEN):
                return d
            self._diag.append(f"  {usage_page:04x}:0001 output={length} cannot take a "
                              f"{WIRE_LEN}-byte frame; not queried")
        return None

    def _query(self, path: bytes, report_id: int) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            self._drain(dev)
            dev.write(make_request(self._next_code(), report_id))
            for attempt in range(READ_ATTEMPTS):
                r = dev.read(WIRE_LEN, READ_TIMEOUT_MS)
                if not r:
                    break
                self._diag.append(f"  reply {attempt + 1}: {hexdump(r)}")
                if parse_power(r, report_id) is not None:
                    return list(r)
                time.sleep(0.02)
            self._diag.append("  no power reply (the mouse is asleep or out of range)")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _drain(self, dev) -> None:
        for _ in range(4):
            if not dev.read(WIRE_LEN, FLUSH_TIMEOUT_MS):
                return

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        out = []
        now = time.time()
        found: set = set()      # answered this poll
        present: set = set()    # enumerated this poll, answering or not
        try:
            infos = hidlist.enumerate(0x373B)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(373b): %s", e)
            return out
        for pid, (name, report_id, usage_page) in PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            key = f"atk_nearlink:{pid:04x}"
            present.add(key)
            d = self._pick(mine, usage_page)
            if d is None:
                continue
            self._diag.append(f"[ATK Nearlink] pid=373b:{pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                              f"report={report_id:02x}")
            reply = self._query(d["path"], report_id)
            parsed = parse_power(reply, report_id)
            if parsed is None:
                if not reply:
                    self._diag.append("    no reply (asleep, or the mouse is idle)")
                continue
            level, charging = parsed
            self._last[key] = (name, level, charging, now)
            found.add(key)
            out.append(DeviceStatus(key, name, level, charging, True,
                                    "atk_nearlink", kind="mouse"))
        # A receiver that is still plugged in keeps its greyed reading, the
        # same rule providers/pulsar.py follows: an idle mouse stops
        # answering, which says nothing about the receiver being gone.
        for key in list(self._last):
            name, level, charging, seen = self._last[key]
            if key in found:
                continue
            if key in present:
                self._diag.append(f"[ATK Nearlink] {name} did not answer; keeping "
                                  f"{level}%{' (charging)' if charging else ''} greyed "
                                  "(its receiver is still plugged in)")
                out.append(DeviceStatus(key, name, level, charging, False,
                                        "atk_nearlink", kind="mouse",
                                        wake_on_input=True))
            else:
                del self._last[key]
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)


# --- collection capabilities (same HidP_GetCaps probe as providers/pulsar.py) ---

class _HIDP_CAPS(ctypes.Structure):
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
    """OutputReportByteLength of one HID collection, or None when Windows does
    not say. The handle is opened with no access rights, so nothing is sent."""
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
    except Exception:            # a probe must never take the provider down
        return None


_CAPS: Dict[bytes, Optional[int]] = {}


def _output_length(path) -> Optional[int]:
    if path not in _CAPS:
        _CAPS[path] = _query_output_length(path)
    return _CAPS[path]
