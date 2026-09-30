"""AJAZZ AJ179 V2 MAX over its 2.4 GHz receiver (249a:5c2f), without AJAZZ's software.

The receiver carries the level two ways, both recorded from the vendor's own
app (AJAZZ Driver 1.0.7.3) in the reporter's USBPcap captures in #74:

  * the app's very first startup read - command 0x10 - is answered by an info
    block that carries the percentage at byte 13:

        10 00 01 0b 4e 36 32 35 00 00 11 01 00 4b 01 00 ... 49
        |  |  |  |  "N625" firmware tag          |        +-- checksum: bytes 4..30
        |  |  |  |                                |            summed, low 8 bits
        |  |  |  |                                +----------- battery percent 0..100
        |  |  |  |                                              (0x4b = 75)
        |  |  |  +-------------------------------------------- payload length (11)
        |  |  +----------------------------------------------- 0x01 on every good reply
        |  +-------------------------------------------------- padding
        +----------------------------------------------------- the command, echoed

  * and the receiver announces the level by itself - a 32-byte frame

        c0 01 4b 00 ...
        |  |  |  +-- padding
        |  |  +----- battery percent 0..100
        |  +-------- 0x01 on a live 2.4 GHz link (0x00 marks some command replies)
        +----------- frame id 0xC0

The two agree on hardware: after charging, the reporter's capture shows 75 %
both in the reply and in the announcements (74 % a moment later), and before
it 13 % - low enough that he was asked to watch a charge. The frame shapes
were cross-checked against johan-akn/aj179-linux (MIT), a native Linux driver
for this exact receiver whose battery daemon listens for the same c0 frames,
and the byte layout against GetTheNya/Aj179PStat, which reads byte 2 of the
same kind of frame on the USB id 3151:402d. Repeating the vendor app's own
read is the whole exchange; nothing else is ever written, and a level is
refused unless the checksum and the 0..100 range fit (0 is refused with the
sibling AJAZZ receiver's rule - a zero there means the link is not up yet,
and a wrong 0 % is worse than no icon).

The vendor channel is the only collection with an output report (the #74
diagnostics dump shows 33-byte in/out on mi_02; nothing else opens), but the
dump carries no usage for it, so every collection except the plain mouse one
is tried in turn and the one that takes the write is remembered - the
vendor-page ranking is a preference, not a filter. The receiver cannot tell
a sleeping mouse from a switched-off one: a silent poll keeps the last level
on a greyed icon for a while, then the icon goes away and comes back with the
next reading. No charging flag was found in either frame, so none is shown.
Only 249a:5c2f is claimed - the receiver the #74 capture came from.

**Unverified** on hardware here: the exchange is the vendor app's own and the
numbers match its captures, but a HaloBattery read has to answer once on the
reporter's mouse before this leaves Unverified.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x249A
PID = 0x5C2F
NAME = "AJAZZ AJ179 V2 MAX"
KEY = f"ajazz:{PID:04x}"

REPORT_SIZE = 32                 # both directions; hidapi adds the report id on Windows
READ_SIZE = REPORT_SIZE + 1
READ_TIMEOUT_MS = 250
READ_ATTEMPTS = 5                # ~1.2 s; the reply comes within milliseconds when awake

INFO_CMD = 0x10                  # the vendor app's first startup read
INFO_PAD = 0x00                  # reply byte 1
INFO_MARK = 0x01                 # reply byte 2 on every good reply
INFO_LEN = 0x0B                  # reply byte 3: the info block is 11 bytes long
LEVEL_INDEX = 13                 # ... and the percentage is its next-to-last byte
CHECKSUM_INDEX = 31              # reply byte 31: bytes 4..30 summed, low 8 bits
CHECKSUM_FROM = 4

ANNOUNCE_ID = 0xC0               # the receiver's own battery announcement
ANNOUNCE_LIVE = 0x01             # byte 1: non-zero on a live link
ANNOUNCE_LEVEL_INDEX = 2

MOUSE_USAGE = (0x0001, 0x0002)   # never opened: the OS owns the pointer stream
VENDOR_PAGE = 0xFF00
MAX_CANDIDATES = 4               # the receiver has a handful of collections at most
ASLEEP_KEEP = 300                # s, as in the other receiver providers


def make_request() -> bytes:
    """The vendor app's startup read; hidapi wants the report id (0) in front."""
    payload = bytearray(REPORT_SIZE)
    payload[0] = INFO_CMD
    return b"\x00" + bytes(payload)


