"""Standard-HID battery fallback.

Many peripherals publish the battery fields the HID specification defines instead
of a vendor protocol: a level on page 0x06 usage 0x20 or page 0x85 usage 0x65, and
on page 0x85 whether the device is charging, discharging, full or on AC. Nothing
in Halo read those devices, so they stayed invisible even when Windows and the
vendor's own tool showed a level.

This provider is the last resort, and deliberately narrow:

* only collections whose own top-level usage is a battery level are opened - a
  keyboard's or a pen tablet's vendor collection is never touched (polling a
  digitizer flipped an XP-Pen's Y axis, BatteryHub issue #232);
* only feature and input reports are read, never written;
* a reading that does not parse, or a level outside 0-100, is dropped rather than
  guessed;
* one icon per (vendor, product), even if the device exposes several collections.

A vendor driver always wins: `drop_hid_battery_duplicates` in the app removes this
provider's reading when another provider already reports the same device.
"""
from __future__ import annotations

from typing import Dict, List

from halo import hidp
from providers.base import DeviceStatus, Provider
from providers import hidlist


class HidBatteryProvider(Provider):
    name = "hid_battery"

    def __init__(self):
        self._diag: List[str] = []

    def poll(self) -> List[DeviceStatus]:
        self._diag = []
        if not hidp.available():
            return []
        try:
            devices = hidlist.enumerate(0)
        except Exception:
            return []
        found: Dict[str, DeviceStatus] = {}
        for device in devices:
            if not isinstance(device, dict):
                continue
            if (device.get("usage_page"), device.get("usage")) not in hidp.LEVEL_USAGES:
                continue
            vid = int(device.get("vendor_id") or 0)
            pid = int(device.get("product_id") or 0)
            path = device.get("path")
            if not vid or not path:
                continue
            key = f"hid:{vid:04x}:{pid:04x}"
            if key in found:
                continue
            try:
                reading = hidp.read(path, vid)
            except Exception:
                reading = None
            if reading is None:
                continue
            name = (device.get("product_string") or "").strip() or "Wireless device"
            found[key] = DeviceStatus(key, name, reading.level, reading.charging, True,
                                      "hid_battery",
                                      charging_known=reading.charging_known)
            self._diag.append(f"[HID] {vid:04x}:{pid:04x} '{name}' {reading.level}%"
                              f"{' charging' if reading.charging else ''}")
        return list(found.values())

    def diagnostics(self) -> List[str]:
        return list(self._diag[-20:])
