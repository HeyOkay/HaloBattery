"""Translations for Halo Battery: the visible app chrome, in English and Spanish.

English ("en") is the original language and the default. Spanish ("es") is a
natural, neutral translation (Windows-like wording, no localisms). Only the
app's own interface is translated here; `get()` never raises and never returns
an empty string unless the English value itself is empty.

What is deliberately *not* translated
-------------------------------------
* Device names: they come from the device (or from the name the user gave it
  with "Rename…"), and are shown exactly as they are.
* `PROVIDER_LABELS` (in halo_battery.pyw): brand / technical identifiers of the
  device types (e.g. "Logitech", "Razer mice and headsets").
* The Diagnostics report and the log: they are support/debug output and stay in
  English, so a report is readable by anyone.
* The `keys` (device identifiers) in status.json and history.json.

Keys are stable English snake_case strings. `get(code, key)` falls back to
English when `code` is unknown or `key` is missing there; if the key is missing
from English too, the key itself is returned (so a missing translation can never
crash the app).
"""
from __future__ import annotations

from typing import Dict

STRINGS: Dict[str, Dict[str, str]] = {
    "en": {
        # ---- tray menu
        "menu_refresh": "Refresh now",
        "menu_preferences": "Preferences",
        "menu_hidden_devices": "Hidden devices",
        "menu_diagnostics": "Diagnostics…",
        "menu_exit": "Exit (v{version})",
        "menu_download_update": "Download update…",
        "menu_download_version": "Download v{version}…",
        "menu_rename": "Rename…",
        "menu_reset_name": "Reset name",
        "menu_icon": "Icon",
        "menu_low_alert_at": "Low battery alert at",
        "menu_hide_device": "Hide this device",
        "menu_show_device": "Show {name}",

        # ---- menu header
        "no_devices_found": "No devices found",
        "no_devices_shown": "No devices shown ({n} hidden)",
        "no_devices_shown_one": "No devices shown (1 hidden)",

        # ---- Preferences
        "pref_poll_interval": "Poll interval",
        "pref_low_battery_alert": "Low battery alert",
        "pref_alert_full": "Alert when fully charged",
        "pref_time_left": "Estimated time left",
        "pref_quiet_gaming": "Quiet while gaming",
        "pref_low_sound": "Sound with the low battery alert",
        "pref_bluetooth": "Windows Bluetooth devices",
        "pref_playstation_full": "PlayStation full mode (Bluetooth)",
        "pref_device_types": "Device types",
        "pref_pictogram": "Device pictogram",
        "pref_percent_icon": "Percentage in the icon",
        "pref_charging_animation": "Charging animation",
        "pref_icon_colour": "Icon colour",
        "pref_status_file": "Status file for other apps",
        "pref_start_windows": "Start with Windows",
        "pref_check_updates": "Check for updates",
        "pref_language": "Language / Idioma",

        # ---- poll interval / alert values
        "interval_15s": "15 s",
        "interval_30s": "30 s",
        "interval_1min": "1 min",
        "interval_2min": "2 min",
        "interval_5min": "5 min",
        "low_off": "Off",
        "low_10": "10%",
        "low_15": "15%",
        "low_20": "20%",
        "low_25": "25%",
        "low_30": "30%",
        "low_default": "Default ({low}%)",
        "low_default_off": "Default (off)",

        # ---- icon
        "theme_auto": "Automatic",
        "theme_white": "White",
        "theme_black": "Black",
        "picto_automatic": "Automatic",
        "picto_mouse": "Mouse",
        "picto_keyboard": "Keyboard",
        "picto_headset": "Headset",
        "picto_controller": "Controller",
        "picto_bluetooth": "Bluetooth",

        # ---- device state (tooltip / status file)
        "state_charging": ", charging",
        "state_asleep": " (last known value, device asleep)",
        "state_no_link": "no link (off or asleep)",

        # ---- estimated time left (history.py)
        "left_less_hour": "less than 1 h of use left",
        "left_one_hour": "about 1 h of use left",
        "left_hours": "about {n} h of use left",
        "left_days": "about {n} days of use left",

        # ---- notifications
        "notify_low": "{name}: {left}. Time to charge.",
        "battery_is_low": "battery is low",
        "percent_left": "{level}% left",
        "fully_charged": "{name} is fully charged.",
        "update_available": "Version {latest} is available. Right-click a battery icon "
                            "and choose \"Download v{latest}…\".",
        "temp_autostart": "Halo Battery is running from a temporary folder (straight from "
                          "the ZIP). Extract the ZIP to a folder of its own, run "
                          "HaloBattery.exe from there, then turn on Start with Windows.",

        # ---- rename dialog
        "rename_prompt": "New name for this device:",
        "rename_title": "Halo Battery - Rename",

        # ---- language names (shown in the selector)
        "language_en": "English",
        "language_es": "Español",
    },
    "es": {
        # ---- tray menu
        "menu_refresh": "Actualizar ahora",
        "menu_preferences": "Preferencias",
        "menu_hidden_devices": "Dispositivos ocultos",
        "menu_diagnostics": "Diagnóstico…",
        "menu_exit": "Salir (v{version})",
        "menu_download_update": "Descargar actualización…",
        "menu_download_version": "Descargar v{version}…",
        "menu_rename": "Cambiar nombre…",
        "menu_reset_name": "Restablecer nombre",
        "menu_icon": "Icono",
        "menu_low_alert_at": "Aviso de batería baja a",
        "menu_hide_device": "Ocultar este dispositivo",
        "menu_show_device": "Mostrar {name}",

        # ---- menu header
        "no_devices_found": "No se encontraron dispositivos",
        "no_devices_shown": "No se muestran dispositivos ({n} ocultos)",
        "no_devices_shown_one": "No se muestran dispositivos (1 oculto)",

        # ---- Preferences
        "pref_poll_interval": "Intervalo de sondeo",
        "pref_low_battery_alert": "Aviso de batería baja",
        "pref_alert_full": "Aviso al cargar por completo",
        "pref_time_left": "Tiempo restante estimado",
        "pref_quiet_gaming": "Silencio durante el juego",
        "pref_low_sound": "Sonido con el aviso de batería baja",
        "pref_bluetooth": "Dispositivos Bluetooth de Windows",
        "pref_playstation_full": "Modo completo de PlayStation (Bluetooth)",
        "pref_device_types": "Tipos de dispositivo",
        "pref_pictogram": "Pictograma del dispositivo",
        "pref_percent_icon": "Porcentaje en el icono",
        "pref_charging_animation": "Animación de carga",
        "pref_icon_colour": "Color del icono",
        "pref_status_file": "Archivo de estado para otras aplicaciones",
        "pref_start_windows": "Iniciar con Windows",
        "pref_check_updates": "Buscar actualizaciones",
        "pref_language": "Language / Idioma",

        # ---- poll interval / alert values
        "interval_15s": "15 s",
        "interval_30s": "30 s",
        "interval_1min": "1 min",
        "interval_2min": "2 min",
        "interval_5min": "5 min",
        "low_off": "Desactivado",
        "low_10": "10%",
        "low_15": "15%",
        "low_20": "20%",
        "low_25": "25%",
        "low_30": "30%",
        "low_default": "Predeterminado ({low}%)",
        "low_default_off": "Predeterminado (desactivado)",

        # ---- icon
        "theme_auto": "Automático",
        "theme_white": "Blanco",
        "theme_black": "Negro",
        "picto_automatic": "Automático",
        "picto_mouse": "Ratón",
        "picto_keyboard": "Teclado",
        "picto_headset": "Auriculares",
        "picto_controller": "Mando",
        "picto_bluetooth": "Bluetooth",

        # ---- device state (tooltip / status file)
        "state_charging": ", cargando",
        "state_asleep": " (último valor conocido, dispositivo suspendido)",
        "state_no_link": "sin conexión (apagado o suspendido)",

        # ---- estimated time left (history.py)
        "left_less_hour": "menos de 1 h de uso restante",
        "left_one_hour": "alrededor de 1 h de uso restante",
        "left_hours": "unas {n} h de uso restantes",
        "left_days": "unos {n} días de uso restantes",

        # ---- notifications
        "notify_low": "{name}: {left}. Es hora de cargar.",
        "battery_is_low": "batería baja",
        "percent_left": "{level}% restante",
        "fully_charged": "{name}: carga completa.",
        "update_available": "La versión {latest} está disponible. Haz clic con el botón "
                            "derecho en un icono de batería y elige «Descargar v{latest}…».",
        "temp_autostart": "Halo Battery se está ejecutando desde una carpeta temporal "
                          "(directamente desde el ZIP). Extrae el ZIP a una carpeta propia, "
                          "ejecuta HaloBattery.exe desde allí y activa «Iniciar con Windows».",
        # ---- rename dialog
        "rename_prompt": "Nuevo nombre para este dispositivo:",
        "rename_title": "Halo Battery - Cambiar nombre",

        # ---- language names (shown in the selector)
        "language_en": "English",
        "language_es": "Español",
    },
}


def get(code: str, key: str) -> str:
    """The text for `key` in `code`, falling back to English.

    Unknown code -> English. Known code but missing key -> English. Missing from
    English too -> the key itself. Never raises KeyError, and never returns an
    empty string unless the English value is empty.
    """
    table = STRINGS.get(code) if isinstance(code, str) else None
    if table is None:
        table = STRINGS["en"]
    if key in table:
        return table[key]
    return STRINGS["en"].get(key, key)
