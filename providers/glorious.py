"""Glorious Model O / O- / D / D- Wireless over their 2.4 GHz receivers (#281, #221).

The current models (258a:2022 Model O, 2024 Model O-, 2023 Model D, 2025 Model D-)
publish vendor collections on `ffa0` and `ffff`; the battery is read with
Sinowealth's framed feature-report query, documented and confirmed on real
hardware by Rodrigo-200/GloriousBatteryMonitor-Go (`HidDeviceService.cs`), whose
comment credits AwesomeTy18/GloriousBatteryMonitor,
glorious-mouse-battery-system-tray, glorious-indicator and OpenRGB's
SinowealthGMOWController. The same query, with the same DevSel, is what
korkje/mxw sends to all four models (src/report/battery.rs, `bfr_w[3]=0x02`,
`bfr_w[4]=0x02`, `bfr_w[6]=0x83`, level in `bfr_r[8]`), opening interface 2.

    request  (64-byte feature report, report id 0):
        00 | 00 00 02 02 00 83 | 00 ...
        index 1-2 reserved, DevSel 0x02 = wireless mouse via the dongle,
        CmdType 0x02 = battery query, Func 0x83 = battery status
    reply:
        [0] report id, [1] status (A1 awake, A2 waking, A4 asleep),
        [2..5] echo, [6] 0x83, [7] charging (0 = no, non-zero = on the charger),
        [8] level %

Response validation is cross-checked against korkje/mxw commit
5b517db12b48dcbeaac9588dea73797c12bdf02b, src/report/battery.rs:26-50:
only status A1 is a live percentage. A4 is asleep; provisional/unknown statuses
must be retried, not recorded as zero. Offsets are also documented in
Rodrigo-200/GloriousBatteryMonitor-Go commit
4271ea7acee99d2569a1b97b27272fb8510e6f55,
src/GBM.Core/Services/HidDeviceService.cs:278-302.

The receiver needs ~100 ms between the write and the read: the query goes over the 2.4 GHz
link to the mouse and back. Report id 0x00 is the one all known Glorious wireless mice
answer on; other ids are only tried if Windows says the collection takes a different
length.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .gwolves import feature_length

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x258A
# wireless pid -> (name, wired pid -> wired name). From mxw's SUPPORTED_MICE and
# GloriousBatteryMonitor-Go's DeviceDatabase.cs. All four answer the same query.
MODELS: Dict[int, Tuple[str, Dict[int, str]]] = {
    0x2022: ("Glorious Model O Wireless", {0x2011: "Model O"}),
    0x2024: ("Glorious Model O- Wireless", {0x2013: "Model O-"}),
    0x2023: ("Glorious Model D Wireless", {0x2012: "Model D"}),
    0x2025: ("Glorious Model D- Wireless", {0x2014: "Model D-"}),
}
PID = 0x2022                       # the confirmed model, kept for the tests and docs
NAME = MODELS[PID][0]
KEY = f"glorious:{PID:04x}"

BATTERY_COMMAND = [0x00, 0x00, 0x02, 0x02, 0x00, 0x83]
STATUS_ASLEEP = 0xA4
STATUS_AWAKE = 0xA1
FUNC_INDEX = 6
CHARGING_INDEX = 7
LEVEL_INDEX = 8

RF_DELAY_S = 0.10                  # the SetFeature -> RF -> GetFeature round trip
READ_ATTEMPTS = 2                  # a waking mouse answers 0xA2 first; retry once
ASLEEP_KEEP = 300

# Wired ids of the same model, from GloriousBatteryMonitor-Go's DeviceDatabase.cs. Charging
# over a USB cable takes the mouse off the 2.4 GHz link and it appears as the wired id
# instead - that project reads "on the charger" from the pid change, not from the reply
# ("Charging is detected via PID change ... not via byte[3]", HidDeviceService.cs), so the
# same signal is used here. 0x2011 is the wired Model O.
WIRED_PIDS = MODELS[PID][1]


def make_request(length: int) -> List[int]:
    """The captured query as a feature report of `length` bytes (id + body)."""
    frame = [0x00] * max(length, 1 + len(BATTERY_COMMAND))
    frame[0] = 0x00
    frame[1:1 + len(BATTERY_COMMAND)] = BATTERY_COMMAND
    return frame


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from the receiver's answer, or None when it is not one."""
    if not r or len(r) <= LEVEL_INDEX:
        return None
    if r[0] != 0x00 or r[FUNC_INDEX] != 0x83:
        return None
    # The outgoing request also has 0x83 at byte 6 and zero at byte 8.
    # Require the positive awake status, rather than guessing from the level.
    if r[1] != STATUS_AWAKE:
        return None
    level = r[LEVEL_INDEX]
    if not 0 <= level <= 100:
        return None
    return level, r[CHARGING_INDEX] != 0


