"""EWEADN / 前行者 mice on their 2.4 GHz receiver (S9, M156).

Protocol recovered from EWEADN's own HUB: the Electron shell loads the web HUB from
hub.eweadn.cn, whose ``pq.json`` maps ``089D:062F`` (usage page 0xFF01, usage 0x0010) to the
``zhanli-receiver`` handler, and the S9 page (``/pq/web/``) implements the exchange:

  * one 33-byte output report, report id 0: ``[0, cmd, 0, 0, 0, 0 ... 0, checksum]`` where
    the checksum is ``sum(bytes 5..31) & 0xFF`` in the last byte;
  * command 0x30 asks for the device info block; the answer echoes the command in byte 0,
    puts the status in byte 1 (0 = ok) and a length in byte 3, then:
        [4..7]  device id, ASCII ("M156" for the S9)
        [8][9]  firmware
        [10]    ic type
        [11]    report max
        [12]    charge flag (1 while charging)
        [13]    battery level, 0..100
        [14]    connect status
  * while the mouse sleeps the receiver still answers, but with ``cmd 02`` and no payload,
    so a poll that gets 0x02 means "asleep", not "broken" - that was the state during the
    first probes here, and moving the mouse turned the same query into
    ``30 00 01 0B 4D 31 35 36 ... 46 01`` = M156, 0x46 = 70 %.
  * the receiver also pushes ``D1 01 <level> ...`` status reports on its own, which is where
    the vendor app gets live battery changes from.

Only the 0xFF01 vendor collection is written to.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

RECEIVER_VID = 0x089D
RECEIVER_PID = 0x062F
WIRED_VID = 0x088D
WIRED_PID = 0x062E

USAGE_PAGE = 0xFF01
REPORT_ID = 0x00
CMD_DEVICE_INFO = 0x30
DEVICE_IDS = {"M156": "EWEADN S9", "M302": "EWEADN H2"}

ATTEMPTS = 3
ATTEMPT_GAP = 0.18
READ_TIMEOUT_MS = 120

ASLEEP_KEEP = 300


def make_request(cmd: int = CMD_DEVICE_INFO) -> bytes:
    f = [0] * 33
    f[0] = REPORT_ID
    f[1] = cmd
    f[32] = sum(f[5:32]) & 0xFF
    return bytes(f)


def parse_info(resp) -> Optional[Tuple[int, bool, str]]:
    """(level, charging, device id) from a device-info answer, or None.

    A sleeping mouse answers ``cmd 02`` with no payload; anything without the echoed
    command, the ok status and a plausible level is refused rather than reported as 0 %.
    """
    if not resp or len(resp) < 15:
        return None
    r = bytes(resp)
    if r[0] != CMD_DEVICE_INFO or r[1] != 0x00:
        return None
    level = r[13]
    if level > 100:
        return None
    dev_id = "".join(chr(b) for b in r[4:8] if 32 <= b < 127).strip()
    return level, bool(r[12]), dev_id


class EweadnProvider(Provider):
    name = "eweadn"

    def __init__(self):
        self._diag: List[str] = []
        self._last = {}
        self._name = "EWEADN S9"
    def _read_info(self, path: bytes) -> Optional[Tuple[int, bool, str]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            try:
                dev.set_nonblocking(True)
            except Exception:
                pass
            for attempt in range(ATTEMPTS):
                try:
                    dev.write(make_request())
                except (OSError, ValueError) as e:
                    self._diag.append(f"    write: {e}")
                    return None
                deadline = time.time() + ATTEMPT_GAP
                while time.time() < deadline:
                    try:
                        resp = dev.read(33, timeout_ms=READ_TIMEOUT_MS)
                    except (OSError, ValueError):
                        break
                    if not resp:
                        continue
                    got = parse_info(resp)
                    if got:
                        self._diag.append(f"    answer (attempt {attempt + 1}): {hexdump(resp, 16)}")
                        return got
                    if len(resp) > 1 and resp[0] == CMD_DEVICE_INFO and resp[1] == 0x02:
                        self._diag.append("    cmd 02: mouse asleep")
                        break
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        targets = []
        try:
            for d in hidlist.enumerate(RECEIVER_VID):
                if d.get("product_id") == RECEIVER_PID and (d.get("usage_page") or 0) == USAGE_PAGE:
                    targets.append((d, False))
            for d in hidlist.enumerate(WIRED_VID):
                if d.get("product_id") == WIRED_PID:
                    targets.append((d, True))
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(eweadn): %s", e)
            return []

        out: List[DeviceStatus] = []
        seen = set()
        for d, wired in targets:
            key = "eweadn"
            if key in seen:
                continue
            product = (d.get("product_string") or "").strip()
            self._diag.append(f"[EWEADN] pid={d.get('product_id'):04x} product='{product}' "
                              f"usage={(d.get('usage_page') or 0):04x}:{(d.get('usage') or 0):04x}")
            got = self._read_info(d["path"])
            if not got:
                continue
            level, charging, dev_id = got
            name = DEVICE_IDS.get(dev_id, product or "EWEADN mouse")
            self._diag.append(f"  -> {dev_id or '?'}: {level}%{' (charging)' if charging else ''}")
            seen.add(key)
            self._last[key] = (level, name, time.time())
            out.append(DeviceStatus(key, name, level, charging or wired, True,
                                    "eweadn", kind="mouse", wake_on_input=True))

        if "eweadn" not in seen and targets:
            # the receiver answers 0x02 while the mouse sleeps. Keep an icon either way:
            # the last known level greyed out, or "no link" when there is nothing yet -
            # wake_on_input makes the app poll again the moment the mouse is moved.
            last = self._last.get("eweadn")
            if last:
                out.append(DeviceStatus("eweadn", last[1], last[0], False, False,
                                        "eweadn", kind="mouse", wake_on_input=True))
            else:
                out.append(DeviceStatus("eweadn", self._name, None, False, False,
                                        "eweadn", kind="mouse", wake_on_input=True))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
