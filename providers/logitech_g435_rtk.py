"""Logitech G435 LIGHTSPEED headset battery over its Realtek receiver (046D:0ACB).

This is the original Realtek receiver, not the newer Holtek variant (046D:0ADF),
and it speaks neither HID++ nor any passive status channel: two long listens on
both vendor collections captured zero frames across volume keys, mute, power
cycles and USB-C charge events. The battery is only reachable through the
receiver's own PA command bridge, and only in bootloader mode.

Host side, verified on hardware with an independent writer
(https://github.com/fr0styJS/g435-lightspeed-battery):

  feature report 6, usage page 0xFFC0, 63 data bytes (64 with the report id):
    [06, 01, 03, 0...]                 enter bootloader
    [06, 08, 03, 0...]                 exit bootloader
    [06, 04, 03, 00, len, ...cmd]      forward a PA command

  PA commands:
    LQ  0x0E  E1 01   bridge -> remote mode (forward to the headset)
    OTA 0x06  31      get battery -> "06 80 00 <u16 LE millivolts> 00 00"
    LQ  0x0E  E1 00   back to local mode

Without the remote-mode switch the receiver answers *itself* with 102, even
with the headset switched off, so that value must never become a level.

Cost: PA commands are only serviced in bootloader mode, which mutes USB audio
for about 0.8 s until it is exited. POLL_FLOOR_S therefore limits the device to
one read per 15 minutes, cached in between (issue #443).
"""
from __future__ import annotations

import time
from typing import List, Optional

from . import hidlist
from .base import DeviceStatus, Provider, log

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x046D
PID = 0x0ACB
VENDOR_PAGE = 0xFFC0             # the receiver's control collection
REPORT_ID = 6
FEATURE_LEN = 64                 # 63 data bytes plus the report id

NAME = "Logitech G435 LIGHTSPEED"
KEY = f"logitech:{PID:04x}"

POLL_FLOOR_S = 900               # 15 minutes, per the maintainer's decision
SETTLE_S = 0.25                  # bootloader needs a moment before it answers
MODE_S = 0.12                    # the mode switch needs a moment to take
READ_S = 1.0                     # a live headset answers in milliseconds
READ_PAUSE_S = 0.05

BATTERY_LOW_MV = 3000            # outside this window the reply is not a battery
BATTERY_HIGH_MV = 4300

# Generic 3.7 V Li-ion curve. The millivolts are exact; the percentage is an
# estimate, which is why the status is reported through `approx`.
CURVE = [(4200, 100), (4100, 90), (4000, 80), (3900, 65), (3800, 50),
         (3750, 40), (3700, 30), (3650, 20), (3600, 10), (3500, 5), (3300, 0)]


def volts_to_percent(mv: int) -> int:
    if mv >= CURVE[0][0]:
        return 100
    for (v_hi, p_hi), (v_lo, p_lo) in zip(CURVE, CURVE[1:]):
        if mv >= v_lo:
            return round(p_lo + (p_hi - p_lo) * (mv - v_lo) / (v_hi - v_lo))
    return 0


def _frame(body: List[int]) -> bytes:
    return bytes(body + [0] * (FEATURE_LEN - len(body)))


ENTER = _frame([REPORT_ID, 1, 3])
EXIT = _frame([REPORT_ID, 8, 3])
REMOTE_ON = _frame([REPORT_ID, 4, 3, 0, 7, 0x50, 0x41, 0x0E, 0, 2, 0xE1, 0x01])
REMOTE_OFF = _frame([REPORT_ID, 4, 3, 0, 7, 0x50, 0x41, 0x0E, 0, 2, 0xE1, 0x00])
BATTERY = _frame([REPORT_ID, 4, 3, 0, 7, 0x50, 0x41, 0x06, 0, 1, 0x00, 0x31])


