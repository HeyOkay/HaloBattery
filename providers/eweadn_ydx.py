"""EWEADN X23 Pro, 2.4 GHz receiver 04F3:026F (YDX protocol).

Source: https://hub.eweadn.cn/deviceJson/other.json maps this receiver to
https://hub.eweadn.cn/ydx/web/. In assets/index-BvEqCbAL.js, HG() calls
receiveFeatureReport(5) to request status; its inputreport handler decodes
report 4. DeviceView-ss1Fdo4r.js maps battery status 1 to charging, 2 to full.
Pinned receiver/size evidence: HeyOkay/HaloBattery PR #406,
ad77d41e109814df2c19d8abbe03e6000ccaad43/providers/eweadn_ydx.py.
Vendor main bundle SHA256 ca5d68f36d8b276a7ff4386b8fc4f478b51d22f6e2813bc0f2e025e4ecefd402;
device-view SHA256 80b55838f8bb10d13f6468aacc3225fbdfb15e7f0761bca0ed425d7cc1928870.
Both collections have usage page 000A, usage 0000 on interface 1. The Windows
descriptors declare 31 payload bytes: input report 4 on one collection,
feature report 5 on another. Open both for the SAME receiver, then GET_FEATURE
5 triggers input report 4. hidapi includes the report ID; WebHID does not.
No output report, SET_FEATURE, configuration or firmware command is sent.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from typing import List

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump
from .endgame_we import receiver_path

VID, PID = 0x04F3, 0x026F
REPORT_LEN = 32
TIMEOUT = 1.5
DRAIN_LIMIT = 32
MAX_RECEIVERS = 256
DEFERRED = object()  # Budget prevented a complete status query/wait, not device-off evidence.


def parse_status(data):
    """(level, charging, online, charging_known), or None for an invalid report."""
    if (not data or len(data) != REPORT_LEN
            or any(type(value) is not int or not 0 <= value <= 255 for value in data)
            or data[0] != 4 or data[1] not in (0, 1)):
        return None
    if not data[1]:
        return None, False, False, False
    level, charge = data[4], data[5]
    if not 0 <= level <= 100:
        return None
    return 100 if charge == 2 else level, charge == 1, True, charge in (0, 1, 2)


def descriptor_role(raw):
    """Legacy HIDAPI only: aggregate short main items under their report ID.

    HID 1.11 6.2.2.2/6.2.2.7: Report Size/Count/ID are global state;
    https://www.usb.org/sites/default/files/hid1_11.pdf
    Input/Output/Feature contribute bits to that report. Push/pop preserve
    the state. Substrings cannot associate a main item with its report.
    Unknown/long/truncated forms or ambiguous report layouts fail closed.
    """
    if (not raw or len(raw) > 4096
            or any(type(value) is not int or not 0 <= value <= 255 for value in raw)):
        return None
    data = bytes(raw)
    size = count = report_id = 0
    stack = []
    reports = {8: {}, 9: {}, 11: {}}
    offset = 0
    while offset < len(data):
        prefix = data[offset]
        offset += 1
        if prefix == 0xFE:  # Long items are unnecessary for the captured layout.
            return None
        length = (0, 1, 2, 4)[prefix & 3]
        if offset + length > len(data):
            return None
        value = int.from_bytes(data[offset:offset + length], "little")
        offset += length
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:
            if tag == 7:
                size = value
            elif tag == 8:
                if length != 1 or not 1 <= value <= 255:
                    return None
                report_id = value
            elif tag == 9:
                count = value
            elif tag == 10:
                if length or len(stack) >= 16:
                    return None
                stack.append((size, count, report_id))
            elif tag == 11:
                if length or not stack:
                    return None
                size, count, report_id = stack.pop()
            elif tag > 6:
                return None
        elif kind == 0 and tag in reports:
            if not length or not report_id or not 0 < size <= 32 or not 0 < count <= 4096:
                return None
            bits = reports[tag].get(report_id, 0) + size * count
            if bits > 4096 * 8:
                return None
            reports[tag][report_id] = bits
        elif kind == 0:
            if (tag not in (10, 12) or (tag == 10 and length != 1)
                    or (tag == 12 and length != 0)):
                return None
        elif kind == 3:
            return None
    if stack or reports[9]:
        return None
    inputs, features = reports[8], reports[11]
    if inputs == {4: 248} and not features:
        return "input"
    if (not inputs and features.get(5) == 248
            and set(features) <= {5, 6} and all(bits == 248 for bits in features.values())):
        return "feature"
    return None


def collection_role(dev):
    """Use native cached caps first; never replace a rejected native contract."""
    metadata = getattr(dev, "get_report_lengths", None)
    if callable(metadata):
        lengths = metadata()
        if (not isinstance(lengths, dict) or set(lengths) != {"input", "output", "feature"}
                or any(type(value) is not int or value < 0 for value in lengths.values())):
            return None
        if lengths == {"input": 32, "output": 0, "feature": 0}:
            return "input"
        if lengths == {"input": 0, "output": 0, "feature": 32}:
            return "feature"
        return None
    descriptor = getattr(dev, "get_report_descriptor", None)
    return descriptor_role(descriptor()) if callable(descriptor) else None


class EweadnYdxProvider(Provider):
    name = "eweadn_ydx"

    def __init__(self):
        self._diag: List[str] = []
        self._last = {}
        self._cursor = 0

    def _read(self, entries, deadline, receivers_left=1):
        # Exactly one of each role is required; never guess using ColXX.
        if len(entries) != 2:
            self._diag.append("  expected two unambiguous status collections")
            return None
        opened = []
        try:
            roles = {}
            for entry in entries:
                if time.monotonic() >= deadline:
                    return DEFERRED
                dev = hid.device()
                opened.append(dev)
                dev.open_path(entry["path"])
                if time.monotonic() >= deadline:
                    return DEFERRED
                role = collection_role(dev)
                if role is None or role in roles:
                    self._diag.append("  ambiguous or unsupported report contracts")
                    return None
                roles[role] = dev
            if set(roles) != {"input", "feature"}:
                return None
            reader, feature = roles["input"], roles["feature"]
            reader.set_nonblocking(True)
            for _ in range(DRAIN_LIMIT):
                if time.monotonic() >= deadline:
                    return DEFERRED
                if not reader.read(REPORT_LEN):
                    break
            else:
                self._diag.append("  status queue did not drain")
                return DEFERRED
            reader.set_nonblocking(False)
            if time.monotonic() >= deadline:
                return DEFERRED
            feature.get_feature_report(5, REPORT_LEN)
            now = time.monotonic()
            if now >= deadline:
                return DEFERRED
            # Share only the remaining RESPONSE wait after collection setup and
            # the trigger. Native calls/cleanup have separate transport bounds.
            response_deadline = now + (deadline - now) / receivers_left
            while True:
                remaining = response_deadline - time.monotonic()
                if remaining <= 0:
                    break
                data = reader.read(REPORT_LEN, timeout_ms=max(1, min(100, int(remaining * 1000))))
                if data and time.monotonic() >= response_deadline:
                    self._diag.append("  status arrived after response budget; deferred")
                    return DEFERRED
                result = parse_status(data)
                if result is not None:
                    self._diag.append(f"  input 04: {hexdump(data, 10)}")
                    return result
            self._diag.append("  no fresh status (mouse asleep/off, or receiver busy)")
        except (OSError, ValueError, AttributeError, TypeError) as error:
            self._diag.append(f"  read: {error}")
        finally:
            for dev in opened:
                try:
                    dev.close()
                except (OSError, ValueError):
                    pass
        return None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        groups = {}
        try:
            for entry in hidlist.enumerate(VID):
                if (entry.get("vendor_id") != VID or entry.get("product_id") != PID
                        or entry.get("interface_number") != 1
                        or entry.get("usage_page") != 0x000A or entry.get("usage") != 0):
                    continue
                path = entry.get("path")
                if isinstance(path, bytes) and path:
                    groups.setdefault(receiver_path(path), {})[path] = entry
        except (OSError, ValueError) as error:
            self._diag.append(f"[EWEADN YDX] enumerate: {error}")
            return []
        out = []
        # A shared protocol deadline bounds new work. Already-issued native
        # operations/cleanup retain their separate transport bounds.
        deadline = time.monotonic() + TIMEOUT
        ordered = sorted(groups.items())
        if len(ordered) > MAX_RECEIVERS:
            self._diag.append("[EWEADN YDX] excess receiver groups ignored")
            ordered = ordered[:MAX_RECEIVERS]
        keys = {"eweadn_ydx:" + hashlib.sha1(path).hexdigest()[:12] for path, _ in ordered}
        self._last = {key: status for key, status in self._last.items() if key in keys}
        if ordered:
            start = self._cursor % len(ordered)
            ordered = ordered[start:] + ordered[:start]
            # Rotation is independent of attempts: a final setup that exhausts
            # this poll's budget must not always lose on the next poll too.
            self._cursor = (start + 1) % len(ordered)
        else:
            self._cursor = 0
        for index, (path, by_path) in enumerate(ordered):
            entries = list(by_path.values())
            key = "eweadn_ydx:" + hashlib.sha1(path).hexdigest()[:12]
            self._diag.append(f"[EWEADN YDX] X23 Pro 04f3:026f ({len(entries)} collections)")
            now = time.monotonic()
            result = self._read(entries, deadline, len(ordered) - index) if now < deadline else DEFERRED
            if result is DEFERRED:
                previous = self._last.get(key, DeviceStatus(key, "EWEADN X23 Pro",
                                                           source=self.name, kind="mouse"))
                out.append(replace(previous, online=False, charging=False,
                                   charging_known=False, polling_stale=True,
                                   polling_paused=False, wake_on_input=False,
                                   ui_params=dict(previous.ui_params)))
                self._diag.append("  status deferred; cached value is not a fresh reading")
                continue
            level, charging, online, known = result or (None, False, False, False)
            status = DeviceStatus(key, "EWEADN X23 Pro", level, charging, online,
                                  self.name, kind="mouse", wake_on_input=True,
                                  charging_known=known)
            out.append(status)
            if result is not None:
                self._last[key] = replace(status, ui_params=dict(status.ui_params))
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
