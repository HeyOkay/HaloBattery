"""Jabra headsets behind a Jabra Link 380 Bluetooth dongle, without Jabra Direct.

Protocol from Watchdog0x/jabridge (Apache-2.0), cmd/jabridge/jabraApi.go - the GN
Protocol ("GNP") as that project recovered it from Jabra's own libjabra, and read
there through Link 380 (0b0e:24c7 / 24c8) and Link 390 (0b0e:2e57) dongles:

  * the dongle's vendor collection, usage page 0xFF00 / usage 0x0001; report id
    0x05 in both directions, 63 data bytes.
  * a request is   05 <dst> 00 <seq> <0x40 | len> <class> <op> [payload]
    and its reply  05 00 <dst> <seq> <0xC0 | len> <class> <op> [payload]
    where len counts the six header bytes after the report id plus the payload.
    dst 0x04 is the headset connected through the dongle (jabridge
    settingTransport); class 0xFE in a reply is a NAK.
  * name:    class 0x02 / op 0x00 -> payload <n> <n bytes UTF-8>
  * battery: class 0x12 / op 0x02 (STATUS__HS_BATTERY) -> payload <flags> <level>
    flags bit 0 = charging, bit 1 = battery low (decodeGNPBatteryPayload).

Both are queries; nothing is ever written to the headset. Checked on a Link 380
(0b0e:24c7) with an Evolve2 65: name "Jabra Evolve2 65", battery `04 15` = 21 %.
A headset that is switched off or out of range answers with a NAK or not at all, and
then no icon is shown, the way any switched-off device leaves the tray.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

JABRA_VID = 0x0B0E
USAGE_PAGE = 0xFF00
USAGE = 0x0001

REPORT_ID = 0x05
FRAME_SIZE = 64                 # report id + 63 bytes
DST_HEADSET = 0x04              # the headset behind the dongle
FLAG_QUERY = 0x40
FLAG_REPLY = 0xC0
CLASS_NAK = 0xFE

CLASS_DEVINFO, OP_NAME = 0x02, 0x00
CLASS_STATUS, OP_BATTERY = 0x12, 0x02

READ_TIMEOUT_MS = 300
POLL_ATTEMPTS = 6               # the dongle also pushes call-control events

# jabridge isKnownDonglePID; it read the battery through 24c7, 24c8 and 2e57
PIDS = {
    0x24C7: "Jabra Link 380",
    0x24C8: "Jabra Link 380",
    0x24C9: "Jabra Link 380",
    0x24CA: "Jabra Link 380",
    0x24E9: "Jabra Link 380",
    0x2E57: "Jabra Link 390",
}


def make_request(seq: int, cls: int, op: int) -> List[int]:
    frame = [REPORT_ID, DST_HEADSET, 0x00, seq & 0xFF, FLAG_QUERY | 6, cls, op]
    return frame + [0x00] * (FRAME_SIZE - len(frame))


def reply_payload(r, seq: int, cls: int, op: int) -> Optional[bytes]:
    """The payload of the reply to (seq, cls, op), b"" for a NAK, None for any other frame."""
    if not r or len(r) < 6:
        return None
    r = bytes(r)
    if r[0] != REPORT_ID or r[1] != 0x00 or r[2] != DST_HEADSET or r[3] != (seq & 0xFF):
        return None
    if r[4] & 0xC0 != FLAG_REPLY:
        return None
    if r[5] == CLASS_NAK:
        return b""
    if len(r) < 7 or r[5] != cls or r[6] != op:
        return None
    length = r[4] & 0x3F
    if length < 6 or length + 1 > len(r):
        return None
    return r[7:length + 1]


def parse_name(payload: Optional[bytes]) -> Optional[str]:
    if not payload:
        return None
    n = payload[0]
    if n == 0 or n > len(payload) - 1:
        return None
    return payload[1:1 + n].decode("utf-8", "replace").strip() or None


def parse_battery(payload: Optional[bytes]):
    """(level, charging) from a battery payload, or None."""
    if not payload or len(payload) < 2:
        return None
    level = payload[1]
    if level > 100:
        return None
    return level, bool(payload[0] & 0x01)


class JabraProvider(Provider):
    name = "jabra"

    def __init__(self):
        self._diag: List[str] = []
        self._seq = 0x20
        self._names: Dict[bytes, str] = {}   # dongle path -> headset name

    def _next_seq(self) -> int:
        s = self._seq
        self._seq = (self._seq + 1) & 0xFF
        return s

    def _query(self, dev, cls: int, op: int) -> Optional[bytes]:
        seq = self._next_seq()
        dev.write(make_request(seq, cls, op))
        for _ in range(POLL_ATTEMPTS):
            r = dev.read(FRAME_SIZE, READ_TIMEOUT_MS)
            if not r:
                continue
            payload = reply_payload(r, seq, cls, op)
            if payload is not None:
                self._diag.append(f"  {cls:02x}/{op:02x}: {hexdump(r, 16)}")
                return payload
        self._diag.append(f"  {cls:02x}/{op:02x}: no reply")
        return None

    def _read(self, path: bytes):
        """(name, level, charging) of the headset behind one dongle, or None."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            battery = parse_battery(self._query(dev, CLASS_STATUS, OP_BATTERY))
            if battery is None:
                self._names.pop(path, None)  # off or a different headset next time
                return None
            name = self._names.get(path)
            if name is None:
                name = parse_name(self._query(dev, CLASS_DEVINFO, OP_NAME))
                if name:
                    self._names[path] = name
            return name, battery[0], battery[1]
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(JABRA_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(jabra): %s", e)
            return []
        out = []
        for d in infos:
            pid = d.get("product_id")
            if pid not in PIDS or (d.get("usage_page"), d.get("usage")) != (USAGE_PAGE, USAGE):
                continue
            self._diag.append(f"[Jabra] pid={pid:04x} '{PIDS[pid]}' "
                              f"iface={d.get('interface_number')}")
            got = self._read(d["path"])
            if got is None:
                continue
            name, level, charging = got
            out.append(DeviceStatus(f"jabra:{pid:04x}", name or f"Jabra headset ({PIDS[pid]})",
                                    level, charging, True, "jabra", kind="headset"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
