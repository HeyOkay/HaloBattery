"""Halo Battery: battery levels of wireless devices in the Windows system tray.

Supported:
  * Razer (BlackShark V2 Pro headset, mice, Barracuda Pro, etc.): directly over USB/HID, no Synapse
  * Audeze Maxwell (2.4 GHz dongle or USB-C cable)
  * WLmouse (Beast X / Beast X Max / Mini Pro)
  * Logitech (HID++ 2.0 mice, keyboards and headsets: Lightspeed / Unifying / Bolt receivers, G HUB not needed)
  * SteelSeries (Arctis Nova, Arctis 1 / 7 / 9 / Pro Wireless / 7+ headsets, GameBuds, Aerox mice,
    GG not needed)
  * MCHOSE (M7 Ultra and the rest of the 0x5253 family, on the 2.4 GHz receiver)
  * HyperX (Cloud II and Cloud III Wireless), JBL Quantum 910, Corsair, Astro A50 Gen 5,
    Keychron, Lofree, Pulsar / ATK / VXE, ASUS ROG / TUF, G-Wolves, LAMZU Maya X and AM Infinity 8K mice
  * Xbox-compatible controllers (Windows.Gaming.Input / XInput)
  * PlayStation controllers (DualShock 4, DualSense): directly over USB/HID
  * 8BitDo controllers in D-input mode (Pro 2, Pro 3, SN30 / SF30 Pro), while Steam
    or a game has them in the enhanced mode (never switched by the app, #101)
  * Nintendo Switch Pro Controller and Joy-Con over Bluetooth
  * Bluetooth devices whose battery level Windows knows (enabled from the menu)

Run:   pythonw halo_battery.pyw
Debug: python halo_battery.pyw --probe
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import zlib
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Set

APP_NAME = "HaloBattery"
APP_TITLE = "Halo Battery"
VERSION = "1.13.0"
LEGACY_NAME = "BatteryTray"      # the app's previous name (settings and autostart are migrated)

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# Define APPDATA_DIR globally for legacy config migration
APPDATA_DIR = os.environ.get("APPDATA", os.path.expanduser("~"))

def _calculate_data_dir(base_dir):
    """Calculate data directory based on portable mode.
    
    Returns:
        tuple: (portable_mode: bool, data_dir: str)
    """
    portable_mode = os.path.exists(os.path.join(base_dir, "portable.txt"))
    
    if portable_mode:
        return (True, base_dir)
    else:
        APPDATA_DIR = os.environ.get("APPDATA", os.path.expanduser("~"))
        data_dir = os.path.join(APPDATA_DIR, APP_NAME)
        return (False, data_dir)

# Calculate data directory using the new function
portable_mode, DATA_DIR = _calculate_data_dir(BASE_DIR)
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "halo_battery.log")
DIAG_PATH = os.path.join(DATA_DIR, "diagnostics.txt")
HISTORY_PATH = os.path.join(DATA_DIR, "history.json")
STATUS_PATH = os.path.join(DATA_DIR, "status.json")

log = logging.getLogger("halo_battery")
log.setLevel(logging.INFO)
_fh = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=1, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_fh)

try:
    import hid  # noqa: E402
except ImportError:
    hid = None
import pystray  # noqa: E402
from pystray import Menu, MenuItem as Item  # noqa: E402

import flyout  # noqa: E402
import history  # noqa: E402
import icons  # noqa: E402
import updates  # noqa: E402
import winevents  # noqa: E402
from providers import hidlist  # noqa: E402
from providers import (AmInfinityProvider, AstroProvider, AsusProvider,  # noqa: E402
                       AudezeProvider, BarracudaProvider, BluetoothProvider, CorsairProvider, DeviceStatus,
                       EightBitDoProvider,
                       GWolvesProvider, HyperXAlpha2Provider, HyperXCloud3Provider, HyperXProvider, JblProvider,
                       KeychronProvider, LamzuProvider, LofreeProvider, LogitechProvider,
                       MchoseProvider, NintendoProvider, PlayStationProvider, PulsarProvider,
                       RazerProvider, SteelSeriesProvider, WLmouseProvider, XInputProvider)
from providers.bluetooth import BluetoothWatcher  # noqa: E402

HEADSET_WORDS = ("blackshark", "kraken", "barracuda", "nari", "thresher", "headset",
                 "headphone", "earbud", "buds", "hammerhead", "airpods")

DEFAULTS = {
    "interval": 60,      # seconds between polls
    "low": 20,           # low battery notification threshold, %
    "full_alert": True,  # notification when a charging device reaches 100 %
    "notify": True,
    "bluetooth": True,   # Windows Bluetooth devices
    "badges": True,      # device pictogram inside the ring
    "animation": True,   # "breathing" arc while charging
    # icon colour: "auto" follows the Windows theme, or the top menu bar while
    # MyDockFinder is running; "white" / "black" are fixed (tray menu > Icon colour);
    # "windows" / "topbar" force one automatic source (config file only)
    "icon_theme": "auto",
    "update_check": True,   # once a day: is there a newer release on GitHub?
    # the Windows 11 style menu (flyout.py); false = the classic Windows menu (config file only)
    "fluent_menu": True,
    # PlayStation controllers over Bluetooth: switch them to the full report to read the
    # battery. Off by default: that mode stays on until the controller is turned off and
    # games that use DirectInput stop seeing the controller (#96)
    "playstation_full_mode": False,
    "disabled_providers": [],     # provider names turned off in Preferences > Device types
    "time_left": True,      # "about N h of use left" in the tooltip (history.py)
    "percent_in_icon": False,  # the level as a number in the ring, instead of the pictogram
    "quiet_fullscreen": True,  # while a game is full screen: hold alerts, poll every 5 min
    "status_file": False,      # write status.json for Rainmeter, Stream Deck, scripts
}

# Preferences > Device types: provider name -> what the user sees. Windows Bluetooth
# devices keep their own switch ("bluetooth" above), as before.
PROVIDER_LABELS = {
    "8bitdo": "8BitDo controllers",
    "am_infinity": "AM Infinity 8K (Angry Miao)",
    "astro": "Astro A50",
    "asus": "ASUS ROG / TUF mice",
    "audeze": "Audeze Maxwell",
    "barracuda": "Razer Barracuda Pro",
    "corsair": "Corsair headsets",
    "gwolves": "G-Wolves mice",
    "hyperx": "HyperX Cloud II Wireless",
    "hyperx_alpha2": "HyperX Cloud Alpha 2",
    "hyperx_cloud3": "HyperX Cloud III Wireless",
    "jbl": "JBL Quantum",
    "keychron": "Keychron",
    "lamzu": "LAMZU mice",
    "lofree": "Lofree keyboards",
    "logitech": "Logitech",
    "mchose": "MCHOSE mice",
    "nintendo": "Nintendo Switch controllers",
    "playstation": "PlayStation controllers",
    "pulsar": "Pulsar / ATK VXE mice",
    "razer": "Razer mice and headsets",
    "steelseries": "SteelSeries",
    "wlmouse": "WLmouse",
    "xinput": "Xbox-compatible controllers",
}


def make_providers() -> list:
    return [RazerProvider(), AudezeProvider(), WLmouseProvider(), MchoseProvider(),
            HyperXAlpha2Provider(), HyperXCloud3Provider(), HyperXProvider(),
            KeychronProvider(), PulsarProvider(),
            JblProvider(), LogitechProvider(), SteelSeriesProvider(), XInputProvider(),
            PlayStationProvider(), EightBitDoProvider(), BarracudaProvider(), NintendoProvider(),
            AsusProvider(), GWolvesProvider(), LofreeProvider(), AstroProvider(), CorsairProvider(),
            LamzuProvider(), AmInfinityProvider()]


# ---------------------------------------------------------------- config
def migrate_legacy_config() -> bool:
    """Copy settings from the old %APPDATA%\\BatteryTray folder on the first run."""
    old = os.path.join(APPDATA_DIR, LEGACY_NAME, "config.json")
    if os.path.exists(CONFIG_PATH) or not os.path.exists(old):
        return False
    try:
        import shutil
        shutil.copyfile(old, CONFIG_PATH)
        return True
    except OSError:
        return False


LIMITS = {"interval": (5, 3600), "low": (0, 100)}   # a hand-edited value outside is not used
_config_lock = threading.Lock()


def _valid_setting(key: str, value) -> bool:
    """A value from the settings file has the type of its default (and a sane range)."""
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        lo, hi = LIMITS.get(key, (value, value))
        return lo <= value <= hi
    return isinstance(value, type(default))


def load_config() -> dict:
    """The defaults, overridden by the settings file. A damaged file (not JSON, or
    not a JSON object) is kept as config.json.bad and the defaults are used; a
    single wrong value (a string for the poll interval, an interval of 0) falls
    back to its default instead of breaking the app."""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        return cfg                               # no settings yet
    except ValueError as e:
        data = e
    if not isinstance(data, dict):
        log.warning("config: %s is damaged (%s), using the defaults", CONFIG_PATH,
                    data if isinstance(data, Exception) else type(data).__name__)
        try:
            os.replace(CONFIG_PATH, CONFIG_PATH + ".bad")
        except OSError:
            pass
        return cfg
    for key, value in data.items():
        if key in DEFAULTS and not _valid_setting(key, value):
            log.warning("config: ignoring %s=%r, using %r", key, value, DEFAULTS[key])
            continue
        cfg[key] = value
    return cfg


def save_config(cfg: dict) -> None:
    """Write the settings to a temporary file first and then swap it in, so a crash
    or a full disk in the middle of writing cannot leave half a file behind."""
    tmp = CONFIG_PATH + ".tmp"
    with _config_lock:                           # the menu and the update check both save
        try:
            text = json.dumps(cfg, ensure_ascii=False, indent=2)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
        except (OSError, TypeError, ValueError, RuntimeError) as e:
            log.warning("save_config: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass


# ------------------------------------------------------------ status file
def write_json(path: str, data: dict) -> None:
    """Write to a temporary file and swap it in, so a reader (Rainmeter reads the
    status file every few seconds) never sees half a file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def remove_status_file() -> None:
    try:
        os.remove(STATUS_PATH)
    except OSError:
        pass


