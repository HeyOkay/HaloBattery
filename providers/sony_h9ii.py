"""Sony INZONE H9 II receiver: read-only HID audio status, not H9 serial.

Protocol source (MIT), marcinjakubowski/zoneout at
49ebedb178f10c84c7e42e0e946c0ac971d08376:
  src/zoneout/protocol.py L8-10,L33-43: exact 054c:0fa8 and status offsets.
  src/zoneout/device.py L139-167: 64-byte output query 06, checksum A2,
    then input reads. SPECS.md defines charging 0/1 and the status offsets.
https://github.com/marcinjakubowski/zoneout/blob/49ebedb178f10c84c7e42e0e946c0ac971d08376/src/zoneout/device.py#L139-L167
The Windows collection (interface 5 FF04:0001) and test-build level confirmation
are in HaloBattery #387. Charging decoding is sourced, but the reporter did not
confirm a cable transition; no hardware was tested for this implementation.

Only the audio-status query below is sent. No H9 serial initialization, settings
commands, feature-report fallback or alternate report sizes are attempted.
"""
from __future__ import annotations

import hashlib
import time
from typing import List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID, PID = 0x054C, 0x0FA8
INTERFACE, PAGE, USAGE = 5, 0xFF04, 0x0001
REPORT_LEN = 64
QUERY = bytes.fromhex("02 0C 01 00 FC 08 96 C3 41 06 01 01 00 A2") + bytes(50)
DRAIN_LIMIT = 32
READ_LIMIT = 40
RECEIVER_BUDGET = 1.0
POLL_BUDGET = 3.0


def parse_status(data) -> Optional[Tuple[int, bool, bool]]:
    """Return level, charging, charging-known for a complete audio reply.

    The source has no response sequence/checksum validation or verified status
    response category. Do not require it to echo the request's magic/category.
    """
    if not isinstance(data, (bytes, bytearray, list, tuple)) or len(data) != REPORT_LEN:
        return None
    if any(type(value) is not int or not 0 <= value <= 255 for value in data):
        return None
    if data[0] != 0x02 or data[9] != 0x06:
        return None
    # Our exact outgoing query is not a battery measurement. Reject its echo
    # without inventing any fixed header for the actual status response.
    if bytes(data) == QUERY:
        return None
    level, charge = data[15], data[14]
    if level > 100:
        return None
    # SPECS.md defines 0/1. Other flag values do not invalidate a valid level,
    # but cannot be presented as a known charging state.
    return level, charge == 1, charge in (0, 1)


def path_key(path) -> Optional[str]:
    """A collection path identifies a receiver; enumeration order/serial do not."""
    if isinstance(path, bytes):
        try:
            path = path.decode("utf-8")
        except UnicodeError:
            return None
    if not isinstance(path, str) or not path or "\0" in path:
        return None
    try:
        normalized = path.casefold().encode("utf-8")
    except UnicodeError:
        return None
    return "sony_h9ii:" + hashlib.sha256(normalized).hexdigest()


class SonyH9IIProvider(Provider):
    name = "sony_h9ii"

    def __init__(self):
        self._diag: List[str] = []
        self._cursor = 0

    def _read(self, path, deadline) -> Optional[Tuple[int, bool, bool]]:
        dev = None
        try:
            dev = hid.device()
            dev.open_path(path)
            metadata = getattr(dev, "get_report_lengths", None)
            expected_write = None
            if metadata is not None and not callable(metadata):
                self._diag.append("  invalid report metadata helper; not querying")
                return None
            if callable(metadata):
                caps = metadata()
                # HIDP_CAPS contains the largest report per collection, including
                # its ID, not the size of report 2 alone. Native write pads to
                # output caps; native read uses input caps then returns 64 bytes.
                if (not isinstance(caps, dict)
                        or any(type(caps.get(kind)) is not int or caps[kind] < REPORT_LEN
                               for kind in ("input", "output"))):
                    self._diag.append("  incompatible input/output report contract; not querying")
                    return None
                expected_write = caps["output"]
            # Legacy hidapi has no cached-caps helper: use exactly the primary
            # source's 64-byte write/read, with no guessed fallback variants.
            for _ in range(DRAIN_LIMIT):
                if time.monotonic() >= deadline:
                    return None
                if not dev.read(REPORT_LEN, 1):
                    break
            else:
                self._diag.append("  input queue did not drain; not querying")
                return None
            if time.monotonic() >= deadline:
                return None
            sent = dev.write(QUERY)
            if (type(sent) is not int or sent < REPORT_LEN
                    or (expected_write is not None and sent != expected_write)):
                self._diag.append(f"  status write refused or short ({sent})")
                return None
            for _ in range(READ_LIMIT):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                data = dev.read(REPORT_LEN, max(1, min(25, int(remaining * 1000))))
                result = parse_status(data)
                if result is not None:
                    self._diag.append(f"  audio status: {hexdump(data, 16)}")
                    return result
            self._diag.append("  no matching audio-status reply")
        except (OSError, ValueError, TypeError) as exc:
            self._diag.append(f"  HID: {exc}")
        finally:
            if dev is not None:
                try:
                    dev.close()
                except (OSError, ValueError):
                    pass
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        deadline = time.monotonic() + POLL_BUDGET
        try:
            infos = hidlist.enumerate(VID)
        except Exception as exc:  # pragma: no cover
            log.warning("hid.enumerate(sony_h9ii): %s", exc)
            return []
        candidates = {}
        fields = ("vendor_id", "product_id", "interface_number", "usage_page", "usage")
        for info in infos:
            if (not isinstance(info, dict)
                    or any(type(info.get(field)) is not int for field in fields)
                    or tuple(info[field] for field in fields) != (VID, PID, INTERFACE, PAGE, USAGE)):
                continue
            key = path_key(info.get("path"))
            if key is not None:
                candidates.setdefault(key, info["path"])
        keys = sorted(candidates)
        if not keys:
            self._cursor = 0
            return []
        start = self._cursor % len(keys)
        keys = keys[start:] + keys[:start]
        out = []
        for offset, key in enumerate(keys):
            now = time.monotonic()
            if now >= deadline:
                self._diag.append("  poll budget reached; remaining receivers deferred")
                break
            # Protocol reads get one second per receiver; native ownership and
            # cleanup have separate transport bounds. Resume after the last
            # attempted receiver on the next poll if the shared budget expires.
            self._cursor = (start + offset + 1) % len(keys)
            self._diag.append(f"[Sony H9 II] pid=0fa8 iface=5 ff04:0001 {key}")
            result = self._read(candidates[key], min(deadline, now + RECEIVER_BUDGET))
            if result is not None:
                level, charging, known = result
                out.append(DeviceStatus(key, "Sony INZONE H9 II", level, charging, True,
                                        self.name, kind="headset", charging_known=known))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
