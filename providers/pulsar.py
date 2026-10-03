"""Pulsar, ATK, VXE and Hitscan wireless mice over USB/HID, without vendor software.

The X2 V3 Mini uses Pulsar's newer Sonix-style 64-byte feature protocol instead of
the 17-byte Compx protocol below. The ids and interface were captured from the
reporter's mouse (3710:3402 on its cable, 3710:5403 on the 8K receiver). The packet
layout and battery command are independently documented by jonkristian/pulsar-x3-python:
feature report 0, command 08 81 01, a little-endian 16-bit sum in bytes 62-63, and
the returned percentage in byte 6. That project lists 3710:5403 among its wireless
ids and sends the transfer on interface 3.

Protocol from andrewrabert/python-pulsar-mouse-tool, which also backs the
"HID: pulsar" driver in review for the Linux kernel and lists these ids:

  * 3554:f508  Pulsar X2 V2 Mini (1 kHz dongle)      3554:f507  the same mouse on the cable
  * 3554:f58f  ATK VXE R1 SE+ (wired)                373b:1085  ATK VXE R1 SE+ (2.4 GHz)
  * 3554:f58a  VXE R1 Pro Max (1 kHz dongle, #87)    3554:f58c  the same mouse on its cable
  * the Kysona M600 and the VXE Dragonfly R1 Pro use the same protocol (their ids are
    not in the tool, so they are not claimed here).

The cable id (3554:f58c) is claimed too, from the reporter's second report in #87: the
wired mouse lists the same eight collections as the receiver, and the panel below reads it
with the same command 0x04 frame. Both transports are confirmed on that reporter's
hardware - the receiver read the mouse's level, and on the cable the level agreed with
ATK's own panel (hub.atk.pro) and the charging flag followed the cable.

The ATK and Compx builds are also handled by the OpenMouse project's ATK/VXE panel
(@openmouse/protocol, drivers/atk): it lists 3554:f58a for the R1 Pro Max receiver
and 3554:f58c for the same mouse on its cable, and reads the battery with command
0x04, taking the level, the flag and the millivolts from the same three places this
file does. It also opens the collection with usage page 0xFF02 and usage 0x0002,
which is why that one is preferred below.

The same 17-byte framing is in G-Wolves' own web driver (mouse.xyz), which also handles
these Compx-based receivers: its get_Crc() is 0x55 minus the sum of the first fifteen
payload bytes, the result goes in byte 15, and the frame is sent with sendReport(8, ...) -
so the frame on the wire sums to 0x55, the rule this file uses. (Its battery read is for
the G-Wolves protocol; the level offsets here rest on the two sources above.)

The Hitscan Hyperlight speaks the same frame, on the same kind of vendor collection
(ff02:0002): sopparus/hitscan-battery mapped it from USBPcap captures of Hitscan Utility
1.0.2 in both cable (3770:0100) and receiver (3770:0200) mode and reads it with a plain
write()/read(), command 0x04, the level in byte 6, the flag in byte 7, checksum 0x55 minus
the sum. Its notes left byte 8 and byte 9 unresolved; across its captures those read
0x1129 (4393 mV) while charging and 0x1073 (4211 mV) on battery - the big-endian millivolt
field this file already reads. Its own warning also applies: the vendor application's
battery indicator is broken (it showed 100 % while the device answered 75), so the raw
byte is the truth, which is what this file reports.

Frames are 17 bytes, big-endian, report id 0x08:

    [0] 0x08      header, which is also the report id
    [1] command   0x04 = power details
    [2..15]        arguments, zero for a power query
    [16] checksum 0x55 - the sum of bytes 0..15, mod 256

A power reply carries the level in byte 6, the power flag in byte 7 (the mouse is on
its cable) and millivolts big-endian in bytes 8-9. The same endpoint also pushes
events (command 0x0a), so a reply is only accepted when the header and the checksum
both check out, the command is the one we asked for, and the level is 0..100.
"""
from __future__ import annotations

import ctypes
import sys
import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

CMD_POWER = 0x04
PAYLOAD_HEADER = 0x08
PAYLOAD_LEN = 17
CHECKSUM_BASE = 0x55

LEVEL_INDEX = 6
POWER_INDEX = 7
VOLTAGE_SLICE = (8, 10)

CONTROL_INTERFACE = 1           # the tool reads its 17-byte replies on interface 1
CONTROL_USAGE = (0xFF02, 0x0002)   # the collection the OpenMouse ATK/VXE panel opens