def pick(infos: List[dict]) -> List[dict]:
    """Vendor collections, the ones Windows says can carry a 65-byte feature report first."""
    vendor = [d for d in infos if (d.get("usage_page") or 0) >= 0xFF00]
    with_len = [d for d in vendor if feature_length(d["path"]) in (None, 65)]
    return with_len + [d for d in vendor if d not in with_len]


class GloriousProvider(Provider):
    name = "glorious"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[int, Tuple[int, float]] = {}
        # True when a query got an answer that was not a battery frame: another app
        # (SignalRGB, #281) is writing to the same feature report and overwrote it.
        self._busy = False
        # True when the write itself was refused: another app holds the report.
        self._refused = False

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        self._busy = False
        self._refused = False
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(glorious): %s", e)
            return []
        out: List[DeviceStatus] = []
        for pid, (name, wired_map) in MODELS.items():
            mine = [d for d in infos if d.get("product_id") == pid]
            if not mine:
                continue
            key = f"glorious:{pid:04x}"
            self._diag.append(f"[Glorious] pid={pid:04x} '{name}'")
            wired = [d for d in infos if d.get("product_id") in wired_map]
            if wired:
                self._diag.append("  on the cable: " + ", ".join(
                    f"{d.get('product_id', 0):04x} ({wired_map.get(d.get('product_id') or 0, '?')})"
                    for d in wired))
            parsed = None
            for d in pick(mine):
                length = feature_length(d["path"]) or 65
                self._diag.append(f"  trying iface={d.get('interface_number')} "
                                  f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                                  f"feature={length}")
                parsed = self._ask(d["path"], length)
                if parsed is not None:
                    break
            last = self._last.get(pid)
            if parsed is None:
                if self._busy:
                    self._diag.append(
                        "  the receiver answered, but not with the battery frame: another "
                        "app (SignalRGB?) is writing to the same report and overwrote it")
                else:
                    self._diag.append("  no battery answer (asleep, off, or not this collection)")
                if wired:
                    # The mouse is on the cable: it is present and charging, but the
                    # receiver cannot say what the level is now, so keep the last one if
                    # there is one.
                    level = last[0] if last else None
                    out.append(DeviceStatus(key, name, level, True, True, "glorious",
                                            kind="mouse"))
                elif last and time.time() - last[1] < ASLEEP_KEEP:
                    out.append(DeviceStatus(key, name, last[0], False, False, "glorious",
                                            kind="mouse"))
                else:
                    out.append(DeviceStatus(key, name, None, False, False, "glorious",
                                            kind="mouse"))
                continue
            level, charging = parsed
            charging = charging or bool(wired)
            self._last[pid] = (level, time.time())
            self._diag.append(f"  -> {level}%{' charging' if charging else ''}")
            out.append(DeviceStatus(key, name, level, charging, True, "glorious",
                                    kind="mouse"))
        return out

    def _ask(self, path, length: int) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for _ in range(READ_ATTEMPTS):
                request = bytes(make_request(length))
                try:
                    sent = dev.send_feature_report(request)
                except (OSError, IOError) as e:
                    # SignalRGB, while it controls this mouse, holds the feature
                    # report: the write itself is refused, before any read (#281).
                    self._diag.append(f"    another app holds the feature report "
                                      f"(write refused: {e})")
                    self._refused = True
                    raise
                if sent is not None and sent != len(request):
                    self._diag.append(f"    feature write incomplete/refused ({sent})")
                    return None
                time.sleep(RF_DELAY_S)
                r = dev.get_feature_report(0x00, length)
                if r:
                    self._diag.append(f"    reply: {hexdump(r, 12)}")
                parsed = parse_reply(r)
                if parsed is not None:
                    return parsed
                if r:
                    self._busy = True
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    error: {e}")
            if self._refused:
                # Let the app mark the query unavailable rather than call the mouse
                # asleep: the last level stays, with the honest reason.
                raise
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
