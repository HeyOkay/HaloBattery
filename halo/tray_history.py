"""Conservative migration of this executable's Windows 11 tray preferences.

NotifyIconSettings is an Explorer implementation detail. Unknown records are
left alone; this module never creates a shell record or resets Explorer's cache.
The caller must use this only for a frozen app, after obtaining its instance
mutex, and call reconcile only after the master notification icon was added.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import tempfile

# Final branded master identity. The unpublished prototype used 0x48424D00;
# migrate that row like other legacy records, then keep this UID stable.
MASTER_UID = 0x48424D10
REGISTRY_PATH = r"Control Panel\NotifyIconSettings"
MAX_BACKUP_BYTES = 4 * 1024 * 1024
MAX_BACKUP_BATCHES = 64


@dataclass(frozen=True)
class _Entry:
    name: str
    values: tuple
    children: tuple

    def get(self, name):
        return next(((value, kind) for key, value, kind in self.values
                     if key.casefold() == name.casefold()), None)


@dataclass(frozen=True)
class PreparedMigration:
    executable: str = ""
    master_absent: bool = False
    promotion: int | None = None
    legacy: tuple = ()


@dataclass(frozen=True)
class ReconcileResult:
    status: str
    removed: int = 0
    skipped: int = 0
    visibility_migrated: bool = False
    error: str = ""


def _registry():
    try:
        import winreg
        return winreg
    except ImportError:
        return None


def _executable(path):
    """Only fully qualified local EXE paths; never a shared Python launcher."""
    if not isinstance(path, str) or not path or "\0" in path or "%" in path:
        return None
    path = path.replace("/", "\\")
    if not re.match(r"^[a-zA-Z]:\\", path) or ":" in path[2:]:
        return None
    result = ntpath.normcase(ntpath.normpath(path))
    name = ntpath.basename(result)
    if not name.endswith(".exe") or re.fullmatch(r"python(?:\d+(?:\.\d+)*)?w?\.exe", name):
        return None
    return result


def _read_entry(registry, root, name):
    with registry.OpenKey(root, name, 0, registry.KEY_READ) as key:
        child_count, value_count, _modified = registry.QueryInfoKey(key)
        children = tuple(sorted(registry.EnumKey(key, i) for i in range(child_count)))
        values = tuple(sorted((registry.EnumValue(key, i) for i in range(value_count)),
                              key=lambda row: row[0].casefold()))
        return _Entry(name, values, children)


def _matches_identity(registry, path, uid, executable):
    return (path is not None and path[1] == registry.REG_SZ
            and _executable(path[0]) == executable and uid is not None
            and uid[1] == registry.REG_DWORD and type(uid[0]) is int
            and 0 <= uid[0] <= 0xFFFFFFFF)


def _own_entries(registry, root, executable):
    child_count, _value_count, _modified = registry.QueryInfoKey(root)
    names = [registry.EnumKey(root, i) for i in range(child_count)]
    result = []
    for name in names:
        if (not re.fullmatch(r"0|[1-9][0-9]{0,19}", name)
                or int(name) > 0xFFFFFFFFFFFFFFFF):
            continue
        try:
            # Most rows belong to other apps. Check only small identity values
            # before copying any IconSnapshot or other potentially large data.
            with registry.OpenKey(root, name, 0, registry.KEY_READ) as key:
                path = registry.QueryValueEx(key, "ExecutablePath")
                uid = registry.QueryValueEx(key, "UID")
                if not _matches_identity(registry, path, uid, executable):
                    continue
            entry = _read_entry(registry, root, name)
        except OSError:
            continue
        path, uid = entry.get("ExecutablePath"), entry.get("UID")
        if (entry.children or any("guid" in name.casefold() for name, _, _ in entry.values)
                or not _matches_identity(registry, path, uid, executable)):
            continue
        result.append(entry)
    return tuple(sorted(result, key=lambda row: int(row.name)))


def _promotion(registry, entries):
    states = []
    for entry in entries:
        state = entry.get("IsPromoted")
        if (state is not None and state[1] == registry.REG_DWORD
                and type(state[0]) is int and state[0] in (0, 1)):
            states.append(state[0])
    return 1 if 1 in states else (0 if 0 in states else None)


def prepare(executable):
    """Capture visibility before ADD; an existing master's preferences always win."""
    executable = _executable(executable)
    registry = _registry()
    if executable is None or registry is None:
        return PreparedMigration()
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, REGISTRY_PATH, 0,
                              registry.KEY_READ) as root:
            entries = _own_entries(registry, root, executable)
    except OSError:
        return PreparedMigration(executable=executable)
    masters = [entry for entry in entries if entry.get("UID")[0] == MASTER_UID]
    legacy = tuple(entry for entry in entries if entry.get("UID")[0] != MASTER_UID)
    return PreparedMigration(executable, not masters, _promotion(registry, legacy), legacy)