def parse_reply(frame) -> Optional[Tuple[int, str]]:
    """(level, "info" | "announcement") from a receiver report, or None without a level."""
    if not frame:
        return None
    f = list(frame)
    if len(f) == READ_SIZE and f[0] == 0x00:      # hidapi hands the report id back
        f = f[1:]
    if len(f) < REPORT_SIZE:
        return None
    if f[0] == ANNOUNCE_ID:
        if f[1] != ANNOUNCE_LIVE:
            return None                            # a c0 00 command reply, not an announcement
        level = f[ANNOUNCE_LEVEL_INDEX]
        if 0 < level <= 100:
            return level, "announcement"
        return None
    if f[0] != INFO_CMD or f[1] != INFO_PAD or f[2] != INFO_MARK or f[3] != INFO_LEN:
        return None
    if sum(f[CHECKSUM_FROM:CHECKSUM_INDEX]) & 0xFF != f[CHECKSUM_INDEX]:
        return None
    level = f[LEVEL_INDEX]
    if 0 < level <= 100:
        return level, "info"
    return None


def candidates(ifaces: List[dict]) -> List[dict]:
    """The collections to try, best first: vendor pages, then the rest.

    The dump carries no usage for the vendor channel (mi_02), so this is a
    preference order, not a filter; the collection that takes the write is
    remembered and tried first from then on. The mouse collection is never
    opened - the OS owns the pointer stream.
    """
    def rank(d: dict) -> tuple:
        return (0 if d.get("usage_page", 0) >= VENDOR_PAGE else 1,)

    seen = set()
    out = []
    for d in ifaces:
        if (d.get("usage_page"), d.get("usage")) == MOUSE_USAGE:
            continue
        key = d.get("path")
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return sorted(out, key=rank)


class AjazzProvider(Provider):
    name = "ajazz"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, float]] = None    # (level, when)
        self._chosen: Optional[bytes] = None

    def _ask(self, d: dict) -> Tuple[Optional[Tuple[int, str]], bool]:
        """Send the vendor app's read and listen briefly.

        -> (reading, accepted): accepted is True when the collection took the
        write - the vendor channel - even if the mouse did not answer.
        """
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None, False
        try:
            try:
                wrote = dev.write(make_request())
            except (OSError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None, False
            if wrote is not None and wrote < 0:
                self._diag.append("    write refused (no output report here)")
                return None, False
            for _ in range(READ_ATTEMPTS):
                r = dev.read(READ_SIZE, READ_TIMEOUT_MS)
                if not r:
                    continue              # quiet until the answer to the read
                got = parse_reply(r)
                self._diag.append(f"    report: {hexdump(r, 20)}"
                                  + (f"  -> {got[0]} % ({got[1]})" if got
                                     else "  (no level in it)"))
                if got is not None:
                    return got, True
            self._diag.append("    silence after the read (mouse off or asleep)")
            return None, True
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    read error: {e}")
            return None, False
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
            log.warning("hid.enumerate(ajazz): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        self._diag.append(f"[AJAZZ] pid={PID:04x} '{NAME}' "
                          f"product='{(mine[0].get('product_string') or '').strip()}'")

        order = candidates(mine)
        if self._chosen is not None:
            order = sorted(order, key=lambda d: d["path"] != self._chosen)
        got = None
        for d in order[:MAX_CANDIDATES]:
            self._diag.append(f"  asking iface={d.get('interface_number')} "
                              f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            got, accepted = self._ask(d)
            if accepted:
                if self._chosen != d["path"]:
                    self._chosen = d["path"]
                break                     # the vendor channel; silence is a sleeping mouse
            if got is not None:
                break

        if got is not None:
            level, _kind = got
            self._last = (level, time.time())
            return [DeviceStatus(KEY, NAME, level, False, True, "ajazz", kind="mouse")]

        # Silent receiver: it cannot tell a sleeping mouse from a switched-off
        # one, so the last value stays (greyed out) for a while, then the icon
        # is hidden and comes back with the next reading.
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, self._last[0], False, False, "ajazz", kind="mouse")]
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
