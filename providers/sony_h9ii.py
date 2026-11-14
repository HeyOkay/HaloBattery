"""Sony INZONE H9 II over its 2.4 GHz receiver's HID control channel.

The H9 II is a different transport from the H9/H7 serial port: its dongle
exposes a 64-byte in/out vendor collection at usage page ``FF04`` (``054C:0FA8``),
and INZONE Hub talks to it with 64-byte report-id-2 frames.

Protocol sequence and the audio-status layout are from marcinjakubowski/zoneout
(MIT), which supports the H9 II and INZONE Buds:

    https://github.com/marcinjakubowski/zoneout
    src/zoneout/protocol.py, src/zoneout/device.py

Request for audio status (command 0x06), 64 bytes:

    02 0C 01 00 FC 08 96 C3 41 06 01 01 00 9C ...

The reply echoes command 0x06 at byte 9; byte 14 is the charging flag and byte
15 the battery percentage on a headset.

Read-only: only the status command is sent. No volume, noise-control, Bluetooth
or firmware setting is written. Unverified against this reporter's hardware until
the test build is run.
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

VID = 0x054C
PID = 0x0FA8
PAGE, USAGE = 0xFF04, 0x0001
REPORT_LEN = 64
REPORT_ID = 0x02
CMD_AUDIO_STATUS = 0x06
MAGIC_1, MAGIC_2 = 0x96, 0xC3
CHARGE_AT = 14
LEVEL_AT = 15
READ_MS = 25
RETRIES = 40


def request(cmd: int = CMD_AUDIO_STATUS) -> List[int]:
    req = [0x00] * REPORT_LEN
    header = [REPORT_ID, 0x0C, 0x01, 0x00, 0xFC, 0x08,
              MAGIC_1, MAGIC_2, 0x41, cmd, 0x01, 0x01, 0x00,
              (cmd + 0x9C) & 0xFF]
    req[:len(header)] = header
    return req


def parse_reply(r) -> Optional[Tuple[int, bool]]:
    """(level, charging) from an audio-status reply, or None."""
    if not r or len(r) <= LEVEL_AT:
        return None
    if r[0] != REPORT_ID or r[9] != CMD_AUDIO_STATUS:
        return None
    level = r[LEVEL_AT]
    if not 0 <= level <= 100:
        return None
    return level, bool(r[CHARGE_AT])


class SonyH9IIProvider(Provider):
    name = "sony_h9ii"

    def __init__(self):
        self._diag: List[str] = []

    def _read(self, path) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            # Drain notifications left by the previous poll before asking.
            while dev.read(REPORT_LEN, 1):
                pass
            sent = dev.write(request())
            if sent is None or sent < 0:
                self._diag.append(f"    write refused ({sent})")
                return None
            for _ in range(RETRIES):
                r = dev.read(REPORT_LEN, READ_MS)
                if not r:
                    continue
                if len(r) > LEVEL_AT and r[9] == CMD_AUDIO_STATUS:
                    parsed = parse_reply(r)
                    if parsed is not None:
                        self._diag.append(f"    answer: {hexdump(r, 12)} -> "
                                          f"{parsed[0]}% charging={parsed[1]}")
                        return parsed
            self._diag.append("    no audio-status reply")
            return None
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"    read/write: {e}")
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
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(sony_h9ii): %s", e)
            return []

        out: List[DeviceStatus] = []
        for d in infos:
            if d.get("product_id") != PID:
                continue
            if (d.get("usage_page"), d.get("usage")) != (PAGE, USAGE):
                continue
            name = (d.get("product_string") or "").strip() or "Sony INZONE H9 II"
            self._diag.append(f"[Sony H9 II] {name} pid={PID:04x} "
                              f"iface={d.get('interface_number')}")
            got = self._read(d["path"])
            if got is None:
                continue
            level, charging = got
            out.append(DeviceStatus("sony_h9ii", "Sony INZONE H9 II", level, charging,
                                    True, self.name, kind="headset"))
            break
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