# ------------------------------------------------------------- autostart
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}"'


def running_from_temp() -> bool:
    """True when this copy runs from a temporary folder: opened straight from the
    ZIP in Explorer or WinRAR, which unpack it into %TEMP% and delete it later.
    "Start with Windows" must not point there - after the next reboot the entry
    would lead nowhere."""
    me = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    me = os.path.normcase(os.path.abspath(me))
    temps = {tempfile.gettempdir(), os.environ.get("TEMP", ""), os.environ.get("TMP", "")}
    for d in temps:
        if d:
            d = os.path.normcase(os.path.abspath(d)).rstrip("\\/")
            if me.startswith(d + os.sep):
                return True
    return False


# The texts of the app's notifications.
def low_battery_text(name: str, level: Optional[int], approx: bool) -> str:
    left = "battery is low" if approx else f"{level}% left"
    return f"{name}: {left}. Time to charge."


def fully_charged_text(name: str) -> str:
    return f"{name} is fully charged."


def update_text(latest: str) -> str:
    return (f"Version {latest} is available. Right-click a battery icon "
            f"and choose \"Download v{latest}…\".")


# Windows titles a notification with the app that sent it. Without an id of its own the
# process is "Python" (pythonw.exe) - that is what the notifications said. The id is set
# for the process at start-up and registered under HKCU with the name (and icon) to show
# in the notification header; nothing needs admin rights.
APP_ID = "HaloBattery"
APP_ID_NAME = "HaloBattery"
APP_ID_KEY = "Software\\Classes\\AppUserModelId\\" + APP_ID
APP_ICON_PATH = os.path.join(DATA_DIR, "notification_icon.png")


def app_icon_png(path: str) -> bool:
    """The app icon (the same drawing as halo.ico) as a PNG for the notification header."""
    try:
        from PIL import Image, ImageDraw
        os.makedirs(os.path.dirname(path), exist_ok=True)
        size, ss = 64, 4
        big = size * ss
        img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(img).ellipse((0, 0, big - 1, big - 1), fill=(32, 32, 32, 255))
        ring = icons.render(75, True, True, 20, light_taskbar=False, badge="")
        ring = ring.resize((int(big * 0.84),) * 2, Image.LANCZOS)
        off = (big - ring.width) // 2
        img.alpha_composite(ring, (off, off))
        img.resize((size, size), Image.LANCZOS).save(path)
        return True
    except Exception as e:
        log.warning("notification icon: %s", e)
        return False


def set_app_id() -> None:
    """Name the notifications "HaloBattery" instead of "Python"."""
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APP_ID_KEY) as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_ID_NAME)
            if app_icon_png(APP_ICON_PATH):
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, APP_ICON_PATH)
    except OSError as e:
        log.warning("app id registration: %s", e)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (OSError, AttributeError) as e:
        log.warning("app id: %s", e)


TEMP_AUTOSTART_TEXT = ("Halo Battery is running from a temporary folder (straight from the ZIP). "
                       "Extract the ZIP to a folder of its own, run HaloBattery.exe from there, "
                       "then turn on Start with Windows.")


def autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(on: bool) -> bool:
    """Turn "Start with Windows" on or off. Returns False when it was not turned on
    because this copy runs from a temporary folder."""
    if sys.platform != "win32":
        return True
    if on and running_from_temp():
        log.warning("autostart: not set, running from a temporary folder (%s)", _launch_command())
        return False
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass
    return True


def migrate_legacy_autostart() -> bool:
    """Replace the old "BatteryTray" autostart entry with the new name and path."""
    if sys.platform != "win32" or running_from_temp():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as k:
            try:
                winreg.QueryValueEx(k, LEGACY_NAME)
            except OSError:
                return False
            winreg.DeleteValue(k, LEGACY_NAME)
        set_autostart(True)
        return True
    except OSError as e:
        log.warning("autostart migration: %s", e)
        return False


