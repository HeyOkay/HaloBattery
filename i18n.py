"""Small, explicit-language renderer. Technical output always uses English.

Catalogs are imported statically so source and PyInstaller builds use the same
translations without resource paths, gettext tooling or a mutable global locale.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from locales import en, fr, ja, zh_CN, zh_TW

DEFAULT_LANGUAGE = "en"


def one_other(count: int) -> str:
    """Plural rule for languages with singular 1 and all other integer counts."""
    return "one" if count == 1 else "other"


def other_only(count: int) -> str:
    """Plural rule for languages without singular/plural grammatical changes."""
    return "other"


def _french_plural(count: int) -> str:
    return "one" if count in (0, 1) else "other"


@dataclass(frozen=True)
class Language:
    """All metadata needed to add a language; counts are nonnegative integers.

    Rules may return additional categories (e.g. few/many). Declare all of them
    in plural_forms and supply each form in the catalog's plural messages.
    Windows primary IDs match every regional variant; an empty tuple opts out
    of primary-ID detection. Exact windows_language_ids can select specific
    regional variants while keeping manual selection available.
    """

    name: str
    catalog: Mapping[str, str | Mapping[str, str]]
    plural_rule: Callable[[int], str] = one_other
    windows_primary_ids: tuple[int, ...] = ()
    plural_forms: tuple[str, ...] = ("one", "other")
    windows_language_ids: tuple[int, ...] = ()


# Static imports above and this single registry also drive menus and validation.
LANGUAGES = {
    "en": Language("English", en.MESSAGES, windows_primary_ids=(0x09,)),
    "fr": Language("Français", fr.MESSAGES, plural_rule=_french_plural,
                   windows_primary_ids=(0x0C,)),
    "zh-TW": Language("繁體中文", zh_TW.MESSAGES, plural_rule=other_only,
                      plural_forms=("other",),
                      windows_language_ids=(0x0404, 0x0C04, 0x1404, 0x7C04)),
    "zh-CN": Language("简体中文", zh_CN.MESSAGES, plural_rule=other_only,
                      plural_forms=("other",),
                      windows_language_ids=(0x0804, 0x1004, 0x0004, 0x7804)),
    "ja": Language("日本語", ja.MESSAGES, plural_rule=other_only,
                   windows_primary_ids=(0x11,), plural_forms=("other",)),
}


def detect_language() -> str:
    """The current user's Windows UI language, not their regional format/keyboard.

    GetUserDefaultUILanguage returns a LANGID. Its primary-language bits cover
    all regional variants of a registered language. Exact LANGIDs allow scripts
    such as Traditional Chinese to be selected without matching Simplified Chinese.
    https://learn.microsoft.com/windows/win32/api/winnls/nf-winnls-getuserdefaultuilanguage
    """
    if sys.platform != "win32":
        return DEFAULT_LANGUAGE
    try:
        import ctypes
        get_language = ctypes.windll.kernel32.GetUserDefaultUILanguage
        get_language.argtypes = []
        get_language.restype = ctypes.c_ushort
        langid = get_language()
        if isinstance(langid, int) and not isinstance(langid, bool) and 0 < langid <= 0xFFFF:
            primary_id = langid & 0x3FF
            for code, language in LANGUAGES.items():
                if langid in language.windows_language_ids or primary_id in language.windows_primary_ids:
                    return code
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return DEFAULT_LANGUAGE


def translate(key: str, *, language: str = DEFAULT_LANGUAGE, count=None, **params) -> str:
    """Render a catalog message; an unsupported language/missing entry uses English.

    An unknown reference key or missing argument is a programming error. Keeping
    these visible makes catalog mistakes testable instead of hiding them in the UI.
    """
    fallback = LANGUAGES[DEFAULT_LANGUAGE]
    selected = LANGUAGES.get(language, fallback)
    if key not in selected.catalog:
        selected = fallback
    message = selected.catalog.get(key)
    if message is None:
        raise KeyError(key)
    if isinstance(message, Mapping):
        if count is None:
            raise ValueError(f"{key} requires count")
        form = selected.plural_rule(count)
        if form in message:
            message = message[form]
        else:
            message = fallback.catalog[key][fallback.plural_rule(count)]
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
