"""Glorious Model O Wireless over its 2.4 GHz receiver (#281).

The receiver (258a:2022) publishes vendor collections on `ffa0` and `ffff`; the battery is
read with Sinowealth's framed feature-report query, documented and confirmed on real
hardware by Rodrigo-200/GloriousBatteryMonitor-Go (`HidDeviceService.cs`), whose comment
credits AwesomeTy18/GloriousBatteryMonitor, glorious-mouse-battery-system-tray,
glorious-indicator and OpenRGB's SinowealthGMOWController:

    request  (64-byte feature report, report id 0):
        00 | 00 00 02 02 00 83 | 00 ...
        index 1-2 reserved, DevSel 0x02 = wireless mouse via the dongle,
        CmdType 0x02 = battery query, Func 0x83 = battery status
    reply:
        [0] report id, [1] status (A1 awake, A2 waking, A4 asleep),
        [2..5] echo, [6] 0x83, [7] charging (0 = no, non-zero = on the charger),
        [8] level %

The receiver needs ~100 ms between the write and the read: the query goes over the 2.4 GHz
link to the mouse and back. Report id 0x00 is the one all known Glorious wireless mice
answer on; other ids are only tried if Windows says the collection takes a different
length.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .gwolves import feature_length

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x258A
PID = 0x2022
NAME = "Glorious Model O Wireless"
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
WIRED_PIDS = {0x2011: "Model O"}


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
    if r[FUNC_INDEX] != 0x83:
        return None
    if r[1] == STATUS_ASLEEP:
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
        self._last: Optional[Tuple[int, float]] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(glorious): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        self._diag.append(f"[Glorious] pid={PID:04x} '{NAME}'")
        wired = [d for d in infos if d.get("product_id") in WIRED_PIDS]
        if wired:
            self._diag.append("  on the cable: " + ", ".join(
                f"{d.get('product_id', 0):04x} ({WIRED_PIDS.get(d.get('product_id'), '?')})"
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
        if parsed is None:
            self._diag.append("  no battery answer (asleep, off, or not this collection)")
            if wired:
                # The mouse is on the cable: it is present and charging, but the receiver
                # cannot say what the level is now, so keep the last one if there is one.
                level = self._last[0] if self._last else None
                return [DeviceStatus(KEY, NAME, level, True, True, "glorious", kind="mouse")]
            if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
                return [DeviceStatus(KEY, NAME, self._last[0], False, False, "glorious",
                                     kind="mouse")]
            return [DeviceStatus(KEY, NAME, None, False, False, "glorious", kind="mouse")]
        level, charging = parsed
        charging = charging or bool(wired)
        self._last = (level, time.time())
        self._diag.append(f"  -> {level}%{' charging' if charging else ''}")
        return [DeviceStatus(KEY, NAME, level, charging, True, "glorious", kind="mouse")]

    def _ask(self, path, length: int) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            for _ in range(READ_ATTEMPTS):
                sent = dev.send_feature_report(bytes(make_request(length)))
                if sent is not None and sent < 0:
                    self._diag.append(f"    feature write refused ({sent})")
                    return None
                time.sleep(RF_DELAY_S)
                r = dev.get_feature_report(0x00, length)
                if r:
                    self._diag.append(f"    reply: {hexdump(r, 12)}")
                parsed = parse_reply(r)
                if parsed is not None:
                    return parsed
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