def refresh_autostart() -> bool:
    """If "Start with Windows" is on but points at another copy of the app
    (e.g. the old single HaloBattery.exe after moving to the folder build),
    point it at the copy that is running now. Only for the built .exe, and never
    at a copy in a temporary folder."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False) or running_from_temp():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            current, _ = winreg.QueryValueEx(k, APP_NAME)
    except OSError:
        return False                         # autostart is off: leave it off
    if str(current).strip().lower() == _launch_command().lower():
        return False
    try:
        set_autostart(True)
        return True
    except OSError as e:
        log.warning("autostart refresh: %s", e)
        return False


def single_instance() -> bool:
    if sys.platform != "win32":
        return True
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\HaloBattery_single_instance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


# SHQueryUserNotificationState: what Windows itself uses to hold back notifications.
# 2 = a full-screen app (a borderless game too), 3 = a Direct3D exclusive full-screen
# game, 4 = presentation mode. 5 = normal; 1, 6 and 7 are not about the screen.
QUNS_FULLSCREEN = (2, 3, 4)
QUIET_INTERVAL = 300       # s between polls while a game is full screen


def fullscreen_app_running() -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    state = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return False
    except (AttributeError, OSError):
        return False
    return state.value in QUNS_FULLSCREEN


# ------------------------------------------------------------- tray icons
WM_RBUTTONUP = 0x0205
ICON_HANDLE_CACHE = 64     # icon handles kept per tray icon (a charging cycle is 30 frames)
IDLE_KEY = "HaloBattery:idle"   # the "no devices found" icon's id
_tray_classes: Dict[type, type] = {}


def icon_uid(key: str) -> int:
    """A tray icon id that is the same on every start for the same device.

    Windows remembers where the user put a tray icon (on the taskbar or in the
    hidden-icons flyout) by the program's path and this id. pystray uses the
    Python object's id, which changes on every start, so Windows saw new icons
    each time and forgot their place (#38)."""
    return zlib.crc32(key.encode("utf-8")) & 0x7FFFFFFF


def _destroy_handles(cache: dict) -> None:
    handles = [h for _img, h in cache.values()]
    cache.clear()
    if sys.platform == "win32":
        import ctypes
        for h in handles:
            try:
                ctypes.windll.user32.DestroyIcon(h)
            except Exception:
                pass


def _make_tray_class(base: type) -> type:
    class TrayIcon(base):
        """pystray's icon with two changes, both in pystray's own private methods,
        which are the same in pystray 0.19.0 - 0.19.5 (the Windows backend):

        * the id Windows knows the icon by is icon_uid(key), not id(self);
        * the icon handle of every image is kept. pystray writes each new image to a
          temporary .ico file and loads it back, so the charging animation wrote
          about ten files a second per charging device, which a disk and an
          antivirus notice (#61). The 30 frames of a cycle are now loaded once."""

        def _message(self, code, flags, **kwargs):
            uid = getattr(self, "_hb_uid", None)
            if uid is None or sys.platform != "win32":
                return super()._message(code, flags, **kwargs)
            import ctypes
            from pystray._util import win32
            win32.Shell_NotifyIcon(code, win32.NOTIFYICONDATAW(
                cbSize=ctypes.sizeof(win32.NOTIFYICONDATAW),
                hWnd=self._hwnd,
                hID=uid,
                uFlags=flags,
                **kwargs))

        def _assert_icon_handle(self):
            cache = getattr(self, "_hb_handles", None)
            if cache is None or self._icon_handle:
                return super()._assert_icon_handle()
            img = self.icon
            hit = cache.get(id(img))
            if hit is not None and hit[0] is img:     # the cache holds img, so its id is not reused
                self._icon_handle = hit[1]
                return None
            super()._assert_icon_handle()
            if self._icon_handle:
                if len(cache) >= ICON_HANDLE_CACHE:
                    _destroy_handles(cache)           # the handle just loaded is not among them
                cache[id(img)] = (img, self._icon_handle)
            return None

        def _release_icon(self):
            if getattr(self, "_hb_handles", None) is None:
                return super()._release_icon()
            self._icon_handle = None                  # the handle stays in the cache
            return None

        def _on_notify(self, wparam, lparam):
            # a right-click opens the Windows 11 style menu (flyout.py); the classic
            # menu is pystray's own and is still there when the flyout cannot open
            host = getattr(self, "_hb_flyout", None)
            if host is not None and lparam == WM_RBUTTONUP and self.menu:
                try:
                    if host.show(self.menu, self):
                        return None
                except Exception as e:
                    log.warning("menu: %s", e)
            return super()._on_notify(wparam, lparam)

        def forget_handles(self) -> None:
            """Free the cached handles once the icon is gone."""
            cache = getattr(self, "_hb_handles", None)
            if cache:
                _destroy_handles(cache)
            self._icon_handle = None

    return TrayIcon


def _pystray_known() -> bool:
    """True for the pystray versions whose private methods TrayIcon overrides (0.19.x).
    A newer pystray gets its plain icon, which works as before 1.12.0."""
    try:
        from pystray import _info
        return tuple(_info.__version__[:2]) == (0, 19)
    except Exception:
        return False


def tray_icon(key: str, *args, **kwargs):
    """A pystray icon with a stable id and cached icon handles (TrayIcon above).
    The class is made from whatever pystray.Icon is at the time of the call."""
    base = pystray.Icon
    if sys.platform == "win32" and not _pystray_known():
        return base(*args, **kwargs)
    cls = _tray_classes.get(base)
    if cls is None:
        cls = _tray_classes[base] = _make_tray_class(base)
    ic = cls(*args, **kwargs)
    ic._hb_uid = icon_uid(key)
    ic._hb_handles = {}
    return ic


# ------------------------------------------------------------ formatting
def badge_for(st: DeviceStatus) -> str:
    if st.kind in ("headset", "mouse", "gamepad", "keyboard"):   # reported by the device itself
        return st.kind
    n = st.name.lower()
    if any(w in n for w in HEADSET_WORDS):
        return "headset"
    if st.source == "playstation":
        return "dualsense" if "dualsense" in n else "dualshock"
    if st.source == "xinput":
        return "gamepad"
    if st.source == "bluetooth":
        return "bluetooth"
    return "mouse"


# the pictograms a user can pick for one device ("Icon" in its menu); "" = automatic
PICTOGRAM_CHOICES = (("", "Automatic"), ("mouse", "Mouse"), ("keyboard", "Keyboard"),
                     ("headset", "Headset"), ("gamepad", "Controller"), ("bluetooth", "Bluetooth"))

GAMEPAD_WORDS = ("controller", "gamepad", "joystick", "joy-con")


def dedupe_controllers(results: List[DeviceStatus], bt: List[DeviceStatus]) -> List[DeviceStatus]:
    """A controller connected over Bluetooth is seen twice: by the controller
    provider and as a Bluetooth device. Windows' own Bluetooth battery value is
    the one shown in Settings, so the controller provider's entry is dropped."""
    bt_pads = [s for s in bt if s.kind == "gamepad" or any(w in s.name.lower() for w in GAMEPAD_WORDS)]
    if not bt_pads:
        return results
    families = {f for f in (device_family(s.name) for s in bt_pads) if f}
    out: List[DeviceStatus] = []
    for s in results:
        if s.source != "xinput":
            out.append(s)
            continue
        if s.via == "bluetooth":
            log.info("[XInput] %s is connected over Bluetooth and shown as a Bluetooth device", s.name)
            continue
        # The provider only knows the transport when the device paths say so, and they do not
        # always: an Xbox Wireless Controller over Bluetooth can come back without the service
        # guid in its path, and Windows.Gaming.Input's unusable report for it (remain=100
        # against full=1000, i.e. 10%) then sat next to the correct Bluetooth value as a second
        # icon. A Bluetooth gamepad of the same device family is the same device. Compared
        # exactly, not as a substring, so the "Xbox controller 1"/"Xbox controller 2" names of
        # two controllers cannot collapse into one icon.
        fam = device_family(s.name)
        if fam and fam in families:
            log.info("[XInput] %s: the Bluetooth reading of the same controller is shown instead", s.name)
            continue
        out.append(s)
    return out


# Words that describe how a device is connected or what shape it is, rather than which
# device it is. The two dedupe helpers pick opposite winners on purpose: for a
# controller over Bluetooth, Windows' own value is the one Settings shows and the
# controller provider's is wrong (see above), while a headset read over HID carries its
# own charging state and the Bluetooth copy of it lags a percent behind.
_TRANSPORT_WORDS = frozenset((
    "bt", "ble", "bluetooth", "wireless", "dongle", "receiver", "headset", "usb",
))


def device_family(name: str) -> str:
    """A device name reduced to what identifies the device, not the connection.

    "Audeze Maxwell", "Audeze Maxwell Headset" and "Audeze Maxwell BT" are one headset
    seen over three connections and have to come out equal.
    """
    words = "".join(c if c.isalnum() else " " for c in (name or "").lower()).split()
    return " ".join(w for w in words if w not in _TRANSPORT_WORDS)


def drop_bluetooth_duplicates(results: List[DeviceStatus],
                              logged: Set[str]) -> List[DeviceStatus]:
    """One icon per device, not one per transport.

    A device that is read over HID is also visible to Windows' own Bluetooth battery
    API once it is paired: a Maxwell reports the same level over the USB-C endpoint and
    over Bluetooth at the same time, which put a second icon in the tray next to the
    live one. The HID reading wins here - it is the device's own protocol and it
    carries the charging state - so the Bluetooth copy is dropped and said so once per
    device rather than every poll. A device HID cannot see (Bluetooth only, nothing
    plugged in) keeps its Bluetooth icon, which is how a headset used purely over
    Bluetooth is covered at all: the vendor collection the provider needs does not
    exist over Bluetooth.
    """
    # controllers are left to dedupe_controllers(): for them the Bluetooth value wins.
    # Only a live HID reading counts: a receiver whose device is not linked (no level)
    # or a greyed-out last value (not online) means the device is elsewhere, often on
    # Bluetooth right now, and dropping that live copy left only the grey icon
    hid = [device_family(st.name) for st in results
           if not st.key.startswith("bt:") and st.source not in ("bluetooth", "xinput")
           and st.online and st.level is not None]
    kept: List[DeviceStatus] = []
    for st in results:
        if st.key.startswith("bt:") or st.source == "bluetooth":
            fam = device_family(st.name)
            # both sides need a name long enough to be a device rather than a fragment:
            # a short HID family ("razer", "g pro") used to match inside an unrelated
            # longer Bluetooth name ("razer barracuda pro", "logitech g pro x") and
            # drop that device's icon
            duplicate = bool(fam) and any(
                fam == h or (min(len(fam), len(h)) >= 6 and (fam in h or h in fam))
                for h in hid)
            if duplicate:
                if st.key not in logged:
                    logged.add(st.key)
                    log.info("[Bluetooth] %s is already read over HID, the "
                             "Bluetooth copy is not shown", st.name)
                continue
            logged.discard(st.key)
        kept.append(st)
    return kept


def describe(st: DeviceStatus, name: Optional[str] = None, left: str = "") -> str:
    """The tooltip text. `name` replaces the device's own name (set with "Rename..."),
    `left` is the estimated time left ("about 5 h of use left"), shown only while the
    device is awake and on battery."""
    return f"{name or st.name}: {device_state(st, left)}"


def device_state(st: DeviceStatus, left: str = "") -> str:
    """The part of describe() after the name: "85%, charging", "no link ..."."""
    if st.approx:
        state = st.approx          # XInput: coarse levels or "not reported yet", never a fake "NN%"
    elif st.level is None:
        state = "no link (off or asleep)"
    else:
        state = f"{st.level}%"
        if st.charging:
            state += ", charging"
        if not st.online:
            state += " (last known value, device asleep)"
        elif left and not st.charging:
            state += f", {left}"
    return state


# ------------------------------------------------------------- hide / rename
def ask_name(current: str) -> Optional[str]:
    """Show a Windows input box for a new device name.
    -> the new name, or None when the user cancels or leaves it empty.

    The box comes from PowerShell (Microsoft.VisualBasic InputBox), which is on every
    Windows. The current name goes to PowerShell in an environment variable, not in
    the command line, so quotes or other characters in a name do no harm."""
    if sys.platform != "win32":
        return None
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
              "Add-Type -AssemblyName Microsoft.VisualBasic; "
              "[Microsoft.VisualBasic.Interaction]::InputBox("
              "'New name for this device:', 'Halo Battery - Rename', $env:HALO_BATTERY_NAME)")
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, timeout=600, creationflags=0x08000000,   # CREATE_NO_WINDOW
            env=dict(os.environ, HALO_BATTERY_NAME=current))
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("rename: %s", e)
        return None
    name = res.stdout.decode("utf-8", "replace").strip()
    return name[:60] or None


# ------------------------------------------------------------- application
# how long to wait for a tray icon's own thread to create its window. pystray makes
# the window in that thread; until then, showing, hiding or stopping the icon has no
# effect (stop() is ignored, and a show is lost).
ICON_READY_TIMEOUT = 5.0


