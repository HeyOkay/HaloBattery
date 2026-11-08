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
    wake_on_input: bool = False  # True: a greyed reading that new input can refresh (a
                                 # sleeping receiver mouse wakes on movement). The
                                 # tray's input wake fires only for these - an icon
                                 # greyed because its device is off must not re-poll
                                 # the app on every keystroke (review, #202).
    charging_known: bool = True  # False: level valid, charger query unavailable; suppress alerts

    polling_paused: bool = False  # cached by choice; explain in menu, keep normal icon

    # Presentation metadata; English approx remains available to diagnostics.
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
