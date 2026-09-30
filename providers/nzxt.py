"""NZXT Lift Elite Wireless over its dongle (1e71:2101), without NZXT CAM.

No public protocol exists for this mouse - OpenRGB's NZXT controller covers the
wired Lift's LEDs only, and CAM keeps the battery inside its closed native
module. Every byte here is therefore taken from a USBPcap capture of CAM
4.76.5 talking to the reporter's own dongle (issue #148, 2026-09-30, battery
at 76-77 %). The conversation is a request in a 64-byte interrupt OUT report
and a reply on interrupt IN, both starting with 0x4e:

    request:  4e 02 81 00 b0 00 ...
              0x4e framing, 02 = the mouse behind the dongle, 81 = "read a
              property", 0xb0 = the telemetry property this provider wants
    reply:    4e 02 97 00 01 40 01 86 0f fd ff 40 01 f6 00 43 00 4d 00 64 ...

Field map, read off the capture (seven telemetry reads at two battery states):

    bytes 17-18  battery percent, little-endian (4d 00 = 77, 4c 00 = 76).
                 CAM's own panel read 76 -> 75 over the same capture, so the
                 reporter's hardware run is what pins the two against each
                 other.
    bytes 8-9    cell voltage, big-endian millivolts (0f fd = 4093 mV); it
                 fell to 4092 in step with the percentage.
    byte 7       flags; 86 in five reads, 82 in two with nothing in the
                 capture explaining the difference - NOT decoded.
    the rest     constant across all seven reads (43 00, 64 00, 1e 00, 08 00
                 and a repeated 40 01) - not decoded, nothing is guessed.

The reply arrived ~25 ms after the request, always preceded by a 4e e5 00
acknowledgement from the dongle, so the read loop skips e5 frames and waits
for the 97 one. A stale frame from the previous poll is drained before the
request is sent.

The dongle splits into six HID collections; the conversation rode the one on
usage page ffca / usage 0001 - the same page the wired Lift's OpenRGB driver
picks - chosen by usage page, never by interface number.

The charging state was never captured (the cable was not plugged in during
the recording), so charging is not claimed at all for this mouse. Claimed for
1e71:2101 only, the receiver the capture came from; the 1e71:2131 keyboard is
wired and has no battery to read. **Unverified on hardware** until the
reporter's test build run; if the dongle should turn out to need CAM running,
the diagnostics will show it acknowledging the request and never answering.
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

NZXT_VID = 0x1E71
RECEIVER_PID = 0x2101
PIDS = {RECEIVER_PID: "NZXT Lift Elite Wireless"}

VENDOR_PAGE = 0xFFCA
VENDOR_USAGE = 0x0001

MAGIC = 0x4E
TARGET_MOUSE = 0x02
OP_READ = 0x81
PROP_TELEMETRY = 0xB0
REPLY_TELEMETRY = 0x97
ACK = 0xE5

REQUEST = bytes([MAGIC, TARGET_MOUSE, OP_READ, 0x00, PROP_TELEMETRY]) + b"\x00" * 59

LEVEL_INDEX = 17
VOLTAGE_INDEX = 8
MIN_FRAME = 27

READ_ATTEMPTS = 10
READ_TIMEOUT_MS = 150
DRAIN_READS = 3
DRAIN_TIMEOUT_MS = 1
MAX_CANDIDATES = 2
ASLEEP_KEEP = 300                 # s, as in the other receiver providers

Reading = Tuple[int, int]         # level %, cell millivolts


def parse_telemetry(r) -> Optional[Reading]:
    """(level, millivolts) from a telemetry reply, or None when it is not one."""
    if not r or len(r) < MIN_FRAME:
        return None
    if r[0] != MAGIC or r[1] != TARGET_MOUSE or r[2] != REPLY_TELEMETRY:
        return None
    level = r[LEVEL_INDEX] | (r[LEVEL_INDEX + 1] << 8)
    if not 0 <= level <= 100:
        return None               # not a percentage - refuse rather than show it
    mv = (r[VOLTAGE_INDEX] << 8) | r[VOLTAGE_INDEX + 1]
    return level, mv


def candidates(ifaces: List[dict]) -> List[dict]:
    """The dongle's vendor collection(s): usage page ffca / usage 0001, in order."""
    seen = set()
    out = []
    for d in ifaces:
        if (d.get("usage_page"), d.get("usage")) != (VENDOR_PAGE, VENDOR_USAGE):
            continue
        if d["path"] in seen:
            continue
        seen.add(d["path"])
        out.append(d)
    return out


class NzxtProvider(Provider):
    name = "nzxt"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, int, float]] = None
        self._chosen: Optional[bytes] = None

    def _read(self, path: bytes) -> Optional[Reading]:
        """Ask the dongle for the mouse's telemetry and read the reply."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for _ in range(DRAIN_READS):   # clear a stale reply from the last poll
                r = dev.read(64, DRAIN_TIMEOUT_MS)
                if not r:
                    break
                self._diag.append(f"    drained stale: {hexdump(r, 8)}")
            try:
                n = dev.write(REQUEST)
            except (OSError, IOError) as e:
                self._diag.append(f"    write refused: {e}")
                return None
            if isinstance(n, int) and n < 0:
                self._diag.append("    Windows refused the 64-byte request "
                                  "(the collection did not take it)")
                return None
            for _ in range(READ_ATTEMPTS):
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue
                if len(r) > 1 and r[0] == MAGIC and r[1] == ACK:
                    self._diag.append("    ack")
                    continue
                reading = parse_telemetry(r)
                self._diag.append(f"    reply: {hexdump(r, 20)}"
                                  + ("" if reading else "  (not the telemetry shape)"))
                if reading is not None:
                    return reading
                time.sleep(0.02)
            self._diag.append("    no telemetry reply in ~1.5 s "
                              "(mouse off, or the request never reached it)")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    read error: {e}")
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
            infos = hidlist.enumerate(NZXT_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(nzxt): %s", e)
            return []
        out: List[DeviceStatus] = []
        for pid, name in PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            key = f"nzxt:{pid:04x}"
            self._diag.append(f"[NZXT] pid={pid:04x} '{name}' "
                              f"'{str(mine[0].get('product_string') or '').strip()}'")
            order = candidates(mine)
            if not order:
                self._diag.append("  no ffca:0001 collection on the dongle - not asked")
                continue
            if self._chosen is not None:
                order = sorted(order, key=lambda d: d["path"] != self._chosen)
            got = None
            for d in order[:MAX_CANDIDATES]:
                self._diag.append(f"  asking on iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                got = self._read(d["path"])
                if got is not None:
                    self._chosen = d["path"]
                    break
            if got is not None:
                level, mv = got
                self._last = (level, mv, time.time())
                self._diag.append(f"  {level} % (cell {mv} mV); the charging state "
                                  f"was not in the capture, so it is not shown")
                out.append(DeviceStatus(key, name, level, False, True, "nzxt",
                                        kind="mouse"))
                continue
            if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
                self._diag.append("  no reply; keeping the last level, greyed out")
                out.append(DeviceStatus(key, name, self._last[0], False, False,
                                        "nzxt", kind="mouse"))
            else:
                self._diag.append("  no reply yet and no earlier level")
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
