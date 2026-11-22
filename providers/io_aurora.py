"""IO Aurora over its 2.4 GHz receiver (3554:fb08) or its cable (3554:fb09).

The receiver identifies itself as ``IO Aurora Receiver`` and exposes the
CompX control collection ``ff02:0002``.  CompX is the report-8 protocol used by
the Lamzu Thorn/Atlantis family; the request and reply shape are shared with
``providers/compx.py``:

    request  08 04 00 ... <checksum>       (17 bytes)
    reply    08 04 ... <level> <power> ... <checksum>
             level at byte 6, power at byte 7 (0/1/2 = battery/charging/full)

Only the read-only power query is sent.  The model is gated on both the
receiver id and the product string because ``3554`` is shared with Lamzu and
other CompX devices.

Source: lavandiI/lamzu-battery at ef51ad02a875bd386f7ba22e4e91bfc98d41fe87,
src/protocol/compx.rs:7-14,65-121 and src/hid.rs:125-158 (request then read,
feature fallback); src/protocol/mod.rs:77-85 defines power 0/1/2 and Other.
https://github.com/lavandiI/lamzu-battery/blob/ef51ad02a875bd386f7ba22e4e91bfc98d41fe87/src/protocol/compx.rs
The owner's #493 inventory proves BOTH identities use interface 1, FF02:0002:
https://github.com/user-attachments/files/35143662/diagnostics-charging.txt
lines 84-99 (receiver FB08 and cable FB09). No other collection is queried.
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
    make_request, parse_power, voltage_mv,
)

VID = 0x3554
PID = 0xFB08          # the 2.4 GHz receiver, "IO Aurora Receiver"
WIRED_PID = 0xFB09    # the same mouse on its charging cable, "IO Aurora"
NAME = "IO Aurora"
KEY = "io_aurora:fb08"
PRODUCT = "io aurora receiver"
PRODUCTS = {PID: PRODUCT, WIRED_PID: "io aurora"}
ASLEEP_KEEP = 300
MAX_REPORT_LEN = 32768  # native transport's bounded report-buffer limit
Reading = Tuple[int, bool, bool]  # level, charging, charging known


def is_aurora(info: dict, pid: Optional[int] = None) -> bool:
    product = info.get("product_string")
    wanted = (PID, WIRED_PID) if pid is None else (pid,)
    actual_pid = info.get("product_id")
    return (info.get("vendor_id") == VID and type(actual_pid) is int
            and actual_pid in wanted and isinstance(product, str)
            and product.strip().lower() == PRODUCTS.get(actual_pid))


def candidate(infos: List[dict], pid: Optional[int] = None) -> Optional[dict]:
    """Pick the CompX control collection of one id, never a keyboard/mouse page."""
    control = [d for d in infos if is_aurora(d, pid)
               and isinstance(d.get("path"), (bytes, str)) and d["path"]
               and type(d.get("interface_number")) is int and d["interface_number"] == 1
               and type(d.get("usage_page")) is int and type(d.get("usage")) is int
               and (d["usage_page"], d["usage"]) == CONTROL_USAGE]
    return control[0] if len(control) == 1 else None


def power_reply(data) -> Optional[Reading]:
    """Keep unknown charger state unknown without changing other CompX providers."""
    if (not isinstance(data, (bytes, bytearray, list, tuple)) or len(data) < PAYLOAD_LEN
            or any(type(value) is not int or not 0 <= value <= 255 for value in data[:PAYLOAD_LEN])):
        return None
    parsed = parse_power(data)
    if parsed is None or data[7] > 3:
        return None
    known = data[7] != 3
    return parsed[0], parsed[1] if known else False, known


class IoAuroraProvider(Provider):
    name = "io_aurora"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, bool, float, bool]] = None

    def _query(self, path) -> Optional[Reading]:
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            expected_write = PAYLOAD_LEN
            feature_length = PAYLOAD_LEN
            metadata = getattr(dev, "get_report_lengths", None)
            if metadata is not None:
                caps = metadata() if callable(metadata) else None
                if (not isinstance(caps, dict)
                        or any(type(caps.get(kind)) is not int or not 0 <= caps[kind] <= MAX_REPORT_LEN
                               for kind in ("input", "output", "feature"))
                        or min(caps["input"], caps["output"]) < PAYLOAD_LEN):
                    self._diag.append("  incompatible cached report lengths; not querying")
                    return None
                # Native write pads to this opened collection's immutable caps.
                # Legacy hidapi returns the exact 17-byte source request length.
                expected_write = caps["output"]
                # An optional shorter feature report can belong to another ID;
                # it must not disable a valid interrupt query on this collection.
                feature_length = caps["feature"] if caps["feature"] >= PAYLOAD_LEN else 0
            # A queued frame predates this request and cannot refresh its timestamp.
            # Require an empty queue before writing; do not accept a fifth stale frame.
            for _ in range(4):
                stale = dev.read(PAYLOAD_LEN, 30)
                if not stale:
                    break
            else:
                self._diag.append("  input queue did not drain; not querying")
                return None
            sent = dev.write(make_request())
            if type(sent) is not int or sent != expected_write:
                self._diag.append(f"  power query write refused or incomplete ({sent})")
                return None
            for attempt in range(READ_ATTEMPTS):
                reply = dev.read(PAYLOAD_LEN, READ_TIMEOUT_MS)
                if not reply:
                    break
                self._diag.append(f"  reply {attempt + 1}: {hexdump(reply)}")
                parsed = power_reply(reply)
                if parsed is not None:
                    mv = voltage_mv(reply)
                    if mv:
                        self._diag.append(f"  {mv} mV")
                    return parsed
                time.sleep(0.02)
            # Some CompX receivers answer the read-only request as a feature report.
            try:
                feature = dev.get_feature_report(0x08, feature_length) if feature_length else None
            except (OSError, ValueError):
                feature = None
            if feature:
                self._diag.append(f"  feature reply: {hexdump(feature)}")
                parsed = power_reply(feature)
                if parsed is not None:
                    return parsed
            self._diag.append("  no power reply (mouse idle or no response)")
        except (OSError, IOError, ValueError, TypeError) as e:
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
        self._diag.append("[IO Aurora] looking for the cable, then the receiver")
        parsed, source = None, None
        for pid, label in ((WIRED_PID, "cable"), (PID, "receiver")):
            d = candidate(infos, pid)
            if d is None:
                continue
            self._diag.append(f"  pid={pid:04x} ({label}) iface={d.get('interface_number')} "
                              f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
            parsed = self._query(d["path"])
            if parsed is not None:
                source = label
                break
        if parsed is not None:
            level, charging, charging_known = parsed
            self._last = (level, charging, time.time(), charging_known)
            self._diag.append(f"  -> {level}%{' charging' if charging else ''} ({source})")
            return [DeviceStatus(KEY, NAME, level, charging, True, "io_aurora",
                                 kind="mouse", charging_known=charging_known)]
        if self._last and time.time() - self._last[2] < ASLEEP_KEEP:
            level, charging, _seen, charging_known = self._last
            return [DeviceStatus(KEY, NAME, level, charging, False, "io_aurora",
                                 kind="mouse", wake_on_input=True, charging_known=charging_known)]
        return [DeviceStatus(KEY, NAME, None, False, False, "io_aurora",
                             kind="mouse", wake_on_input=True, charging_known=False)]

    def diagnostics(self) -> List[str]:
        return list(self._diag)
