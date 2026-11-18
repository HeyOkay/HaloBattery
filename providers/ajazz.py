"""AJAZZ AJ179 V2 MAX and AJ159 PRO receivers, without AJAZZ's software.

AJ159 PRO (3151:402d), interface 2 / FFFF:0002: SET_FEATURE report 0,
opcode F7 and zero payload, wait 30 ms, GET_FEATURE 05; Windows level byte 3.
Independent source: Aiacos/ajazz-control-center at
03a29f7a90dd32057ca7cd0d6e6fa8f13d5400f3, src/devices/mouse/src/aj_series.cpp
L225-264, L290-312 and docs/research/reverse-engineering/capture-evidence.md
L54-91 (captured on 3151:5007):
https://github.com/Aiacos/ajazz-control-center/blob/03a29f7a90dd32057ca7cd0d6e6fa8f13d5400f3/src/devices/mouse/src/aj_series.cpp#L225-L312
Same-target confirmation: #289, test4 at commit
95ac6df038f1ec3d27653c89be0d2fdfc43d5253; the owner's diagnostics L46-50
records 05 00 00 46 01 00 01 02 (70%), GET05/33 refused then GET05/65 accepted:
https://github.com/HeyOkay/HaloBattery/issues/289#issuecomment-6899703056
https://github.com/user-attachments/files/34882030/diagnostics.txt
Zero means unknown (source L245-248), not an empty battery; charging is unknown.
Feature sizes come from opened collection caps. Legacy HIDAPI uses 65 bytes;
its short SET padding is documented in hidapi73d292a windows/hid.c L1189-1213.
No AJ179 output query or GET_FEATURE F7 is used for this target. Kysona remains held.

The AJ179 receiver carries the level two ways, both recorded from the vendor's own
app (AJAZZ Driver 1.0.7.3) in the reporter's USBPcap captures in #74:

  * the app's very first startup read - command 0x10 - is answered by an info
    block that carries the percentage at byte 13:

        10 00 01 0b 4e 36 32 35 00 00 11 01 00 4b 01 00 ... 49
        |  |  |  |  "N625" firmware tag          |  |     +-- checksum: bytes 4..30
        |  |  |  |                                |  |         summed, low 8 bits
        |  |  |  |                                |  +-------- 0x00 while the mouse is
        |  |  |  |                                |            on its charging cable
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
on a greyed icon for a while, then the icon drops to "no link (off or asleep)"
until a reading comes back (it does not vanish while the receiver is there,
#74). The info block's byte after the level (14) is the charging flag:
it read 0x01 in the wireless captures (13 %, 75 %, and 100 % on the cable)
and 0x00 in the diagnostics saved while charging at 56 % - charging shows
from it. An announcement carries no flag, so it keeps the last state - but
only on the mouse's own USB path, which is the one place charging is possible
at all (the wireless link drops while the mouse charges); a wireless answer
is never a charging one. And when an announcement and an info block are both
waiting on that path, the info block wins: it is the newer frame and it
carries the flag. Both rules came from his reports of the ring breathing
while the mouse was not charging (#74).
the receiver (249a:5c2f) and the mouse's own USB id (248a:5d2e) are claimed:
while the wireless link is down - the docked case, e.g. right after a restart
- the mouse itself answers on its cable, with the same read and the same
flag (#74).

The level is confirmed on the reporter's mouse. The charging flag's both
states are on the record from his hardware: 0x01 off the cable (13 %, 75 %,
100 %) and 0x00 while charging at 56 % - and the vendor app's own screen
shows no charging at 100 % either, the same rule the app follows.

His later run watched the ring itself: it breathes while charging and stops on
unplug. Known device quirk (full 32-byte frames compared, #74): right after a
host restart the mouse answers its *stored* status on its cable - 100 % / done
while it was really at 90 % - and that answer has the exact shape of a fresh
one, with nothing marking it as old. The vendor app's own read set gets the
same stale value; only the mouse's next replug refreshes it. The app shows
what the mouse answers and corrects itself as soon as a fresh answer arrives.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .pulsar import output_length

VID = 0x249A
PID = 0x5C2F
WIRED_VID = 0x248A                 # the mouse's own USB id while its cable is in
WIRED_PID = 0x5D2E
NAME = "AJAZZ AJ179 V2 MAX"
KEY = f"ajazz:{PID:04x}"

# AJ159's F7/05 exchange and exact collection are confirmed in #289 above.
# Kysona's matching collection shape (#251) does not establish its protocol.
ALT_TARGETS = (
    (0x3151, 0x402D, "AJAZZ AJ159 PRO"),
    (0x3151, 0x5031, "Kysona Uranus"),
)

# Only AJ159's level is confirmed. Kysona has no confirmed request/reply.
ENABLED_ALT_TARGETS = frozenset(((0x3151, 0x402D),))

REPORT_SIZE = 32                 # both directions; hidapi adds the report id on Windows
OUTPUT_LEN = REPORT_SIZE + 1     # what OutputReportByteLength reports for the vendor
                                 # channel (33, the report id included - #74's dump)
READ_SIZE = REPORT_SIZE + 1
READ_TIMEOUT_MS = 250
READ_ATTEMPTS = 5                # ~1.2 s; the reply comes within milliseconds when awake

INFO_CMD = 0x10                  # the vendor app's first startup read
INFO_PAD = 0x00                  # reply byte 1
INFO_MARK = 0x01                 # reply byte 2 on every good reply
INFO_LEN = 0x0B                  # reply byte 3: the info block is 11 bytes long
LEVEL_INDEX = 13                 # ... and the percentage is its next-to-last byte
FLAG_INDEX = 14                  # the byte after it: 0x00 while the mouse sits on
                                 # its charging cable, 0x01 otherwise (0x01 in the
                                 # wireless captures at 13 % and 75 %, 0x00 in the
                                 # one taken while charging at 56 %)
CHECKSUM_INDEX = 31              # reply byte 31: bytes 4..30 summed, low 8 bits
CHECKSUM_FROM = 4

ANNOUNCE_ID = 0xC0               # the receiver's own battery announcement
ANNOUNCE_LIVE = 0x01             # byte 1: non-zero on a live link
ANNOUNCE_LEVEL_INDEX = 2

MOUSE_USAGE = (0x0001, 0x0002)   # never opened: the OS owns the pointer stream
VENDOR_PAGE = 0xFF00
MAX_CANDIDATES = 6               # the 8K receiver has five: four on interface 1 plus
                                 # the vendor pair on interface 2 (#289 - the fourth one
                                 # cut the dump below the channel that actually answers)
ASLEEP_KEEP = 300                # s, as in the other receiver providers


def make_request() -> bytes:
    """The vendor app's startup read; hidapi wants the report id (0) in front."""
    payload = bytearray(REPORT_SIZE)
    payload[0] = INFO_CMD
    return b"\x00" + bytes(payload)