SONIX_VID = 0x3710
SONIX_PIDS = {
    0x5403: ("Pulsar X2 V3 Mini (8K wireless)", False),
    0x3402: ("Pulsar X2 V3 Mini (wired)", True),
}
SONIX_INTERFACE = 3
SONIX_WIRE_LEN = 64
SONIX_REPORT_LEN = 65          # hidapi report id + the 64-byte USB control payload
SONIX_BATTERY_COMMAND = (0x08, 0x81, 0x01)
SONIX_LEVEL_INDEX = 7          # wire byte 6, shifted by hidapi's report-id byte


class _HIDP_CAPS(ctypes.Structure):
    """The part of HIDP_CAPS this file needs (same shape as providers/gwolves.py)."""
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
    """OutputReportByteLength of one HID collection, or None when Windows does not
    say. The handle is opened with no access rights, so nothing is sent to the device."""
    if sys.platform != "win32":
        return None
    try:
        p = path.decode("utf-8", "ignore") if isinstance(path, (bytes, bytearray)) else str(path)
        k32, hidd = ctypes.windll.kernel32, ctypes.windll.hid
        k32.CreateFileW.restype = ctypes.c_void_p
        handle = k32.CreateFileW(p, 0, 3, None, 3, 0, None)   # no access, share r/w, open existing
        if handle in (None, ctypes.c_void_p(-1).value):
            return None
        try:
            pp = ctypes.c_void_p()
            if not hidd.HidD_GetPreparsedData(ctypes.c_void_p(handle), ctypes.byref(pp)):
                return None
            try:
                caps = _HIDP_CAPS()
                if hidd.HidP_GetCaps(pp, ctypes.byref(caps)) != 0x00110000:   # HIDP_STATUS_SUCCESS
                    return None
                return caps.OutputReportByteLength
            finally:
                hidd.HidD_FreePreparsedData(pp)
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:            # a probe must never take the provider down
        return None


_CAPS: Dict[bytes, Optional[int]] = {}      # collection path -> output report length


def output_length(path) -> Optional[int]:
    """Cached _query_output_length. The paths change when a receiver is re-plugged,
    so the cache never outlives the collection it describes."""
    if path not in _CAPS:
        _CAPS[path] = _query_output_length(path)
    return _CAPS[path]


READ_ATTEMPTS = 4
READ_TIMEOUT_MS = 250
FLUSH_TIMEOUT_MS = 30

# vendor id -> product ids
PIDS: Dict[int, Dict[int, str]] = {
    0x3554: {
        0xF508: "Pulsar X2 V2 Mini (wireless)",
        0xF507: "Pulsar X2 V2 Mini (wired)",
        0xF58F: "ATK VXE R1 SE+ (wired)",
        0xF58A: "VXE R1 Pro Max (2.4 GHz)",
        0xF58C: "VXE R1 Pro Max (wired)",
    },
    0x373B: {
        0x1085: "ATK VXE R1 SE+ (2.4 GHz)",
    },
    0x3770: {
        0x0200: "Hitscan Hyperlight (2.4 GHz)",
        0x0100: "Hitscan Hyperlight (wired)",
    },
}


def checksum(payload: List[int]) -> int:
    return (CHECKSUM_BASE - sum(payload)) % 256


def make_request() -> List[int]:
    frame = [PAYLOAD_HEADER, CMD_POWER] + [0x00] * (PAYLOAD_LEN - 3)
    frame.append(checksum(frame))
    return frame


def parse_power(r) -> Optional[Tuple[int, bool]]:
    """(percent, power flag) from a power reply, or None when this is not one."""
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


def make_sonix_request() -> List[int]:
    """hidapi form of the X2 V3's 64-byte report-0 feature request.

    The first zero is hidapi's report id; the second starts the 64-byte packet that
    the libusb reference sends. Its checksum stays at wire bytes 62-63.
    """
    wire = ([0x00] + list(SONIX_BATTERY_COMMAND) +
            [0x00] * (SONIX_WIRE_LEN - len(SONIX_BATTERY_COMMAND) - 3))
    check = sum(wire) & 0xFFFF
    wire += [check & 0xFF, check >> 8]
    return [0x00] + wire


def parse_sonix_power(response) -> Optional[int]:
    """Battery percentage from a Sonix response; reject missing/bogus values."""
    if not response or len(response) <= SONIX_LEVEL_INDEX:
        return None
    if tuple(response[2:5]) != SONIX_BATTERY_COMMAND:
        return None
    level = response[SONIX_LEVEL_INDEX]
    return level if 0 <= level <= 100 else None


