"""Razer BlackShark V3 X HyperSpeed on its 2.4 GHz receiver (1532:057d, #280)
or USB cable (1532:057c).

This is the newer BlackShark protocol, not the 2023 "PA" frames and not the
BlackShark V3's 0x02/ff14 exchange.  It was reverse-engineered byte-for-byte
from Synapse captures by everyoneexe/blackshark-linux (`PROTOCOL.md`,
`blackshark-rs/src/protocol.rs`, MIT) for the Xbox edition (1532:0A5B).  The
receiver publishes the same vendor pages on the PC edition's interface 0; this
provider was confirmed on that hardware in #280 (test build 2 showed a live
level).  The cable identity is 1532:057c; openrazer-win's `blackshark_v3x`
driver tests the same class and commands on both ids.  The charging state is
available as class 0x07, command 0x04 (read as 0x84), as implemented by
openrazer PR #2899 and openrazer-win.

Every frame is a 64-byte feature report with report id 0x07:

    [0]=0x07  [1]=0  [2]=transaction id
    [6]=argument count  [7]=class  [8]=command  [9..]=arguments
    [62]=XOR of bytes 3..60

Battery is a read-only GET: class 0x07, command 0x80, one zero argument.
The reply keeps the same fields; byte 10 is the percentage (0..100).
Charging is class 0x07, command 0x84, two zero arguments; its reply carries
one argument at byte 9, where 0 means on battery and 1 means charging.

The repository opens the first interface that accepts the feature report; a
receiver may expose several vendor pages, so all of them are tried here.  No
configuration, firmware or lighting command is ever sent.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x1532
PID_RECEIVER = 0x057D
PID_CABLE = 0x057C
PIDS = (PID_CABLE, PID_RECEIVER)
PID = PID_RECEIVER          # kept for the receiver-only callers and tests
NAME = "Razer BlackShark V3 X HyperSpeed"
KEY = f"blackshark_v3x:{PID_RECEIVER:04x}"

REPORT_ID = 0x07
REPORT_LEN = 64
TRANSACTION = 0x03
CRC_FIRST, CRC_LAST, CRC_INDEX = 3, 60, 62
CLASS_BATTERY = 0x07
CMD_BATTERY = 0x80
CMD_CHARGING = 0x84
STATUS_BUSY = 0x01
STATUS_OK = 0x02
MAX_ARGUMENTS = 52
SETTLE_S = 0.05          # the reference waits 50 ms after SET before GET
REPLY_TRIES = 4


def checksum(frame) -> int:
    crc = 0
    for b in frame[CRC_FIRST:CRC_LAST + 1]:
        crc ^= b
    return crc


def make_query(transaction: int = TRANSACTION,
               command: int = CMD_BATTERY) -> bytes:
    """A read request; the battery and charging queries differ only in command."""
    frame = bytearray(REPORT_LEN)
    frame[0] = REPORT_ID
    frame[2] = transaction
    frame[6] = 0x02          # argument count: two zero bytes
    frame[7] = CLASS_BATTERY
    frame[8] = command
    frame[9] = 0x00
    frame[10] = 0x00
    frame[CRC_INDEX] = checksum(frame)
    return bytes(frame)


def parse_reply(data, command: int = CMD_BATTERY) -> Optional[int]:
    """Battery percentage or charging flag from the reply, or None.

    The PC receiver can answer a read with a short status frame
    (`07 01 03 ...` in the #280 test build) or with status BUSY while the
    headset is still getting the answer ready.  A finished reply has the
    request's transaction id and class/command, a valid checksum, status OK
    (0x02) and the expected arguments: the level at byte 10 for the battery,
    or the 0/1 charge flag at byte 9 for charging.
    """
    d = list(data or [])
    if len(d) < REPORT_LEN or d[0] != REPORT_ID:
        return None
    if d[2] != TRANSACTION:
        return None
    minimum = 2 if command == CMD_BATTERY else 1
    if d[6] < minimum or d[6] > MAX_ARGUMENTS:
        return None
    if d[7] != CLASS_BATTERY or d[8] != command:
        return None
    if d[CRC_INDEX] != checksum(d):
        return None
    if d[1] != STATUS_OK:
        return None
    if command == CMD_CHARGING:
        state = d[9]
        return state if state in (0, 1) else None
    level = d[10]
    if 0 <= level <= 100:
        return level
    return None


def candidates(infos: List[dict]) -> List[dict]:
    """The command collection(s), cable first, then receiver, vendor pages first."""
    mine = [d for d in infos if d.get("product_id") in PIDS]
    cable = [d for d in mine if d.get("product_id") == PID_CABLE]
    receiver = [d for d in mine if d.get("product_id") == PID_RECEIVER]
    result = []
    for group in (cable, receiver):
        vendor = [d for d in group if (d.get("usage_page") or 0) >= 0xFF00]
        other = [d for d in group if d not in vendor]
        result.extend(vendor + other)
    return result


class BlackSharkV3XProvider(Provider):
    name = "blackshark_v3x"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[int] = None

    def _query(self, dev, command: int, diag: List[str]) -> Optional[int]:
        """Send one read command and wait for a finished reply."""
        sent = dev.send_feature_report(make_query(command=command))
        if sent is not None and sent < 0:
            diag.append(f"    feature write refused ({sent})")
            return None
        for attempt in range(REPLY_TRIES):
            time.sleep(SETTLE_S)
            r = dev.get_feature_report(REPORT_ID, REPORT_LEN)
            if r:
                diag.append(f"    reply {attempt + 1}: len={len(r)} "
                            f"{hexdump(r, 16)}")
            value = parse_reply(r, command=command)
            if value is not None:
                return value
        return None

    def _ask(self, path, diag: List[str]) -> Optional[Tuple[int, bool, bool]]:
        """(level, charging, charging_known), or None when the headset is silent."""
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            diag.append(f"    open: {e}")
            return None
        try:
            level = self._query(dev, CMD_BATTERY, diag)
            if level is None:
                return None
            charging = self._query(dev, CMD_CHARGING, diag)
            if charging is None:
                return level, False, False
            return level, bool(charging), True
        except (OSError, IOError, ValueError) as e:
            diag.append(f"    error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(blackshark_v3x): %s", e)
            return []
        present = [d for d in infos if d.get("product_id") in PIDS]
        if not present:
            return []
        for pid in PIDS:
            if any(d.get("product_id") == pid for d in present):
                label = NAME + (" (USB cable)" if pid == PID_CABLE else "")
                self._diag.append(f"[BlackShark V3 X] pid={pid:04x} '{label}'")
        for d in candidates(infos):
            self._diag.append(f"  trying iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            got = self._ask(d["path"], self._diag)
            if got is not None:
                level, charging, charging_known = got
                self._last = level
                name = NAME + (" (USB cable)" if d.get("product_id") == PID_CABLE else "")
                state = (" charging" if charging else " on battery"
                         if charging_known else " charging unknown")
                self._diag.append(f"  -> {level}%{state}")
                return [DeviceStatus(KEY, name, level, charging, True, self.name,
                                     kind="headset", charging_known=charging_known)]
        self._diag.append("  no battery reply (wrong channel, headset off or asleep)")
        if self._last is not None:
            return [DeviceStatus(KEY, NAME, self._last, False, False, self.name,
                                 kind="headset", charging_known=False)]
        return [DeviceStatus(KEY, NAME, None, False, False, self.name,
                             kind="headset", charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