def parse_mv(frame) -> Optional[int]:
    """Millivolts from ``06 80 00 <mv lo> <mv hi> ...``, or None.

    The reply is searched for rather than indexed: the first feature read after
    entering bootloader can come back without the ``P I`` prefix, so a fixed
    offset is not safe. The local-mode answer (``06 80 00 66 ...``) parses to
    102 mV and is refused by the window.
    """
    if not frame:
        return None
    data = bytes(frame)
    i = data.find(b"\x06\x80\x00")
    if i < 0 or len(data) < i + 5:
        return None
    mv = data[i + 3] | (data[i + 4] << 8)
    return mv if BATTERY_LOW_MV <= mv <= BATTERY_HIGH_MV else None


def pick(infos: List[dict]) -> Optional[dict]:
    """The 0xFFC0 collection; the consumer and telephony ones carry no battery."""
    for d in infos:
        if d.get("usage_page") == VENDOR_PAGE:
            return d
    return None


class LogitechG435RtkProvider(Provider):
    name = "logitech_g435_rtk"

    def __init__(self):
        self._diag: List[str] = []
        self._level: Optional[int] = None       # last level, kept greyed when offline
        self._online = False
        self._when: Optional[float] = None      # monotonic of the last completed read
        self._path: Optional[bytes] = None      # receiver path the cache belongs to

    # ------------------------------------------------------------------ poll

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:                         # pragma: no cover
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:                  # pragma: no cover
            log.warning("hid.enumerate(logitech_g435_rtk): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            self._forget()                      # receiver gone: drop the cache
            return []
        d = pick(mine)
        if d is None:                           # pragma: no cover - defensive
            return []
        path = d.get("path")
        if self._path is not None and path != self._path:
            # a replug can hand back a different path; a cached level would be
            # served for up to 15 minutes otherwise
            self._forget()
        self._path = path
        self._diag.append(f"[G435] pid={PID:04x} '{NAME}' "
                          f"iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")

        now = time.monotonic()
        if self._when is not None and now - self._when < POLL_FLOOR_S:
            self._diag.append(f"  cached ({int(now - self._when)} s ago)")
            return [self._status()]

        mv = self._read(d)
        if mv is None:
            self._online = False
        else:
            self._level = volts_to_percent(mv)
            self._online = True
            self._diag.append(f"  {mv} mV -> about {self._level}%")
        self._when = time.monotonic()           # only after a completed read
        return [self._status()]

    def _forget(self) -> None:
        self._level = None
        self._online = False
        self._when = None
        self._path = None

    def _status(self) -> DeviceStatus:
        return DeviceStatus(KEY, NAME, self._level, False, self._online, "logitech",
                            kind="headset",
                            approx=(f"about {self._level}%" if self._level is not None else ""),
                            charging_known=False)

    # ------------------------------------------------------------------ read

    def _read(self, d: dict) -> Optional[int]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
        except Exception as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            self._send(dev, ENTER)
            time.sleep(SETTLE_S)
            self._get(dev)                      # drain the enter ack
            try:
                self._send(dev, REMOTE_ON)
                time.sleep(MODE_S)
                self._send(dev, BATTERY)
                deadline = time.monotonic() + READ_S
                while time.monotonic() < deadline:
                    frame = self._get(dev)
                    mv = parse_mv(frame) if frame else None
                    if mv is not None:
                        return mv
                    time.sleep(READ_PAUSE_S)
                self._diag.append("  no battery reply")
                return None
            finally:
                # the headset must never be left in remote mode, even when the
                # read threw or timed out (review, #443)
                self._send(dev, REMOTE_OFF)
        finally:
            # and the receiver must never be left in bootloader mode: audio is
            # muted for as long as it stays there
            self._send(dev, EXIT)
            dev.close()

    def _send(self, dev, frame: bytes) -> None:
        try:
            sent = dev.send_feature_report(frame)
            if sent is not None and sent < 0:
                self._diag.append(f"  feature write refused ({sent})")
        except Exception as e:                  # pragma: no cover - hidapi raises OSError
            self._diag.append(f"  write: {e}")

    def _get(self, dev):
        try:
            return dev.get_feature_report(REPORT_ID, FEATURE_LEN)
        except Exception as e:                  # pragma: no cover
            self._diag.append(f"  read: {e}")
            return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
