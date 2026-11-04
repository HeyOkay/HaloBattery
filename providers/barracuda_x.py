"""Razer Barracuda X (2022) over its 2.4 GHz dongle (#287 follow-up, #332/#264 family).

The dongle (1532:0552, "Razer Barracuda X 2.4", Macronix) does not answer the Razer mouse
request on its vendor collection, and the headset ("PA") frames are refused outright -
`WriteFile: (0x00000001) Incorrect function` in the reporter's diagnostics. It bridges USB
HID to an **Airoha** chip and speaks Airoha's RACE protocol instead:

    app mode   > 01 40                    < 01 40 01 01
    request    > 01 80 <len> 50 41 <group> <seq> <body...>
    reply      < 01 80 <len> 50 49 ....... <payload>
               payload: [10]=group, [11]=0x80|seq, [12]=status, [13..14]=mV (LE)

    remote mode  group 0x0E, body 02 E1 01   (the dongle forwards to the headset only then;
                                              in local mode it answers GET_BATTERY itself
                                              with a meaningless value)
    GET_BATTERY  group 0x06, body 01 00 31
    local mode   group 0x0E, body 02 E1 00   (always, on the way out)

The headset reports **voltage only** - no percentage and no charging flag over 2.4 GHz (its
Bluetooth side has a standard level). The percentage is interpolated on a Li-ion curve and
charging is inferred the way mehdibouchami/razer-barracuda-x-battery does it: several samples
~350 ms apart, charging when their spread is at least 12 mV or the lowest is at or above
4180 mV.

Mirrored from that project (MIT), which in turn credits KhromotozzDevOut/razer-barracuda-
battery-tray for the Macronix framing. Only the 0x0552 (Macronix) bridge is claimed here;
the 0x0550 YS-Tech bridge uses a CRC-16/XMODEM frame and an 'O'/'F' input-report ack, and is
documented but not implemented until a 0550 unit can test it.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from . import hidlist
from .base import DeviceStatus, Provider, hexdump, log

try:
    import hid  # type: ignore
except Exception:  # pragma: no cover - only when the package is missing
    hid = None

VID = 0x1532
PID = 0x0552
NAME = "Razer Barracuda X (2022)"
KEY = f"barracuda-x:{PID:04x}"

VENDOR_PAGE = 0xFF00
REPORT_SIZE = 64
REPORT_ID = 0x01                 # Macronix bridge; the YS-Tech 0x0550 uses 0x02
RACE_MAGIC_OUT = b"PA"           # 0x50 0x41
RACE_MAGIC_IN = b"PI"            # 0x50 0x49
HEADER_LEN = 10                  # 50 49 + 6 bytes, the last two = payload length (LE)

REMOTE_MODE = (0x0E, (0x02, 0xE1, 0x01))
LOCAL_MODE = (0x0E, (0x02, 0xE1, 0x00))
GET_BATTERY = (0x06, (0x01, 0x00, 0x31))

SAMPLES = 7                      # ~2.5 s in total, as in the reference
SAMPLE_GAP_S = 0.35
CHARGE_JITTER_MV = 12            # ~3 mV on battery, 30-45 mV while charging
CHARGE_VOLTAGE_MV = 4180
MV_MIN, MV_MAX = 2500, 4500

# Voltage (mV) for 0, 10, ... 100 %, checked against the Razer mobile app (3975 mV = 70 %).
CURVE = (3300, 3680, 3750, 3790, 3830, 3870, 3910, 3960, 4020, 4080, 4150)

READ_TIMEOUT_MS = 800
ASLEEP_KEEP = 300

_sequence = 0


def next_sequence() -> int:
    global _sequence
    _sequence = _sequence % 127 + 1        # 1..127, as Razer's tool does
    return _sequence


def race_request(group: int, seq: int, body) -> List[int]:
    return [0x50, 0x41, group, seq] + list(body)


def wrap(report_id: int, race) -> List[int]:
    """``01 80 <len> 50 41 ...`` - the Macronix bridge frame."""
    return [report_id, 0x80, len(race)] + list(race)


def find_frame(report, offset: int, length: int, group: int, seq: int):
    """Walk the RACE frames inside one input report and return the matching one."""
    end = min(offset + length, len(report))
    pos = offset
    while pos + HEADER_LEN <= end:
        if report[pos] != RACE_MAGIC_IN[0] or report[pos + 1] != RACE_MAGIC_IN[1]:
            return None
        payload_len = report[pos + 8] | (report[pos + 9] << 8)
        frame_end = pos + HEADER_LEN + payload_len
        if frame_end > end:
            return None
        payload = list(report[pos + HEADER_LEN:frame_end])
        if len(payload) >= 2 and payload[0] == group and payload[1] == (0x80 | seq):
            return payload
        pos = frame_end
    return None


def battery_mv(payload) -> Optional[int]:
    """Millivolts from a GET_BATTERY answer, or None if it is not one."""
    if not payload or len(payload) < 5 or payload[0] != GET_BATTERY[0]:
        return None
    if payload[2] != 0x00:                  # status
        return None
    mv = payload[3] | (payload[4] << 8)
    return mv if MV_MIN <= mv <= MV_MAX else None


def to_percent(mv: int) -> int:
    """Linear interpolation on the discharge curve, as the reference does it."""
    if mv <= CURVE[0]:
        return 0
    for i in range(1, len(CURVE)):
        if mv < CURVE[i]:
            return (i - 1) * 10 + (mv - CURVE[i - 1]) * 10 // (CURVE[i] - CURVE[i - 1])
    return 100


def is_charging(samples) -> bool:
    if not samples:
        return False
    return max(samples) - min(samples) >= CHARGE_JITTER_MV or min(samples) >= CHARGE_VOLTAGE_MV


def pick(infos: List[dict]) -> Optional[dict]:
    vendor = [d for d in infos
              if d.get("usage_page") == VENDOR_PAGE
              and (d.get("output_report_byte_length") or 0) > 0
              and (d.get("input_report_byte_length") or 0) > 0]
    if vendor:
        vendor.sort(key=lambda d: 0 if (d.get("input_report_byte_length") == REPORT_SIZE
                                        and d.get("output_report_byte_length") == REPORT_SIZE)
                    else 1)
        return vendor[0]
    return next((d for d in infos if d.get("usage_page") == VENDOR_PAGE), None)


class BarracudaXProvider(Provider):
    name = "barracuda_x"

    def __init__(self):
        self._diag: List[str] = []
        self._last: Optional[Tuple[int, float]] = None

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if hid is None:
            return []
        try:
            infos = hidlist.enumerate(VID)
        except Exception as e:  # pragma: no cover
            log.warning("hid.enumerate(barracuda x): %s", e)
            return []
        mine = [d for d in infos if d.get("product_id") == PID]
        if not mine:
            return []
        d = pick(mine)
        if d is None:  # pragma: no cover - pick() only returns None for an empty list
            return []
        self._diag.append(f"[Barracuda X] pid={PID:04x} '{NAME}' "
                          f"iface={d.get('interface_number')} "
                          f"{d.get('usage_page', 0):04x}:{d.get('usage', 0):04x}")
        parsed = self._ask(d)
        if parsed is not None:
            level, charging = parsed
            self._last = (level, time.time())
            self._diag.append(f"  -> {level}%{' charging' if charging else ''}")
            return [DeviceStatus(KEY, NAME, level, charging, True, "barracuda_x",
                                 kind="headset")]
        self._diag.append("  no answer (headset off, out of range, or the dongle refused)")
        if self._last and time.time() - self._last[1] < ASLEEP_KEEP:
            return [DeviceStatus(KEY, NAME, self._last[0], False, False, "barracuda_x",
                                 kind="headset")]
        return [DeviceStatus(KEY, NAME, None, False, False, "barracuda_x", kind="headset")]

    # ------------------------------------------------------------------ device

    def _ask(self, d: dict) -> Optional[Tuple[int, bool]]:
        dev = hid.device()  # type: ignore[union-attr]
        try:
            dev.open_path(d["path"])
        except Exception as e:
            self._diag.append(f"  open: {e}")
            return None
        try:
            if not self._enter_app_mode(dev):
                self._diag.append("  app mode handshake failed")
                return None
            payload = self._race(dev, *REMOTE_MODE)
            if payload is None or len(payload) < 3 or payload[2] != 0x00:
                self._diag.append("  remote mode refused")
                return None
            samples = []
            try:
                for i in range(SAMPLES):
                    if i:
                        time.sleep(SAMPLE_GAP_S)
                    reply = self._race(dev, *GET_BATTERY)
                    mv = battery_mv(reply)
                    if mv is not None:
                        samples.append(mv)
            finally:
                self._race(dev, *LOCAL_MODE)
            if not samples:
                self._diag.append("  no voltage in any sample")
                return None
            mv = min(samples)
            charging = is_charging(samples)
            self._diag.append(f"  {mv} mV (samples {min(samples)}..{max(samples)}), "
                              f"{to_percent(mv)}%{' charging' if charging else ''}")
            return to_percent(mv), charging
        except Exception as e:  # pragma: no cover - hidapi raises OSError
            self._diag.append(f"  error: {e}")
            return None
        finally:
            try:
                dev.close()
            except Exception:
                pass

    def _enter_app_mode(self, dev) -> bool:
        try:
            sent = dev.write([REPORT_ID, 0x40])
            if sent is not None and sent < 0:
                self._diag.append(f"  app mode write refused ({sent})")
                return False
        except Exception as e:
            self._diag.append(f"  app mode write: {e}")
            return False
        deadline = time.time() + READ_TIMEOUT_MS / 1000.0
        while time.time() < deadline:
            r = dev.read(REPORT_SIZE, 200)
            if not r:
                continue
            if r[0] == REPORT_ID and r[1] == 0x40:
                return len(r) >= 4 and r[2] == 0x01 and r[3] == 0x01
        return False

    def _race(self, dev, group: int, body) -> Optional[List[int]]:
        seq = next_sequence()
        frame = wrap(REPORT_ID, race_request(group, seq, body))
        sent = dev.write(frame)
        if sent is not None and sent < 0:
            self._diag.append(f"  write refused ({sent})")
            return None
        deadline = time.time() + READ_TIMEOUT_MS / 1000.0
        while time.time() < deadline:
            r = dev.read(REPORT_SIZE, 200)
            if not r:
                continue
            # hidapi may or may not return the report id in front
            if r[0] == REPORT_ID:
                if len(r) < 3 or r[1] != 0x80:
                    continue
                payload = find_frame(r, 3, r[2], group, seq)
            else:
                payload = find_frame(r, 0, len(r), group, seq)
            if payload is not None:
                self._diag.append(f"  race {group:02x}/{seq:02x}: {hexdump(payload, 8)}")
                return payload
        return None

    def diagnostics(self) -> List[str]:
        return list(self._diag)
