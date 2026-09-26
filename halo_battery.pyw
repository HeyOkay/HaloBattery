"""Halo Battery: battery levels of wireless devices in the Windows system tray.

Supported:
  * Razer (BlackShark V2 Pro headset, mice, etc.): directly over USB/HID, no Synapse
  * Audeze Maxwell (2.4 GHz dongle or USB-C cable)
  * WLmouse (Beast X / Beast X Max / Mini Pro)
  * Logitech (HID++ 2.0 mice and keyboards: Lightspeed / Unifying receivers, G HUB not needed)
  * SteelSeries (Arctis Nova 7 and Nova 5 headsets, GG not needed)
  * MCHOSE (M7 Ultra and the rest of the 0x5253 family, on the 2.4 GHz receiver)
  * Bluetooth devices whose battery level Windows knows (enabled from the menu)

Run:   pythonw halo_battery.pyw
Debug: python halo_battery.pyw --probe
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Set

APP_NAME = "HaloBattery"
APP_TITLE = "Halo Battery"
VERSION = "1.10.1"
LEGACY_NAME = "BatteryTray"      # the app's previous name (settings and autostart are migrated)

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

APPDATA_DIR = os.environ.get("APPDATA", os.path.expanduser("~"))
DATA_DIR = os.path.join(APPDATA_DIR, APP_NAME)
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "halo_battery.log")
DIAG_PATH = os.path.join(DATA_DIR, "diagnostics.txt")

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

import icons  # noqa: E402
import winevents  # noqa: E402
from providers import hidlist  # noqa: E402
from providers import (AudezeProvider, BluetoothProvider, DeviceStatus, HyperXProvider,  # noqa: E402
                       LogitechProvider, MchoseProvider, RazerProvider, SteelSeriesProvider,
                       WLmouseProvider, XInputProvider)
from providers.bluetooth import BluetoothWatcher  # noqa: E402

HEADSET_WORDS = ("blackshark", "kraken", "barracuda", "nari", "thresher", "headset",
                 "headphone", "earbud", "buds", "hammerhead", "airpods")

DEFAULTS = {
    "interval": 60,      # seconds between polls
    "low": 20,           # low battery notification threshold, %
    "notify": True,
    "bluetooth": True,   # Windows Bluetooth devices
    "badges": True,      # device pictogram inside the ring
    "animation": True,   # "breathing" arc while charging
    # icon colour: "auto" follows the Windows theme, or the top menu bar while
    # MyDockFinder is running; "white" / "black" are fixed (tray menu > Icon colour);
    # "windows" / "topbar" force one automatic source (config file only)
    "icon_theme": "auto",
}


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


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log.warning("save_config: %s", e)


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


def set_autostart(on: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except OSError:
                pass


def migrate_legacy_autostart() -> bool:
    """Replace the old "BatteryTray" autostart entry with the new name and path."""
    if sys.platform != "win32":
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
    point it at the copy that is running now. Only for the built .exe."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
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


# ------------------------------------------------------------ formatting
def badge_for(st: DeviceStatus) -> str:
    if st.kind in ("headset", "mouse", "gamepad"):     # reported by the device itself
        return st.kind
    n = st.name.lower()
    if any(w in n for w in HEADSET_WORDS):
        return "headset"
    if st.source == "xinput":
        return "gamepad"
    if st.source == "bluetooth":
        return "bluetooth"
    return "mouse"


GAMEPAD_WORDS = ("controller", "gamepad", "joystick", "joy-con")


def dedupe_controllers(results: List[DeviceStatus], bt: List[DeviceStatus]) -> List[DeviceStatus]:
    """A controller connected over Bluetooth is seen twice: by the controller
    provider and as a Bluetooth device. Windows' own Bluetooth battery value is
    the one shown in Settings, so the controller provider's entry is dropped."""
    bt_pads = [s for s in bt if s.kind == "gamepad" or any(w in s.name.lower() for w in GAMEPAD_WORDS)]
    if not bt_pads:
        return results
    out = [s for s in results if not (s.source == "xinput" and s.via == "bluetooth")]
    for s in results:
        if s not in out:
            log.info("[XInput] %s is connected over Bluetooth and shown as a Bluetooth device", s.name)
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
    # controllers are left to dedupe_controllers(): for them the Bluetooth value wins
    hid = [device_family(st.name) for st in results
           if not st.key.startswith("bt:") and st.source not in ("bluetooth", "xinput")]
    kept: List[DeviceStatus] = []
    for st in results:
        if st.key.startswith("bt:") or st.source == "bluetooth":
            fam = device_family(st.name)
            duplicate = bool(fam) and any(
                fam == h or (len(fam) >= 6 and (fam in h or h in fam)) for h in hid)
            if duplicate:
                if st.key not in logged:
                    logged.add(st.key)
                    log.info("[Bluetooth] %s is already read over HID, the "
                             "Bluetooth copy is not shown", st.name)
                continue
            logged.discard(st.key)
        kept.append(st)
    return kept


def describe(st: DeviceStatus) -> str:
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
    return f"{st.name}: {state}"


