"""Ninjutso Sora V3 on its 2.4 GHz receiver (093a:eb02), #462.

The Sora V3 uses the current NinjaForce WebHID protocol, not the older Sora V2
report-5 protocol.  OpenMouse's Ninjutso implementation (derived from the
NinjaForce client) documents the current frame:

    feature report id 6, 15-byte payload
    request  <cmd> 00 00 01 00 <len> 00 <args...>
    reply    06 <cmd> 00 00 01 00 <len> 00 <data...>

Battery level is command 0x12 and charging is a separate command 0x11; both
return their value in the first data byte (reply byte 8).  The receiver is
2dc8-free: 093a:eb02 with the vendor collection on usage page 0xFF01 (0xFF00
is the secure/control collection).  This is a read-only query pair; nothing is
written or saved.

The source is OpenMouse-Project/mouse-protocol, src/ninjutso and
src/drivers/ninjutso, which credits NinjaForce's shipped WebHID client.  It is
not marked hardware-verified there, so this build is test build 1.
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

VID = 0x093A
RECEIVER_PID = 0xEB02
PAGE_MAIN = 0xFF01
PAGE_SECURE = 0xFF00
REPORT_ID = 0x06
PAYLOAD_LEN = 15
CMD_CHARGING = 0x11
CMD_BATTERY = 0x12

NAME = "Ninjutso Sora V3"
KEY = "ninjutso_sora_v3:eb02"

ATTEMPTS = 4
FIRST_DELAY = 0.03
RETRY_DELAY = 0.06
ASLEEP_KEEP = 300               # s, as in the other receiver providers

Reading = Tuple[int, bool, bool]  # level, charging, charging_known


def make_request(command: int) -> List[int]:
    """The 15-byte current-protocol read request, without the report id."""
    pkt = [0] * PAYLOAD_LEN
    pkt[0] = command
    pkt[3] = 0x01               # read
    pkt[5] = 0x00               # length/index
    return pkt


def parse_value(reply, command: int) -> Optional[int]:
    """The first data byte of a reply, or None if it is not this command.

    hidapi returns the report id as byte 0, so a normal reply is
    06 <cmd> ... <len> 00 <data>.  The raw form (no report id) is accepted
    too, because some hidapi builds strip it when the report is opened by
    report id rather than by path.
    """
    if not reply:
        return None
    r = list(reply)
    if len(r) >= 9 and r[0] == REPORT_ID and r[1] == command:
        return r[8]
    if len(r) >= 8 and r[0] == command:
        return r[7]
    return None


def _candidate_paths(infos: List[dict]) -> List[dict]:
    """The receiver's main and secure collections, main first."""
    out = []
    for page in (PAGE_MAIN, PAGE_SECURE):
        for d in infos:
            if (d.get("product_id") == RECEIVER_PID
                    and d.get("usage_page") == page
                    and d.get("usage") == 0x0001):
                out.append(d)
    return out


class NinjutsoSoraV3Provider(Provider):
    name = "ninjutso_sora_v3"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(093a): %s", e)
            return []
        paths = _candidate_paths(infos)
        if not paths:
            return []

        got = None
        for d in paths:
            self._diag.append(f"[Ninjutso Sora V3] pid=093a:{d.get('product_id'):04x} "
                              f"'{d.get('product_string') or ''}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page'):04x}:{d.get('usage'):04x}")
            got = self._read(d["path"])
            if got is not None:
                break
            self._diag.append("  no answer; trying the other collection")

        if got is None:
            self._diag.append("  no answer (mouse asleep, off, or out of range)")
            if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
                level, charging, _ = self._last
                return [DeviceStatus(KEY, NAME, level, charging, False,
                                     "ninjutso_sora_v3", kind="mouse",
                                     wake_on_input=True, charging_known=False)]
            return [DeviceStatus(KEY, NAME, None, False, False,
                                 "ninjutso_sora_v3", kind="mouse",
                                 wake_on_input=True, charging_known=False)]

        level, charging, charging_known = got
        self._diag.append(f"  -> {level}%"
                          f"{' (charging)' if charging else ''}"
                          f"{'' if charging_known else ' (charger unknown)'}")
        self._last = (level, charging, time.time())
        return [DeviceStatus(KEY, NAME, level, charging, True,
                             "ninjutso_sora_v3", kind="mouse",
                             wake_on_input=True, charging_known=charging_known)]

    def _read(self, path) -> Optional[Reading]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            level = self._query(dev, CMD_BATTERY)
            if level is None:
                return None
            if not 0 <= level <= 100:
                self._diag.append(f"    battery byte out of range: {level}")
                return None
            charge = self._query(dev, CMD_CHARGING)
            if charge is None:
                return level, False, False
            return level, bool(charge), True
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _query(self, dev, command: int) -> Optional[int]:
        packet = [REPORT_ID] + make_request(command)
        for attempt in range(ATTEMPTS):
            try:
                sent = dev.send_feature_report(packet)
                if sent is not None and sent < 0:
                    raise OSError("send_feature_report returned -1")
            except (OSError, ValueError) as e:
                self._diag.append(f"    cmd 0x{command:02x} send: {e}")
                return None
            time.sleep(FIRST_DELAY if attempt == 0 else RETRY_DELAY)
            try:
                reply = dev.get_feature_report(REPORT_ID, 16)
            except (OSError, ValueError) as e:
                self._diag.append(f"    cmd 0x{command:02x} read: {e}")
                continue
            if not reply:
                continue
            self._diag.append(f"    cmd 0x{command:02x} reply: {hexdump(reply, 16)}")
            value = parse_value(reply, command)
            if value is not None:
                return value
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
