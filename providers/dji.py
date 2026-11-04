"""DJI wireless microphones (Mic Mini / Mic Mini 2) over USB, without the vendor app.

The receiver (`2ca3:4011`, "Wireless Mic Rx") hides its status on interface 6, a
vendor-specific USB interface (class ff / subclass f0) with bulk IN 0x86 and bulk OUT
0x06 - not on HID. Its four HID collections (consumer control, telephony and two
64-byte vendor pages ff90/ff82) are silent, and there is no battery anywhere else:
Windows reports nothing for a USB microphone.

Frames are DJI's DUML framing: 0x55, total length, 0x04, a CRC-8 over the first three
bytes (a function of length alone), the body, then a CRC-16 (reflected 0x8408 / X-25).
v2 firmware seeds the CRC-16 with 0x3692 and the whole frame then checksums to 0.
The device pushes on its own; nothing is ever sent to it.

v2 firmware streams three pushes: a *status* push carrying one 32-byte slot
per connected transmitter, an *identity* push (serials and product names) and an
*audio-level* push. Measured here: status ~2 Hz, audio level ~16 Hz, identity ~1 Hz. In the status push, header offset 44 is the
authoritative connected bitmask (0x01 = TX1, 0x02 = TX2) and each transmitter's slot
carries, at slot offset +7:

    0x02          the transmitter is docked / charging (per TX)
    (byte>>2)&7   the battery gauge, per TX: 1 = full ... 7 = the terminal reading
                  immediately before the transmitter shuts off

That gauge is a seven-step gauge, not a percentage, so the tray shows an estimate and
the tooltip says so (`approx`), with the raw gauge always in the diagnostics. A
transmitter that is switched off simply has no slot and loses its bit in the bitmask,
so its icon leaves the tray - the same as any switched-off device. That has to survive a
brief dropout, so a transmitter counts as off only after GRACE_PUSHES consecutive pushes
without its bit.

Protocol from ShadowBitBasher/DJI-Mic-Control (PROTOCOL.md, Unlicense): its usb id
table is this receiver's id, and its Mic Mini model is what "DJI Mic Mini 2"
transmitters report themselves as. Frame layout, both CRC seeds and the gauge were
re-derived against a live receiver here - 118-byte status pushes, 182-byte identity
pushes naming both transmitters, every frame CRC-valid.

Windows cannot open interface 6 until WinUSB (or libusbK) is bound to it: run Zadig
once, pick the receiver's "Interface 6", target driver WinUSB, Replace Driver, then
replug. Nothing else changes - the audio path keeps its own driver. Without pyusb, or
with the interface still on no driver, this provider reports nothing and says so in
the diagnostics instead of breaking the poll.
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional, Tuple

try:
    import usb.core
    import usb.util
except ImportError:                    # pragma: no cover - depends on the install
    usb = None

try:
    import libusb_package
except ImportError:                    # pragma: no cover
    libusb_package = None

from .base import DeviceStatus, Provider

VID, PID = 0x2CA3, 0x4011
IFACE = 6
EP_IN = 0x86
READ_TIMEOUT_MS = 1000
RETRY_SECONDS = 5.0                    # after a failed open/read, before trying again
STALE_SECONDS = 8.0                    # a transmitter not heard for this long is dropped
GRACE_PUSHES = 3                       # ... but a slot missing for fewer consecutive pushes than
                                       # this is a dropout, not a switched-off transmitter
                                       # (~1.5 s at 2 Hz, well inside one poll interval)

# The gauge is 1..7 with 1 = full and 7 = terminal, so a percentage is an estimate.
# The mapping is deliberately conservative at the bottom end and is labelled as an
# estimate in the tooltip (see `approx` below).
GAUGE_TO_PERCENT = {1: 100, 2: 90, 3: 75, 4: 60, 5: 35, 6: 15, 7: 5}

# CRC-16 X-25 (reflected 0x8408), the polynomial DJI uses on every packet.
_T = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0x8408 if _c & 1 else _c >> 1
    _T.append(_c)


def crc16(data, init: int = 0x0000) -> int:
    crc = init
    for b in data:
        crc = (crc >> 8) ^ _T[(crc ^ b) & 0xFF]
    return crc


def crc_ok(frame) -> bool:
    """v2 firmware seeds with 0x3692 so the whole frame lands on 0; v1 seeds 0x0000
    for a residue of 0xbb01. Accept either, so one parser reads both firmwares."""
    return crc16(frame, 0x3692) == 0 or crc16(frame, 0x0000) == 0xBB01


def split_frames(buf: bytes) -> Tuple[List[bytes], bytes]:
    """(whole DUML frames, leftover). 0x55 + length + 0x04; the 182-byte identity push
    is the largest frame either firmware sends, so a partial tail is kept, never lost."""
    out, i, n = [], 0, len(buf)
    while i + 3 < n:
        if buf[i] != 0x55 or buf[i + 2] != 0x04:
            i += 1
            continue
        ln = buf[i + 1]
        if ln < 14:
            i += 1
            continue
        if i + ln > n:
            break           # the rest of this frame has not arrived yet: keep it
        out.append(bytes(buf[i:i + ln]))
        i += ln
    return out, bytes(buf[i:])


def dialect(frame: bytes) -> str:
    """v2 firmware marks its pushes with `5b 03 03` at offsets 9..11, v1 with `5b 03 00`."""
    if len(frame) > 11 and frame[9] == 0x5B and frame[10] == 0x03:
        return "v2" if frame[11] == 0x03 else "v1"
    return "?"


def identity_records(frame: bytes) -> Dict[str, dict]:
    """Records `[tag][unit][reserved:3][len][data]` from offset 14 of an identity or
    audio-level push -> {"rx"|"tx1"|"tx2": {...}}."""
    units: Dict[str, dict] = {}
    i, end = 14, max(14, len(frame) - 2)
    while i + 6 <= end:
        tag, unit, ln = frame[i], frame[i + 1], frame[i + 5]
        data = frame[i + 6:i + 6 + ln]
        if len(data) != ln:
            break
        name = {0: "rx", 1: "tx1", 2: "tx2"}.get(unit)
        if name:
            info = units.setdefault(name, {})
            if tag == 0x01 and len(data) >= 18:      # firmware + serial
                info["firmware"] = "%d.%d.%d.%d" % (data[3], data[2], data[1], data[0])
                info["serial"] = data[4:18].decode("ascii", "replace").strip("\x00 ")
            elif tag == 0x06:                        # product name
                info["product"] = data.decode("ascii", "replace").strip("\x00 ")
        i += 6 + ln
    return units


def record_tags(frame: bytes) -> set:
    """The record tags present in an identity/level push (0x01 firmware+serial,
    0x05 audio level, 0x06 product name). The two pushes have similar lengths, so
    the length alone cannot tell them apart."""
    tags, i, end = set(), 14, max(14, len(frame) - 2)
    while i + 6 <= end:
        tag, ln = frame[i], frame[i + 5]
        if len(frame[i + 6:i + 6 + ln]) != ln:
            break
        tags.add(tag)
        i += 6 + ln
    return tags


def decode_status(frame: bytes) -> Optional[dict]:
    """A v2 status push -> {"tx1"/"tx2": {unit, gauge, charging}} (absent transmitters
    simply have no slot), or None when this is not one."""
    if dialect(frame) != "v2" or len(frame) not in (54, 86, 118):
        return None
    out: Dict[str, dict] = {}
    for i in range((len(frame) - 54) // 32):
        slot = frame[52 + 32 * i:52 + 32 * i + 32]
        unit = slot[1]                            # physical TX number, not slot position
        if unit not in (1, 2):
            continue
        gauge = (slot[7] >> 2) & 0x07
        out["tx%d" % unit] = {"unit": unit, "gauge": gauge,
                              "charging": bool(slot[7] & 0x02), "raw": slot.hex(" ")}
    return out


class _Receiver(threading.Thread):
    """Keeps the receiver open and caches the newest state of every transmitter.

    The device pushes ~10 Hz; draining it continuously means a poll never waits, a
    frame spanning two reads is not lost, and a re-plug is picked up by itself.
    """

    def __init__(self):
        super().__init__(daemon=True, name="dji-mic")
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.state: Dict[str, dict] = {}          # tx1/tx2 -> newest known fields
        self.info: Dict[str, dict] = {}           # identities, kept until re-connect
        self.error = ""
        self.opened = False
        self.counts: Dict[str, int] = {}
        self.dialect = "?"
        self.bitmask = 0                  # header offset 44 of the newest status push

    # --- thread body -----------------------------------------------------------
    def run(self):
        while not self._stop.is_set():
            try:
                self._session()
            except Exception as exc:              # unplugged, or the driver was lost
                self.error = "%s: %s" % (type(exc).__name__, exc)
                self.opened = False
                self._stop.wait(RETRY_SECONDS)

    def _session(self):
        if usb is None:
            raise RuntimeError("pyusb is not installed")
        backend = libusb_package.get_libusb1_backend() if libusb_package else None
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
        if dev is None:
            raise RuntimeError("receiver %04x:%04x not found" % (VID, PID))
        try:
            usb.util.claim_interface(dev, IFACE)
        except Exception as exc:
            raise RuntimeError(
                "cannot open interface %d (%s) - bind WinUSB to it with Zadig "
                "(pick the DJI receiver's 'Interface 6', then Replace Driver)"
                % (IFACE, exc))
        self.error = ""
        self.opened = True
        buf = b""
        try:
            while not self._stop.is_set():
                try:
                    chunk = dev.read(EP_IN, 4096, timeout=READ_TIMEOUT_MS)
                except usb.core.USBTimeoutError:
                    continue
                got, buf = split_frames(buf + bytes(chunk))
                for frame in got:
                    self._absorb(frame)
        finally:
            try:
                usb.util.release_interface(dev, IFACE)
            except Exception:
                pass
            self.opened = False

    def _absorb(self, frame: bytes):
        # Every push carries a CRC-16, so a frame that does not verify is never acted
        # on: a mis-split byte sequence can then never move a battery level.
        if not crc_ok(frame):
            self.counts["bad-crc"] = self.counts.get("bad-crc", 0) + 1
            return
        seen_dialect = dialect(frame)
        if seen_dialect != "?":       # the 14-byte ACKs carry no version marker
            self.dialect = seen_dialect
        status = decode_status(frame)
        if status is not None:
            kind = "status"
        else:
            tags = record_tags(frame)
            if tags & {0x01, 0x06}:
                kind = "identity"
            elif 0x05 in tags:
                kind = "level"      # audio level: not a battery, counted only
            elif len(frame) == 14:
                kind = "ack"
            else:
                kind = "other"
        self.counts[kind] = self.counts.get(kind, 0) + 1
        now = time.time()
        with self._lock:
            if kind == "status":
                connected = frame[44] if len(frame) > 44 else 0
                self.bitmask = connected
                for name, tx in status.items():
                    entry = self.state.setdefault(name, {})
                    entry.update(tx)
                    entry["misses"] = 0
                    entry.pop("gone", None)
                    entry["seen"] = now
                # A transmitter is present while its bit in header offset 44 is set - the
                # receiver clears it the moment the transmitter disconnects (measured 0x03
                # with both on, 0x01 with TX2 switched off). A slot that has gone missing
                # only counts as "off" after GRACE_PUSHES consecutive pushes, so a brief
                # dropout does not make the icon blink out and back.
                for name in list(self.state):
                    bit = {"tx1": 0x01, "tx2": 0x02}.get(name)
                    if bit and connected & bit:
                        continue
                    entry = self.state[name]
                    entry["misses"] = entry.get("misses", 0) + 1
                    if entry["misses"] >= GRACE_PUSHES:
                        entry["gone"] = now
            elif kind == "identity":
                # identities arrive on their own push: keep what is already known
                # rather than resetting it on every status tick
                for name, info in identity_records(frame).items():
                    self.info.setdefault(name, {}).update(info)

    # --- api -------------------------------------------------------------------
    def snapshot(self):
        with self._lock:
            return ({k: dict(v) for k, v in self.state.items()},
                    {k: dict(v) for k, v in self.info.items()},
                    dict(self.counts), self.dialect, self.error, self.opened, self.bitmask)

    def stop(self):
        self._stop.set()


class DjiProvider(Provider):
    """Two tray entries, one per transmitter, read from the receiver's status stream."""

    name = "dji"

    def __init__(self, start: bool = True):
        # The receiver pushes on its own, so the reader is started with the provider:
        # by the time the app asks for a level the newest state is already cached, and
        # draining it in the background means a re-plug needs no retry from the poll.
        self._receiver = _Receiver()
        self._diag: List[str] = []
        if start:
            self._receiver.start()

    def _reader(self) -> _Receiver:
        return self._receiver

    def poll(self) -> List[DeviceStatus]:
        if usb is None:
            self._diag = ["[DJI] pyusb is not installed - the microphone provider is off "
                          "(pip install pyusb libusb-package)"]
            return []
        rx = self._reader()
        state, info, counts, dialect_name, error, opened, bitmask = rx.snapshot()
        now = time.time()
        out: List[DeviceStatus] = []
        for name in ("tx1", "tx2"):
            entry = state.get(name)
            if not entry:
                continue
            if entry.get("gone"):
                continue                       # switched off: its icon leaves the tray
            if now - entry.get("seen", 0) > STALE_SECONDS:
                continue
            serial = info.get(name, {}).get("serial") or ""
            product = info.get(name, {}).get("product") or "DJI Mic"
            gauge = entry.get("gauge") or 0
            level = GAUGE_TO_PERCENT.get(gauge)
            approx = "" if level is None else "about %d%% (gauge %d of 7)" % (level, gauge)
            key = "dji:%s" % (serial or name)
            out.append(DeviceStatus(key=key, name="%s %s" % (product, name.upper()),
                                    level=level, charging=bool(entry.get("charging")),
                                    online=True, source="dji", approx=approx, kind="microphone"))
        lines = ["[DJI] %s" % ("receiver open, interface %d" % IFACE if opened
                               else (error or "receiver not found")),
                 "[DJI] libusb: %s" % (libusb_package.get_library_path() if libusb_package
                                       else "pyusb default"),
                 "[DJI] frames: %s" % (", ".join("%s x%d" % kv for kv in sorted(counts.items()))
                                       or "none"), "[DJI] dialect: %s, connected=0x%02x"
                 % (dialect_name, bitmask)]
        for name in ("tx1", "tx2"):
            entry, meta = state.get(name), info.get(name, {})
            if not entry:
                lines.append("[DJI] %s: no slot (%d missed pushes: switched off, or out of range)"
                             % (name, entry.get("misses", 0) if entry else 0))
                continue
            lines.append("[DJI] %s: %s serial=%s fw=%s gauge=%d charging=%s seen=%.1fs ago"
                         % (name, meta.get("product", "?"), meta.get("serial", "?"),
                            meta.get("firmware", "?"), entry.get("gauge", 0),
                            bool(entry.get("charging")), now - entry.get("seen", now)))
            if entry.get("raw"):
                lines.append("[DJI] %s raw slot: %s" % (name, entry["raw"]))
        self._diag = lines
        return out

    def diagnostics(self) -> List[str]:
        return self._diag