class PulsarProvider(Provider):
    name = "pulsar"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        """The collection to open.

        The ATK/VXE panel of the OpenMouse project opens the collection with usage
        page 0xFF02 and usage 0x0002 and sends its report-0x08 frames there, so that
        one is tried first - the R1 Pro Max dongle has it next to four other
        interface-1 collections, and the first of those is not the one it answers on.
        Without it, the first interface-1 collection is used, which is where the
        reference tool reads its replies.

        A collection whose output report cannot carry the 17-byte frame is skipped
        even when it has the right usage: Windows refuses that write and no reply
        comes, which is indistinguishable from a device that is off. The length is
        only known when Windows says so, and an unknown length never disqualifies a
        collection. The probe is the same read-only HidP_GetCaps query that
        providers/gwolves.py uses for its feature length, cached per collection path.
        (Spotted by ahmedkhursheed23 in #87, from mouse.xyz's writeFile().)
        """
        control = [d for d in infos
                   if (d.get("usage_page"), d.get("usage")) == CONTROL_USAGE]
        for d in control + infos:
            if output_length(d["path"]) in (None, PAYLOAD_LEN):
                if not control:
                    self._diag.append("  no control collection; using "
                                      f"iface={d.get('interface_number')} "
                                      f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                                      f"(output={output_length(d['path'])})")
                return d
            self._diag.append(f"  {d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                              f"output={output_length(d['path'])} cannot take a "
                              f"{PAYLOAD_LEN}-byte frame; skipped")
        if control:
            return control[0]
        self._diag.append(f"  no interface {CONTROL_INTERFACE} collection; "
                          f"falling back to the first of {len(infos)}")
        return infos[0] if infos else None

    def _query(self, path: bytes) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            self._drain(dev)
            dev.write(make_request())
            for attempt in range(READ_ATTEMPTS):
                r = dev.read(PAYLOAD_LEN, READ_TIMEOUT_MS)
                if not r:
                    break
                self._diag.append(f"  reply {attempt + 1}: {hexdump(r)}")
                if parse_power(r) is not None:
                    return list(r)
                time.sleep(0.02)
            self._diag.append("  no power reply (events and short frames are ignored)")
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
            if not dev.read(PAYLOAD_LEN, FLUSH_TIMEOUT_MS):
                return

    def _query_sonix(self, path: bytes) -> Optional[int]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                n = dev.send_feature_report(make_sonix_request())
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  feature send: {e}")
                return None
            if n is not None and n < 0:
                self._diag.append("  feature send: refused")
                return None
            time.sleep(0.05)
            try:
                response = dev.get_feature_report(0, SONIX_REPORT_LEN)
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  feature read: {e}")
                return None
            self._diag.append(f"  feature reply: {hexdump(response, 16)}")
            return parse_sonix_power(response)
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _poll_sonix(self) -> List[DeviceStatus]:
        out = []
        try:
            infos = hidlist.enumerate(SONIX_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(%04x): %s", SONIX_VID, e)
            return out
        for pid, (name, wired) in SONIX_PIDS.items():
            mine = [d for d in infos if d.get("product_id") == pid]
            if not mine:
                continue
            controls = [d for d in mine if d.get("interface_number") == SONIX_INTERFACE]
            if not controls:
                self._diag.append(f"[Pulsar Sonix] pid={SONIX_VID:04x}:{pid:04x} "
                                  f"'{name}': no interface {SONIX_INTERFACE}")
                continue
            d = next((x for x in controls if x.get("usage_page", 0) >= 0xFF00), controls[0])
            self._diag.append(f"[Pulsar Sonix] pid={SONIX_VID:04x}:{pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            level = self._query_sonix(d["path"])
            if level is not None:
                out.append(DeviceStatus(f"pulsar:{SONIX_VID:04x}{pid:04x}", name, level,
                                        wired, True, "pulsar", kind="mouse"))
        return out

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        out = self._poll_sonix()
        for vid, pids in PIDS.items():
            try:
                infos = hidlist.enumerate(vid)
            except Exception as e:  # pragma: no cover
                log.warning("hid.enumerate(%04x): %s", vid, e)
                continue
            for pid in pids:
                mine = [d for d in infos if d["product_id"] == pid]
                if not mine:
                    continue
                d = self._pick(mine)
                if d is None:
                    continue
                name = pids[pid]
                self._diag.append(f"[Pulsar] pid={vid:04x}:{pid:04x} '{name}' "
                                  f"iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                                  f" output={output_length(d['path'])}")
                reply = self._query(d["path"])
                parsed = parse_power(reply)
                if parsed is None:
                    continue
                level, on_cable = parsed
                mv = voltage_mv(reply)
                if mv:
                    self._diag.append(f"  {mv} mV")
                out.append(DeviceStatus(f"pulsar:{vid:04x}{pid:04x}", name, level, on_cable,
                                        True, "pulsar", kind="mouse"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
