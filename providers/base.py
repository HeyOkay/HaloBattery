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
                                 # e.g. "about 55% (medium)"; shown instead of "NN%".
                                 # Also used for placeholder/derived levels ("on cable,
                                 # charging", "charging (about N% before ...)"); only
                                 # an "about ..." text is a real coarse reading, see
                                 # is_coarse_reading().
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
    polling_stale: bool = False   # native I/O failed; retained reading is not current

    # Power states kept at the end so every existing positional construction keeps
    # its meaning (key, name, level, charging, online, source).
    full: bool = False            # on power and fully charged (charging may also be set)
    asleep: bool = False          # True with online=False: the receiver answered but the
                                  # device is asleep, so the level is its last reading; it
                                  # is not on a charger and must not keep a charging flag

    # Presentation metadata; English approx remains available to diagnostics.
    ui_message: str = ""
    ui_params: dict = field(default_factory=dict)


COARSE_PREFIX = "about "


def is_coarse_reading(st: "DeviceStatus") -> bool:
    """True when ``approx`` marks a live coarse grade the device reported.

    The coarse readers (XInput, Logitech HID++ grades, SteelSeries coarse
    models, DJI gauge) describe their level as "about NN% (...)". Every other
    ``approx`` text goes with a level the device did not report as a number:
    a wired XInput pad's placeholder 100 or last wireless level ("on cable,
    charging"), a Logitech level from before the cable was plugged in
    ("charging (about NN% before it was plugged in)"), a remembered level
    ("NN% (last known; ...)"), or a level whose state is incomplete.
    """
    return st.level is not None and st.approx.startswith(COARSE_PREFIX)


class Provider:
    """A provider knows how to find its devices and read their battery level."""
    name = "base"

    def poll(self) -> List[DeviceStatus]:
        raise NotImplementedError

    def diagnostics(self) -> List[str]:
        return []

    def legacy_key_aliases(self) -> dict[str, str]:
        """Cached old-to-current device keys, only for unambiguous identities.

        Called after a successful poll on its existing provider worker/lease.
        Implementations must return metadata from that poll without performing
        device I/O, enumeration, waits or other native work.
        """
        return {}


def hexdump(data, limit: int = 32) -> str:
    if data is None:
        return "None"
    return " ".join(f"{b:02x}" for b in list(data)[:limit])
