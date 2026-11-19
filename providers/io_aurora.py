"""IO Aurora over its 2.4 GHz receiver (3554:fb08).

The receiver identifies itself as ``IO Aurora Receiver`` and exposes the
CompX control collection ``ff02:0002``.  CompX is the report-8 protocol used by
the Lamzu Thorn/Atlantis family; the request and reply shape are shared with
``providers/compx.py``:

    request  08 04 00 ... <checksum>       (17 bytes)
    reply    08 04 ... <level> <power> ... <checksum>
             level at byte 6, power at byte 7 (0 = battery, non-zero = cable)

Only the read-only power query is sent.  The model is gated on both the
receiver id and the product string because ``3554`` is shared with Lamzu and
other CompX devices.
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
from .compx import (
    CONTROL_USAGE, PAYLOAD_LEN, READ_ATTEMPTS, READ_TIMEOUT_MS,
    make_request, output_length, parse_power, voltage_mv,
)

VID = 0x3554
PID = 0xFB08
NAME = "IO Aurora"
KEY = "io_aurora:fb08"
PRODUCT = "io aurora receiver"
ASLEEP_KEEP = 300


def is_aurora(info: dict) -> bool:
    product = (info.get("product_string") or "").strip().lower()
    return info.get("product_id") == PID and "io aurora" in product


def candidate(infos: List[dict]) -> Optional[dict]:
    """Pick the CompX control collection, never a keyboard/mouse page."""
    mine = [d for d in infos if is_aurora(d)]
    control = [d for d in mine
               if (d.get("usage_page"), d.get("usage")) == CONTROL_USAGE]
    if control:
        return control[0]
    for d in mine:
        if output_length(d.get("path")) in (None, PAYLOAD_LEN):
            return d
    return mine[0] if mine else None


class IoAuroraProvider(Provider):
    name = "io_aurora"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float]] = None

    def _query(self, path) -> Optional[Tuple[int, bool]]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            # Drop reports left over from the receiver's previous poll, but keep
            # a battery frame if one was already waiting.
            for _ in range(4):
                stale = dev.read(PAYLOAD_LEN, 30)
                if not stale:
                    break
                parsed = parse_power(stale)
                if parsed is not None:
                    return parsed
            dev.write(make_request())
            for attempt in range(READ_ATTEMPTS):
                reply = dev.read(PAYLOAD_LEN, READ_TIMEOUT_MS)
                if not reply:
                    break
                self._diag.append(f"  reply {attempt + 1}: {hexdump(reply)}")
                parsed = parse_power(reply)
                if parsed is not None:
                    mv = voltage_mv(reply)
                    if mv:
                        self._diag.append(f"  {mv} mV")
                    return parsed
                time.sleep(0.02)
            # Some CompX receivers answer the read-only request as a feature report.
            try:
                feature = dev.get_feature_report(0x08, PAYLOAD_LEN)
            except (OSError, ValueError):
                feature = None
            if feature:
                self._diag.append(f"  feature reply: {hexdump(feature)}")
                parsed = parse_power(feature)
                if parsed is not None:
                    return parsed
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
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(io aurora): %s", e)
            return []
        if not any(is_aurora(d) for d in infos):
            return []
        self._diag.append(f"[IO Aurora] pid={PID:04x} 'IO Aurora Receiver'")
        d = candidate(infos)
        if d is None:
            return []
        self._diag.append(f"  iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
                          f"output={output_length(d.get('path'))}")
        parsed = self._query(d["path"])
        if parsed is not None:
            level, charging = parsed
            self._last = (level, charging, time.time())
            self._diag.append(f"  -> {level}%{' charging' if charging else ''}")
            return [DeviceStatus(KEY, NAME, level, charging, True, "io_aurora",
                                 kind="mouse")]
        if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
            level, charging, _seen = self._last
            return [DeviceStatus(KEY, NAME, level, charging, False, "io_aurora",
                                 kind="mouse", wake_on_input=True)]
        return [DeviceStatus(KEY, NAME, None, False, False, "io_aurora",
                             kind="mouse", wake_on_input=True)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
