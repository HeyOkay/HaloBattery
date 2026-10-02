"""HyperX Cloud II Wireless over USB/HID, without NGENUITY.

Protocol (Sapd/HeadsetControl's hyperx_cloud_2_wireless device, which lists the
same two product ids):

  * the dongle's vendor collection, usage page 0xFF90 / usage 0x0303. It has to be
    picked by usage: HeadsetControl gives interface 0 for it while a dongle here
    reports it on interface 3, and the same dongle carries four collections on
    that interface (000c, ff00, ff90, ffc0), so taking the first one would talk to
    the wrong endpoint.
  * request: 52 bytes, 06 ff bb <cmd> 00 ...   (0x06 is the report id)
  * reply:   20 bytes, its first four bytes echo the request: 06 ff bb <cmd>
      cmd 02, battery:  reply[7] = percent, reply[5..6] = millivolts
      cmd 03, charging: reply[4] == 1 means charging

The Kingston-branded revision (0951:1718, sold before HyperX moved to HP vendor
ids) is the same headset with a longer exchange, from three sources that agree
byte for byte: HeadsetControl's hyperx_cloud_2_wireless_kingston.hpp (written from
LennardKittner/HyperHeadset), HyperHeadset's own cloud_ii_wireless.rs, and
Agustin-Jerusalinsky/hyperx-cloud-II-battery, a script for this exact id:

  * the vendor collection is usage page 0xFF13 / usage 0x0001 (the Cloud III
    family's page; the consumer-control collections next to it are not the
    battery endpoint)
  * request: 62 bytes, 06 00 02 00 9A 00 00 68 4A 8E 0A 00 00 00 BB <cmd> <payload>
  * the reference projects read one input report before each write and ignore the
    result (HeadsetControl calls it prepareDevice), so this does too, then waits
    100 ms and reads once
  * reply: report id 0x0B, 0B 00 BB <cmd> ...   byte 7 is the level for command
    0x02; byte 4 is the charging state for command 0x03 (1 charging, 2 fully
    charged - both mean on the cable - 0 not charging, anything else an error)

A reply that does not echo the command is ignored, and a level above 100 is
refused rather than shown as a made-up number.

Unverified on hardware: the reporter's diagnostics in #192 pin the Kingston dongle
(0951:1718, vendor collections on interface 3) and his run is the confirmation.
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

HYPERX_VID = 0x03F0
USAGE_PAGE = 0xFF90
USAGE = 0x0303

CMD_LEVEL = 0x02
CMD_CHARGING = 0x03
LEVEL_INDEX = 0x07
CHARGING_INDEX = 0x04

WRITE_TIMEOUT = 0.1          # HeadsetControl waits 100 ms between request and read
READ_TIMEOUT_MS = 1000
REPLY_LEN = 20

# Cloud II Wireless. 0x0696 is the older dongle revision, 0x018b the newer one.
PIDS = {
    0x0696: "HyperX Cloud II Wireless",
    0x018B: "HyperX Cloud II Wireless",
}

# The Kingston-branded revision: the same headset, its own exchange (docstring).
KINGSTON_VID = 0x0951
KINGSTON_USAGE_PAGE = 0xFF13
KINGSTON_USAGE = 0x0001
KINGSTON_PIDS = {
    0x1718: "HyperX Cloud II Wireless",
}
KINGSTON_PACKET_LEN = 62
KINGSTON_READ_LEN = 64
KINGSTON_READ_TIMEOUT_MS = 1000
KINGSTON_WRITE_PAUSE = 0.1
KINGSTON_REPORT_ID = 0x06          # the prepare read and the request's first byte
KINGSTON_REPLY_ID = 0x0B           # its replies carry report id 0x0B
KINGSTON_REPLY_MARK = 0xBB
KINGSTON_CMD_LEVEL = 0x02
KINGSTON_CMD_CHARGING = 0x03


def make_request(cmd: int) -> List[int]:
    return [0x06, 0xFF, 0xBB, cmd, 0x00] + [0x00] * 47


def parse_level(r) -> Optional[int]:
    if not r or len(r) <= LEVEL_INDEX:
        return None
    if list(r[:4]) != [0x06, 0xFF, 0xBB, CMD_LEVEL]:
        return None
    level = r[LEVEL_INDEX]
    return level if 0 <= level <= 100 else None


def parse_charging(r) -> Optional[bool]:
    if not r or len(r) <= CHARGING_INDEX:
        return None
    if list(r[:4]) != [0x06, 0xFF, 0xBB, CMD_CHARGING]:
        return None
    return r[CHARGING_INDEX] == 1


def kingston_request(cmd: int, payload: int = 0) -> List[int]:
    """The Kingston revision's 62-byte request. Byte 15 is the command, 16 its
    payload; bytes 4..13 are the fixed preamble all three references carry."""
    req = [0x06, 0x00, 0x02, 0x00, 0x9A, 0x00, 0x00, 0x68, 0x4A, 0x8E, 0x0A,
           0x00, 0x00, 0x00, 0xBB, cmd, payload]
    return req + [0x00] * (KINGSTON_PACKET_LEN - len(req))


def parse_kingston_reply(r, cmd: int) -> Optional[List[int]]:
    """A Kingston-revision reply for `cmd`, or None. The references check the
    same three bytes: report id 0x0B, the 0xBB marker and the command echo."""
    if not r or len(r) < 8:
        return None
    f = list(r)
    if f[0] != KINGSTON_REPLY_ID or f[2] != KINGSTON_REPLY_MARK or f[3] != cmd:
        return None
    return f


def parse_kingston_level(reply) -> Optional[int]:
    if not reply or len(reply) <= 7:
        return None
    level = reply[7]
    return level if 0 <= level <= 100 else None


def parse_kingston_charging(reply) -> Optional[bool]:
    if not reply or len(reply) <= 4:
        return None
    status = reply[4]
    if status == 0:
        return False
    if status in (1, 2):           # charging / fully charged: on the cable either way
        return True
    return None                    # anything else is an error in the references


class HyperXProvider(Provider):
    name = "hyperx"

    def __init__(self):
        self._diag: List[str] = []

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        """The battery collection by usage page/usage. Without it nothing is written:
        the other collections on that interface are not the battery endpoint, so the
        probe only lists what the dongle offers."""
        for d in infos:
            if (d.get("usage_page"), d.get("usage")) == (USAGE_PAGE, USAGE):
                return d
        offered = ", ".join(f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}" for d in infos)
        self._diag.append(f"  no usage {USAGE_PAGE:04x}:{USAGE:04x} collection (found: {offered})")
        return None

    def _query(self, path: bytes, cmd: int) -> Optional[List[int]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            dev.write(make_request(cmd))
            time.sleep(WRITE_TIMEOUT)
            r = dev.read(REPLY_LEN, READ_TIMEOUT_MS)
            if not r:
                self._diag.append(f"  cmd {cmd:02x}: no reply")
                return None
            self._diag.append(f"  cmd {cmd:02x} reply: {hexdump(r)}")
            return list(r)
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  cmd {cmd:02x} error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _pick_kingston(self, infos: List[dict]) -> Optional[dict]:
        """The Kingston dongle's battery collection: its vendor page, 0xFF13.

        The consumer-control collections next to it are not the battery endpoint, so
        nothing is written when that page is absent - a device without it is unknown
        firmware, not a device to guess at."""
        vendor = [d for d in infos if d.get("usage_page") == KINGSTON_USAGE_PAGE]
        for d in vendor:
            if d.get("usage") == KINGSTON_USAGE:
                return d
        if vendor:
            return vendor[0]
        offered = ", ".join(f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}"
                             for d in infos)
        self._diag.append(f"  no {KINGSTON_USAGE_PAGE:04x} vendor collection "
                          f"(found: {offered})")
        return None

    def _query_kingston(self, path: bytes, cmd: int) -> Optional[List[int]]:
        """One Kingston-revision request -> its reply, or None.

        The references read one input report before every write and ignore the
        result (HeadsetControl's prepareDevice), so this does too; the write is
        followed by a 100 ms pause and one bounded read."""
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            try:
                dev.get_input_report(KINGSTON_REPORT_ID, KINGSTON_PACKET_LEN)
            except (OSError, IOError, ValueError):
                pass                  # the references ignore a failed prepare read
            dev.write(kingston_request(cmd))
            time.sleep(KINGSTON_WRITE_PAUSE)
            r = dev.read(KINGSTON_READ_LEN, KINGSTON_READ_TIMEOUT_MS)
            if r:
                self._diag.append(f"  cmd {cmd:02x} reply: {hexdump(r)}")
            reply = parse_kingston_reply(r, cmd)
            if reply is None:
                self._diag.append(f"  cmd {cmd:02x}: no reply for this command")
            return reply
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  cmd {cmd:02x} error: {e}")
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
            infos = hidlist.enumerate(HYPERX_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(hyperx): %s", e)
            return []
        out = []
        seen = set()
        for pid in PIDS:
            mine = [d for d in infos if d["product_id"] == pid and d["path"] not in seen]
            if not mine:
                continue
            d = self._pick(mine)
            if d is None:
                continue
            seen.add(d["path"])
            name = PIDS[pid]
            self._diag.append(f"[HyperX] pid={pid:04x} '{name}' "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            level = parse_level(self._query(d["path"], CMD_LEVEL))
            if level is None:
                continue
            charging = parse_charging(self._query(d["path"], CMD_CHARGING)) or False
            out.append(DeviceStatus(f"hyperx:{pid:04x}", name, level, charging, True,
                                    "hyperx", kind="headset"))

        # The Kingston-branded revision speaks its own, longer exchange (docstring).
        try:
            k_infos = hidlist.enumerate(KINGSTON_VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(hyperx/kingston): %s", e)
            k_infos = []
        for pid in KINGSTON_PIDS:
            mine = [d for d in k_infos if d["product_id"] == pid]
            if not mine:
                continue
            d = self._pick_kingston(mine)
            if d is None:
                continue
            name = KINGSTON_PIDS[pid]
            self._diag.append(f"[HyperX] pid=0951:{pid:04x} '{name}' (Kingston) "
                              f"iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            level = parse_kingston_level(
                self._query_kingston(d["path"], KINGSTON_CMD_LEVEL))
            if level is None:
                continue
            charging = parse_kingston_charging(
                self._query_kingston(d["path"], KINGSTON_CMD_CHARGING)) or False
            out.append(DeviceStatus(f"hyperx:{pid:04x}", name, level, charging, True,
                                    "hyperx", kind="headset"))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