def parse_reply(frame) -> Optional[Tuple[int, str, Optional[bool]]]:
    """(level, "info" | "announcement", charging) from a receiver report, or None.

    charging is None for an announcement - those frames carry the level only.
    """
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
            return level, "announcement", None
        return None
    if f[0] != INFO_CMD or f[1] != INFO_PAD or f[2] != INFO_MARK or f[3] != INFO_LEN:
        return None
    if sum(f[CHECKSUM_FROM:CHECKSUM_INDEX]) & 0xFF != f[CHECKSUM_INDEX]:
        return None
    level = f[LEVEL_INDEX]
    if 0 < level <= 100:
        return level, "info", f[FLAG_INDEX] == 0x00
    return None


def parse_aj159_status(frame) -> Optional[int]:
    """The confirmed numbered Windows report: 05 00 00 <level> ... ."""
    if not isinstance(frame, (bytes, bytearray, memoryview, list, tuple)) or len(frame) < 4:
        return None
    if any(type(value) is not int or not 0 <= value <= 255 for value in frame):
        return None
    if tuple(frame[:3]) != (0x05, 0x00, 0x00):
        return None
    level = frame[3]
    return level if 1 <= level <= 100 else None


def candidates(ifaces: List[dict]) -> List[dict]:
    """The collections to try, best first.

    The dump carries no usage for the vendor channel (mi_02), but it is the only
    collection with an output report - 33 bytes both ways - so the read-only
    OutputReportByteLength probe ranks it first. Without it a cold start wrote the
    ask to the keyboard and consumer collections first, which must refuse it
    (#246 review, @ahmedkhursheed23). A collection Windows will not classify keeps
    the old vendor-page preference, and one that reports a length it cannot take is
    tried last. This is a preference order, not a filter; the collection that takes
    the write is remembered and tried first from then on. The mouse collection is
    never opened - the OS owns the pointer stream.
    """
    def rank(d: dict) -> tuple:
        length = output_length(d["path"])
        if length == OUTPUT_LEN:
            return (0,)
        if length is None:
            return (1, 0 if d.get("usage_page", 0) >= VENDOR_PAGE else 1)
        return (2,)

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
        self._charging = False                            # from the info block's flag
        self._chosen: Optional[bytes] = None
        self._wired_chosen: Optional[bytes] = None
        # the AJ159 PRO target keeps its own state (#289)
        self._alt: Dict[int, dict] = {}   # pid -> chosen path / last / charging

    def _ask_aj159(self, d: dict) -> Tuple[Optional[int], bool]:
        """One sourced F7/05 exchange on AJ159's confirmed control collection."""
        if (d.get("vendor_id", 0x3151) != 0x3151 or d.get("product_id") != 0x402D
                or d.get("interface_number") != 2
                or (d.get("usage_page"), d.get("usage")) != (0xFFFF, 0x0002)):
            return None, False
        dev = hid.device()
        try:
            dev.open_path(d["path"])
            metadata = getattr(dev, "get_report_lengths", None)
            length = 65  # Windows GET05/65 succeeded in the owner's test4 diagnostics.
            if callable(metadata):
                caps = metadata()
                length = caps.get("feature") if isinstance(caps, dict) else None
            if type(length) is not int or not 4 <= length <= 32768:
                self._diag.append("    AJ159: no usable feature report size")
                return None, False
            poll = bytes((0x00, 0xF7)) + bytes(length - 2)
            wrote = dev.send_feature_report(poll)
            if type(wrote) is not int or wrote != length:
                self._diag.append(f"    AJ159: incomplete feature poll ({wrote!r}/{length})")
                return None, False
            time.sleep(0.03)
            reply = dev.get_feature_report(0x05, length)
            level = parse_aj159_status(reply)
            try:
                preview = hexdump(reply, 12)
            except (TypeError, ValueError):
                preview = "invalid feature reply"
            self._diag.append(f"    AJ159 report 05 len={length}: {preview}"
                              + (f" -> {level}%" if level is not None else " (no current level)"))
            return level, True
        except (OSError, ValueError) as error:
            self._diag.append(f"    AJ159 feature exchange: {error}")
            return None, False
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _ask(self, d: dict) -> Tuple[Optional[Tuple[int, str, Optional[bool]]], bool]:
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
            announce = None
            for _ in range(READ_ATTEMPTS):
                r = dev.read(READ_SIZE, READ_TIMEOUT_MS)
                if not r:
                    continue              # quiet until the answer to the read
                got = parse_reply(r)
                self._diag.append(f"    report: {hexdump(r)}"
                                  + (f"  -> {got[0]} % ({got[1]}"
                                     f"{', charging' if got[2] else ''})" if got
                                     else "  (no level in it)"))
                if got is None:
                    continue
                if got[1] == "announcement":
                    # An announcement carries the level but no charging flag.
                    # Keep the newest one as the fallback, but read on: the ask
                    # just sent provokes an info block, and only that carries the
                    # flag. Returning the announcement instead let a stale
                    # "charging" survive until a later poll - after unplugging,
                    # the ring kept breathing until the next full answer (#74).
                    announce = got
                    continue
                return got, True
            if announce is not None:
                return announce, True
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

    def _ask_wired(self) -> Tuple[Optional[Tuple[int, str, Optional[bool]]], bool]:
        """The mouse on its own USB cable: the same read on its own id (#74).

        -> (reading, present): present is True while the mouse's USB device
        exists (its wireless link is down - the docked case), even when
        nothing answered.
        """
        try:
            wired = [d for d in hidlist.enumerate(WIRED_VID)
                     if d.get("product_id") == WIRED_PID]
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(ajazz wired): %s", e)
            return None, False
        if not wired:
            return None, False
        self._diag.append(f"[AJAZZ] usb mouse pid={WIRED_PID:04x} "
                          f"product='{(wired[0].get('product_string') or '').strip()}' "
                          f"(its wireless link is down)")
        order = candidates(wired)
        if self._wired_chosen is not None:
            order = sorted(order, key=lambda d: d["path"] != self._wired_chosen)
        got = None
        for d in order[:MAX_CANDIDATES]:
            self._diag.append(f"  asking iface={d.get('interface_number')} "
                              f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            got, accepted = self._ask(d)
            if accepted:
                if self._wired_chosen != d["path"]:
                    self._wired_chosen = d["path"]
                break
            if got is not None:
                break
        return got, True

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        found = self._poll_primary()
        if found:
            return found
        return self._poll_alt()

    def _poll_alt(self) -> List[DeviceStatus]:
        """The 3151-family receivers, tried only when the AJ179 is not there."""
        out: List[DeviceStatus] = []
        cache: Dict[int, List[dict]] = {}
        for vid, pid, name in ALT_TARGETS:
            if (vid, pid) != (0x3151, 0x402D) or (vid, pid) not in ENABLED_ALT_TARGETS:
                continue
            if vid not in cache:
                try:
                    cache[vid] = hidlist.enumerate(vid)
                except Exception as e:  # pragma: no cover
                    log.warning("hid.enumerate(ajazz alt): %s", e)
                    cache[vid] = []
            mine = [d for d in cache[vid] if d.get("product_id") == pid]
            state = self._alt.setdefault(pid, {"chosen": None, "last": None, "charging": False})
            key = f"ajazz:{pid:04x}"
            got = None
            if mine:
                self._diag.append(f"[AJAZZ] pid={pid:04x} '{name}' "
                                  f"product='{(mine[0].get('product_string') or '').strip()}'")
                order = [d for d in mine if d.get("interface_number") == 2
                         and (d.get("usage_page"), d.get("usage")) == (0xFFFF, 0x0002)
                         and d.get("vendor_id", vid) == vid]
                if state["chosen"] is not None:
                    order = sorted(order, key=lambda d: d["path"] != state["chosen"])
                for d in order[:1]:
                    self._diag.append(f"  asking iface={d.get('interface_number')} "
                                      f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                    got, accepted = self._ask_aj159(d)
                    if accepted:
                        state["chosen"] = d["path"]
                        break
                    if got is not None:
                        break
            if got is not None:
                level, charging = got, False
                state["last"] = (level, time.time())
                out.append(DeviceStatus(key, name, level, charging, True, "ajazz", kind="mouse",
                                        charging_known=False))
            elif mine:
                last = state["last"]
                if last and time.time() - last[1] < ASLEEP_KEEP:
                    out.append(DeviceStatus(key, name, last[0], False, False,
                                            "ajazz", kind="mouse", charging_known=False))
                else:
                    out.append(DeviceStatus(key, name, None, False, False, "ajazz", kind="mouse",
                                            charging_known=False))
        return out

    def _poll_primary(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(ajazz): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        got = None
        if mine:
            self._diag.append(f"[AJAZZ] pid={PID:04x} '{NAME}' "
                              f"product='{(mine[0].get('product_string') or '').strip()}'")
            order = candidates(mine)
            if self._chosen is not None:
                order = sorted(order, key=lambda d: d["path"] != self._chosen)
            for d in order[:MAX_CANDIDATES]:
                self._diag.append(f"  asking iface={d.get('interface_number')} "
                                  f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                got, accepted = self._ask(d)
                if accepted:
                    if self._chosen != d["path"]:
                        self._chosen = d["path"]
                    break                 # the vendor channel; silence is a sleeping mouse
                if got is not None:
                    break

        # The wireless link can be down while the mouse is on its cable (its radio
        # sleeps when docked / charging, e.g. right after a restart). The mouse is
        # still present then - ask it the same read on its own USB id (#74).
        if got is None:
            got, wired_present = self._ask_wired()
        else:
            wired_present = False

        if got is not None:
            level, _kind, charging = got
            if charging is None:
                # An announcement carries no flag. Keep the last state only on
                # the mouse's own USB path, the one place charging is possible
                # at all: the wireless link drops while the mouse sits on its
                # cable, so a wireless answer can never be a charging one. The
                # kept state used to survive into the first wireless polls
                # after unplugging and kept the charging ring breathing while
                # the mouse was not charging (#74).
                charging = self._charging if wired_present else False
            else:
                self._charging = charging
            self._last = (level, time.time())
            return [DeviceStatus(KEY, NAME, level, charging, True, "ajazz", kind="mouse")]

        # Silent: the mouse may be asleep, switched off, out of range, or charging
        # on a cable the receiver puts its radio down for. While the receiver - or
        # the mouse's cable - is still there, the icon stays: the last level while
        # it is fresh, "no link (off or asleep)" after that (#74).
        if not mine and not wired_present:
            return []
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, self._last[0], self._charging, False, "ajazz",
                                 kind="mouse")]
        return [DeviceStatus(KEY, NAME, None, False, False, "ajazz", kind="mouse")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
