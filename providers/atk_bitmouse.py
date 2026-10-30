"""The newer ATK / VXE NearLink receivers (#291), read by providers/pulsar.py.

373b:1216, product string "NK mouse NANO dongle", has none of the ff02:0002 collection the
other mice of that file answer on. Its configuration channel is ff05:0001 and it speaks
another protocol, which OpenMouse calls BITMOUSE (@openmouse/protocol, src/bitmouse),
taken from ATK's own configurator (hub.atk.pro). The receiver is shared: the VXE R1 of
#291, the ATK X1S and the A9 Plus 2.0 NK have all been seen on this id, so the name in
PIDS does not claim one model.

noosxe/atk-tool pull request #15 (closed for its size, not merged) tested this exact id
and captured the battery exchange, so the two sources meet on the frame:

    OUT 08 00 72 00 00 01 07 01 00 ...
    IN  08 72 00 05 00 07 01 5a bc 10 00 ...     0x5a = 90 %

Frames are report id 0x08 and 63 bytes:

    request  [1] checksum, the sum of bytes 2..63  [2]=0x72  [3] request length
             [4] sequence  [5] target, 1 = the mouse behind a receiver  [6] command
             [7] expected reply length
    reply    [1]=0x72  [2] status, 0xff = error  [3] sequence  [4] target
             [5] command  [6] payload length  [7..] payload

The request here is the one the vendor's configurator sends (OpenMouse: "the firmware
rejects a mismatched pair" of lengths). atk-tool sends length 0 and checksum 0 for the
same command, and that was answered too.

atk-tool reads byte 3 as a charging flag (0x3b on the cable, 0x05 on the receiver) and
bytes 8-9 as millivolts. By the layout above those are the sequence number and bytes
past the payload length, which OpenMouse says are leftovers of the previous exchange,
so neither is used: charging comes from its own command, 15.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from .base import hexdump

REPORT_ID = 0x08
FRAME_LEN = 63
MARKER = 0x72
STATUS_ERROR = 0xFF
TARGET_MOUSE = 1

CMD_BATTERY = 7
CMD_CHARGING = 15
REQUEST_LEN = 2         # [paramLen, cmdLen] is [2, 1] for both commands
REPLY_LEN = 1

USAGE = (0xFF05, 0x0001)

PIDS: Dict[Tuple[int, int], str] = {
    (0x373B, 0x1216): "ATK VXE NK mouse (2.4 GHz)",
}

REPORT_LEN = FRAME_LEN + 1
REPLY_TIMEOUT_S = 0.7   # what OpenMouse's driver allows for one exchange
READ_TIMEOUT_MS = 100
FLUSH_TIMEOUT_MS = 30


def make_request(command: int) -> List[int]:
    frame = [0x00] * FRAME_LEN
    frame[1] = MARKER
    frame[2] = REQUEST_LEN
    frame[4] = TARGET_MOUSE
    frame[5] = command
    frame[6] = REPLY_LEN
    frame[0] = sum(frame[1:]) & 0xFF
    return [REPORT_ID] + frame


def parse_reply(r, command: int) -> Optional[int]:
    """The first payload byte of the reply to this command, or None when this is not one."""
    if not r or len(r) < 8:
        return None
    if r[0] != REPORT_ID or r[1] != MARKER or r[2] == STATUS_ERROR:
        return None
    if r[5] != command or r[6] < 1:
        return None
    return r[7]


def pick(infos: List[dict]) -> Optional[dict]:
    """The configuration channel. No fallback: the other collections of this receiver
    are not known to take these frames, and one of them is the keyboard's."""
    for d in infos:
        if (d.get("usage_page"), d.get("usage")) == USAGE:
            return d
    return None


def _exchange(dev, command: int, diag: List[str]) -> Optional[int]:
    dev.write(make_request(command))
    deadline = time.monotonic() + REPLY_TIMEOUT_S
    while time.monotonic() < deadline:
        r = dev.read(REPORT_LEN, READ_TIMEOUT_MS)
        if not r:
            continue
        diag.append(f"  reply: {hexdump(r, 10)}")
        value = parse_reply(r, command)
        if value is not None:
            return value
    return None


def read_power(path: bytes, diag: List[str]) -> Optional[Tuple[int, bool]]:
    """(percent, charging), or None when the mouse gives no level.

    A level of 0 is refused: a mouse that can answer is not empty, and no source says
    what a receiver answers for a mouse that sleeps, so a 0 must not raise a low battery
    alert."""
    dev = hid.device()
    try:
        dev.open_path(path)
    except (OSError, IOError) as e:
        diag.append(f"  open: {e}")
        return None
    try:
        for _ in range(4):      # replies of an earlier run must not pass for ours
            if not dev.read(REPORT_LEN, FLUSH_TIMEOUT_MS):
                break
        level = _exchange(dev, CMD_BATTERY, diag)
        if level is None or not 1 <= level <= 100:
            diag.append(f"  no level (got {level})")
            return None
        return level, _exchange(dev, CMD_CHARGING, diag) == 1
    except (OSError, IOError, ValueError) as e:
        diag.append(f"  query error: {e}")
        return None
    finally:
        try:
            dev.close()
        except Exception:
            pass