class DeviceIcon:
    """A separate tray icon for one device."""

    def __init__(self, app: "App", key: str):
        self.app = app
        self.key = key
        self.status: Optional[DeviceStatus] = None
        self.frames: Optional[list] = None      # "breathing" frames while charging
        self._state = None                      # to avoid redrawing when nothing changed
        self._images: Dict[tuple, object] = {}  # state -> image or frames, both colours
        self.icon = tray_icon(key, f"{APP_NAME}_{abs(hash(key))}",
                              icons.render(None, False, False, light_taskbar=app.light_taskbar),
                              APP_TITLE, app.build_menu(self))
        self.icon._hb_flyout = getattr(app, "flyout", None)
        # pystray calls the setup function once the icon's window exists. The app shows
        # the icon itself (in _update), after that, so a show is never lost.
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.icon.run, args=(lambda icon: self.ready.set(),),
                                       daemon=True)
        self.thread.start()

    def update(self, st: DeviceStatus) -> None:
        with self.app.lock:
            self._update(st)

    def _update(self, st: DeviceStatus) -> None:
        self.status = st
        badge = self.app.pictogram(st) if self.app.cfg["badges"] else ""
        animate = (self.app.cfg["animation"] and st.charging and st.online
                   and st.level is not None)
        # the number replaces the pictogram; a device that only reports rough steps
        # (st.approx) keeps its pictogram rather than showing a made-up exact number
        text = (str(st.level) if self.app.cfg.get("percent_in_icon") and st.level is not None
                and not st.approx else "")
        state = (st.level, st.charging, st.online, self.app.low_for(st),
                 self.app.light_taskbar, badge, animate, text)
        if state != self._state:
            self._state = state
            art = self._art(state)
            if animate:
                self.frames = art
                self.icon.icon = art[self.app.anim_tick % len(art)]
            else:
                self.frames = None
                self.icon.icon = art
        title = describe(st, self.app.display_name(st), self.app.time_left_text(st))
        # the tray tooltip is limited to 127 characters
        if self.icon.title != title[:127]:
            self.icon.title = title[:127]
            # pystray builds the Windows menu once and keeps its texts. The menu header
            # shows the same text as the tooltip, so rebuild the menu when it changes;
            # otherwise the header keeps "No devices found" from before the first reading
            try:
                self.icon.update_menu()
            except Exception:
                pass
        ready = getattr(self, "ready", None)
        if not self.icon.visible and (ready is None or ready.wait(ICON_READY_TIMEOUT)):
            try:
                self.icon.visible = True
            except Exception:
                pass

    def _art(self, state: tuple):
        """The image (or the charging frames) for a state. The same state in the
        other colour is drawn at the same time, so when the bar colour flips the
        icon switches without rendering anything."""
        if state not in self._images:
            self._images.clear()
            level, charging, online, low, light, badge, animate, text = state
            for lt in (light, not light):
                if animate:
                    art = icons.charging_frames(level, online, low, lt, badge, text=text)
                else:
                    art = icons.render(level, charging, online, low, lt, badge, text=text)
                self._images[(level, charging, online, low, lt, badge, animate, text)] = art
        return self._images[state]

    def tick(self, i: int) -> None:
        with self.app.lock:
            frames = self.frames
            if frames:
                try:
                    self.icon.icon = frames[i % len(frames)]
                except Exception:
                    pass

    def stop(self) -> None:
        with self.app.lock:
            self.frames = None                  # the animation stops touching the icon
        try:
            self.icon.stop()
        except Exception:
            pass
        forget = getattr(self.icon, "forget_handles", None)
        if forget is not None:
            forget()                            # free the cached icon handles