# ------------------------------------------------------------- application
class DeviceIcon:
    """A separate tray icon for one device."""

    def __init__(self, app: "App", key: str):
        self.app = app
        self.key = key
        self.status: Optional[DeviceStatus] = None
        self.frames: Optional[list] = None      # "breathing" frames while charging
        self._state = None                      # to avoid redrawing when nothing changed
        self._images: Dict[tuple, object] = {}  # state -> image or frames, both colours
        self.icon = pystray.Icon(f"{APP_NAME}_{abs(hash(key))}",
                                 icons.render(None, False, False, light_taskbar=app.light_taskbar), APP_TITLE, app.build_menu(self))
        self.thread = threading.Thread(target=self.icon.run, daemon=True)
        self.thread.start()

    def update(self, st: DeviceStatus) -> None:
        with self.app.lock:
            self._update(st)

    def _update(self, st: DeviceStatus) -> None:
        self.status = st
        badge = badge_for(st) if self.app.cfg["badges"] else ""
        animate = (self.app.cfg["animation"] and st.charging and st.online
                   and st.level is not None)
        state = (st.level, st.charging, st.online, self.app.cfg["low"],
                 self.app.light_taskbar, badge, animate)
        if state != self._state:
            self._state = state
            art = self._art(state)
            if animate:
                self.frames = art
                self.icon.icon = art[self.app.anim_tick % len(art)]
            else:
                self.frames = None
                self.icon.icon = art
        title = describe(st)
        # the tray tooltip is limited to 127 characters
        if self.icon.title != title[:127]:
            self.icon.title = title[:127]
        if not self.icon.visible:
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
            level, charging, online, low, light, badge, animate = state
            for lt in (light, not light):
                if animate:
                    art = icons.charging_frames(level, online, low, lt, badge)
                else:
                    art = icons.render(level, charging, online, low, lt, badge)
                self._images[(level, charging, online, low, lt, badge, animate)] = art
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
        self.frames = None
        try:
            self.icon.stop()
        except Exception:
            pass


