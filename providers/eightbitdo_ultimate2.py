"""8BitDo Ultimate 2 Wireless on its 2.4 GHz dongle in XInput mode (2dc8:310b), #520.

The controller appears as an Xbox pad, so providers/xinput.py owns the icon; this
module only supplies the level. On the dongle Windows.Gaming.Input reports its
remain=full=1000 placeholder and the vendor collection sends nothing by itself
(test builds 1-4, #461/#520) - but that collection is a request/answer channel,
and 8BitDo Ultimate Software V2 asks it about the controller behind the dongle
with one fixed query (the app's getControllerVersion_CMD):

    usage page 0xFF7A, usage 0x0001, output report 0x81, input report 0x02 (64 bytes)
    request   81 04 21 01 00 00 06 00 ...            zero-padded to 64 bytes
    answer    02 04 04 00 21 01 ...   byte 18 = battery %, 19-20 = firmware x100,
                                      21 = beta, 23 = colorway

Only a dongle answers it; a controller on its cable (or switched off) does not.
The frame and the byte positions are from MiguelSanzBr/8bitdo-ultimate2-linux (MIT:
src/ultimate2/protocol.py QUERY_PAD / ANSWER_PAD, device.py pad_status), which
reverse-engineered the protocol from the official app and shows this level in its
own GUI. No code is copied.

Charging: the answer has no documented charging flag. The same family's input
report keeps the level in bits 0-6 and the charging state in bit 7 (SDL's 8BitDo
driver), and a plain percentage never sets bit 7, so a set bit 7 is read as
charging; a clear one leaves the state unknown rather than "not charging". Each
answer is logged raw, so one report taken on the charger settles it. The
device-info query (81 04 00 01, answered 02 04 04 00 00 01 with the answering
device's PID at bytes 22-23: 0x6013 for a dongle, 0x6012 for the controller
itself) is logged too, to tell a dongle from a cable.

Only the 0xFF7A collection is ever opened. The controller's XInput collection
(0001:0005) is never touched: reading it made XInput fail and the pad disappear
(test build 3, #461). Both requests are queries; nothing is written to the
controller's settings.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import hexdump, log

VID = 0x2DC8
PID = 0x310B
NAME = "8BitDo Ultimate 2"
PAGE, USAGE = 0xFF7A, 0x0001
REPORT_SIZE = 64                       # report id 0x81 + 63 bytes

QUERY_PAD = bytes([0x81, 0x04, 0x21, 0x01, 0x00, 0x00, 0x06])
ANSWER_PAD = bytes([0x02, 0x04, 0x04, 0x00, 0x21, 0x01])
QUERY_INFO = bytes([0x81, 0x04, 0x00, 0x01])
ANSWER_INFO = bytes([0x02, 0x04, 0x04, 0x00, 0x00, 0x01])

LEVEL_BYTE = 18
LEVEL_MASK, CHARGING_BIT = 0x7F, 0x80
FIRMWARE_LO, FIRMWARE_HI = 19, 20
EDITION_BYTE = 23
INFO_PID_LO, INFO_PID_HI = 22, 23      # the answering device's PID, little-endian
INFO_PIDS = {0x6013: "dongle", 0x6012: "the controller itself, on its cable"}

PAD_TIMEOUT = 0.25                     # a dongle answers at once; on the cable nobody does
INFO_TIMEOUT = 0.25
READ_TIMEOUT_MS = 25
DRAIN_READS = 16
DRAIN_TIMEOUT_MS = 2
MAX_FRAMES = 8                         # unrelated frames skipped per request


@dataclass
class Reading:
    name: str
    level: int
    charging: bool = False
    approx: str = ""
    charging_known: bool = False


def frame(raw: bytes) -> bytes:
    """A request as written: the report id first, zero-padded to the report size."""
    return bytes(raw).ljust(REPORT_SIZE, b"\0")


def parse_pad_answer(data) -> Optional[Reading]:
    """The controller's level from the dongle's answer, or None.

    0 and anything above 100 are no reading: the controller answers while it is
    on, so a 0 would only ever become a false low-battery alert.
    """
    if not data or len(data) <= EDITION_BYTE or bytes(data[:len(ANSWER_PAD)]) != ANSWER_PAD:
        return None
    raw = data[LEVEL_BYTE]
    level = raw & LEVEL_MASK
    if not 1 <= level <= 100:
        return None
    charging = bool(raw & CHARGING_BIT)
    return Reading(NAME, level, charging=charging, charging_known=charging)


def parse_info_pid(data) -> Optional[int]:
    """The PID of whichever device answered the info query, or None."""
    if not data or len(data) <= INFO_PID_HI or bytes(data[:len(ANSWER_INFO)]) != ANSWER_INFO:
        return None
    return data[INFO_PID_LO] | data[INFO_PID_HI] << 8


def _drain(dev) -> None:
    """Drop anything already queued, so an old frame is not taken for the answer."""
    for _ in range(DRAIN_READS):
        try:
            if not dev.read(REPORT_SIZE, DRAIN_TIMEOUT_MS):
                return
        except (OSError, ValueError):
            return


def _ask(dev, raw: bytes, want: bytes, timeout: float, diag: List[str], label: str):
    """Write one request and return the first answer starting with `want`, or None."""
    _drain(dev)
    try:
        sent = dev.write(frame(raw))
    except (OSError, ValueError) as e:
        diag.append(f"    [8BitDo Ultimate 2] {label}: write failed: {e}")
        return None
    # Windows counts the report id in the length, so a full write can return 64 or
    # 65 (hidapi pads to the collection's report length); only a negative is a failure.
    if sent is None or sent < 0:
        diag.append(f"    [8BitDo Ultimate 2] {label}: write refused ({sent})")
        return None
    deadline = time.time() + timeout
    frames = 0
    while frames < MAX_FRAMES:
        left = int((deadline - time.time()) * 1000)
        if left < 1:
            break
        try:
            data = dev.read(REPORT_SIZE, min(left, READ_TIMEOUT_MS))
        except (OSError, ValueError) as e:
            diag.append(f"    [8BitDo Ultimate 2] {label}: read failed: {e}")
            return None
        if not data:
            continue
        frames += 1
        if bytes(data[:len(want)]) == want:
            diag.append(f"    [8BitDo Ultimate 2] {label} answer: {hexdump(data, 32)}")
            return list(data)
        diag.append(f"    [8BitDo Ultimate 2] {label}: other frame skipped: {hexdump(data, 12)}")
    diag.append(f"    [8BitDo Ultimate 2] {label}: no answer within {timeout:.2f} s")
    return None


def _query(path, diag: List[str]) -> Optional[Reading]:
    dev = hid.device()  # type: ignore[union-attr]
    try:
        dev.open_path(path)
    except (OSError, IOError, ValueError) as e:
        diag.append(f"    [8BitDo Ultimate 2] open: {e}")
        return None
    try:
        info = _ask(dev, QUERY_INFO, ANSWER_INFO, INFO_TIMEOUT, diag, "device info")
        pid = parse_info_pid(info)
        if pid is not None:
            diag.append(f"    [8BitDo Ultimate 2] answered by {pid:#06x} "
                        f"({INFO_PIDS.get(pid, 'unknown')})")
        answer = _ask(dev, QUERY_PAD, ANSWER_PAD, PAD_TIMEOUT, diag, "controller query")
        if answer is None:
            diag.append("    [8BitDo Ultimate 2] the controller did not answer: on its cable, "
                        "switched off, or this dongle does not pass the query on")
            return None
        got = parse_pad_answer(answer)
        if got is None:
            diag.append(f"    [8BitDo Ultimate 2] byte {LEVEL_BYTE} = {answer[LEVEL_BYTE]:#04x}: "
                        "not a level")
            return None
        firmware = answer[FIRMWARE_LO] | answer[FIRMWARE_HI] << 8
        diag.append(f"    [8BitDo Ultimate 2] -> {got.level}%"
                    f"{', charging (bit 7)' if got.charging else ', charging state not in the answer'}"
                    f" (byte {LEVEL_BYTE} = {answer[LEVEL_BYTE]:#04x}, controller firmware "
                    f"{firmware / 100:.2f}, edition {answer[EDITION_BYTE]})")
        return got
    finally:
        try:
            dev.close()
        except Exception:
            pass


def read_connected(diag: List[str]) -> Optional[Reading]:
    """Ask the dongle about the controller once; None when nothing answers."""
    if hid is None:
        return None
    try:
        infos = hidlist.enumerate(VID)
    except Exception as e:  # pragma: no cover
        log.warning("hid.enumerate(8bitdo ultimate2): %s", e)
        return None
    candidates = [d for d in infos
                  if d.get("product_id") == PID
                  and ((d.get("usage_page") or 0), (d.get("usage") or 0)) == (PAGE, USAGE)]
    if not candidates:
        diag.append("    [8BitDo Ultimate 2] no 0xFF7A:0x0001 collection")
        return None
    # A dongle and the controller on its cable can both be plugged in: each has its
    # own collection, and only the dongle answers the controller query.
    for d in candidates:
        got = _query(d["path"], diag)
        if got is not None:
            return got
    return None
