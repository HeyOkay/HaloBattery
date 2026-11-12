"""HyperX Cloud Alpha Wireless over its 2.4 GHz dongle, without NGENUITY.

Two public sources agree on the bytes (see docs/protocols.md):

  * HeadsetControl, lib/devices/hyperx_cloud_alpha_wireless.hpp (d4fb352)
  * davidsantanaalves/HyperXBatteryMonitor, Devices/CloudAlphaWirelessDevice.cs (9d01847)

The exchange:

  * request: a 31-byte output report, 21 bb <selector> 00 ...   (0x21 is the report id)
  * reply:   an input report that echoes 21 bb <selector>, the answer in byte 3
      0x03  connection: byte 3 == 1 means the headset is not connected (HeadsetControl only)
      0x0C  charging:   byte 3 == 1 charging, 0 not charging (HyperXBatteryMonitor refuses
                        any other value)
      0x0B  level:      byte 3 is the percent (HyperXBatteryMonitor refuses a value above 100)
  * the dongle also sends unsolicited reports (0x25 level, 0x26 charging, 0x23 mute), so
    a reply is only accepted when it echoes the selector that was asked.
  * both sources write an output report; neither uses a feature report.
  * the collection is the vendor page 0xFF43 / usage 0x0202 of interface 3, the first
    collection there (HyperXBatteryMonitor opens ...&MI_03&Col01); the other collection
    on that interface is consumer control (000c:0001), which cannot carry report 0x21.

While the headset is charging HeadsetControl reports no level; HyperXBatteryMonitor reads
the level next to the charging state. This provider asks for the level either way and
shows it, with the charging flag.
"""
from __future__ import annotations

import time
from typing import List, Optional

try:
    import hid
except ImportError:              # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

HP_VID = 0x03F0
PID = 0x098D
NAME = "HyperX Cloud Alpha Wireless"

USAGE_PAGE = 0xFF43
USAGE = 0x0202

REPORT_ID = 0x21
MARKER = 0xBB
SEL_CONNECTION = 0x03
SEL_LEVEL = 0x0B
SEL_CHARGING = 0x0C

REQUEST_LEN = 31
READ_LEN = 64
READ_TIMEOUT_MS = 200
REPLY_DEADLINE_MS = 2000          # HeadsetControl waits up to 2 s for each reply

VALUE_INDEX = 3
NOT_CONNECTED = 1


def make_request(selector: int) -> List[int]:
    return [REPORT_ID, MARKER, selector] + [0x00] * (REQUEST_LEN - 3)


def is_reply(r, selector: int) -> bool:
    return bool(r) and len(r) > VALUE_INDEX and list(r[:3]) == [REPORT_ID, MARKER, selector]


def parse_connected(r) -> Optional[bool]:
    """True when the headset is connected to the dongle, None if r is not a reply."""
    if not is_reply(r, SEL_CONNECTION):
        return None
    return r[VALUE_INDEX] != NOT_CONNECTED


def parse_level(r) -> Optional[int]:
    if not is_reply(r, SEL_LEVEL):
        return None
    level = r[VALUE_INDEX]
    return level if 0 <= level <= 100 else None


def parse_charging(r) -> Optional[bool]:
    if not is_reply(r, SEL_CHARGING):
        return None
    state = r[VALUE_INDEX]
    return bool(state) if state in (0, 1) else None


class HyperXAlphaWirelessProvider(Provider):
    name = "hyperx_alpha_wireless"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        """The vendor collection by usage page/usage. Without it nothing is written: the
        other collection on that interface is consumer control."""
        for d in infos:
            if (d.get("usage_page"), d.get("usage")) == (USAGE_PAGE, USAGE):
                return d
        offered = ", ".join(f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}" for d in infos)
        self._diag.append(f"  no usage {USAGE_PAGE:04x}:{USAGE:04x} collection (found: {offered})")
        return None

    def _ask(self, dev, selector: int):
        """-> (sent, reply). hidapi returns -1 from write() on failure instead of raising,
        so a negative result is a failed send, not a silent headset."""
        try:
            n = dev.write(make_request(selector))
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  sel {selector:02x} write: {e}")
            return False, None
        if n is not None and n < 0:
            try:
                err = str(dev.error() or "")
            except Exception:
                err = ""
            self._diag.append(f"  sel {selector:02x} write -> {n} {err}".rstrip())
            return False, None
        deadline = time.monotonic() + REPLY_DEADLINE_MS / 1000.0
        while time.monotonic() < deadline:
            try:
                r = list(dev.read(READ_LEN, READ_TIMEOUT_MS) or [])
            except (OSError, IOError, ValueError) as e:
                self._diag.append(f"  sel {selector:02x} read: {e}")
                return True, None
            if not r:
                continue
            if is_reply(r, selector):
                self._diag.append(f"  sel {selector:02x} reply: {hexdump(r, 8)}")
                return True, r
            self._diag.append(f"  sel {selector:02x} ignored: {hexdump(r, 8)}")
        self._diag.append(f"  sel {selector:02x}: no reply")
        return True, None

    def _read(self, path: bytes):
        """-> None if the request could not be sent, else (connected, level, charging)."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            sent, r = self._ask(dev, SEL_CONNECTION)
            if not sent:
                return None
            if r is None or not parse_connected(r):
                return False, None, False          # headset off, or the dongle is silent
            _, r = self._ask(dev, SEL_CHARGING)
            charging = bool(parse_charging(r))
            _, r = self._ask(dev, SEL_LEVEL)
            return True, parse_level(r), charging
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
            infos = hidlist.enumerate(HP_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(hyperx_alpha_wireless): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        d = self._pick(mine)
        if d is None:
            return []
        self._diag.append(f"[HyperX] pid={PID:04x} '{NAME}' iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
        res = self._read(d["path"])
        if res is None:
            return []
        connected, level, charging = res
        key = f"hyperx:{PID:04x}"
        if not connected or (level is None and not charging):
            # The dongle is there but the headset is off or silent: grey icon, no level.
            return [DeviceStatus(key, NAME, None, False, False, "hyperx_alpha_wireless",
                                 kind="headset")]
        return [DeviceStatus(key, NAME, level, charging, True, "hyperx_alpha_wireless",
                             kind="headset")]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
