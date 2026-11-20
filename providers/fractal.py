"""Fractal Design Scape headset (vendor 36BC), receiver 0001 or cable 2001.

Both expose a 64-byte vendor collection on usage page FF00 / usage 0001.
Output report 0x02 carries the device type and command; input report 0x02
echoes them. `GET_DEVICE_STATE` (0x21) is sent to the dongle type 0x11 and
then to the headset type 0xF1:

  * dongle payload byte 4 = 1 when the headset is linked
  * headset payload byte 15 = percentage, byte 23 = 1 on the USB cable

From Fractal's Adjust Pro WebHID configurator. Only the state query is sent.
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

VID = 0x36BC
DONGLE = 0x0001
CABLE = 0x2001
PIDS = (DONGLE, CABLE)
PAGE, USAGE = 0xFF00, 0x0001
REPORT = 0x02
TYPE_DONGLE = 0x11
TYPE_HEADSET = 0xF1
GET_STATE = 0x21
READ_TIMEOUT_MS = 250
LISTEN_MS = 1000
KEY = "fractal:scape"
NAME = "Fractal Scape"


def parse_state(r, device_type: int) -> Optional[list]:
    if not r or len(r) < 24:
        return None
    b = list(r)
    if b[0] != REPORT or b[1] != device_type or b[2] != GET_STATE:
        return None
    return b


class FractalProvider(Provider):
    name = "fractal"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(fractal): %s", e)
            return []

        eligible = False
        for d in infos:
            pid = d.get("product_id")
            if pid not in PIDS:
                continue
            if ((d.get("usage_page"), d.get("usage")) != (PAGE, USAGE)
                    or (d.get("output_report_byte_length") or 0) < 4
                    or (d.get("input_report_byte_length") or 0) < 24):
                continue
            eligible = True
            cable = pid == CABLE
            got = self._read(d, cable)
            if got is None:
                continue
            level, charging = got
            self._diag.append(f"  -> {level}% charging={charging}")
            return [DeviceStatus(KEY, NAME, level, charging, True, "fractal",
                                 kind="headset",
                                 via="USB cable" if cable else "2.4 GHz dongle")]
        if not eligible:
            return []
        self._diag.append("  no answer (headset off or out of range)")
        return [DeviceStatus(KEY, NAME, None, False, False, "fractal", kind="headset")]

    def _read(self, d, cable: bool) -> Optional[Tuple[int, bool]]:
        out_len = d.get("output_report_byte_length") or 64
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
            if not cable:
                if not self._send(dev, TYPE_DONGLE, out_len):
                    return None
                r = self._wait(dev, TYPE_DONGLE)
                if r is None or r[4] != 1:
                    self._diag.append("  dongle says the headset is not linked")
                    return None
            if not self._send(dev, TYPE_HEADSET, out_len):
                return None
            r = self._wait(dev, TYPE_HEADSET)
            if r is None:
                return None
            if r[15] > 100:
                self._diag.append(f"  level out of range: {r[15]}")
                return None
            return r[15], r[23] == 1
        except (OSError, IOError, ValueError) as e:  # pragma: no cover
            self._diag.append(f"  error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _send(self, dev, device_type: int, out_len: int) -> bool:
        req = bytes([REPORT, device_type, GET_STATE])
        req += bytes(max(0, out_len - len(req)))
        dev.write(req)
        return True

    def _wait(self, dev, device_type: int) -> Optional[list]:
        end = time.time() + LISTEN_MS / 1000.0
        while time.time() < end:
            r = dev.read(64, READ_TIMEOUT_MS)
            if not r:
                continue
            self._diag.append(f"  {hexdump(r, 24)}")
            parsed = parse_state(r, device_type)
            if parsed is not None:
                return parsed
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
