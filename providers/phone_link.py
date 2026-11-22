"""Read only Phone Link's selected-device companion cache, never the phone.

File-format evidence (independently implemented; no upstream code copied):
PaulBayfield/devices.bayfield.dev, revision 4cd6e3c4fc3b7d61837e60b933318e51b620d253,
agent/src/devices/phonelink/__init__.py:47-111; krisgfx9/Wireless-Device-Battery-Monitor,
revision bbc203045b185ce3f0b1fe66597830515822e849, src/drivers/phonelink.cpp:79-109.
The observed 1.26092.119.0 header/status-row shape is checked explicitly below.
Identity uses the per-entry Metadata.Id, not the shared outer metadata group.

This is an undocumented, localized Windows 11 cache, not a real-time API. Even
its connected label can be stale. Previously accepted values remain explicitly
last-reported; an ancient cache cannot seed a level on cold startup.
No device request, app launch, network, service activation or private-content
database access is performed. See docs/protocols.md#phone-link-selected-cache.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid

from .base import DeviceStatus, Provider


PACKAGE = "Microsoft.YourPhone_8wekyb3d8bbwe"
MAX_BYTES = 1024 * 1024
FRESH_CACHE_AGE = 300.0
# Admission policy for a previously unseen cached reading, not a heartbeat or
# a sensor timestamp. Once admitted, an unchanged reading is kept as last-known.
INITIAL_CACHE_MAX_AGE = 3 * 3600.0
CLOCK_TOLERANCE = 5.0
CARD_PATH = Path("LocalState", "StartMenu", "StartMenuCompanion.json")
IDENTITY_PATH = Path("LocalCache", "DeviceMetadataStorage.json")
_CONNECTED_SUFFIX = " - Connected to your PC"
_BATTERY = re.compile(r"Battery ([0-9]{1,3})% remaining(, charging)?")
_MESSAGES = {
    "stale": ("state.phone_link_stale", "Phone Link status is out of date; open Phone Link to refresh"),
    "unavailable": ("state.phone_link_unavailable", "Phone Link battery status unavailable"),
}


@dataclass(frozen=True)
class CacheSnapshot:
    card: dict
    metadata: dict
    card_mtime: float


def _text(value, limit=256):
    return (type(value) is str and 0 < len(value) <= limit
            and not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate cache key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("invalid cache number")


def _bounded_shape(value):
    pending = [(value, 0)]
    count = 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 12000 or depth > 24:
            raise ValueError("cache structure too large")
        if isinstance(node, dict):
            if len(node) + len(pending) + count > 12000:
                raise ValueError("cache structure too large")
            pending.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            if len(node) + len(pending) + count > 12000:
                raise ValueError("cache structure too large")
            pending.extend((child, depth + 1) for child in node)
        elif isinstance(node, str) and len(node) > MAX_BYTES:
            raise ValueError("cache string too large")


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _no_links(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("linked cache path refused")


def _read_document(path):
    _no_links(path)
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
            raise ValueError("cache file size refused")
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    if len(raw) > MAX_BYTES or _stamp(before) != _stamp(after):
        raise OSError("cache changed during read")
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                       parse_constant=_invalid_constant)
    _bounded_shape(value)
    if not isinstance(value, dict):
        raise ValueError("cache root refused")
    return value, after


def _read_snapshot(package_root=None):
    """The single injectable local-I/O boundary; no I/O at import/construction."""
    if package_root is None:
        local = os.environ.get("LOCALAPPDATA")
        if sys.platform != "win32" or not local:
            return None
        package_root = Path(local) / "Packages" / PACKAGE
    root = Path(package_root)
    card_path, metadata_path = root / CARD_PATH, root / IDENTITY_PATH
    # A producer can atomically replace either file while the other is read.
    # At most one retry; no locks or writes to Phone Link's private state.
    for attempt in range(2):
        try:
            card, card_info = _read_document(card_path)
            metadata, metadata_info = _read_document(metadata_path)
            if (_stamp(card_path.stat()) == _stamp(card_info)
                    and _stamp(metadata_path.stat()) == _stamp(metadata_info)):
                return CacheSnapshot(card, metadata, card_info.st_mtime)
        except FileNotFoundError:
            return None
        except OSError:
            if attempt:
                raise
    raise OSError("cache snapshot changed")


def selected_identity(card, metadata):
    """Bind the card's selected name to ONE linked mobile's per-entry UUID."""
    if not isinstance(card, dict) or card.get("type") != "AdaptiveCard":
        return None
    speak = card.get("speak")
    if not _text(speak, 320) or not speak.endswith(_CONNECTED_SUFFIX):
        return None
    name = speak[:-len(_CONNECTED_SUFFIX)]
    if not _text(name):
        return None
    groups = metadata.get("DeviceMetadatas") if isinstance(metadata, dict) else None
    if not isinstance(groups, dict) or len(groups) > 64:
        return None
    matches = {}
    count = 0
    for entries in groups.values():
        if not isinstance(entries, list):
            return None
        count += len(entries)
        if count > 256:
            return None
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("IsLinked") is not True:
                continue
            wrapper = entry.get("Metadata")
            inner = wrapper.get("Metadata") if isinstance(wrapper, dict) else None
            if not isinstance(inner, dict) or inner.get("DisplayName") != name:
                continue
            # The same metadata file can contain Windows PCs. IsLinked alone
            # is not a phone classification. Unknown platforms fail closed.
            platform = inner.get("OsName")
            ident = wrapper.get("Id")
            if type(platform) is not str or platform.casefold() not in ("android", "ios"):
                return None
            if not _text(ident, 36) or len(ident) != 36:
                return None
            try:
                parsed = uuid.UUID(ident)
            except ValueError:
                return None
            if parsed.int == 0:
                return None
            matches[str(parsed)] = name
    if len(matches) != 1:
        return None
    ident, name = next(iter(matches.items()))
    return "phone_link:" + ident, name


def battery_from_card(card):
    """Only the known header status row, never arbitrary card/notification text."""
    try:
        row = card["body"][5]
        if row["type"] != "ColumnSet":
            return None
        columns = row["columns"]
        readings = []
        for index, expected in ((4, "Image"), (6, "Container")):
            column = columns[index]
            if column["type"] != "Column":
                return None
            item = column["items"][0]
            if item["type"] != expected:
                return None
            label = item.get("tooltip")
            if not _text(label, 100):
                return None
            match = _BATTERY.fullmatch(label)
            if match is None or int(match[1]) > 100:
                return None
            # Bare "remaining" proves percentage, not charger state. Only
            # explicitly reported charging is known by this initial profile.
            readings.append((int(match[1]), True if match[2] else None))
        return readings[0] if readings[0] == readings[1] else None
    except (KeyError, IndexError, TypeError):
        return None


class PhoneLinkProvider(Provider):
    name = "phone_link"

    def __init__(self, *, snapshot_reader=None, clock=None):
        self._reader = snapshot_reader
        self._clock = clock if clock is not None else time.time
        self._diag = []
        self._accepted = None  # one (UUID key, mtime, percent, charger), never a card/metadata DOM

    def poll(self):
        self._diag = []
        try:
            snapshot = (self._reader or _read_snapshot)()
            if not isinstance(snapshot, CacheSnapshot):
                self._accepted = None
                self._diag.append("[Phone Link] companion cache unavailable")
                return []
            identity = selected_identity(snapshot.card, snapshot.metadata)
            if identity is None:
                self._accepted = None
                self._diag.append("[Phone Link] selected mobile identity unavailable or ambiguous")
                return []
            if self._accepted is not None and self._accepted[0] != identity[0]:
                self._accepted = None
            now, modified = self._clock(), snapshot.card_mtime
            if (type(now) not in (int, float) or type(modified) not in (int, float)
                    or not math.isfinite(now) or not math.isfinite(modified)):
                self._accepted = None
                return [self._unavailable(identity, "unavailable")]
            age = now - modified
            if age < -CLOCK_TOLERANCE:
                self._accepted = None
                return [self._unavailable(identity, "stale")]
            reading = battery_from_card(snapshot.card)
            if reading is None:
                self._accepted = None
                return [self._unavailable(identity, "unavailable")]
            level, charging = reading
            signature = (identity[0], modified, level, charging)
            if age > INITIAL_CACHE_MAX_AGE and signature != self._accepted:
                self._accepted = None
                return [self._unavailable(identity, "stale")]
            self._accepted = signature
            if age > FRESH_CACHE_AGE:
                self._diag.append("[Phone Link] last-reported level retained; connection and charging unconfirmed")
                key, name = identity
                return [DeviceStatus(key, name, level, False, False, self.name,
                    kind="phone", charging_known=False,
                    ui_message="state.phone_link_last_reported", ui_params={"level": level})]
            self._diag.append("[Phone Link] selected companion-cache level accepted; measurement time unverified")
            key, name = identity
            return [DeviceStatus(key, name, level, charging is True, True, self.name,
                                 kind="phone", charging_known=charging is not None)]
        except (OSError, ValueError, TypeError, RecursionError):
            self._accepted = None
            # No raw exception strings, paths, card text or metadata are logged.
            self._diag.append("[Phone Link] companion cache unreadable or unsupported")
            return []

    def _unavailable(self, identity, reason):
        message, text = _MESSAGES[reason]
        self._diag.append("[Phone Link] " + reason + "; percentage withheld")
        key, name = identity
        # Expected source unavailability, not a failed native poll. Keeping
        # polling_stale False preserves the specific translated ui_message.
        return DeviceStatus(key, name, None, False, False, self.name, text,
                            kind="phone", charging_known=False, ui_message=message)

    def diagnostics(self):
        return list(self._diag)
