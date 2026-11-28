"""Waizowl OGM mice on their 8K 2.4 GHz receiver (1915:0728, product string "OGM 8K").

Protocol taken from Waizowl's own web driver, Waizowl Hub (https://owlnest.waizowl.com/, linked
from waizowl.com under "W Hub"). Its bundle ``assets/js/index-BRQYaWvg.js`` has a device module
for this receiver:

    productId: 1832 (0x0728), vendorId: 6421 (0x1915), reportId: 0, wirelessOrUsb: "wireless",
    Name: "OGM 8K", Validate: {usage: 1, usagePage: 65290 (0xFF0A)},
    request: {DRIVER_NOTIFY_CMD: [65, 0, 129, 1], DRIVER_NOTIFY_DONGLE_STATUS: [1, 0, 129, 1], ...}
    responseRouter: DRIVER_NOTIFY_DATA = first byte 65 and byte 3 in (1, 3)
                    DRIVER_NOTIFY_DONGLE_STATUS = first byte 1 and byte 3 == 1
    parsers: parseNotify(e) -> {battery: e[6], currentConfig: e[7] + 1, hz: e[9], currentDPI: e[8]}
             parseDongleStatus(e) -> {sleepStatus: e[4] >> 3 & 1, ...}   (1 = mouse connected)

Both requests are in the module's ``initSequence``, which the hub sends every time it opens the
mouse. Every command goes through the bundle's ``getCheckSum`` (exported as ``x``):

    pad the command with zeros to 63 bytes, then byte 63 = (0xA1 - sum(bytes 0..62)) & 0xFF

and is sent with ``sendReport(0, frame)``, so the 64-byte frame goes out on report id 0.

Measured on hardware (OGM Pro V2 on the OGM 8K receiver):

    -> 01 00 81 01 00 .. 00 1E    dongle status
    <- 01 00 8C 01 0A 15 19 28 07 06 01 F5 07 00 .. 00 A9      byte 4 bit 3 = 1: mouse awake
    -> 41 00 81 01 00 .. 00 DE    notify
    <- 41 00 8C 01 00 00 2E 00 01 03 06 00 .. 00 9B            byte 6 = 0x2E = 46 %

The hub routes replies by their first byte and byte 3 only (``responseRouter``) and never checks
a checksum on them, so neither does this parser. The last byte is not a dependable checksum: it
made all 64 bytes sum to 0xA1 at 46 %, but at 29 % the mouse answered

    <- 41 00 8C 01 00 00 1D 00 01 03 06 00 .. 00 2C

where that rule would give 0xAC. Refusing such frames hid the mouse while its battery ran low.
The hub does not read a charging flag for this receiver, so none is claimed.

On a USB cable the OGM Pro V2 enumerates as 1915:07A2 ("OGM V2 GV") and the receiver path is not
used. The hub's module for productId 1954 (0x07A2, wirelessOrUsb: "usb", Name: "OGM PRO V2", same
Validate 0xFF0A, reportId 0) has a different command table:

    request: {DRIVER_NOTIFY_CMD: [1, 0, 129, 1], ...}
    responseRouter: DRIVER_NOTIFY_DATA = first byte 1 and byte 3 in (1, 3)
    parsers: parseNotify(e) -> {battery: e[6], ...}

Measured on hardware while charging:

    -> 01 00 81 01 00 .. 00 1E
    <- 01 00 8C 01 00 01 64 00 01 03 03 00 .. 00 A7             byte 6 = 0x64 = 100 %

That 100 % is not the charge: the mouse read 29 % on the receiver just before it was plugged
in, and 35 % on the receiver right after it was unplugged. So the cable reply only tells that
the mouse is there and charging. It is shown like a Logitech mouse that does not report its
level while charging: "about N % before it was plugged in" from the last receiver reading, or
"level not reported while charging" when there is none. The wired mouse and the receiver share
one tray icon; the cable wins while both are plugged in.

Only the FF0A:0001 collection (the hub's ``Validate``) of these vendor/product pairs is written
to, and only with these read requests. A refused or short write stops the exchange.
"""
from __future__ import annotations

import time
from typing import List, Optional

import hid

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

VENDOR = 0x1915
RECEIVER = 0x0728
WIRED = 0x07A2
PRODUCTS = {RECEIVER: "Waizowl OGM 8K",
            WIRED: "Waizowl OGM Pro V2"}
# one icon per mouse: the cabled OGM Pro V2 is the mouse that sits on the 8K receiver
KEY = "waizowl:0728"
USAGE_PAGE = 0xFF0A
USAGE = 0x0001
REPORT_ID = 0x00

FRAME_LEN = 64
CHECKSUM_BASE = 0xA1

CMD_NOTIFY = (0x41, 0x00, 0x81, 0x01)
CMD_DONGLE = (0x01, 0x00, 0x81, 0x01)
CMD_WIRED_NOTIFY = (0x01, 0x00, 0x81, 0x01)   # the wired module's DRIVER_NOTIFY_CMD

ATTEMPTS = 2
REPLY_WAIT = 0.25
READ_TIMEOUT_MS = 50


def make_frame(cmd) -> bytes:
    """The hub's getCheckSum: zero-pad to 63 bytes, then (0xA1 - sum) & 0xFF."""
    body = list(cmd) + [0] * (FRAME_LEN - 1 - len(cmd))
    return bytes(body + [(CHECKSUM_BASE - sum(body)) & 0xFF])


def _valid(resp) -> Optional[bytes]:
    """A full 64-byte reply, or None. No checksum check: see the module docstring."""
    if not resp or len(resp) < FRAME_LEN:
        return None
    return bytes(resp[:FRAME_LEN])


