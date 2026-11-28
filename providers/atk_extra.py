"""ATK devices with their own transports, separate from the Compx/Pulsar mice.

  * ATK 8K Nano dongle (`373b:1178`): Holtek receiver, FF10:0001. A feature
    report `00 82` requests GetConfigData; the reply arrives as an input report
    starting with 0x82. Byte 9 is the link state (0 = no mouse paired/awake),
    byte 8 holds the level in bits 0..6 and the charging flag in bit 7.

  * ATK Mercury I / M1 (`373b:107c`): FF22:0001 feature mailbox. `aa 01`
    identifies the firmware, then two 32-bit memory reads (`0100fe94` pointer,
    then `0100fe78` capacity). The pointer value `0100fe5c` confirms the
    layout. It exposes no charging state.

  * ATK Z87 (`373b:1012`): FF1C:0092, 64-byte output/input, product string
    exactly "ATK Z87 Dongle". Request `04 seqLE 1a ...`; reply `04 seqLE 1a`
    with byte 8 = percent and byte 9 = 0/1/2 (1 or 2 = charging).

Protocol and evidence: BatteryHub 1.1.1 src/drivers/atk_bitmouse.cpp,
atk_mercury.cpp and atk_keyboard.cpp (GPL-3.0-or-later; protocol reference
only). The Z87 and Nano paths are hardware-tested by that project; Mercury's
protocol comes from Joule PR #79. Halo has not seen these units on hardware.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x373B
NANO_PID = 0x1178
MERCURY_PID = 0x107C
Z87_PID = 0x1012

NANO_USAGE = (0xFF10, 0x0001)
MERCURY_USAGE = (0xFF22, 0x0001)
Z87_USAGE = (0xFF1C, 0x0092)

NANO_DEADLINE_S = 0.7
MERCURY_SETTLE_S = 0.3
MERCURY_POINTER = 0x0100FE94
MERCURY_EXPECTED = 0x0100FE5C
MERCURY_CAPACITY = 0x0100FE78
Z87_DEADLINE_S = 1.0


def _payload(report):
    b = bytes(report or b"")
    if b and b[0] == 0x00 and len(b) > 1:
        b = b[1:]
    return b


def parse_nano(report) -> Optional[Tuple[int, bool]]:
    report = _payload(report)
    if len(report) < 11 or report[0] != 0x82:
        return None
    if report[9] == 0:                     # no mouse linked: off or asleep
        return None
    raw = report[8]
    level = raw & 0x7F
    if not 1 <= level <= 100:
        return None
    return level, bool(raw & 0x80)


def parse_z87(report, seq: int) -> Optional[Tuple[int, bool]]:
    report = _payload(report)
    if len(report) < 10:
        return None
    if report[0] != 0x04 or report[1] != (seq & 0xFF) or report[2] != (seq >> 8):
        return None
    if report[3] == 0xFF:                  # sleeping keyboard
        return None
    if report[3] != 0x1A or report[5] or report[6] or report[8] > 100:
        return None
    if report[9] > 2:
        return None
    return report[8], report[9] in (1, 2)


def _feature(dev, request: bytes, report_id: int = 0xAA) -> Optional[bytes]:
    try:
        dev.send_feature_report(request)
    except (OSError, ValueError):
        return None
    time.sleep(MERCURY_SETTLE_S)
    for length in (64, 65):
        try:
            reply = dev.get_feature_report(report_id, length)
        except (OSError, ValueError):
            continue
        if reply:
            return bytes(reply)
    return None


def _memory_value(dev, address: int) -> Optional[int]:
    request = bytearray(64)
    request[0] = 0xAA
    request[1] = 0x03
    request[3] = 0x01
    request[4] = 0x04
    request[5:9] = address.to_bytes(4, "big")
    reply = _feature(dev, bytes(request))
    if reply is None or len(reply) < 7:
        return None
    if reply[0] != 0xAA or reply[1] != 0x03 or reply[2] != 0x04:
        return None
    return int.from_bytes(reply[3:7], "little")


def parse_mercury(reply) -> Optional[Tuple[int, None]]:
    """A Mercury firmware hello, used only to prove the mailbox answered."""
    if not reply or len(reply) < 6 or reply[0] != 0xAA or reply[1] != 0x01:
        return None
    if reply[2:6] not in (b"\x00\x00\x00\x07", b"\x00\x00\x01\x03"):
        return None
    return 0, None


def read_mercury(path: bytes) -> Optional[int]:
    dev = hid.device()
    try:
        dev.open_path(path)
    except (OSError, IOError):
        return None
    try:
        hello = _feature(dev, bytes([0xAA, 0x01] + [0] * 62))
        if parse_mercury(hello) is None:
            return None
        pointer = _memory_value(dev, MERCURY_POINTER)
        if pointer != MERCURY_EXPECTED:
            return None
        level = _memory_value(dev, MERCURY_CAPACITY)
        return level if level is not None and 0 <= level <= 100 else None
    finally:
        try:
            dev.close()
        except Exception:
            pass


class AtkExtraProvider(Provider):
    name = "atk_extra"

    def __init__(self):
        self._diag: List[str] = []
        self._z87_sequence = 0

    def _nano(self, infos: List[dict]) -> Optional[DeviceStatus]:
        mine = [d for d in infos if d.get("product_id") == NANO_PID
                and (d.get("usage_page"), d.get("usage")) == NANO_USAGE]
        if not mine:
            return None
        d = mine[0]
        dev = hid.device()
        try:
            dev.open_path(d["path"])
            try:
                dev.send_feature_report(bytes([0x00, 0x82] + [0] * 7))
            except (OSError, ValueError) as e:
                self._diag.append(f"[ATK Nano] feature send: {e}")
                return None
            deadline = time.monotonic() + NANO_DEADLINE_S
            while time.monotonic() < deadline:
                reply = dev.read(64, 100)
                if not reply:
                    continue
                self._diag.append(f"[ATK Nano] reply: {hexdump(reply, 14)}")
                parsed = parse_nano(reply)
                if parsed is not None:
                    level, charging = parsed
                    return DeviceStatus("atk:nano8k:1178", "ATK 8K Nano mouse", level,
                                        charging, True, "atk_extra", kind="mouse")
            self._diag.append("[ATK Nano] no config reply")
            return None
        except (OSError, ValueError) as e:
            self._diag.append(f"[ATK Nano] query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _mercury(self, infos: List[dict]) -> Optional[DeviceStatus]:
        mine = []
        for d in infos:
            path = d.get("path") or b""
            if isinstance(path, str):
                text = path.lower()
            else:
                text = path.decode("latin-1").lower()
            if (d.get("product_id") == MERCURY_PID
                    and (d.get("usage_page"), d.get("usage")) == MERCURY_USAGE
                    and "&mi_00&col02" in text):
                mine.append(d)
        if not mine:
            return None
        level = read_mercury(mine[0]["path"])
        if level is None:
            self._diag.append("[ATK Mercury] mailbox did not answer")
            return None
        return DeviceStatus("atk:mercury:107c", "ATK Mercury I / M1", level, False,
                            True, "atk_extra", kind="headset", charging_known=False)

    def _z87(self, infos: List[dict]) -> Optional[DeviceStatus]:
        mine = []
        for d in infos:
            if (d.get("product_id") == Z87_PID
                    and (d.get("usage_page"), d.get("usage")) == Z87_USAGE
                    and (d.get("product_string") or "").strip() == "ATK Z87 Dongle"):
                mine.append(d)
        if not mine:
            return None
        d = mine[0]
        self._z87_sequence = self._z87_sequence % 0xFFFF + 1
        seq = self._z87_sequence
        request = bytearray(64)
        request[0] = 0x04
        request[1] = seq & 0xFF
        request[2] = seq >> 8
        request[3] = 0x1A
        dev = hid.device()
        try:
            dev.open_path(d["path"])
            for _ in range(4):
                if not dev.read(64, 30):
                    break
            dev.write(bytes([0x00]) + bytes(request))
            deadline = time.monotonic() + Z87_DEADLINE_S
            while time.monotonic() < deadline:
                reply = dev.read(64, 100)
                if not reply:
                    continue
                self._diag.append(f"[ATK Z87] reply: {hexdump(reply, 12)}")
                parsed = parse_z87(reply, seq)
                if parsed is not None:
                    level, charging = parsed
                    return DeviceStatus("atk:z87:1012", "ATK Z87", level, charging,
                                        True, "atk_extra", kind="keyboard")
            self._diag.append("[ATK Z87] no battery reply")
            return None
        except (OSError, ValueError) as e:
            self._diag.append(f"[ATK Z87] query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(atk_extra): %s", e)
            return []
        out = []
        for fn in (self._nano, self._mercury, self._z87):
            found = fn(infos)
            if found is not None:
                out.append(found)
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
