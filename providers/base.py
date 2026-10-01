"""Common types shared by the battery providers."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("halo_battery")


@dataclass
class DeviceStatus:
    key: str                     # stable device identifier (one tray icon per key)
    name: str                    # human-readable name
    level: Optional[int] = None  # 0..100, None = unknown
    charging: bool = False
    online: bool = True          # False = receiver present, but the device is asleep/off
    source: str = ""             # razer / wlmouse / logitech / steelseries / bluetooth / xinput
    approx: str = ""             # set when the device only reports coarse levels,
                                 # e.g. "about 55% (medium)"; shown instead of "NN%"
    kind: str = ""               # headset / mouse / keyboard / gamepad when known
                                 # (picks the pictogram); "" = guess from the source
    via: str = ""                # "bluetooth": a controller connected over Bluetooth
    # Presentation only: keep approx in English for diagnostics and status.json.
    # Appended fields preserve existing positional constructors and protocol tests.
    ui_message: str = ""
    ui_params: dict = field(default_factory=dict)


class Provider:
    """A provider knows how to find its devices and read their battery level."""
    name = "base"

    def poll(self) -> List[DeviceStatus]:
        raise NotImplementedError

    def diagnostics(self) -> List[str]:
        return []


def hexdump(data, limit: int = 32) -> str:
    if data is None:
        return "None"
    return " ".join(f"{b:02x}" for b in list(data)[:limit])