class App:
    def __init__(self):
        self.cfg = load_config()
        self.light_taskbar = False
        self.theme_evt = threading.Event()   # "re-check the icon colour now"
        self.win_events: Optional[winevents.WindowEventWatcher] = None
        self.light_taskbar = self.compute_light()
        self.providers = make_providers()
        self.key_provider: Dict[str, str] = {}   # device key -> provider name
        self.history = history.History(HISTORY_PATH)
        self.history.load()
        self.held: Dict[tuple, tuple] = {}      # (key, title) -> (text, title), held while quiet
        self.was_quiet = False
        self.bt = BluetoothProvider()
        self.icons: Dict[str, DeviceIcon] = {}
        self.placeholder: Optional[pystray.Icon] = None
        self.lock = threading.RLock()
        self._bt_dup_logged: Set[str] = set()   # Bluetooth copies already reported
        self.wake = threading.Event()
        self.stop_evt = threading.Event()
        self.diag_requested = threading.Event()
        self.alerted: Dict[str, bool] = {}
        self.full_state: Dict[str, str] = {}   # key -> charging / full / idle
        self.missing: Dict[str, int] = {}
        self.bt_cache: List[DeviceStatus] = []
        self.anim_tick = 0
        self.bt_wake = threading.Event()      # "poll Bluetooth now"
        self.bt_fresh = threading.Event()     # fresh result for diagnostics
        self.bt_watch: Optional[BluetoothWatcher] = None
        self.bt_watch_failed = False
        self.update: Optional[tuple] = None   # (version, release page) when a newer one exists
        self.update_wake = threading.Event()  # "check for updates now"
        # the tray menu (flyout.py); None = pystray's classic menu
        self.flyout: Optional[flyout.FlyoutHost] = (
            flyout.FlyoutHost() if sys.platform == "win32" and self.cfg.get("fluent_menu", True) else None)

    # ---------------- menu
    def build_menu(self, owner: Optional[DeviceIcon]) -> Menu:
        # the flyout shows the state on the line below the name (flyout.HeaderItem)
        def header_title():
            if owner and owner.status:
                return self.display_name(owner.status) or owner.status.name
            hidden = len(self._settings_map("hidden"))
            return f"No devices shown ({hidden} hidden)" if hidden else "No devices found"

        def header_detail():
            if owner and owner.status:
                return device_state(owner.status, self.time_left_text(owner.status))
            return ""

        def set_interval(sec):
            self.cfg["interval"] = sec
            save_config(self.cfg)
            self.wake.set()

        def set_low(p):
            self.cfg["low"] = p
            save_config(self.cfg)
            self.wake.set()

        def toggle(key):
            def _f(icon, item):
                self.cfg[key] = not self.cfg[key]
                save_config(self.cfg)
                if key == "bluetooth":
                    self.bt_cache = []
                    self.bt_wake.set()
                if key == "update_check":
                    if self.cfg[key]:
                        self.cfg["update_last"] = 0     # turned back on: check right away
                        self.update_wake.set()
                    else:
                        self.update = None
                        self.refresh_menus()
                if key == "status_file" and not self.cfg[key]:
                    remove_status_file()        # no stale levels left behind for other apps
                self.wake.set()
            return _f

        def set_theme(mode):
            def _f(icon, item):
                self.cfg["icon_theme"] = mode
                save_config(self.cfg)
                self.refresh_theme()
                self.theme_evt.set()
            return _f

        def toggle_autostart(icon, item):
            try:
                if not set_autostart(not autostart_enabled()):
                    icon.notify(TEMP_AUTOSTART_TEXT, "Start with Windows")
            except OSError as e:
                log.warning("autostart: %s", e)

        intervals = [(15, "15 s"), (30, "30 s"), (60, "1 min"), (120, "2 min"), (300, "5 min")]
        themes = [("auto", "Automatic"), ("white", "White"), ("black", "Black")]
        lows = [(0, "Off"), (10, "10%"), (15, "15%"), (20, "20%"), (25, "25%"), (30, "30%")]

        def update_text(_item):
            return f"Download v{self.update[0]}…" if self.update else "Download update…"

        def renamed(_item):
            return bool(owner and owner.status and owner.status.key in self._settings_map("names"))

        def picked(value):
            return lambda _item: bool(owner and owner.status) and (
                self._settings_map("icons").get(owner.status.key, "") == value)

        def pick(value):
            # pystray accepts only actions with 0-2 parameters
            return lambda icon, item: self.set_pictogram(owner, value)

        def show_again(key):
            # pystray accepts only actions with 0-2 parameters, so no "k=key" default here
            return lambda icon, item: self.unhide(key)

        def device_low_picked(value):
            return lambda _item: bool(owner and owner.status) and (
                self.device_low(owner.status.key) == value)

        def pick_device_low(value):
            return lambda icon, item: self.set_device_low(owner, value)

        def default_low_text(_item):
            low = self.cfg["low"]
            return f"Default ({low}%)" if low else "Default (off)"

        def provider_on(name):
            return lambda _item: name not in self.disabled_providers()

        def flip_provider(name):
            return lambda icon, item: self.toggle_provider(name)

        def provider_items():
            for name, label in sorted(PROVIDER_LABELS.items(), key=lambda kv: kv[1].lower()):
                yield Item(label, flip_provider(name), checked=provider_on(name))

        def hidden_items():
            # built each time the menu opens, so it always shows the current list
            hidden = self._settings_map("hidden")
            for key, name in sorted(hidden.items(), key=lambda kv: str(kv[1]).lower()):
                yield Item(f"Show {name}", show_again(key))

        # items for the device of this icon only (the "no devices" icon has none)
        device_items = [
            # the flyout has the pencil next to the name instead
            flyout.classic_only(Item("Rename…", lambda i, it: self.rename(owner))),
            Item("Reset name", lambda i, it: self.reset_name(owner), visible=renamed),
            Item("Icon", Menu(*[Item(label, pick(value), checked=picked(value), radio=True)
                                for value, label in PICTOGRAM_CHOICES])),
            Item("Low battery alert at", Menu(
                Item(default_low_text, pick_device_low(None), checked=device_low_picked(None), radio=True),
                *[Item(t, pick_device_low(p), checked=device_low_picked(p), radio=True)
                  for p, t in lows])),
            Item("Hide this device", lambda i, it: self.hide(owner)),
        ] if owner is not None else []

        # all settings in one submenu, so the main menu keeps only the things used often
        preferences = Menu(
            # - / + in the menu; the classic menu shows them as a list to pick from
            flyout.CounterItem("Poll interval", intervals, lambda: self.cfg["interval"], set_interval),
            flyout.CounterItem("Low battery alert", lows, lambda: self.cfg["low"], set_low),
            Item("Alert when fully charged", toggle("full_alert"),
                 checked=lambda it: self.cfg.get("full_alert", True)),
            Item("Estimated time left", toggle("time_left"),
                 checked=lambda it: self.cfg.get("time_left", True)),
            Item("Quiet while gaming", toggle("quiet_fullscreen"),
                 checked=lambda it: self.cfg.get("quiet_fullscreen", True)),
            Menu.SEPARATOR,
            Item("Windows Bluetooth devices", toggle("bluetooth"),
                 checked=lambda it: self.cfg["bluetooth"]),
            # off: a PS4 / PS5 controller over Bluetooth shows its level only while Steam or a
            # game has it in the full mode; on: the app switches it, which some games do not
            # survive until the controller is turned off and on (#96)
            Item("PlayStation full mode (Bluetooth)", toggle("playstation_full_mode"),
                 checked=lambda it: self.cfg.get("playstation_full_mode", False)),
            Item("Device types", Menu(provider_items)),
            Item("Device pictogram", toggle("badges"),
                 checked=lambda it: self.cfg["badges"]),
            Item("Percentage in the icon", toggle("percent_in_icon"),
                 checked=lambda it: self.cfg.get("percent_in_icon", False)),
            Item("Charging animation", toggle("animation"),
                 checked=lambda it: self.cfg["animation"]),
            Item("Icon colour", Menu(*[
                Item(t, set_theme(m), checked=lambda it, m=m: self.cfg.get("icon_theme", "auto") == m, radio=True)
                for m, t in themes])),
            Menu.SEPARATOR,
            Item("Status file for other apps", toggle("status_file"),
                 checked=lambda it: self.cfg.get("status_file", False)),
            Item("Start with Windows", toggle_autostart,
                 checked=lambda it: autostart_enabled()),
            Item("Check for updates", toggle("update_check"),
                 checked=lambda it: self.cfg.get("update_check", True)),
        )

        return Menu(
            flyout.HeaderItem(header_title, header_detail,
                              edit=(lambda icon: self.rename(owner)) if owner is not None else None),
            Item(update_text, lambda i, it: self.open_update(),
                 visible=lambda it: self.update is not None),
            *device_items,
            Menu.SEPARATOR,
            Item("Refresh now", lambda i, it: self.wake.set(), default=True),
            Item("Preferences", preferences),
            Item("Hidden devices", Menu(hidden_items),
                 visible=lambda it: bool(self._settings_map("hidden"))),
            Menu.SEPARATOR,
            Item("Diagnostics…", lambda i, it: self.request_diag()),
            Item(f"Exit (v{VERSION})", lambda i, it: self.quit()),
        )

    # ---------------- hide / rename
    def _settings_map(self, key: str) -> Dict[str, str]:
        """cfg["hidden"] or cfg["names"] (device key -> name), or cfg["icons"] (device
        key -> pictogram). A value that is not a dict (a hand-edited or damaged settings
        file) is replaced by an empty one."""
        value = self.cfg.get(key)
        if not isinstance(value, dict):
            value = self.cfg[key] = {}
        return value

    def display_name(self, st: DeviceStatus) -> str:
        """The name the user gave the device, or the device's own name."""
        name = self._settings_map("names").get(st.key)
        return name if isinstance(name, str) and name else st.name

    def hide(self, owner: Optional[DeviceIcon]) -> None:
        """Remove the icon and remember the device, so it does not come back."""
        if owner is None or owner.status is None:
            return
        st = owner.status
        with self.lock:
            self._settings_map("hidden")[st.key] = self.display_name(st)
            save_config(self.cfg)
            ic = self.icons.pop(st.key, None)
            self.missing.pop(st.key, None)
            self.alerted.pop(st.key, None)
        log.info("hidden: %s [%s]", self.display_name(st), st.key)
        if ic is not None:
            # stop the icon from another thread: this runs in the icon's own menu callback
            threading.Thread(target=ic.stop, daemon=True).start()
        self.refresh_menus()
        self.wake.set()           # the next poll shows the "no devices" icon if none is left

    def unhide(self, key: str) -> None:
        with self.lock:
            name = self._settings_map("hidden").pop(key, None)
            save_config(self.cfg)
        log.info("shown again: %s [%s]", name, key)
        self.refresh_menus()
        self.wake.set()           # the next poll gives the device its icon again

    def rename(self, owner: Optional[DeviceIcon]) -> None:
        if owner is None or owner.status is None:
            return
        # the input box waits for the user: do not block the tray menu while it is open
        threading.Thread(target=self._rename, args=(owner,), daemon=True).start()

    def _rename(self, owner: DeviceIcon) -> None:
        st = owner.status
        if st is None:
            return
        new = ask_name(self.display_name(st))
        if new is None or new == self.display_name(st):
            return
        with self.lock:
            names = self._settings_map("names")
            if new == st.name:
                names.pop(st.key, None)       # back to the device's own name
            else:
                names[st.key] = new
            hidden = self._settings_map("hidden")
            if st.key in hidden:
                hidden[st.key] = new
            save_config(self.cfg)
        log.info("renamed [%s] to %r", st.key, new)
        owner.update(owner.status or st)      # new tooltip at once
        self.refresh_menus()

    def reset_name(self, owner: Optional[DeviceIcon]) -> None:
        if owner is None or owner.status is None:
            return
        with self.lock:
            self._settings_map("names").pop(owner.status.key, None)
            save_config(self.cfg)
        owner.update(owner.status)
        self.refresh_menus()

    def pictogram(self, st: DeviceStatus) -> str:
        """The pictogram the user picked for this device, or the automatic one."""
        choice = self._settings_map("icons").get(st.key)
        if isinstance(choice, str) and choice in icons.PICTOS:
            return choice
        return badge_for(st)

    def set_pictogram(self, owner: Optional[DeviceIcon], value: str) -> None:
        """"Icon" in the device menu: "" goes back to the automatic pictogram."""
        if owner is None or owner.status is None:
            return
        with self.lock:
            chosen = self._settings_map("icons")
            if value:
                chosen[owner.status.key] = value
            else:
                chosen.pop(owner.status.key, None)
            save_config(self.cfg)
        log.info("icon of [%s]: %s", owner.status.key, value or "automatic")
        owner.update(owner.status)            # redraw at once
        self.refresh_menus()

    # ---------------- low battery alert per device
    def device_low(self, key: str) -> Optional[int]:
        """The alert level set for this device only, or None to follow Preferences.
        A value that is not a whole number 0..100 (a hand-edited settings file) is ignored."""
        value = self._settings_map("lows").get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100:
            return value
        return None

    def low_for(self, st: DeviceStatus) -> int:
        """The low battery level of this device: its own, or the one in Preferences."""
        own = self.device_low(st.key)
        return self.cfg["low"] if own is None else own

    def set_device_low(self, owner: Optional[DeviceIcon], value: Optional[int]) -> None:
        """"Low battery alert at" in the device menu: None goes back to the default."""
        if owner is None or owner.status is None:
            return
        key = owner.status.key
        with self.lock:
            lows = self._settings_map("lows")
            if value is None:
                lows.pop(key, None)
            else:
                lows[key] = value
            save_config(self.cfg)
            self.alerted.pop(key, None)       # the new level may alert at once
        log.info("low battery alert of [%s]: %s", key, "default" if value is None else f"{value}%")
        owner.update(owner.status)            # the ring turns red at the new level
        self.refresh_menus()

    # ---------------- device types
    def disabled_providers(self) -> Set[str]:
        """Providers turned off in Preferences > Device types. A value that is not a
        list (a hand-edited settings file) counts as none turned off."""
        value = self.cfg.get("disabled_providers")
        if not isinstance(value, list):
            return set()
        return {v for v in value if isinstance(v, str)}

    def toggle_provider(self, name: str) -> None:
        """Turn one device type on or off. Off, its devices are not opened at all, and
        their icons go away at once rather than after the next two polls."""
        with self.lock:
            off = self.disabled_providers()
            if name in off:
                off.discard(name)
                gone = []
            else:
                off.add(name)
                gone = [k for k, p in self.key_provider.items() if p == name]
            self.cfg["disabled_providers"] = sorted(off)
            save_config(self.cfg)
            stopped = []
            for key in gone:
                ic = self.icons.pop(key, None)
                self.missing.pop(key, None)
                self.alerted.pop(key, None)
                if ic is not None:
                    stopped.append(ic)
        log.info("device type %s: %s", name, "off" if name in off else "on")
        for ic in stopped:
            # stop the icon from another thread: this runs in an icon's own menu callback
            threading.Thread(target=ic.stop, daemon=True).start()
        self.refresh_menus()
        self.wake.set()

    # ---------------- time left
    def time_left_text(self, st: DeviceStatus) -> str:
        """"about 5 h of use left", or "" when turned off or not known yet."""
        if not self.cfg.get("time_left", True) or st.approx:
            return ""
        seconds = self.history.seconds_left(st.key, st.level)
        return history.format_left(seconds) if seconds is not None else ""

    # ---------------- icon colour
    def compute_light(self) -> bool:
        """True when the icons should be drawn for a light bar (black icons).
        With the standard Windows shell this is the Windows theme, as before.
        MyDockFinder draws its own macOS-style menu bar and switches it between
        light and dark by the wallpaper or the full-screen app, so while it is
        running the icons follow what is on screen at the top instead."""
        mode = self.cfg.get("icon_theme", "auto")
        if mode in ("white", "black"):          # set by hand (e.g. a transparent taskbar)
            return mode == "black"
        if mode == "auto":
            mode = "topbar" if icons.mydockfinder_running() else "windows"
        if mode == "topbar":
            res = icons.top_bar_is_light(self.light_taskbar)
            return self.light_taskbar if res is None else res
        return icons.taskbar_is_light()

    def refresh_theme(self) -> None:
        light = self.compute_light()
        if light == self.light_taskbar:
            return
        self.light_taskbar = light
        for ic in list(self.icons.values()):
            if ic.status is not None:
                ic.update(ic.status)            # the state key includes the colour -> redraw
        if self.placeholder is not None:
            self.placeholder.icon = icons.render(None, False, False, light_taskbar=light)
        log.info("icon colour: %s (%s%s)", "black" if light else "white", self.cfg.get("icon_theme"),
                 ", MyDockFinder" if icons.mydockfinder_running() else "")

    def follows_top_bar(self) -> bool:
        mode = self.cfg.get("icon_theme", "auto")
        return mode == "topbar" or (mode == "auto" and icons.mydockfinder_running())

    def theme_loop(self):
        """Keep the icon colour in step with the bar.

        With the standard Windows shell: a registry read every 1.5 s, as before.

        While MyDockFinder is running: the colour is re-checked the moment a
        window is maximized, restored, snapped, moved, minimized, closed or
        brought to the front (system window events, the same ones MyDockFinder
        reacts to), and again 0.1, 0.3 and 0.6 s later, when the window
        animation has settled. A poll every 0.25 s covers everything else
        (a new wallpaper, a page turning dark inside a maximized window).
        One check is five window lookups under the bar plus, when windows
        cover it, a small screen sample: a few milliseconds."""
        checks: List[float] = []        # scheduled re-checks (time.monotonic())
        last = 0.0
        while True:
            fast = self.follows_top_bar()
            if fast and self.win_events is None:
                self.win_events = winevents.WindowEventWatcher(self.theme_evt)
                log.info("window events: %s", "on" if self.win_events.start() else "unavailable")
            elif not fast and self.win_events is not None:
                self.win_events.stop()          # fixed colour chosen, or MyDockFinder closed
                self.win_events = None
            now = time.monotonic()
            if checks:
                timeout = max(0.0, checks[0] - now)
            else:
                timeout = 0.25 if fast else 1.5
            hit = self.theme_evt.wait(timeout)
            if self.stop_evt.is_set():
                return
            now = time.monotonic()
            if hit:
                self.theme_evt.clear()
                if not fast:
                    continue
                # right away (at most every 30 ms while a window is being dragged),
                # then once the maximize / restore animation has finished
                checks = sorted({max(now, last + 0.03), now + 0.1, now + 0.3, now + 0.6})
                if checks[0] > now:
                    continue
            checks = [t for t in checks if t > now + 0.005]
            last = now
            try:
                self.refresh_theme()
            except Exception:
                log.exception("theme")

    # ---------------- diagnostics
    def request_diag(self):
        self.diag_requested.set()
        if self.cfg["bluetooth"] and not (self.bt_watch and self.bt_watch.running()):
            # fallback mode: fresh Bluetooth poll first, then the report
            self.bt_fresh.clear()
            self.bt_wake.set()
            threading.Thread(target=self._diag_after_bt, daemon=True).start()
        else:
            self.wake.set()

    def _diag_after_bt(self):
        self.bt_fresh.wait(70)
        self.wake.set()

    def write_diag(self, results: List[DeviceStatus]):
        lines = [f"{APP_TITLE} v{VERSION}  {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"Python {sys.version.split()[0]}  {sys.platform}", ""]
        lines.append("=== Poll result ===")
        lines += [describe(s) + f"   [{s.key}]" for s in results] or ["(nothing)"]
        hidden, names = self._settings_map("hidden"), self._settings_map("names")
        lines += [f"hidden by the user: {n}   [{k}]" for k, n in hidden.items()]
        lines += [f"renamed by the user: {n}   [{k}]" for k, n in names.items()]
        lines += [f"icon picked by the user: {v}   [{k}]"
                  for k, v in self._settings_map("icons").items()]
        lines += [f"low battery alert set by the user: {v}%   [{k}]"
                  for k, v in self._settings_map("lows").items()]
        disabled = self.disabled_providers()
        if disabled:
            lines.append("device types turned off: " + ", ".join(sorted(disabled)))
        lines.append(f"quiet while gaming: {'on' if self.cfg.get('quiet_fullscreen', True) else 'off'}, "
                     f"full-screen app in front now: {fullscreen_app_running()}, "
                     f"{len(self.held)} notification(s) held")
        lines.append("status file: " + (STATUS_PATH if self.cfg.get("status_file") else "off"))
        lines.append("")
        lines.append("=== Battery history (time left) ===")
        lines += self.history.report()
        lines.append("")
        lines.append("=== Icon colour ===")
        lines.append(f"mode: {self.cfg.get('icon_theme', 'auto')}, icons drawn for a "
                     f"{'light' if self.light_taskbar else 'dark'} bar")
        we = self.win_events
        lines.append("window events: " + ("not used" if we is None else
                     f"{'on' if we.running() else 'unavailable'}, {we.count} received"))
        try:
            lines += icons.theme_report()
        except Exception as e:
            lines.append(f"(failed: {e})")
        lines.append("")
        lines.append("=== Protocol details ===")
        lines.append("PlayStation full mode over Bluetooth: "
                     + ("on (the app switches the controller)" if self.cfg.get("playstation_full_mode")
                        else "off (listen only)"))
        for p in self.providers + ([self.bt] if self.cfg["bluetooth"] else []):
            if p.name not in disabled:
                lines += p.diagnostics()
        lines.append("")
        lines.append("=== All HID devices ===")
        lines += dump_hid()
        lines.append("")
        lines.append("=== Recent log entries ===")
        try:
            with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
                lines += [l.rstrip("\n") for l in f.readlines()[-120:]]
        except OSError:
            lines.append("(log is empty)")
        text = "\n".join(lines)
        with open(DIAG_PATH, "w", encoding="utf-8") as f:
            f.write(text)
        if sys.platform == "win32":
            try:
                os.startfile(DIAG_PATH)  # type: ignore[attr-defined]
            except OSError:
                pass
        return text

    # ---------------- polling
    def poll_once(self) -> List[DeviceStatus]:
        results: List[DeviceStatus] = []
        disabled = self.disabled_providers()
        for p in self.providers:
            if p.name in disabled:
                continue          # turned off in Preferences > Device types: not opened at all
            if isinstance(p, PlayStationProvider):
                p.switch_bluetooth = bool(self.cfg.get("playstation_full_mode", False))
            try:
                found = p.poll()
            except Exception:
                log.exception("provider %s", p.name)
                continue
            for st in found:
                self.key_provider[st.key] = p.name
            results += found
        if self.cfg["bluetooth"]:
            # Bluetooth is polled in its own thread (bt_loop); only the cache is used here
            bt = list(self.bt_cache)
            results = dedupe_controllers(results, bt) + bt
            results = drop_bluetooth_duplicates(results, self._bt_dup_logged)
        return results

    def _bt_update(self, res: List[DeviceStatus]):
        """A snapshot from the Bluetooth watcher: show it right away."""
        if self.cfg["bluetooth"]:
            self.bt_cache = res
        self.bt_fresh.set()
        self.wake.set()

    def bt_loop(self):
        """Separate thread: PowerShell can take a few seconds and must not delay
        the mouse and headset icons. On Windows a long-lived watcher reports
        connects and disconnects within a few seconds; if it cannot run, one-shot
        polls once a minute are the fallback."""
        while not self.stop_evt.is_set():
            if self.cfg["bluetooth"] and sys.platform == "win32" and not self.bt_watch_failed:
                if self.bt_watch is None:
                    self.bt_watch = BluetoothWatcher(self.bt, self._bt_update).start()
                if self.bt_watch.failed:
                    self.bt_watch_failed = True
                    self.bt_watch = None
                    continue
                self.bt_wake.wait(2)
                self.bt_wake.clear()
                continue
            if self.bt_watch is not None:            # Bluetooth turned off in the menu
                self.bt_watch.stop()
                self.bt_watch = None
            if self.cfg["bluetooth"]:
                t0 = time.time()
                try:
                    res = self.bt.poll()
                except Exception:
                    log.exception("bluetooth")
                    res = []
                log.info("bluetooth: %d device(s) in %.1f s", len(res), time.time() - t0)
                if self.cfg["bluetooth"]:
                    self.bt_cache = res
                self.bt_fresh.set()
                self.wake.set()                 # show the result right away
            else:
                self.bt_cache = []
                self.bt_fresh.set()
            self.bt_wake.wait(60)
            self.bt_wake.clear()

    def apply(self, results: List[DeviceStatus]):
        seen = set()
        hidden = self._settings_map("hidden")
        now = time.time()
        for st in results:
            if st.key in hidden:
                continue          # hidden by the user: no icon and no low battery alert
            seen.add(st.key)
            self.history.record(st.key, st.level, st.charging, st.online, now,
                                coarse=bool(st.approx))
            self.missing.pop(st.key, None)
            ic = self.icons.get(st.key)
            if ic is None:
                ic = DeviceIcon(self, st.key)
                self.icons[st.key] = ic
            ic.update(st)
            self.check_alert(ic, st)
            self.check_full(ic, st)

        # device gone (receiver unplugged): remove the icon after 2 misses in a row;
        # XInput reports a switched-off controller reliably, the Bluetooth provider
        # already confirms a disconnect itself, and a PlayStation controller's
        # presence comes from the reliable HID list (and its key switches between
        # the cable-only and Bluetooth forms when a cable is added to a BT pad),
        # so those go at once
        for key in list(self.icons):
            if key not in seen:
                self.missing[key] = self.missing.get(key, 0) + 1
                limit = 1 if key.startswith(("xinput:", "bt:", "ps:")) else 2
                if self.missing[key] >= limit:
                    self.icons.pop(key).stop()
                    # the icon is gone: stop counting, otherwise the quick
                    # 3-second re-check in wait_next() would go on forever
                    self.missing.pop(key, None)

        self.show_placeholder(not self.icons)
        self.history.save()

    def show_placeholder(self, show: bool) -> None:
        """The "no devices" icon. It is made once and after that only shown or hidden.

        Stopping it and making a new one each time a device came or went could leave
        a copy in the tray: pystray ignores stop() until the icon's thread has made
        its window, and its default setup can show the icon again after stop() (#38,
        #95). Showing and hiding one icon, after its window exists, has neither problem.
        """
        if self.placeholder is None:
            if not show:
                return
            ready = threading.Event()
            self.placeholder = tray_icon(IDLE_KEY, f"{APP_NAME}_idle",
                                         icons.render(None, False, False, light_taskbar=self.light_taskbar),
                                         f"{APP_TITLE}: no devices found",
                                         self.build_menu(None))
            self.placeholder._hb_flyout = getattr(self, "flyout", None)
            self.placeholder_ready = ready
            threading.Thread(target=self.placeholder.run, args=(lambda icon: ready.set(),),
                             daemon=True).start()
        if not self.placeholder_ready.wait(ICON_READY_TIMEOUT):
            log.warning("tray: the \"no devices\" icon did not start in %.0f s", ICON_READY_TIMEOUT)
            return
        if bool(self.placeholder.visible) != show:
            try:
                self.placeholder.visible = show
            except Exception as e:
                log.warning("tray: %s", e)

    def check_alert(self, ic: DeviceIcon, st: DeviceStatus):
        low = self.low_for(st)
        if not low or st.level is None or not st.online:
            return
        if st.charging or st.level > low + 5:
            self.alerted[st.key] = False
            return
        if st.level <= low and not self.alerted.get(st.key):
            self.alerted[st.key] = True
            try:
                self.notify(ic.icon, st.key,
                            low_battery_text(self.display_name(st), st.level, bool(st.approx)),
                            "Low battery")
            except Exception as e:
                log.warning("notify: %s", e)

    def check_full(self, ic: DeviceIcon, st: DeviceStatus):
        """A notification when a charging device reaches 100 %, once per charge.

        Only a device that was seen charging below 100 % gets it, so a device that is
        already full when the app starts does not. Some devices stop reporting
        "charging" when they are full, so 100 % right after charging counts too. A level
        that goes 100 -> 99 -> 100 on the charger does not give a second one: the alert
        comes again only after the device leaves the charger or drops below 95 %."""
        if st.level is None or not st.online:
            return
        prev = self.full_state.get(st.key)
        if st.level >= 100 and prev == "charging" and self.cfg.get("full_alert", True):
            try:
                self.notify(ic.icon, st.key, fully_charged_text(self.display_name(st)),
                            "Fully charged")
            except Exception as e:
                log.warning("notify: %s", e)
        if st.level >= 100:
            self.full_state[st.key] = "full"
        elif not st.charging:
            self.full_state[st.key] = "idle"
        elif not (prev == "full" and st.level >= 95):
            self.full_state[st.key] = "charging"

    def loop(self):
        while not self.stop_evt.is_set():
            # snapshot BEFORE polling: anything that changes while the poll runs
            # (a controller switched off mid-poll) still triggers the next poll
            sig = self.change_signature()
            results = self.poll_once()
            try:
                self.apply(results)
            except Exception:
                log.exception("apply")
            quiet = self.quiet()
            if quiet != self.was_quiet:
                self.was_quiet = quiet
                log.info("full-screen app %s", "in front: quiet" if quiet else "gone")
            if not quiet and self.held:
                self.flush_held()
            try:
                self.write_status(results)
            except Exception:
                log.exception("status file")
            if self.diag_requested.is_set():
                self.diag_requested.clear()
                try:
                    self.write_diag(results)
                except Exception:
                    log.exception("diag")
            self.wait_next(sig)
            self.wake.clear()

    @staticmethod
    def usb_signature():
        """Set of present HID interfaces, used to notice plug and unplug events
        (mouse put on the cable, receiver removed, etc.). On Windows this only
        lists device paths and does not open any device; hid.enumerate() would
        open every HID device, the keyboard included, every 2.5 s."""
        try:
            paths = hidlist.interface_paths()
        except Exception:
            paths = None
        if paths is not None:
            return paths
        if hid is None:
            return None
        try:
            return frozenset((d["vendor_id"], d["product_id"]) for d in hid.enumerate())
        except Exception:
            return None

    def change_signature(self):
        """Cheap snapshot of what is connected: HID devices (receivers, cables)
        plus XInput controller slots (a controller switched on behind a receiver
        that stays plugged in does not change the HID list)."""
        xs = None
        disabled = self.disabled_providers()
        for p in self.providers:
            if isinstance(p, XInputProvider) and p.name not in disabled:
                try:
                    xs = p.connected_slots()
                except Exception:
                    xs = None
        return (self.usb_signature(), xs)

    def wait_next(self, sig=None):
        """Wait for the next scheduled poll, but wake up early when a device is
        plugged in, unplugged, switched on or off."""
        interval = self.cfg["interval"]
        disabled = self.disabled_providers()
        if any(getattr(p, "pending", False) for p in self.providers if p.name not in disabled):
            interval = min(interval, 3)   # a new controller has no battery info yet: re-check soon
        if any(self.missing.values()):
            interval = min(interval, 3)   # a device just went missing: confirm quickly instead of in a minute
        quiet = self.quiet()
        if quiet:
            # a game is full screen: every poll talks to the devices, so do it rarely.
            # Plugging something in still polls at once (the signature check below)
            interval = max(self.cfg["interval"], QUIET_INTERVAL)
        deadline = time.time() + interval
        if sig is None:
            sig = self.change_signature()
        while not self.stop_evt.is_set():
            left = deadline - time.time()
            if left <= 0 or self.wake.wait(min(2.5, left)):
                return
            now = self.change_signature()
            if now != sig:
                time.sleep(1.0)          # give Windows time to finish setting up the device
                return
            if quiet and not self.quiet():
                return                   # the game is closed: poll now and show what was held

    def anim_loop(self):
        """Advances the "breathing" frames of charging devices; other icons are left alone."""
        step = icons.BREATH_PERIOD / icons.BREATH_FRAMES
        while not self.stop_evt.wait(step):
            self.anim_tick += 1
            for ic in list(self.icons.values()):
                ic.tick(self.anim_tick)

    # ---------------- update check
    def refresh_menus(self) -> None:
        """Rebuild the tray menus, so an item that appeared or went away shows up."""
        targets = [ic.icon for ic in list(self.icons.values())]
        if self.placeholder is not None:
            targets.append(self.placeholder)
        for icon in targets:
            try:
                icon.update_menu()
            except Exception:
                pass

    def notify_any(self, text: str, title: str, key: str = "") -> None:
        """A tray notification from whichever icon is there."""
        icon = next((ic.icon for ic in list(self.icons.values())), None) or self.placeholder
        if icon is not None:
            try:
                self.notify(icon, key, text, title)
            except Exception as e:
                log.warning("notify: %s", e)

    # ---------------- status file for other apps
    def status_data(self, results: List[DeviceStatus], running: bool = True) -> dict:
        """What status.json holds: every device that has an icon, as the tooltip shows
        it. `running` is false in the file the app leaves behind when it exits."""
        hidden = self._settings_map("hidden")
        devices = []
        for st in results:
            if st.key in hidden:
                continue
            secs = None if st.approx else self.history.seconds_left(st.key, st.level)
            if st.charging or not st.online:
                secs = None
            devices.append({
                "key": st.key,
                "name": self.display_name(st),
                "level": st.level,
                "charging": st.charging,
                "online": st.online,
                "kind": self.pictogram(st),
                "approx": st.approx or None,
                "low_alert_at": self.low_for(st),
                "seconds_left": None if secs is None else int(secs),
                "text": describe(st, self.display_name(st), self.time_left_text(st)),
            })
        now = time.time()
        return {"app": APP_TITLE, "version": VERSION, "running": running,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                "updated_unix": int(now), "devices": devices}

    def write_status(self, results: List[DeviceStatus]) -> None:
        if self.cfg.get("status_file"):
            write_json(STATUS_PATH, self.status_data(results))

    # ---------------- quiet while a game is full screen
    def quiet(self) -> bool:
        """True while "Quiet while gaming" is on and a full-screen app is in front."""
        return bool(self.cfg.get("quiet_fullscreen", True)) and fullscreen_app_running()

    def notify(self, icon, key: str, text: str, title: str) -> None:
        """Show a notification now, or hold it until the full-screen app is gone. Only
        the newest one per device and kind is kept, so a long game ends with one
        "Low battery" per device rather than a pile of them."""
        if self.quiet():
            self.held[(key, title)] = (text, title)
            log.info("held while full screen: %s", text)
            return
        icon.notify(text, title)

    def flush_held(self) -> None:
        """The full-screen app is gone: show what was held, except a low battery alert
        for a device that has been put on the charger (or topped up) since."""
        held, self.held = self.held, {}
        for (key, title), (text, _) in held.items():
            ic = self.icons.get(key)
            st = ic.status if ic is not None else None
            if title == "Low battery" and st is not None and (
                    st.charging or (st.level is not None and st.level > self.low_for(st))):
                continue
            self.notify_any(text, title, key)

    def open_update(self) -> None:
        url = self.update[1] if self.update else updates.RELEASES_URL
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception as e:
            log.warning("open %s: %s", url, e)

    def update_loop(self):
        """Once a day ask GitHub for the latest release; nothing is downloaded or
        installed, the menu only offers the release page."""
        # what the last check found is shown right away, without waiting for the network
        seen = self.cfg.get("update_latest", "")
        if self.cfg.get("update_check", True) and updates.is_newer(seen, VERSION):
            self.update = (seen, self.cfg.get("update_url") or updates.RELEASES_URL)
        if self.stop_evt.wait(30):               # let the icons come up first
            return
        while not self.stop_evt.is_set():
            last = float(self.cfg.get("update_last", 0) or 0)
            if self.cfg.get("update_check", True) and time.time() - last >= updates.CHECK_EVERY:
                self.check_update()
            self.update_wake.wait(3600)
            self.update_wake.clear()

    def check_update(self) -> None:
        try:
            latest, url = updates.fetch_latest(VERSION)
        except Exception as e:
            log.info("update check failed: %s", e)   # offline, rate limit: try again later
            self.cfg["update_last"] = time.time() - updates.CHECK_EVERY + 3 * 3600
            save_config(self.cfg)
            return
        self.cfg.update(update_last=time.time(), update_latest=latest, update_url=url)
        if updates.is_newer(latest, VERSION):
            log.info("update available: v%s (running v%s)", latest, VERSION)
            self.update = (latest, url)
            self.refresh_menus()
            if self.cfg.get("update_notified") != latest:
                self.cfg["update_notified"] = latest
                self.notify_any(update_text(latest), f"{APP_TITLE} update")
        else:
            self.update = None
        save_config(self.cfg)

    def quit(self):
        self.stop_evt.set()
        self.history.save(force=True)
        if self.cfg.get("status_file"):
            try:
                write_json(STATUS_PATH, self.status_data([], running=False))
            except OSError as e:
                log.warning("status file: %s", e)
        self.update_wake.set()
        self.theme_evt.set()
        if self.win_events is not None:
            self.win_events.stop()
        self.wake.set()
        self.bt_wake.set()
        if self.bt_watch is not None:
            self.bt_watch.stop()
        for ic in list(self.icons.values()):
            ic.stop()
        if self.placeholder:
            self.placeholder.stop()
        host = getattr(self, "flyout", None)
        if host is not None:
            host.stop()

    def run(self):
        log.info("start v%s", VERSION)
        worker = threading.Thread(target=self.loop, daemon=True)
        worker.start()
        threading.Thread(target=self.theme_loop, daemon=True).start()
        threading.Thread(target=self.anim_loop, daemon=True).start()
        threading.Thread(target=self.bt_loop, daemon=True).start()
        threading.Thread(target=self.update_loop, daemon=True).start()
        if self.flyout is not None:
            self.flyout.start()                  # tkinter is ready by the first right-click
        try:
            while not self.stop_evt.is_set():
                self.stop_evt.wait(1)
        except KeyboardInterrupt:
            self.quit()
        time.sleep(0.5)


def dump_hid() -> List[str]:
    if hid is None:
        return ["hidapi is not installed"]
    lines = []
    try:
        devs = hid.enumerate()
    except Exception as e:
        return [f"hid.enumerate: {e}"]
    for d in sorted(devs, key=lambda x: (x["vendor_id"], x["product_id"], x.get("interface_number", 0))):
        lines.append(
            f"VID={d['vendor_id']:04x} PID={d['product_id']:04x} if={d.get('interface_number')} "
            f"usage={d.get('usage_page', 0):04x}:{d.get('usage', 0):04x} "
            f"'{d.get('manufacturer_string') or ''}' '{d.get('product_string') or ''}'")
    return lines


def _probe_emit(sections):
    """Show the probe's output.

    In a console this prints. Without one - a windowed build started plainly,
    where `print()` goes nowhere - the text goes to `HaloBattery-probe.txt`
    next to the executable and is opened with the default editor, so a probe
    run is never silent (this is how the first probe build looked blank in #28).
    """
    text = "\n".join(sections)
    if sys.stdout is not None:
        try:
            print(text)
            return None
        except (OSError, ValueError):
            pass
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.getcwd()
    path = os.path.join(base, "HaloBattery-probe.txt")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except OSError:
        path = os.path.join(tempfile.gettempdir(), "HaloBattery-probe.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    try:
        os.startfile(path)
    except OSError:
        pass
    return path


def probe():
    """Console mode: a single poll with verbose output."""
    # providers can deepen their diagnostics for this run (the Corsair receiver
    # family logs every write and every frame; see #28)
    os.environ["HALO_PROBE"] = "1"
    # device names come from Windows and may contain any characters; a cp1252
    # console would otherwise crash on them
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    app = App.__new__(App)
    app.cfg = load_config()
    app.providers = make_providers()
    app.bt = BluetoothProvider()
    res = []
    sections = []
    for p in app.providers + [app.bt]:
        if isinstance(p, PlayStationProvider):
            p.switch_bluetooth = bool(app.cfg.get("playstation_full_mode", False))
        res += p.poll()
        sections.append("\n".join(p.diagnostics()))
    sections.append("\n=== Summary ===")
    for s in res:
        sections.append(describe(s))
    if not res:
        sections.append("Nothing found.")
    # always list every HID device: the case worth dumping is a device that did
    # not answer while others did, and that never reaches the branch above
    sections.append("\nAll HID devices:")
    sections.append("\n".join(dump_hid()))
    _probe_emit(sections)


def main():
    # before any window exists: without it Windows draws the app at 100 % and
    # stretches it on a scaled screen, which made the menu text small and blurry
    flyout.enable_dpi_awareness()
    if "--probe" in sys.argv:
        probe()
        return
    if not single_instance():
        return
    set_app_id()                   # before the tray icons: notifications say "HaloBattery"
    # the app used to be called "Battery Tray": pick up its settings and autostart
    if migrate_legacy_config():
        log.info("settings migrated from %%APPDATA%%\\%s", LEGACY_NAME)
    if migrate_legacy_autostart():
        log.info("autostart entry migrated from %s", LEGACY_NAME)
    elif refresh_autostart():
        log.info("autostart entry now points at %s", sys.executable)
    App().run()


if __name__ == "__main__":
    main()
