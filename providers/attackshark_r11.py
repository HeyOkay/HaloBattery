"""Attack Shark R11 Ultra on its CompX 8K receiver (3554:fb44).

CompX's report-8 power query, the same exchange the LAMZU Atlantis/Thorn and the
Pulsar receivers use (``providers/compx.py``, decoded from the vendor app's own
parse): a 17-byte output report ``08 04 ...`` answered with the same header, the
level at byte 6, a power/cable flag at byte 7 and a checksum in the last byte.

The dongle exposes the CompX control collection ``ff02:0002`` - the usage every
CompX mouse uses for this exchange - next to its ``ff03`` / ``ff04`` / ``ff05``
vendor pages, which are left alone. Nothing is read from the mouse/keyboard
collections; only the one power question is written, to that control collection.

Source: the CompX notes behind #69 (blak0p's protocol documents, validated on
the R1/X6 dongles) and the LAMZU Atlantis support, whose frame builder and
parser this module reuses unchanged (``providers/compx.py``).

While charging, this mouse can report a level a few points below the one the
vendor app keeps showing (95 % at a full 100 %), and a poll at the plug-in can
catch a 5-point drop first. The provider publishes the higher of the pre-cable
level and the charging reply, then accepts fresh replies again once the cable
is out.

**Confirmed on hardware** in #525: the level matched the vendor software at
90 % and 100 %, and the charging state held at both levels and cleared within a
poll or two after unplugging. If the dongle stops answering, the diagnostics
name every collection tried and the raw reply.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

try:
    import hid
except ImportError:                  # pragma: no cover
    hid = None

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log
from .compx import (CONTROL_USAGE, PAYLOAD_LEN as COMPX_PAYLOAD_LEN,
                    make_request as compx_make_request,
                    output_length as compx_output_length,
                    parse_power as compx_parse_power)

VID = 0x3554                    # CompX
PID = 0xFB44                    # R11 Ultra, 8K dongle
NAME = "Attack Shark R11 Ultra"
KEY = "attackshark:fb44"

READ_ATTEMPTS = 6
READ_TIMEOUT_MS = 250
DRAIN_TIMEOUT_MS = 30
ASLEEP_KEEP = 300               # s: then the icon goes away instead of greying forever


class AttackSharkR11Provider(Provider):
    name = "attackshark_r11"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None
        # The mouse can report a lower level while charging (95 % at a full 100 %),
        # and a poll around the plug-in can catch 85 % on the way from 90 %. Keep
        # the higher of that pre-cable level and the charging reply, which matches
        # the vendor app in both cases.
        self._precharge: Optional[int] = None

    def _pick(self, infos: List[dict]) -> Optional[dict]:
        control = [d for d in infos
                   if (d.get("usage_page"), d.get("usage")) == CONTROL_USAGE]
        sized = [d for d in control
                 if compx_output_length(d["path"]) in (None, COMPX_PAYLOAD_LEN)]
        return (sized or control or [None])[0]

    def _query(self, path) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            for _ in range(4):                  # drain whatever the dongle queued
                if not dev.read(COMPX_PAYLOAD_LEN, DRAIN_TIMEOUT_MS):
                    break
            dev.write(compx_make_request())
            for attempt in range(READ_ATTEMPTS):
                r = dev.read(COMPX_PAYLOAD_LEN, READ_TIMEOUT_MS)
                if not r:
                    break
                self._diag.append(f"  reply {attempt + 1}: {hexdump(r)}")
                parsed = compx_parse_power(r)
                if parsed is not None:
                    return parsed
                time.sleep(0.02)
            self._diag.append("  no power reply (mouse idle or no response)")
        except (OSError, IOError, ValueError) as e:
            self._diag.append(f"  query error: {e}")
        finally:
            try:
                dev.close()
            except Exception:
                pass
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = [d for d in hidlist.enumerate(VID) if d.get("product_id") == PID]
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(attack shark r11): %s", e)
            return []
        if not infos:
            self._last = None
            self._precharge = None       # a new receiver is a new session
            return []
        self._diag.append(f"[Attack Shark R11] pid={PID:04x} interfaces={len(infos)}")
        picked = self._pick(infos)
        if picked is not None:
            self._diag.append(f"  listening on iface={picked.get('interface_number')} "
                              f"{picked.get('usage_page', 0):04x}:{picked.get('usage', 0):04x}")
            parsed = self._query(picked["path"])
            if parsed is not None:
                level, charging = parsed
                if charging:
                    if self._precharge is None and self._last is not None and not self._last[1]:
                        self._precharge = self._last[0]
                    if self._precharge is not None:
                        level = max(level, self._precharge)
                else:
                    self._precharge = None
                self._last = (level, charging, time.time())
                return [DeviceStatus(KEY, NAME, level, charging, True,
                                     "attackshark_r11", kind="mouse")]
        else:
            self._diag.append("  no ff02:0002 control collection")
        last = self._last
        if last is not None and time.time() - last[2] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, last[0], last[1], False,
                                 "attackshark_r11", kind="mouse", wake_on_input=True)]
        # The silence outlasted the greyed icon: the session is over, so the
        # pre-cable level must not leak into whatever is connected next.
        self._last = None
        self._precharge = None
        return []

    def diagnostics(self) -> List[str]:
        return list(self._diag)
