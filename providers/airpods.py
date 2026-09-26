"""AirPods battery over Bluetooth LE, without a driver or the vendor app.

AirPods report no battery to Windows: there is no HID collection and no PnP
battery property for them, so the Bluetooth provider cannot see them at all.
They do broadcast their levels in a BLE advertisement - the same "proximity
pairing" packet an iPhone reads - which is how Macs and the third-party Windows
tools display them.

Layout (AirPodsDesktop's AppleCP::AirPods and RustPods' airpods_battery_cli;
WinRT_ and bleak hand the manufacturer data over *without* the 0x4C 0x00 company
prefix, so every index is two lower than in a raw capture):

    data[0]     0x07, proximity pairing message type
    data[3:5]   model id, little endian: 0x2014 AirPods Pro 2, 0x200E AirPods Pro,
                0x2013 AirPods 3, 0x200F AirPods 2
    data[5]     case battery in the high nibble, charging flags in the low bits:
                0x04 case, 0x02 left, 0x01 right
    data[6]     left battery in the high nibble, right in the low nibble
    data[7]     lid open 0x04, left in ear 0x02, right in ear 0x01

Batteries are nibbles times ten, so 0x98 means left 90%, right 80%. A nibble of
0xF means "not available" and is reported as unknown, never as 150%.

Verified against a live capture on 2026-09-26 - an AirPods 3 advertising
07 19 01 13 20 2b 98 8f ..., decoded as left 90%, right 80%, case 20% - and the
offsets are the ones both reference projects use.

The catch with reading advertisements is that they are broadcast to everyone: any
AirPods in range qualify, not just yours. A pair across the room was captured at
-85 dBm (and -75 dBm on a later scan) while a pair on the same desk is -55 dBm, so
anything weaker
than MIN_RSSI is ignored and the diagnostics print what was dropped and why.

bleak is an added dependency (see requirements.txt). Without it installed this
provider reports nothing and explains itself in the diagnostics instead of
breaking the poll.
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional, Tuple

try:
    import bleak
except ImportError:              # pragma: no cover
    bleak = None

from .base import DeviceStatus, Provider, log

APPLE_COMPANY_ID = 76
PROXIMITY_TYPE = 0x07
UNKNOWN_NIBBLE = 0x0F

MODELS = {
    0x2014: "AirPods Pro 2",
    0x200E: "AirPods Pro",
    0x2013: "AirPods 3",
    0x200F: "AirPods 2",
}

MIN_RSSI = -70           # dBm; a pair in the same room reads -40..-65, so anything
                         # weaker than this is somebody else's pair
SCAN_SECONDS = 5.0       # BLE advertisements repeat every second or so
KEEP_SECONDS = 180       # keep showing a pair that is briefly out of range


def _nibble(v: int) -> Optional[int]:
    """One 4-bit battery field times ten, or None when the nibble says 'unknown'."""
    if v == UNKNOWN_NIBBLE:
        return None
    level = v * 10
    return level if 0 <= level <= 100 else None


def parse_proximity(data) -> Optional[dict]:
    """Decode one Apple manufacturer-data blob, or None if it is not proximity pairing."""
    data = bytes(data or b"")
    if len(data) < 8 or data[0] != PROXIMITY_TYPE:
        return None

    model_id = (data[4] << 8) | data[3]
    status, battery, lid = data[5], data[6], data[7]

    return {
        "model": MODELS.get(model_id, f"AirPods (0x{model_id:04X})"),
        "model_id": model_id,
        "left": _nibble((battery & 0xF0) >> 4),
        "right": _nibble(battery & 0x0F),
        "case": _nibble((status & 0xF0) >> 4),
        "charging": {"case": bool(status & 0x04),
                     "left": bool(status & 0x02),
                     "right": bool(status & 0x01)},
        "lid_open": bool(lid & 0x04),
        "left_in_ear": bool(lid & 0x02),
        "right_in_ear": bool(lid & 0x01),
    }


async def _scan(seconds: float):
    """One BLE scan: {address: (rssi, name, parsed)} for Apple proximity packets."""
    found = await bleak.BleakScanner.discover(timeout=seconds, return_adv=True)
    out: Dict[str, Tuple[int, str, dict]] = {}
    for address, (device, adv) in found.items():
        payload = (adv.manufacturer_data or {}).get(APPLE_COMPANY_ID)
        if not payload:
            continue
        parsed = parse_proximity(payload)
        if parsed:
            out[address] = (adv.rssi, device.name or "", parsed)
    return out


class AirPodsProvider(Provider):
    """Reads the battery out of AirPods' BLE advertisements.

    The level shown is the weaker of the two buds - that is the one about to run
    out - and the charging flag follows the buds rather than the case, since the
    case can be charging while the buds are in your ears.
    """
    name = "airpods"

    def __init__(self):
        self._diag: List[str] = []
        self._seen: Dict[str, Tuple[float, DeviceStatus]] = {}

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if bleak is None:
            self._diag.append("[AirPods] bleak is not installed, skipping the BLE scan "
                              "(pip install -r requirements.txt)")
            return []

        try:
            found = asyncio.run(_scan(SCAN_SECONDS))
        except Exception as e:                       # adapter off, no radio, driver trouble
            self._diag.append(f"[AirPods] BLE scan failed: {e}")
            return []

        now = time.time()
        fresh = set()
        self._diag.append(f"[AirPods] BLE scan: {len(found)} AirPods advertising")
        for address, (rssi, name, p) in found.items():
            if not p:                    # never trust a producer to filter for us
                continue
            if rssi < MIN_RSSI:
                self._diag.append(f"[AirPods] {address} {p['model']} at {rssi} dBm is weaker "
                                  f"than {MIN_RSSI} dBm, ignored (not close enough to be yours)")
                continue
            buds = [b for b in (p["left"], p["right"]) if b is not None]
            if not buds:
                self._diag.append(f"[AirPods] {address} {p['model']}: no bud level in the "
                                  f"packet (left={p['left']}, right={p['right']}), skipped")
                continue
            level = min(buds)
            label = f"{p['model']} ({address})"
            status = DeviceStatus(
                f"airpods:{address}", label, level,
                charging=bool(p["charging"]["left"] or p["charging"]["right"]),
                online=True, source="airpods", kind="headset")
            self._seen[address] = (now, status)
            fresh.add(address)
            self._diag.append(
                f"[AirPods] {address} {p['model']} rssi={rssi} left={p['left']} right={p['right']} "
                f"case={p['case']} charging={p['charging']} lid={p['lid_open']} "
                f"in_ear={p['left_in_ear']}/{p['right_in_ear']} -> showing {level}%")

        out: List[DeviceStatus] = []
        for address, (when, status) in list(self._seen.items()):
            if address in fresh:
                out.append(status)
            elif now - when <= KEEP_SECONDS:
                out.append(status)
                self._diag.append(f"[AirPods] {address}: kept for {int(KEEP_SECONDS - (now - when))} s "
                                  f"more without a fresh advertisement")
            else:
                del self._seen[address]
                self._diag.append(f"[AirPods] {address}: not seen for {int(now - when)} s, dropped")
        return out

    def diagnostics(self) -> List[str]:
        return self._diag
