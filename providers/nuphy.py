"""NuPhy keyboards: WH80 over USB and Halo96 V2 over its 2.4 GHz receiver.

WH80 (`19f5:a011`, USB cable): NuPhyIO's read-only GetFunc query on the
unnumbered 64-byte reports of interface 1, usage 0001:0000. The reply's byte 8
holds the percentage in bits 0..6 and the charging flag in bit 7. The official
firmware reports in 10 % steps, so the level is shown as reported.

Halo96 V2 (`19f5:3247`, 2.4 GHz receiver): only with the custom QMK/VIA
firmware from ollycrossley/nuphy-custom-firmware. The keyboard broadcasts a
consumer-control usage sequence about every 15 s: an optional 0x0f charging
prefix, 0x07 start, two digit usages (0x08 + high nibble, 0x10 + low nibble)
and a 0x00 release. A dedicated reader keeps the timing of that burst without
making the normal poll wait for the next broadcast; samples expire after 35 s.

Protocol and hardware evidence: BatteryHub 1.1.1 docs/nuphy.md and
docs/nuphy-halo96-v2.md (GPL-3.0-or-later; protocol reference only), itself
based on NuPhyIO's SDK and a Halo96 V2 capture. The WH80 Bluetooth id
(`19f5:324a`) is left to the normal Windows Bluetooth battery property; it is
not a HID collection this provider can open.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Dict, List, Optional, Tuple

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VID = 0x19F5
WH80_PID = 0xA011
HALO96_PID = 0x3247

WH80_REQUEST = bytes([0x55, 0x05, 0x00, 0x00, 0x01, 0x20, 0x00, 0x00])
WH80_REPORT_LEN = 64
WH80_DEADLINE_S = 0.4

HALO96_PRODUCT = "NuPhy Halo96 V2 Dongle"
HALO96_REPORT_LEN = 3
HALO96_READ_TIMEOUT_MS = 250
HALO96_SAMPLE_LIFETIME_S = 35.0
HALO96_GAP_S = 0.5


def make_wh80_request() -> bytes:
    """The 65-byte wire report: report id 0, then the SDK's 64-byte body."""
    body = bytearray(WH80_REPORT_LEN)
    body[0:8] = WH80_REQUEST
    body[3] = sum(body[4:]) & 0xFF
    return bytes([0x00]) + bytes(body)


def _strip_report_id(response) -> Optional[bytes]:
    if not response:
        return None
    b = bytes(response)
    if len(b) == WH80_REPORT_LEN + 1 and b[0] == 0x00:
        b = b[1:]
    return b if len(b) == WH80_REPORT_LEN else None


def parse_wh80(response) -> Optional[Tuple[int, bool]]:
    """(percent, charging) from a GetFunc reply, or None when it is not one."""
    b = _strip_report_id(response)
    if b is None:
        return None
    if b[0] != 0xAA or b[1] != 0x05 or b[2] != 0x00:
        return None
    if b[4] != 0x01 or b[5] != 0x20 or b[6] != 0x00 or b[7] != 0x00:
        return None
    if (sum(b[4:]) & 0xFF) != b[3]:
        return None
    raw = b[8]
    level = raw & 0x7F
    if level > 100:
        return None
    return level, bool(raw & 0x80)


class Halo96Frame:
    """The custom firmware's consumer-control usage sequence."""

    START = 0
    CHARGING = 1
    HIGH = 2
    LOW = 3
    RELEASE = 4

    def __init__(self):
        self.next = self.START
        self.started = 0.0
        self.level = 0
        self.charging = False

    def consume(self, report, now: float) -> Optional[Tuple[int, bool]]:
        if not report or len(report) < HALO96_REPORT_LEN or report[0] != 0x03:
            return None
        usage = report[1] | (report[2] << 8)
        if usage == 0x0F:
            self.next = self.CHARGING
            self.started = now
            return None
        if usage == 0x07:
            self.charging = (self.next == self.CHARGING
                             and now - self.started <= HALO96_GAP_S)
            self.next = self.HIGH
            self.started = now
            return None
        if now - self.started > HALO96_GAP_S:
            self.next = self.START
            return None
        if self.next == self.HIGH and 0x08 <= usage <= 0x0E:
            self.level = (usage - 0x08) << 4
            self.next = self.LOW
            return None
        if self.next == self.LOW and 0x10 <= usage <= 0x1F:
            self.level |= usage - 0x10
            self.next = self.RELEASE
            return None
        if self.next == self.RELEASE and usage == 0x00 and self.level <= 100:
            self.next = self.START
            return self.level, self.charging
        self.next = self.START
        return None