def _json_value(value):
    if isinstance(value, bytes):
        return {"base64": base64.b64encode(value).decode("ascii")}
    if value is None or type(value) in (str, int):
        return value
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError("unsupported registry value in tray history backup")


def _backup(executable, data_dir, entries):
    """Keep one bounded, atomic journal; refuse cleanup instead of losing backups."""
    suffix = hashlib.sha256(executable.encode("utf-8")).hexdigest()[:16]
    destination = Path(data_dir) / ("tray-history-backup-" + suffix + ".json")
    try:
        with destination.open("rb") as stream:
            encoded = stream.read(MAX_BACKUP_BYTES + 1)
    except FileNotFoundError:
        document = {"format": 1, "executable": executable, "batches": []}
    else:
        if len(encoded) > MAX_BACKUP_BYTES:
            raise ValueError("tray history backup exceeds its size limit")
        document = json.loads(encoded.decode("utf-8"))
        if (not isinstance(document, dict) or document.get("format") != 1
                or document.get("executable") != executable
                or not isinstance(document.get("batches"), list)):
            raise ValueError("existing tray history backup has an unknown format")
    records = [{"key": entry.name, "children": list(entry.children),
                "values": [{"name": name, "type": kind, "value": _json_value(value)}
                           for name, value, kind in entry.values]} for entry in entries]
    if any(isinstance(batch, dict) and batch.get("records") == records
           for batch in document["batches"]):
        return
    if len(document["batches"]) >= MAX_BACKUP_BATCHES:
        raise ValueError("tray history backup reached its batch limit")
    document["batches"].append({"created_utc": datetime.now(timezone.utc).isoformat(),
                                "registry": "HKEY_CURRENT_USER\\" + REGISTRY_PATH,
                                "records": records})
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_BACKUP_BYTES:
        raise ValueError("tray history backup exceeds its size limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp",
                                             dir=str(destination.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def reconcile(executable, data_dir, prepared=None):
    """Back up and remove only own non-master leaf records after master ADD.

    ``pending`` asks the caller to retry after Explorer creates its master row.
    Missing or unfamiliar schemas and access failures never block tray startup.
    ``prepare`` is optional; without it no visibility preferences are changed.
    """
    executable = _executable(executable)
    registry = _registry()
    if executable is None or registry is None:
        return ReconcileResult("skipped")
    try:
        root = registry.OpenKey(registry.HKEY_CURRENT_USER, REGISTRY_PATH, 0,
                                registry.KEY_READ | registry.KEY_WRITE)
    except FileNotFoundError:
        return ReconcileResult("pending")
    except OSError as error:
        return ReconcileResult("error", error=str(error))
    removed = skipped = 0
    migrated = False
    try:
        with root:
            entries = _own_entries(registry, root, executable)
            masters = [entry for entry in entries if entry.get("UID")[0] == MASTER_UID]
            if not masters:
                return ReconcileResult("pending")
            if len(masters) != 1:
                return ReconcileResult("skipped")
            master = masters[0]
            legacy = tuple(entry for entry in entries if entry.get("UID")[0] != MASTER_UID)
            inherit = (isinstance(prepared, PreparedMigration)
                       and prepared.executable == executable and prepared.master_absent
                       and prepared.promotion is not None and prepared.legacy == legacy
                       and master.get("IsPromoted") is None)
            if not legacy and not inherit:
                return ReconcileResult("done")
            _backup(executable, data_dir, legacy + ((master,) if inherit else ()))
            if inherit:
                # User/Explorer changes since ADD take priority over migration.
                if _read_entry(registry, root, master.name) == master:
                    with registry.OpenKey(root, master.name, 0, registry.KEY_SET_VALUE) as key:
                        registry.SetValueEx(key, "IsPromoted", 0, registry.REG_DWORD,
                                            prepared.promotion)
                    migrated = True
            for entry in legacy:
                try:
                    current_master = _read_entry(registry, root, master.name)
                    if (current_master.get("UID") != master.get("UID")
                            or current_master.get("ExecutablePath") != master.get("ExecutablePath")
                            or current_master.children
                            or any("guid" in name.casefold()
                                   for name, _, _ in current_master.values)):
                        skipped += 1
                        continue
                    if _read_entry(registry, root, entry.name) != entry:
                        skipped += 1
                        continue
                    # DeleteKey refuses newly added children; never recurse.
                    registry.DeleteKey(root, entry.name)
                    removed += 1
                except OSError:
                    skipped += 1
    except (OSError, ValueError, TypeError) as error:
        return ReconcileResult("error", removed, skipped, migrated, str(error))
    return ReconcileResult("done", removed, skipped, migrated)
