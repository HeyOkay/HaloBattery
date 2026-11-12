"""HyperX Pulsefire mice on the NGENUITY "universal" HID protocol (#304).

The protocol is PMarinov1994/ngenuity-linux-cli's, reverse engineered from NGENUITY
3.1.0 (hxn-srv-communication-hub.exe) and confirmed on a Pulsefire Haste 2 Wireless;
only the protocol facts are used here:

  * the dongle's vendor interface, USB interface 2, usage page 0xFF00, 64-byte packets:
    [report id][63 bytes], and the report id is the command byte
  * the battery query is `50 02` (WirelessRFBatteryInformation); the answer arrives as
    input report `51 02` (id = command + 1, byte 1 = sub):
        [2]    level, percent 0..100
        [3]    charge status: 0 not charging, 1 charging, 2 full, 3 charger error,
               0xFF unknown
        [4..5] temperature, i16 LE (degrees C)
        [6..7] voltage, u16 LE (mV)
        [8]    gauge chip type
        [9]    charger chip type
  * `52 01` asks the RF link state and answers `53 01 <connected>` (1 = connected,
    0 = asleep or off)
  * NGENUITY waits at least 10 ms between packets

The reporter's dongle of #304 enumerates as 03f0:0d97 with exactly that vendor
collection on interface 2; the reference project's own dongle is 03f0:0f98, and 0x0d97
appears in the device-information reply as one of the device's product ids, so both are
claimed. Unverified on this app's side until a reporter runs it.

The mouse sleeps when idle, so a silent poll keeps the last reading greyed for a while,
the way the Finalmouse provider does; the level itself is the mouse's own gauge, not a
voltage curve.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

HYPERX_VID = 0x03F0
PIDS = {
    0x0D97: "HyperX Pulsefire Haste 2 Wireless",
    0x0F98: "HyperX Pulsefire Haste 2 Wireless",
}
VENDOR_USAGE_PAGE = 0xFF00
VENDOR_USAGE = 0x0001

CMD_BATTERY, SUB_BATTERY = 0x50, 0x02
CMD_LINK, SUB_LINK = 0x52, 0x01
REPLY_BATTERY = 0x51
REPLY_LINK = 0x53

REPORT_LEN = 64            # including the report id
PACE_S = 0.010             # NGENUITY waits at least this long between packets
TIMEOUT_S = 0.4            # the dongle has to reach the mouse over RF first

# how long a silent mouse keeps its (greyed-out) icon before it is hidden, s
ASLEEP_KEEP = 300

# charge status byte -> (charging, charging_known)
CHARGE = {
    0x00: (False, True),
    0x01: (True, True),
    0x02: (True, True),      # full: still on the charger
    0x03: (False, False),    # charger error: no usable charging state
    0xFF: (False, False),    # unknown
}


def request(cmd: int, sub: int) -> List[int]:
    """One 64-byte packet: report id = command, then the sub-command, zero padded."""
    return [cmd, sub] + [0x00] * (REPORT_LEN - 2)


def parse_battery(r) -> Optional[Tuple[int, bool, bool, int, Optional[int]]]:
    """(level, charging, charging_known, mV, temperature C) from a `51 02` reply."""
    r = list(r or [])
    if len(r) < 10 or r[0] != REPLY_BATTERY or r[1] != SUB_BATTERY:
        return None
    level = r[2]
    if not 0 <= level <= 100:
        return None
    charging, known = CHARGE.get(r[3], (False, False))
    mv = r[6] | (r[7] << 8)
    temp = int.from_bytes(bytes(r[4:6]), "little", signed=True)
    return level, charging, known, mv, temp


def parse_link(r) -> Optional[bool]:
    r = list(r or [])
    if len(r) < 3 or r[0] != REPLY_LINK or r[1] != SUB_LINK:
        return None
    return r[2] != 0


class HyperXNgenuityProvider(Provider):
    name = "hyperx_ngenuity"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Dict[str, Tuple[int, bool, bool, float]] = {}

    def _ask(self, dev, cmd: int, sub: int, reply_id: int) -> Optional[List[int]]:
        """Send one packet and return the first reply with this id and sub, or None."""
        try:
            dev.write(request(cmd, sub))
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  cmd {cmd:02x} {sub:02x}: write: {e}")
            return None
        time.sleep(PACE_S)
        end = time.time() + TIMEOUT_S
        while time.time() < end:
            r = dev.read(REPORT_LEN, 50)
            r = list(r or [])
            if not r:
                continue
            if r[0] == reply_id and len(r) > 1 and r[1] == sub:
                self._diag.append(f"  reply: {hexdump(r, 10)}")
                return r
        self._diag.append(f"  cmd {cmd:02x} {sub:02x}: no reply"
                          " (the mouse is asleep, off, or out of range)")
        return None

    def _read(self, path: bytes):
        """-> (level, charging, charging_known, linked) - level None when unreadable."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None, False, True, None
        try:
            link = self._ask(dev, CMD_LINK, SUB_LINK, REPLY_LINK)
            linked = parse_link(link) if link is not None else None
            if linked is False:
                return None, False, True, False
            reply = self._ask(dev, CMD_BATTERY, SUB_BATTERY, REPLY_BATTERY)
            parsed = parse_battery(reply) if reply is not None else None
            if parsed is None:
                return None, False, True, linked
            level, charging, known, mv, temp = parsed
            if mv:
                self._diag.append(f"  {mv} mV" + (f", {temp} C" if temp else ""))
            return level, charging, known, True
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  error: {e}")
            return None, False, True, None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(HYPERX_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(hyperx): %s", e)
            return []
        out: List[DeviceStatus] = []
        present = set()
        for pid, name in PIDS.items():
            mine = [d for d in infos if d["product_id"] == pid]
            if not mine:
                continue
            vendor = sorted((d for d in mine if d.get("usage_page") == VENDOR_USAGE_PAGE),
                            key=lambda d: (d.get("interface_number") != 2, d.get("usage") != VENDOR_USAGE))
            key = f"hyperx_ngenuity:{pid:04x}"
            present.add(key)
            if not vendor:
                offered = ", ".join(f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                                    for d in mine)
                self._diag.append(f"[HyperX] pid={pid:04x}: no usage page "
                                  f"{VENDOR_USAGE_PAGE:04x} collection (found: {offered})")
                continue
            d = vendor[0]
            self._diag.append(f"[HyperX] pid={pid:04x} '{name}' iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            level, charging, known, linked = self._read(d["path"])
            if level is not None:
                self._last[key] = (level, charging, known, time.time())
                out.append(DeviceStatus(key, name, level, charging, True, "hyperx_ngenuity",
                                        kind="mouse", charging_known=known))
                continue
            if linked is False:
                self._diag.append("  the mouse is not connected to the dongle (off or asleep)")
            last = self._last.get(key)
            if last and time.time() - last[3] < ASLEEP_KEEP:
                out.append(DeviceStatus(key, name, last[0], last[1], False, "hyperx_ngenuity",
                                        kind="mouse", charging_known=last[2],
                                        wake_on_input=True))
        # a dongle that is gone drops its device (and its kept reading)
        for key in list(self._last):
            if key not in present:
                del self._last[key]
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