class App:
    def __init__(self):
        self.cfg = load_config()
        self.light_taskbar = False
        self.theme_evt = threading.Event()   # "re-check the icon colour now"
        self.win_events: Optional[winevents.WindowEventWatcher] = None
        self.light_taskbar = self.compute_light()
        self.providers = [RazerProvider(), AudezeProvider(), WLmouseProvider(), MchoseProvider(),
                          HyperXProvider(), LogitechProvider(), SteelSeriesProvider(), XInputProvider()]
        self.bt = BluetoothProvider()
        self.icons: Dict[str, DeviceIcon] = {}
        self.placeholder: Optional[pystray.Icon] = None
        self.lock = threading.RLock()
        self._bt_dup_logged: Set[str] = set()   # Bluetooth copies already reported
        self.wake = threading.Event()
        self.stop_evt = threading.Event()
        self.diag_requested = threading.Event()
        self.alerted: Dict[str, bool] = {}
        self.missing: Dict[str, int] = {}
        self.bt_cache: List[DeviceStatus] = []
        self.anim_tick = 0
        self.bt_wake = threading.Event()      # "poll Bluetooth now"
        self.bt_fresh = threading.Event()     # fresh result for diagnostics
        self.bt_watch: Optional[BluetoothWatcher] = None
        self.bt_watch_failed = False

    # ---------------- menu
    def build_menu(self, owner: Optional[DeviceIcon]) -> Menu:
        def header_text(_item):
            if owner and owner.status:
                return describe(owner.status)
            return "No devices found"

        def set_interval(sec):
            def _f(icon, item):
                self.cfg["interval"] = sec
                save_config(self.cfg)
                self.wake.set()
            return _f

        def set_low(p):
            def _f(icon, item):
                self.cfg["low"] = p
                save_config(self.cfg)
                self.wake.set()
            return _f

        def toggle(key):
            def _f(icon, item):
                self.cfg[key] = not self.cfg[key]
                save_config(self.cfg)
                if key == "bluetooth":
                    self.bt_cache = []
                    self.bt_wake.set()
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
                set_autostart(not autostart_enabled())
            except OSError as e:
                log.warning("autostart: %s", e)

        intervals = [(15, "15 seconds"), (30, "30 seconds"), (60, "1 minute"),
                     (120, "2 minutes"), (300, "5 minutes")]
        themes = [("auto", "Automatic"), ("white", "White"), ("black", "Black")]
        lows = [(0, "Off"), (10, "10%"), (15, "15%"), (20, "20%"), (25, "25%"), (30, "30%")]

        return Menu(
            Item(header_text, None, enabled=False),
            Menu.SEPARATOR,
            Item("Refresh now", lambda i, it: self.wake.set(), default=True),
            Item("Poll interval", Menu(*[
                Item(t, set_interval(s), checked=lambda it, s=s: self.cfg["interval"] == s, radio=True)
                for s, t in intervals])),
            Item("Low battery alert at", Menu(*[
                Item(t, set_low(p), checked=lambda it, p=p: self.cfg["low"] == p, radio=True)
                for p, t in lows])),
            Item("Windows Bluetooth devices", toggle("bluetooth"),
                 checked=lambda it: self.cfg["bluetooth"]),
            Item("Device pictogram", toggle("badges"),
                 checked=lambda it: self.cfg["badges"]),
            Item("Charging animation", toggle("animation"),
                 checked=lambda it: self.cfg["animation"]),
            Item("Icon colour", Menu(*[
                Item(t, set_theme(m), checked=lambda it, m=m: self.cfg.get("icon_theme", "auto") == m, radio=True)
                for m, t in themes])),
            Item("Start with Windows", toggle_autostart,
                 checked=lambda it: autostart_enabled()),
            Menu.SEPARATOR,
            Item("Diagnostics…", lambda i, it: self.request_diag()),
            Item(f"Exit (v{VERSION})", lambda i, it: self.quit()),
        )

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
        for p in self.providers + ([self.bt] if self.cfg["bluetooth"] else []):
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
        for p in self.providers:
            try:
                results += p.poll()
            except Exception:
                log.exception("provider %s", p.name)
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
        for st in results:
            seen.add(st.key)
            self.missing.pop(st.key, None)
            ic = self.icons.get(st.key)
            if ic is None:
                ic = DeviceIcon(self, st.key)
                self.icons[st.key] = ic
                time.sleep(0.3)   # let the icon register
            ic.update(st)
            self.check_alert(ic, st)

        # device gone (receiver unplugged): remove the icon after 2 misses in a row;
        # XInput reports a switched-off controller reliably, and the Bluetooth
        # provider already confirms a disconnect itself, so those go at once
        for key in list(self.icons):
            if key not in seen:
                self.missing[key] = self.missing.get(key, 0) + 1
                limit = 1 if key.startswith(("xinput:", "bt:")) else 2
                if self.missing[key] >= limit:
                    self.icons.pop(key).stop()
                    # the icon is gone: stop counting, otherwise the quick
                    # 3-second re-check in wait_next() would go on forever
                    self.missing.pop(key, None)

        if self.icons and self.placeholder:
            self.placeholder.stop()
            self.placeholder = None
        elif not self.icons and not self.placeholder:
            self.placeholder = pystray.Icon(f"{APP_NAME}_idle",
                                            icons.render(None, False, False, light_taskbar=self.light_taskbar),
                                            f"{APP_TITLE}: no devices found",
                                            self.build_menu(None))
            threading.Thread(target=self.placeholder.run, daemon=True).start()

    def check_alert(self, ic: DeviceIcon, st: DeviceStatus):
        low = self.cfg["low"]
        if not low or st.level is None or not st.online:
            return
        if st.charging or st.level > low + 5:
            self.alerted[st.key] = False
            return
        if st.level <= low and not self.alerted.get(st.key):
            self.alerted[st.key] = True
            try:
                left = "battery is low" if st.approx else f"{st.level}% left"
                ic.icon.notify(f"{st.name}: {left}. Time to charge.", "Low battery")
            except Exception as e:
                log.warning("notify: %s", e)

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
        for p in self.providers:
            if isinstance(p, XInputProvider):
                try:
                    xs = p.connected_slots()
                except Exception:
                    xs = None
        return (self.usb_signature(), xs)

    def wait_next(self, sig=None):
        """Wait for the next scheduled poll, but wake up early when a device is
        plugged in, unplugged, switched on or off."""
        interval = self.cfg["interval"]
        if any(getattr(p, "pending", False) for p in self.providers):
            interval = min(interval, 3)   # a new controller has no battery info yet: re-check soon
        if any(self.missing.values()):
            interval = min(interval, 3)   # a device just went missing: confirm quickly instead of in a minute
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

    def anim_loop(self):
        """Advances the "breathing" frames of charging devices; other icons are left alone."""
        step = icons.BREATH_PERIOD / icons.BREATH_FRAMES
        while not self.stop_evt.wait(step):
            self.anim_tick += 1
            for ic in list(self.icons.values()):
                ic.tick(self.anim_tick)

    def quit(self):
        self.stop_evt.set()
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

    def run(self):
        log.info("start v%s", VERSION)
        worker = threading.Thread(target=self.loop, daemon=True)
        worker.start()
        threading.Thread(target=self.theme_loop, daemon=True).start()
        threading.Thread(target=self.anim_loop, daemon=True).start()
        threading.Thread(target=self.bt_loop, daemon=True).start()
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


def probe():
    """Console mode: a single poll with verbose output."""
    # device names come from Windows and may contain any characters; a cp1252
    # console would otherwise crash on them
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    app = App.__new__(App)
    app.cfg = load_config()
    app.providers = [RazerProvider(), AudezeProvider(), WLmouseProvider(), MchoseProvider(),
                     HyperXProvider(), LogitechProvider(), SteelSeriesProvider(), XInputProvider()]
    app.bt = BluetoothProvider()
    res = []
    for p in app.providers + [app.bt]:
        res += p.poll()
        print("\n".join(p.diagnostics()))
    print("\n=== Summary ===")
    for s in res:
        print(describe(s))
    if not res:
        print("Nothing found. All HID devices:")
        print("\n".join(dump_hid()))


def main():
    if "--probe" in sys.argv:
        probe()
        return
    if not single_instance():
        return
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
