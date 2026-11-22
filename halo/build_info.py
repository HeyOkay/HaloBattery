"""Build-only channel identity and local notes; user settings cannot select a channel."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

STABLE = "stable"
PRE_RELEASE = "pre-release"
METADATA_LIMIT = 4096
NOTES_LIMIT = 1024 * 1024


def _read(path: Path, limit: int) -> str:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("bundled text exceeds its size limit")
    return data.decode("utf-8")


def channel(version: str) -> str:
    """Only a matching frozen build can identify itself as a pre-release."""
    if not getattr(sys, "frozen", False):
        return STABLE
    try:
        root = Path(sys._MEIPASS)
        info = json.loads(_read(root / "halo" / "build-info.json", METADATA_LIMIT))
        if (isinstance(info, dict) and type(info.get("schema")) is int and info["schema"] == 1
                and isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
                and info.get("version") == version and info.get("channel") == PRE_RELEASE):
            return PRE_RELEASE
    except (AttributeError, OSError, TypeError, ValueError, RecursionError):
        pass
    return STABLE


def preview_notes() -> str:
    """The bundled/source Unreleased section, without a network request or a future stable tag."""
    try:
        root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        text = _read(root / "CHANGELOG.md", NOTES_LIMIT)
        match = re.search(r"(?m)^## \[Unreleased\][^\n]*\n", text)
        if match is None:
            return ""
        rest = text[match.end():]
        end = re.search(r"(?m)^## ", rest)
        return rest[:end.start() if end else None].strip()
    except (AttributeError, OSError, TypeError, ValueError):
        return ""
