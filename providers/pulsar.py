"""Pulsar, ATK and VXE wireless mice over USB/HID, without vendor software.

Protocol from andrewrabert/python-pulsar-mouse-tool, which also backs the
"HID: pulsar" driver in review for the Linux kernel and lists these ids:

  * 3554:f508  Pulsar X2 V2 Mini (1 kHz dongle)      3554:f507  the same mouse on the cable
  * 3554:f58f  ATK VXE R1 SE+ (wired)                373b:1085  ATK VXE R1 SE+ (2.4 GHz)
  * the Kysona M600 and the VXE Dragonfly R1 Pro use the same protocol (their ids are
    not in the tool, so they are not claimed here).

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

READ_ATTEMPTS = 4
READ_TIMEOUT_MS = 250
FLUSH_TIMEOUT_MS = 30

# vendor id -> product ids
PIDS: Dict[int, Dict[int, str]] = {
    0x3554: {
        0xF508: "Pulsar X2 V2 Mini (wireless)",
        0xF507: "Pulsar X2 V2 Mini (wired)",
        0xF58F: "ATK VXE R1 SE+ (wired)",
    },
    0x373B: {
        0x1085: "ATK VXE R1 SE+ (2.4 GHz)",
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


class PulsarProvider(Provider):
    name = "pulsar"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        for d in infos:
            if d.get("interface_number") == CONTROL_INTERFACE:
                return d
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

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        out = []
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
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
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
