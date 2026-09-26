"""Update check: is there a newer Halo Battery release on GitHub?

Once a day the app asks GitHub for the latest release (the public REST API, no token,
one small request). When it is newer than the running version, the tray shows a
notification once for that version and the menu gets a "Download vX.Y.Z..." item that
opens the release page. Nothing is downloaded or installed automatically. The check can
be turned off in the menu ("Check for updates").
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Optional, Tuple

REPO = "HeyOkay/HaloBattery"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600          # seconds between checks
TIMEOUT = 10


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """'v1.11.0' / '1.11.0' -> (1, 11, 0); anything else -> None."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)\s*$", text or "")
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def fetch_latest(current: str) -> Tuple[str, str]:
    """-> (version without the 'v', release page URL). Raises on network or API errors."""
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"HaloBattery/{current}",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        raise ValueError(f"unexpected tag name {tag!r}")
    return tag.lstrip("vV").strip(), str(data.get("html_url") or RELEASES_URL)
