"""Rapoo gaming devices that report their battery on input report 7.

Rapoo's own web driver at hub.rapoo.com ("RAPOO HUB - VT Generation 2 Series
Web Driver") reads the battery of every device it supports without ever asking
for it: the device pushes a status report on input report 7 and the driver's
parsers decode it. All four of its protocol parsers (Realtek 2-in-1, Telink,
Telink base and VT nRF54L) share the same layout for that report:

    byte 0: connection type (low nibble: 0 = 2.4 GHz receiver, 1 = Bluetooth,
            2 = USB) and the sensor type (high nibble)
    byte 1: DPI gear
    byte 2-3: DPI X, byte 4-5: DPI Y
    byte 6: battery state - 0 invalid, 1 on battery, 2 charging
    byte 7: battery percent 0..100
    byte 8: Bluetooth mode, byte 9-10: report rates, byte 11: config

Nothing is ever written to the devices; the vendor collections are listened on
in turn and the one that answers is remembered, exactly like the 0xBB listener
for the 2023 receivers in issue #109 (nothing about which collection carries
the report is visible before hardware runs it).

Two devices are claimed, from issue #193's diagnostics - each over the two
ids it appears under, so one icon covers both of its states:

  * the VT7 (Gen-2) mouse: 24AE:1413 (its 2.4 GHz receiver; Rapoo's own device
    map points this receiver id at the "VT7" model) and 24AE:4613, the id the
    same mouse shows up under while it sits on its charging cable (seen in the
    reporter's 2026-10-03 run: 1413 gone from the tree, 4613 present, the
    byte-identical collection shape).
  * the V700DIY-98 keyboard: 24AE:4824 ("Rapoo Gaming Keyboard", the id from
    the first dump - the keyboard was plugged in for charging then) and
    24AE:1924 ("Rapoo 2.4G Wireless Device"), the id it presents over its own
    2.4 GHz receiver in the 2026-10-03 run.

The mouse is the same generation the web driver is named for, so the push is
expected there - charging included: both ids feed the same icon, and a reading
that reports charging wins over one that does not. The keyboard is claimed
from the family's convention - the same report-7 status carried by the Telink
keyboard protocols in the driver - so it is listened for and its frames are
logged raw; the 2026-10-03 runs never heard it (its receiver id was not
claimed then), so its answer is still open.

Unverified: neither device has answered this provider yet.
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

RAPOO_VID = 0x24AE
VT7_RECEIVER = 0x1413      # the mouse's 2.4 GHz receiver
VT7_CABLE = 0x4613         # ... and the same mouse on its charging cable
V700DIY_KEYBOARD = 0x4824  # the keyboard on its cable / plugged in
V700DIY_RECEIVER = 0x1924  # ... and its own 2.4 GHz receiver
# pid -> (name, kind, the id its icon is keyed on): both states of one device
# share an icon, and a reading that reports charging wins in the merge below.
PIDS = {
    VT7_RECEIVER: ("Rapoo VT7 (Gen-2)", "mouse", VT7_RECEIVER),
    VT7_CABLE: ("Rapoo VT7 (Gen-2)", "mouse", VT7_RECEIVER),
    V700DIY_RECEIVER: ("Rapoo V700DIY-98", "keyboard", V700DIY_KEYBOARD),
    V700DIY_KEYBOARD: ("Rapoo V700DIY-98", "keyboard", V700DIY_KEYBOARD),
}

VENDOR_PAGE = 0xFF00
PREFERRED_USAGES = (0x0002, 0x000E)   # the vendor collections every dump shows first
MOUSE_USAGE = (0x0001, 0x0002)        # never opened: the OS owns the pointer stream
KEYBOARD_USAGE = (0x0001, 0x0006)     # never opened: the OS owns the key stream

REPORT_ID = 0x07
STATUS_INDEX = 7                      # buffer indexes: byte 0 is the report id
LEVEL_INDEX = 8
INVALID = 0x00
ON_BATTERY = 0x01
CHARGING = 0x02

READ_ATTEMPTS = 12                    # ~3 s per collection: the push comes every ~1-3 s
READ_TIMEOUT_MS = 250
MAX_CANDIDATES = 3                    # the dumps show this many vendor collections and up
QUIET_RESCAN_EVERY = 5                # a silent device is fully re-scanned occasionally
ASLEEP_KEEP = 300                     # s, as in the other receiver providers

Reading = Tuple[int, bool]


def parse_report(r) -> Optional[Reading]:
    """(level, charging) from a report-7 status push, or None when it is not one."""
    if not r or len(r) <= LEVEL_INDEX:
        return None
    if r[0] != REPORT_ID:
        return None
    status = r[STATUS_INDEX]
    if status not in (ON_BATTERY, CHARGING):
        return None                   # 0 = the device calls the reading invalid; not shown
    level = r[LEVEL_INDEX]
    if not 0 <= level <= 100:
        return None
    return level, status == CHARGING


def candidates(ifaces: List[dict]) -> List[dict]:
    """The collections to listen on, best first: vendor page, then the rest.

    Report 7 rides one of the device's vendor collections, but which of them
    Windows hands it out on is not known before a run, so every collection is
    a candidate except the plain mouse and keyboard ones (the OS owns those).
    The same usage listed twice is one collection; the vendor usages are kept
    in a fixed order.
    """
    def rank(d: dict) -> tuple:
        page = d.get("usage_page", 0)
        usage = d.get("usage", 0)
        if page == VENDOR_PAGE and usage in PREFERRED_USAGES:
            return (0, PREFERRED_USAGES.index(usage))
        if page == VENDOR_PAGE:
            return (1, 0)
        return (2, 0)

    seen = set()
    out = []
    for d in ifaces:
        if (d.get("usage_page"), d.get("usage")) in (MOUSE_USAGE, KEYBOARD_USAGE):
            continue
        key = (d.get("usage_page"), d.get("usage"))
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return sorted(out, key=rank)


class RapooProvider(Provider):
    name = "rapoo"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[int, bool, float]] = {}
        self._chosen: Dict[int, bytes] = {}
        self._quiet: Dict[int, int] = {}
        self._meta: Dict[str, Tuple[str, str]] = {}            # key -> (name, kind)

    def _listen(self, path: bytes) -> Optional[Reading]:
        """Read what the device pushes; nothing is ever sent."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for _ in range(READ_ATTEMPTS):
                r = dev.read(64, READ_TIMEOUT_MS)
                if not r:
                    continue              # quiet until the next push
                reading = parse_report(r)
                self._diag.append(f"    report: {hexdump(r, 12)}"
                                  + ("" if reading else "  (not the report-7 battery shape)"))
                if reading is not None:
                    return reading
                time.sleep(0.02)
            self._diag.append("    nothing in ~3 s (device off, or another collection)")
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
            infos = hidlist.enumerate(RAPOO_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(rapoo): %s", e)
            return []
        found: Dict[str, List[Tuple[int, bool, str, str]]] = {}   # key -> readings
        for pid, (name, kind, logical) in PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            key = f"rapoo:{logical:04x}"
            self._meta[key] = (name, kind)
            self._diag.append(f"[Rapoo] pid={pid:04x} '{name}' "
                              f"'{(mine[0].get('product_string') or '').strip()}'")
            order = candidates(mine)
            if self._chosen.get(pid):
                order = sorted(order, key=lambda d: d["path"] != self._chosen[pid])
            # a device that has stayed silent is listened on its first candidate
            # only, and fully re-scanned every few polls in case it moved
            quiet = self._quiet.get(pid, 0)
            deep = self._chosen.get(pid) is None and (
                quiet == 0 or quiet % QUIET_RESCAN_EVERY == 0)
            budget = order[:MAX_CANDIDATES] if deep else order[:1]
            got = None
            for d in budget:
                self._diag.append(f"  listening on iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
                got = self._listen(d["path"])
                if got is not None:
                    self._chosen[pid] = d["path"]
                    break
            if got is not None:
                self._quiet[pid] = 0
                found.setdefault(key, []).append((got[0], got[1], name, kind))
                continue
            self._quiet[pid] = quiet + 1
            self._diag.append("  nothing heard; keeping the last level, greyed out"
                              if self._last.get(key) else
                              "  nothing heard yet and no earlier level")

        out: List[DeviceStatus] = []
        for key, readings in found.items():
            # one device over two ids: a reading that reports charging wins
            # over one that does not (a charging cable beats a stale receiver)
            readings.sort(key=lambda r: (not r[1],))
            level, charging, name, kind = readings[0]
            self._last[key] = (level, charging, time.time())
            out.append(DeviceStatus(key, name, level, charging, True, "rapoo",
                                    kind=kind))
        # silent: a receiver cannot tell a switched-off mouse from one that went
        # to sleep a few seconds ago, so keep the last value greyed out for a while
        now = time.time()
        for key, last in self._last.items():
            if key in found or now - last[2] >= ASLEEP_KEEP:
                continue
            name, kind = self._meta.get(key, ("Rapoo device", "mouse"))
            out.append(DeviceStatus(key, name, last[0], last[1], False, "rapoo",
                                    kind=kind))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
