"""Inphic In9 Pro (shown as 'Inphic KP 8K') over its 2.4 GHz dongle, without INPHIC HUB.

The mouse announces its level and charging state by itself - nothing is ever
written to it. The frames were decoded from the vendor's own Windows app
(INPHIC HUB, the driver the reporter linked in #160): the app enumerates
1d57:fa65 with hidapi, opens the collection whose usage page is 0x000A and
usage 0x0000 (next to the vendor page ff00:0001, which it keeps for its
writes) and reads its reports there. On Windows hidapi hands the report id
back first, which is why the app's parse starts one byte in:

    03 95 40 01 4b 00 ...
    |  |  |  |  +-- level 0..100 (0x4b = 75) - on the 0x01 frames only
    |  |  |  +----- 0x01 level, 0x02 full (the app shows 100 %), 0x03 charging
    |  |  +-------- the battery command (0x40)
    |  +----------- model code among 0x95 / 0x90 / 0x93 / 0x99
    +-------------- report id

On an 0x03 frame the app starts a 30 ms breathing animation over its battery
display and keeps the last level; 0x02 stops the animation and shows 100 %;
every other sub-command stores the level byte (1..100) and stops the
animation. This file mirrors that mapping. A level outside 1..100 is
refused, never shown - a wrong 0 % is worse than no icon.

The dongle also carries the mouse and keyboard collections; those are left
to Windows. The status collections are tried in turn - the vendor app's
000a:0000 first - and the one that delivers frames is remembered. The frames
come by themselves every couple of seconds, so a poll that misses one keeps
the last level: the icon stays lit for a while, then greys out, then goes
away - a level is never invented.

The same `40` command byte and level position appear in the earlier
recordings of this chip family's receivers (the `03 55 40 01 4b` frame in
the notes behind #69), so the family shapes agree with the vendor app's
parse. Only 1d57:fa65 is claimed here - the dongle the diagnostics came from.

The support is **confirmed on hardware**: the reporter's run of the test build
(#160) shows the level, and the charging state too: the reporter watched the
ring turn green while charging and return to normal after unplugging.

Two generations of this ODM family share the fa60 receiver, with different
status shapes, so both live here behind the model bytes proven on the units:

- the `0x55` shape - `03 55 40 <sub> <level>`, the sub-commands of the Inphic
  row - is the Attack Shark X11: caught by its reporter's probe in #163
  announcing `03 55 40 01 1f` (31 %) on the same `000a:0000` collection,
  read-only, and fully confirmed in his runs - level and charging state both
  (his diagnostics caught the `40 03` frame on the cable).
- the `0x10` shape - `03 10 40 <stage> <level/10>` - is the X6/R1 generation:
  decoded in blak0p's attack-shark-linux protocol documents (validated live
  on the dongle: idle `03 10 40 01 0a` = 100 %) and caught on the R1's own
  receiver by its reporter's probe in #69 (`03 10 40 01 09` = 90 %). The
  level arrives in steps of ten and the frames carry no charging state; the
  dongle's config ACK (`03 10 50 ...`) and DPI-button (`03 10 10 ...`)
  reports are excluded by the `0x40` command check.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

try:
    import hid
except ImportError:                  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x1D57
# The ODM family's receivers, each with the model codes proven on it: the Inphic
# codes are INPHIC HUB's own accept list (#160); 0x55 is the Attack Shark X11
# code its reporter's probe caught announcing 31 % (#163); 0x10 is the X6/R1
# generation's status event - its reporter's probe caught `03 10 40 01 09`
# (90 %) on the R1's own receiver (#69), the shape blak0p's X6 protocol
# documents validated live on the dongle.
PIDS = {
    0xFA65: ("Inphic In9 Pro", (0x95, 0x90, 0x93, 0x99)),
    0xFA60: ("Attack Shark X11 / R1", (0x55, 0x10)),
}

REPORT_ID = 0x03                    # hidapi hands it back first on Windows
CMD_BATTERY = 0x40
SUB_FULL = 0x02                     # charge complete: the app shows 100 %
SUB_CHARGING = 0x03                 # on the cable: the app runs its animation
X6_EVENT = 0x10                     # the X6/R1 generation's status event; its
                                    # heartbeat level arrives in X6_STEP steps
X6_STEP = 10
LEVEL_MIN, LEVEL_MAX = 1, 100

READ_SIZE = 100                     # the vendor app's read buffer
READ_TIMEOUT_MS = 250
READ_ATTEMPTS = 5                   # ~1.2 s on the remembered collection
SWEEP_ATTEMPTS = 2                  # ... and per collection while looking
MAX_CANDIDATES = 4
ONLINE_FRESH = 90                   # s: how long one observed frame keeps the
                                    # icon lit even if a poll misses the next one
ASLEEP_KEEP = 300                   # s: then greyed, then gone
RESWEEP_AFTER = 600                 # s: a known collection quiet this long gets the
                                    # other candidates swept again - at most once
                                    # per window, so an off mouse does not sweep
                                    # every poll (#181 review, @ahmedkhursheed23)

STATUS_USAGE = (0x000A, 0x0000)     # where INPHIC HUB reads the frames
VENDOR_USAGE = (0xFF00, 0x0001)     # its second handle (writes); tried second
MOUSE_USAGE = (0x0001, 0x0002)      # never opened: the OS owns the pointer stream
KEYBOARD_USAGE = (0x0001, 0x0006)   # the keyboard collections are left to it too


def parse_frame(frame, models) -> Optional[Tuple[Optional[int], bool]]:
    """(level, charging) from a device report, or None when it is not one.

    models: the model codes proven for the receiver at hand - each receiver of
    the family only reads the codes seen on it.
    level is None when the frame only carries the charging state - the level
    from the last full report stays on screen, as in the vendor app.
    """
    if not frame:
        return None
    f = list(frame)
    if f[0] == REPORT_ID:            # hidapi hands the report id back
        f = f[1:]
    if len(f) < 4:
        return None
    if f[0] not in models or f[1] != CMD_BATTERY:
        return None
    if f[0] == X6_EVENT:
        # The X6/R1 generation (blak0p's protocol documents, live-validated:
        # idle `03 10 40 01 0a` = 100 %; the R1's own push in #69): the
        # heartbeat's level arrives in steps of ten, and its byte 3 is the
        # DPI stage, not a sub-command - and there is no charging state in
        # this generation's frames. The dongle's ACK (`... 50 ..`) and the
        # DPI-button report (`... 10 ..`) share the shape and are already
        # refused by the command check above.
        level = f[3] * X6_STEP
        if LEVEL_MIN <= level <= LEVEL_MAX:
            return level, False
        return None
    if f[2] == SUB_CHARGING:
        return None, True
    if f[2] == SUB_FULL:
        return 100, True
    level = f[3]
    if LEVEL_MIN <= level <= LEVEL_MAX:
        return level, False
    return None


def candidates(ifaces: List[dict]) -> List[dict]:
    """The collections to try, best first: the vendor app's status collection,
    its vendor page, then the rest. The mouse and keyboard collections are
    never opened - the OS owns those streams."""
    def rank(d: dict) -> tuple:
        usage = (d.get("usage_page", 0), d.get("usage", 0))
        if usage == STATUS_USAGE:
            return (0,)
        if usage == VENDOR_USAGE:
            return (1,)
        return (2,)

    seen = set()
    out = []
    for d in ifaces:
        if (d.get("usage_page"), d.get("usage")) in (MOUSE_USAGE, KEYBOARD_USAGE):
            continue
        key = d.get("path")
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return sorted(out, key=rank)


class InphicProvider(Provider):
    name = "inphic"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[int, Tuple[Optional[int], float]] = {}    # pid -> (level, when)
        self._charging: Dict[int, bool] = {}
        self._chosen: Dict[int, bytes] = {}                        # pid -> collection path
        self._swept: Dict[int, float] = {}                         # pid -> last sweep

    def _listen(self, d: dict, attempts: int,
                models: Tuple[int, ...]) -> Optional[Tuple[Optional[int], bool]]:
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for _ in range(attempts):
                r = dev.read(READ_SIZE, READ_TIMEOUT_MS)
                if not r:
                    continue             # the frames come by themselves
                got = parse_frame(r, models)
                self._diag.append(f"    report: {hexdump(r, 20)}"
                                  + (f"  -> level {got[0]}, "
                                     f"{'charging' if got[1] else 'on battery'}" if got
                                     else "  (not a battery frame)"))
                if got is not None:
                    return got
            self._diag.append("    no frame in the window "
                              "(the mouse announces every couple of seconds)")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    read error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _poll_pid(self, pid: int, name: str, models: Tuple[int, ...],
                  mine: List[dict]) -> Optional[DeviceStatus]:
        key = f"inphic:{pid:04x}"
        self._diag.append(f"[Inphic] pid={pid:04x} '{name}' "
                          f"product='{(mine[0].get('product_string') or '').strip()}'")

        order = candidates(mine)
        chosen = self._chosen.get(pid)
        known = chosen is not None and any(d["path"] == chosen for d in order)
        last = self._last.get(pid)
        now = time.time()
        since_heard = now - last[1] if last else None
        since_sweep = now - self._swept.get(pid, 0.0)
        # A known collection is listened to alone; the others are swept again only
        # while there is none, when the known one has left the enumeration, or once
        # the known one has been quiet for RESWEEP_AFTER - not on every poll, or a
        # mouse that is off overnight would sweep every collection each time
        # (review by @ahmedkhursheed23).
        if chosen is None:
            sweep = True
        elif not known:
            sweep = since_sweep >= RESWEEP_AFTER
        else:
            sweep = (since_heard is not None and since_heard >= RESWEEP_AFTER
                     and since_sweep >= RESWEEP_AFTER)
        if sweep:
            self._swept[pid] = now
        if chosen is not None:
            order = sorted(order, key=lambda d: d["path"] != chosen)
        got = None
        for d in order[:MAX_CANDIDATES]:
            is_chosen = d["path"] == chosen
            if not sweep and not is_chosen:
                break                 # the known collection alone is enough
            # The vendor app's own collection gets the full window while looking
            # for it; the rest only get a short listen before moving on.
            patience = (d.get("usage_page"), d.get("usage")) == STATUS_USAGE
            attempts = READ_ATTEMPTS if is_chosen or patience else SWEEP_ATTEMPTS
            self._diag.append(f"  listening on iface={d.get('interface_number')} "
                              f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            got = self._listen(d, attempts, models)
            if got is not None:
                if chosen != d["path"]:
                    self._chosen[pid] = d["path"]
                break

        if got is not None:
            level, charging = got
            ts = time.time()
            if level is not None:
                self._last[pid] = (level, ts)
            elif last is not None:
                self._last[pid] = (last[0], ts)       # a frame with no level: heartbeat
            else:
                self._last[pid] = (None, ts)          # charging before any level was seen
            self._charging[pid] = charging
            return DeviceStatus(key, name, self._last[pid][0], charging, True,
                                "inphic", kind="mouse")

        # No frame in the window. The pushes come every couple of seconds, so a
        # single miss says nothing yet: the last level stays lit for a while,
        # then greys out, then the icon is hidden until the next frame.
        if last is not None:
            age = time.time() - last[1]
            if age < ASLEEP_KEEP:
                return DeviceStatus(key, name, last[0], self._charging.get(pid, False),
                                    age < ONLINE_FRESH, "inphic", kind="mouse")
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(inphic): %s", e)
            return []
        out = []
        for pid, (name, models) in PIDS.items():
            mine = [d for d in infos if d.get("product_id") == pid]
            if not mine:
                continue
            st = self._poll_pid(pid, name, models, mine)
            if st is not None:
                out.append(st)
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
