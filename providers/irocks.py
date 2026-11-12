"""iRocks M31R over its 2.4 GHz receiver and its cable (#309).

The M31R speaks the Compx 17-byte frame, but on a different transport from the Pulsar /
ATK / VXE receivers: its command collection (``ff02:0002``) has no output report, so the
request goes out as a **feature** report there, and the reply arrives as an **input**
report on another collection, ``ff01:0000``. Replies may carry header ``0x09`` as well
as ``0x08``; the checksum (``0x55 - sum``) still counts the header. Bytes 8-9 are always
zero on this model, so no millivolts are read.

The receiver also answers a power query from a cache while its mouse is off or asleep,
so it is asked ``0x03`` (connection status) first: only a valid answer with byte 2 == 0
and byte 6 == 1 means online, and only then is the battery read. Anything else is an
offline or sleeping mouse: the last reading taken while online stays greyed for
``ASLEEP_KEEP``, and nothing is read from the receiver's cache. The cable has no offline
state - a mouse on its cable is awake - so it is never asked and never kept.

All of this came from @jimchen5209's hardware work in #309 (their branch ``m31r``): the
request/reply collections, the 0x09 header, the captured 55 % / 75 % frames, the 0x03
online check and the wired id 14a5:c010. Frames below are the ones they measured.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x14A5
RECEIVER_PID = 0xC009
CABLE_PID = 0xC010
PIDS: Dict[int, str] = {
    RECEIVER_PID: "iRocks M31R (2.4 GHz)",
    CABLE_PID: "iRocks M31R (wired)",
}
KEYS: Dict[int, str] = {RECEIVER_PID: "irocks:c009", CABLE_PID: "irocks:c010"}

COMMAND_USAGE = (0xFF02, 0x0002)      # feature report length 17, no output report
REPLY_USAGE = (0xFF01, 0x0000)        # input report length 17

FRAME_LEN = 17
HEADER = 0x08
REPLY_HEADERS = (0x08, 0x09)
CMD_CONNECT = 0x03
CMD_POWER = 0x04
CHECKSUM_BASE = 0x55
LEVEL_INDEX = 6
FLAG_INDEX = 7
ONLINE_INDEX = 6

READ_ATTEMPTS = 8                    # events and other commands are skipped
READ_TIMEOUT_MS = 250
ONLINE_TIMEOUT_MS = 500
FLUSH_TIMEOUT_MS = 30
ASLEEP_KEEP = 300                    # s, as in the other receiver providers


def checksum(frame: List[int]) -> int:
    return (CHECKSUM_BASE - sum(frame)) % 256


def make_request(command: int = CMD_POWER) -> List[int]:
    frame = [HEADER, command] + [0x00] * (FRAME_LEN - 3)
    frame.append(checksum(frame))
    return frame


def _checksum_ok(frame: List[int]) -> bool:
    return len(frame) >= FRAME_LEN and frame[FRAME_LEN - 1] == checksum(frame[:FRAME_LEN - 1])


def parse_power(reply) -> Optional[Tuple[int, bool]]:
    """(level, on-cable) from a power reply; None when this is not one."""
    if not reply or len(reply) < FRAME_LEN:
        return None
    frame = list(reply[:FRAME_LEN])
    if frame[0] not in REPLY_HEADERS or frame[1] != CMD_POWER:
        return None
    if not _checksum_ok(frame):
        return None
    level = frame[LEVEL_INDEX]
    if not 0 <= level <= 100:
        return None
    return level, frame[FLAG_INDEX] != 0


def parse_online(reply) -> Optional[bool]:
    """Whether the mouse is online, from a 0x03 answer; None when this is not one."""
    if not reply or len(reply) < FRAME_LEN:
        return None
    frame = list(reply[:FRAME_LEN])
    if frame[0] not in REPLY_HEADERS or frame[1] != CMD_CONNECT:
        return None
    if not _checksum_ok(frame):
        return None
    if frame[2] != 0:
        return None
    return frame[ONLINE_INDEX] == 1


class IRocksProvider(Provider):
    name = "irocks"

    def __init__(self):
        self._diag: List[str] = []
        # key -> (name, level, charging, when it was last read while online)
        self._last: Dict[str, Tuple[str, int, bool, float]] = {}

    # ------------------------------------------------------------------ HID

    def _read_reply(self, dev, parse, what: str, attempts: int = READ_ATTEMPTS,
                    timeout: int = READ_TIMEOUT_MS):
        for attempt in range(attempts):
            frame = dev.read(FRAME_LEN, timeout)
            if not frame:
                break
            self._diag.append(f"  reply {attempt + 1}: {hexdump(frame)}")
            if parse(frame) is not None:
                return list(frame)
            time.sleep(0.02)
        self._diag.append(f"  no {what} reply (events and short frames are ignored)")
        return None

    def _drain(self, dev) -> None:
        for _ in range(4):
            if not dev.read(FRAME_LEN, FLUSH_TIMEOUT_MS):
                return

    def _query(self, infos: List[dict], check_online: bool):
        """-> (online, power reply). online False means the mouse is off/asleep."""
        command = next((d for d in infos
                        if (d.get("usage_page"), d.get("usage")) == COMMAND_USAGE), None)
        replies = next((d for d in infos
                        if (d.get("usage_page"), d.get("usage")) == REPLY_USAGE), None)
        if command is None or replies is None:
            self._diag.append("  needs the ff02:0002 and ff01:0000 collections; "
                              f"{'ff02:0002' if command is None else 'ff01:0000'} not found")
            return None, None
        opened = []
        state: Optional[bool] = None
        try:
            for d in (replies, command):
                dev = hid.device()  # type: ignore[union-attr]
                try:
                    dev.open_path(d["path"])
                except (OSError, IOError) as e:
                    self._diag.append(f"  open: {e}")
                    return None, None
                opened.append(dev)
            reader, writer = opened
            if check_online:
                state = False
            self._drain(reader)
            if check_online:
                n = writer.send_feature_report(bytes(make_request(CMD_CONNECT)))
                if n is not None and n < 0:
                    self._diag.append("  online query: send refused")
                    return False, None
                answer = self._read_reply(reader, parse_online, "online",
                                          attempts=READ_ATTEMPTS, timeout=ONLINE_TIMEOUT_MS)
                online = parse_online(answer)
                self._diag.append(f"  online: {online if answer else 'no answer'}"
                                  f"{'' if online else ' - treated as offline or asleep'}")
                if not online:
                    return False, None
                state = True
                self._drain(reader)
            n = writer.send_feature_report(bytes(make_request(CMD_POWER)))
            if n is not None and n < 0:
                self._diag.append("  send: refused")
                return state, None
            return state, self._read_reply(reader, parse_power, "power")
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
            return state, None
        finally:
            for dev in opened:
                try:
                    dev.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------ poll

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(irocks): %s", e)
            return []
        out: List[DeviceStatus] = []
        present = set()
        found = set()
        now = time.time()
        for pid, name in PIDS.items():
            mine = [d for d in infos if d.get("product_id") == pid]
            if not mine:
                continue
            key = KEYS[pid]
            present.add(key)
            self._diag.append(f"[iRocks] pid={VID:04x}:{pid:04x} '{name}'")
            online, reply = self._query(mine, check_online=(pid == RECEIVER_PID))
            if online is False:
                # The receiver says its mouse is off or asleep (or would not say).
                self._diag.append("    mouse offline or asleep; no battery read")
                continue
            parsed = parse_power(reply)
            if parsed is None:
                self._diag.append("    no good power reply")
                continue
            level, charging = parsed
            self._last[key] = (name, level, charging, now)
            found.add(key)
            out.append(DeviceStatus(key, name, level, charging, True, "irocks", kind="mouse"))

        # A receiver that stops answering keeps its last reading greyed for ASLEEP_KEEP;
        # the cable has no offline state, so it is never kept. A device that left the USB
        # tree is dropped at once.
        for key in list(self._last):
            if key in found:
                continue
            name, level, charging, seen = self._last[key]
            if key not in present:
                del self._last[key]
            elif now - seen >= ASLEEP_KEEP:
                self._diag.append(f"[iRocks] {name}: no online reading for {ASLEEP_KEEP} s; "
                                  "not reported any more")
                del self._last[key]
            elif key == KEYS[RECEIVER_PID]:
                out.append(DeviceStatus(key, name, level, charging, False, "irocks",
                                        kind="mouse"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