class _Halo96Reader:
    """One receiver, read continuously; the poll only takes the latest sample."""

    def __init__(self, path: bytes):
        self.path = path
        self._lock = threading.Lock()
        self._sample: Optional[Tuple[int, bool, float]] = None
        self._diag: List[str] = []
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="nuphy-halo96", daemon=True)
        self.thread.start()

    def take(self) -> Optional[Tuple[int, bool]]:
        with self._lock:
            sample = self._sample
        if sample is None:
            return None
        level, charging, when = sample
        if time.monotonic() - when > HALO96_SAMPLE_LIFETIME_S:
            return None
        return level, charging

    def diagnostics(self) -> List[str]:
        with self._lock:
            return list(self._diag)

    def stop(self) -> None:
        self._stop.set()

    def _note(self, text: str) -> None:
        with self._lock:
            self._diag.append(text)

    def _run(self) -> None:
        dev = hid.device()
        try:
            dev.open_path(self.path)
        except (OSError, IOError) as e:
            self._note(f"  open: {e}")
            return
        frame = Halo96Frame()
        try:
            while not self._stop.is_set():
                try:
                    report = dev.read(HALO96_REPORT_LEN, HALO96_READ_TIMEOUT_MS)
                except (OSError, ValueError) as e:
                    self._note(f"  read: {e}")
                    return
                if not report:
                    continue
                parsed = frame.consume(report, time.monotonic())
                if parsed is None:
                    continue
                with self._lock:
                    self._sample = (parsed[0], parsed[1], time.monotonic())
                    self._diag.append(f"  battery report: {hexdump(report, 4)} -> {parsed[0]}%"
                                      f"{' charging' if parsed[1] else ''}")
        finally:
            try:
                dev.close()
            except Exception:
                pass


def _identity(path: bytes) -> str:
    return hashlib.sha256(b"path:" + bytes(path)).hexdigest()[:12]


class NuphyProvider(Provider):
    name = "nuphy"

    def __init__(self):
        self._diag: List[str] = []
        self._readers: Dict[bytes, _Halo96Reader] = {}

    def _wh80(self, infos: List[dict]) -> Optional[DeviceStatus]:
        mine = [d for d in infos if d.get("product_id") == WH80_PID]
        if not mine:
            return None
        picked = None
        for d in mine:
            if (d.get("interface_number") == 1
                    and (d.get("usage_page"), d.get("usage")) == (0x0001, 0x0000)):
                picked = d
                break
        if picked is None:
            self._diag.append(f"[NuPhy] WH80: no interface 1 0001:0000 collection "
                              f"among {len(mine)}; not queried")
            return None
        dev = hid.device()
        try:
            dev.open_path(picked["path"])
        except (OSError, IOError) as e:
            self._diag.append(f"[NuPhy] WH80 open: {e}")
            return None
        try:
            for _ in range(4):
                if not dev.read(WH80_REPORT_LEN, 30):
                    break
            dev.write(make_wh80_request())
            deadline = time.monotonic() + WH80_DEADLINE_S
            while time.monotonic() < deadline:
                try:
                    r = dev.read(WH80_REPORT_LEN, 100)
                except (OSError, ValueError) as e:
                    self._diag.append(f"[NuPhy] WH80 read: {e}")
                    return None
                if not r:
                    continue
                self._diag.append(f"[NuPhy] WH80 reply: {hexdump(r, 12)}")
                parsed = parse_wh80(r)
                if parsed is None:
                    continue
                level, charging = parsed
                return DeviceStatus("nuphy:wh80", "NuPhy WH80", level, charging,
                                    True, "nuphy", kind="keyboard")
            self._diag.append("[NuPhy] WH80: no battery reply")
            return None
        except (OSError, ValueError) as e:
            self._diag.append(f"[NuPhy] WH80 query: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _halo96(self, infos: List[dict]) -> Optional[DeviceStatus]:
        mine = [d for d in infos if d.get("product_id") == HALO96_PID]
        if not mine:
            return None
        present = set()
        out = None
        for d in mine:
            if (d.get("interface_number") != 1
                    or (d.get("usage_page"), d.get("usage")) != (0x000C, 0x0001)
                    or (d.get("product_string") or "").strip() != HALO96_PRODUCT):
                continue
            path = d["path"]
            present.add(path)
            reader = self._readers.get(path)
            if reader is None or not reader.thread.is_alive():
                if reader is not None:
                    reader.stop()
                reader = self._readers[path] = _Halo96Reader(path)
            sample = reader.take()
            self._diag += reader.diagnostics()
            if sample is None:
                self._diag.append("[NuPhy] Halo96 V2: no fresh battery broadcast yet")
                continue
            level, charging = sample
            out = DeviceStatus(f"nuphy:halo96:{_identity(path)}", "NuPhy Halo96 V2",
                               level, charging, True, "nuphy", kind="keyboard")
            break
        for path in [p for p in self._readers if p not in present]:
            self._readers.pop(path).stop()
        if out is None and not present:
            self._diag.append("[NuPhy] Halo96 V2 receiver id present, but not the "
                              "'NuPhy Halo96 V2 Dongle' product; not opened")
        return out

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(nuphy): %s", e)
            return []
        out = []
        wh80 = self._wh80(infos)
        if wh80 is not None:
            out.append(wh80)
        halo = self._halo96(infos)
        if halo is not None:
            out.append(halo)
        return out

    def diagnostics(self) -> List[str]:
        return list(self._diag)