def parse_notify(resp) -> Optional[int]:
    """Battery level from a DRIVER_NOTIFY_DATA reply, or None."""
    r = _valid(resp)
    if r is None or r[0] != 0x41 or r[3] not in (1, 3):
        return None
    level = r[6]
    if level > 100:
        return None
    return level


def parse_wired_notify(resp) -> Optional[int]:
    """Battery level from the cabled mouse's DRIVER_NOTIFY_DATA reply, or None."""
    r = _valid(resp)
    if r is None or r[0] != 0x01 or r[3] not in (1, 3):
        return None
    level = r[6]
    if level > 100:
        return None
    return level


def parse_dongle(resp) -> Optional[bool]:
    """True when the receiver says the mouse is awake, False asleep, None if not this reply."""
    r = _valid(resp)
    if r is None or r[0] != 0x01 or r[3] != 0x01:
        return None
    return bool((r[4] >> 3) & 1)


class WaizowlProvider(Provider):
    name = "waizowl"

    def __init__(self):
        self._diag: List[str] = []
        self._last = {}

    def diagnostics(self) -> List[str]:
        return list(self._diag)

    def _ask(self, dev, cmd, parse):
        frame = make_frame(cmd)
        for attempt in range(ATTEMPTS):
            try:
                sent = dev.write(bytes([REPORT_ID]) + frame)
            except (OSError, ValueError) as e:
                self._diag.append(f"    write: {e}")
                return None
            if type(sent) is not int or sent != FRAME_LEN + 1:
                # a refused write must not pick up an unrelated reply still queued
                self._diag.append(f"    write refused/short ({sent})")
                return None
            deadline = time.time() + REPLY_WAIT
            while time.time() < deadline:
                try:
                    resp = dev.read(FRAME_LEN + 1, timeout_ms=READ_TIMEOUT_MS)
                except (OSError, ValueError):
                    return None
                if not resp:
                    continue
                got = parse(resp)
                if got is not None:
                    self._diag.append(
                        f"    answer 0x{cmd[0]:02x} (attempt {attempt + 1}): {hexdump(resp, 10)}")
                    return got
        return None

    def _open(self, path: bytes):
        dev = hid.device()
        try:
            dev.open_path(path)
        except (OSError, IOError) as e:
            self._diag.append(f"    open: {e}")
            return None
        try:
            dev.set_nonblocking(True)
        except Exception:
            pass
        return dev

    @staticmethod
    def _close(dev):
        try:
            dev.close()
        except Exception:
            pass

    def _read(self, path: bytes):
        """(awake, level) for one receiver; level None when the mouse did not answer."""
        dev = self._open(path)
        if dev is None:
            return None, None
        try:
            awake = self._ask(dev, CMD_DONGLE, parse_dongle)
            if awake is False:
                return False, None
            return awake, self._ask(dev, CMD_NOTIFY, parse_notify)
        finally:
            self._close(dev)

    def _read_wired(self, path: bytes) -> Optional[int]:
        dev = self._open(path)
        if dev is None:
            return None
        try:
            return self._ask(dev, CMD_WIRED_NOTIFY, parse_wired_notify)
        finally:
            self._close(dev)

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        try:
            infos = hidlist.enumerate(VENDOR)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(waizowl): %s", e)
            return []

        paths = {}
        for d in infos:
            pid = d.get("product_id")
            if d.get("vendor_id") != VENDOR or pid not in PRODUCTS:
                continue
            if (d.get("usage_page"), d.get("usage")) != (USAGE_PAGE, USAGE):
                continue
            paths.setdefault(pid, d)
        if not paths:
            return []

        # on the cable first: the mouse answers there even while the receiver says asleep
        d = paths.get(WIRED)
        if d is not None:
            self._diag.append(f"[Waizowl] cable pid={WIRED:04x} iface={d.get('interface_number')}")
            reported = self._read_wired(d["path"])
            if reported is not None:
                # the cable reply says 100 % while charging, whatever the cell holds (at 29 %
                # before plugging in it said 100, and back on the receiver it read 35), so
                # the level from the cable is never shown or remembered
                before = self._last.get(KEY)
                self._diag.append(f"  -> {KEY}: cable, charging (cable reports {reported}%, "
                                  f"last receiver level {before})")
                if before is not None:
                    return [DeviceStatus(KEY, PRODUCTS[WIRED], before, True, True, "waizowl",
                                         approx=f"charging (about {before}% before it was "
                                                "plugged in)",
                                         kind="mouse", ui_message="state.charging_was",
                                         ui_params={"level": before})]
                return [DeviceStatus(KEY, PRODUCTS[WIRED], None, True, True, "waizowl",
                                     approx="charging (level not reported while charging)",
                                     kind="mouse", ui_message="state.charging_level_unknown")]
            self._diag.append("    no answer on the cable")

        d = paths.get(RECEIVER)
        if d is not None:
            name = PRODUCTS[RECEIVER]
            self._diag.append(f"[Waizowl] pid={RECEIVER:04x} iface={d.get('interface_number')}")
            awake, level = self._read(d["path"])
            if level is not None:
                self._last[KEY] = level
                self._diag.append(f"  -> {KEY}: {level}%")
                return [DeviceStatus(KEY, name, level, False, True, "waizowl",
                                     kind="mouse", wake_on_input=True)]
            self._diag.append("    no level (mouse asleep or off)"
                              if awake is not True else "    awake but no level answer")
        else:
            name = PRODUCTS[WIRED]
        # greyed last level (or an empty icon) until the mouse answers again
        return [DeviceStatus(KEY, name, self._last.get(KEY), False, False, "waizowl",
                             kind="mouse", wake_on_input=True)]
