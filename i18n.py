"""Small, explicit-language renderer. Technical output always uses English.

Catalogs are imported statically so source and PyInstaller builds use the same
translations without resource paths, gettext tooling or a mutable global locale.
"""
from __future__ import annotations

import sys

from locales import en, fr

LANGUAGES = {"en": "English", "fr": "Français"}
CATALOGS = {"en": en.MESSAGES, "fr": fr.MESSAGES}


def detect_language() -> str:
    """The current user's Windows UI language, not their regional format/keyboard.

    GetUserDefaultUILanguage returns a LANGID. Its primary-language bits cover
    all French variants (France, Canada, Belgium, Switzerland, ...).
    https://learn.microsoft.com/windows/win32/api/winnls/nf-winnls-getuserdefaultuilanguage
    """
    if sys.platform != "win32":
        return "en"
    try:
        import ctypes
        get_language = ctypes.windll.kernel32.GetUserDefaultUILanguage
        get_language.argtypes = []
        get_language.restype = ctypes.c_ushort
        langid = get_language()
        french = isinstance(langid, int) and 0 < langid <= 0xFFFF and (langid & 0x3FF) == 0x0C
        return "fr" if french else "en"
    except (AttributeError, OSError, TypeError, ValueError):
        return "en"


def translate(key: str, *, language: str = "en", count=None, **params) -> str:
    """Render a catalog message; an unsupported language/missing entry uses English.

    An unknown reference key or missing argument is a programming error. Keeping
    these visible makes catalog mistakes testable instead of hiding them in the UI.
    """
    catalog = CATALOGS.get(language, en.MESSAGES)
    if key not in catalog:
        catalog = en.MESSAGES
    message = catalog.get(key)
    if message is None:
        raise KeyError(key)
    if isinstance(message, dict):
        if count is None:
            raise ValueError(f"{key} requires count")
        # English: 1; French: 0 and 1. Counts here are nonnegative integers.
        singular = count in (0, 1) if catalog is fr.MESSAGES else count == 1
        form = "one" if singular else "other"
        if form in message:
            message = message[form]
        else:
            message = en.MESSAGES[key]["one" if count == 1 else "other"]
        params = dict(params, count=count)
    return message.format(**params)


def tooltip(text: str) -> str:
    """Win32's 128-WCHAR tooltip buffer includes the terminating NUL.

    A supplementary Unicode character (e.g. emoji in a device name) takes two
    UTF-16 code units. Do not split its surrogate pair at the buffer boundary.
    """
    return text.encode("utf-16-le")[:254].decode("utf-16-le", "ignore")


def format_left(seconds: float, *, language: str = "en") -> str:
    """Format an estimate; the history calculation itself is language-independent."""
    hours = seconds / 3600.0
    if hours < 1:
        return translate("duration.less_hour", language=language)
    if hours < 48:
        return translate("duration.hours", language=language, count=round(hours))
    return translate("duration.days", language=language, count=round(hours / 24))
