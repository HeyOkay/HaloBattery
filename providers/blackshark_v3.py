"""Razer BlackShark V3, 2.4 GHz dongle 1532:057A (#292).

Not the 2023 model's "PA" protocol: the dongle takes those frames on ff14:0001 and never
answers them, and it refuses the 90-byte feature reports of the other Razer devices.
The format is from the OpenRazer pull request #2794 (driver/razerkraken_driver.c, head
31c04d5, not merged at the time of writing) and from a Synapse capture of this dongle
that the author of that pull request posted in #292.

Frames are 64 bytes on the ff14:0001 collection (interface 5), report id 0x02:

    [0]=0x02 report id  [1] status (0x02 in a reply)  [2]=0x60 transaction id
    [6]=4 + argument count  [9] direction  [10] class
    [11] 0x00 in a request, 0x01 in a reply, 0x02 in a push
    [12] argument count  [13] first value  [62] XOR of bytes 0..61

Classes: 0x21 battery (percent), 0x2a charging (0 or 1), 0x20 link (a push with [13]=1
says the radio link came back). A value of 0xff means "not ready": the link is not up
yet. A query is answered with an input report on the same collection, not with a
feature report.

From the capture, with Synapse showing 97 % (only the non-zero bytes [0..13]):

    OUT 02 00 60 . . . 04 . . 00 02 00 00 00   [62]=64  wake 1
    OUT 02 00 60 . . . 04 . . 00 2a 00 00 00   [62]=4c  wake 2
    OUT 02 00 60 . . . 04 . . 80 2a 00 00 00   [62]=cc  wake 3, also the charging query
    IN  02 02 60 . . . 05 . . 80 2a 01 01 00   [62]=cf  not charging
    OUT 02 00 60 . . . 04 . . 80 21 00 00 00   [62]=c7  battery query
    IN  02 02 60 . . . 05 . . 80 21 01 01 61   [62]=a5  97 %

The wake frames go out once per connection, as Synapse does (OpenRazer's
razer_blackshark_v3_handshake sends the same three).

This headset must not be polled like the others. OpenRazer's daemon read it about every
2 s and that kept resetting the radio link, so the driver now queries at connect and
after a link-up push, and otherwise caches what the headset pushes; Synapse also stops
querying shortly after connect. The same here: the collection stays open between polls
so that Windows buffers the pushes for the next poll, and a query only goes out at
connect, after a link-up push, or when the headset has been silent for REFRESH_S, which
the author of the driver advises to keep at minutes. Nothing is written from a thread,
so turning the provider off stops the queries too.

Windows gives no sign when the headset is switched off, so that shows at the next query:
the level can stay on screen for up to REFRESH_S.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from .base import hexdump

PID = 0x057A
USAGE_PAGE = 0xFF14
USAGE = 0x0001

REPORT_ID = 0x02
REPORT_LEN = 64
TRANSACTION = 0x60
CHECKSUM_INDEX = 62

DIR_WAKE = 0x00
DIR_QUERY = 0x80

CLASS_INIT = 0x02
CLASS_LINK = 0x20
CLASS_BATTERY = 0x21
CLASS_CHARGING = 0x2A

KIND_REPLY = 0x01
KIND_PUSH = 0x02
NOT_READY = 0xFF

REFRESH_S = 300.0       # the longest silence before asking again
ATTEMPTS = 3            # queries per ask, like the few Synapse sends after a connect
RETRY_S = 0.7           # between them; OpenRazer's re-query waits the same
LINK_SETTLE_S = 0.6     # after a link-up push, before the first query
WAKE_WAIT_S = 0.05      # the capture shows the answer to a wake frame within 4 ms
REPLY_WAIT_S = 0.3      # the capture shows the battery reply after about 50 ms
READ_TIMEOUT_MS = 50
BUFFERED_TIMEOUT_MS = 1 # never 0: hidapi's read() takes that as "wait until a report comes"
MAX_BUFFERED = 128      # reports taken per poll; hidapi asks Windows to hold 64


def checksum(frame) -> int:
    crc = 0
    for b in frame[:CHECKSUM_INDEX]:
        crc ^= b
    return crc


def make_frame(cmd_class: int, direction: int = DIR_QUERY) -> bytes:
    b = bytearray(REPORT_LEN)
    b[0] = REPORT_ID
    b[2] = TRANSACTION
    b[6] = 4
    b[9] = direction
    b[10] = cmd_class
    b[CHECKSUM_INDEX] = checksum(b)
    return bytes(b)


def parse_frame(data) -> Optional[Tuple[int, int]]:
    """(class, value) of a reply or a push, or None when this is neither."""
    d = list(data or [])
    if len(d) <= CHECKSUM_INDEX or d[0] != REPORT_ID or d[CHECKSUM_INDEX] != checksum(d):
        return None
    if d[11] not in (KIND_REPLY, KIND_PUSH) or d[12] < 1:
        return None
    return d[10], d[13]


def is_candidate(info: dict) -> bool:
    return info.get("usage_page") == USAGE_PAGE and info.get("usage") == USAGE


class Session:
    """The open collection and what the headset said on it. poll() is the only way in:
    it takes what was pushed since the last call and asks only when a query is due."""

    def __init__(self, path: bytes):
        self.dev = hid.device()
        self.dev.open_path(path)
        self.level: Optional[int] = None
        self.charging = False
        self._woken = False         # the wake frames went out on this link
        self._link_up = False       # a link-up push was seen and not acted on yet
        self._next_query = 0.0      # time.monotonic() of the next query

    def close(self) -> None:
        try:
            self.dev.close()
        except Exception:
            pass

    def _handle(self, data, diag: List[str]) -> None:
        parsed = parse_frame(data)
        if parsed is None:
            return
        diag.append(f"    {hexdump(data, 14)}")
        cmd_class, value = parsed
        if cmd_class == CLASS_BATTERY and value <= 100:
            self.level = value
            self._next_query = time.monotonic() + REFRESH_S
        elif cmd_class == CLASS_CHARGING and value <= 1:
            self.charging = bool(value)
        elif cmd_class == CLASS_LINK and value == 1:
            # whatever is cached is from before the link dropped
            self.level = None
            self.charging = False
            self._woken = False
            self._link_up = True
            self._next_query = 0.0

    def _listen(self, seconds: float, diag: List[str]) -> None:
        """Read until the battery answers or the time is up."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            data = self.dev.read(REPORT_LEN, READ_TIMEOUT_MS)
            if data:
                self._handle(data, diag)
                if self.level is not None:
                    return

    def _write(self, frame: bytes, diag: List[str]) -> None:
        n = self.dev.write(frame)
        if n is None or n < 0:
            raise OSError(f"write -> {n}")
        diag.append(f"    sent class {frame[10]:02x} dir {frame[9]:02x}")

    def _ask(self, diag: List[str]) -> None:
        if self._link_up:
            self._link_up = False
            time.sleep(LINK_SETTLE_S)
        self.level = None
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(RETRY_S)
            if not self._woken:
                self._write(make_frame(CLASS_INIT, DIR_WAKE), diag)
                self._listen(WAKE_WAIT_S, diag)
                self._write(make_frame(CLASS_CHARGING, DIR_WAKE), diag)
                self._listen(WAKE_WAIT_S, diag)
            self._write(make_frame(CLASS_CHARGING), diag)
            self._listen(WAKE_WAIT_S, diag)
            self._write(make_frame(CLASS_BATTERY), diag)
            self._listen(REPLY_WAIT_S, diag)
            if self.level is not None:
                self._woken = True
                return
        diag.append("    no battery reply (headset off, or the link is not up yet)")
        self._woken = False
        self._next_query = time.monotonic() + REFRESH_S

    def poll(self, diag: List[str]) -> Tuple[Optional[int], bool]:
        """-> (level, charging); the level is None while the headset gives none.
        OSError or ValueError means the handle is dead: close it and open a new one."""
        for _ in range(MAX_BUFFERED):
            data = self.dev.read(REPORT_LEN, BUFFERED_TIMEOUT_MS)
            if not data:
                break
            self._handle(data, diag)
        if time.monotonic() >= self._next_query:
            self._ask(diag)
        return self.level, self.charging
