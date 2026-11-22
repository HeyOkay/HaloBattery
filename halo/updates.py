"""Updates: is there a newer Halo Battery release on GitHub, and installing it.

Once a day the app asks GitHub for the latest release (the public REST API, no token,
one small request); "Check for updates now" in the menu asks at once. When a newer
release exists, the tray shows a notification once for that version and the menu gets
an "Update to vX.Y.Z" item. The automatic check can be turned off in Preferences
("Check for updates").

Installing (the built app only; from source the item opens the release page):

1. download() fetches the release zip (HaloBattery-<ver>.zip, the HaloBattery folder that
   the GitHub workflow builds) into a staging folder in %TEMP%, checks its size and the
   SHA-256 that GitHub lists for the asset (or the release's SHA256SUMS.txt), and
   unpacks it next to the download. A release without a checksum is refused.
2. The running app copies its OWN program files into the staging folder (the "runner"),
   starts that copy with --finish-update and exits. The installing is done by the version
   that is already installed and known to work, never by the download, so a release with
   a broken installer cannot break updating. No script, no PowerShell, no admin rights.
3. finish_update(), in the runner, waits for the old process to end, moves the old
   HaloBattery.exe and _internal folder aside (*.old), copies the new ones in, and starts
   the app from its usual folder. If anything fails on the way, the old files are put
   back and the old app is started again.
4. A durable transaction record protects the old files before replacement. Startup
   cleanup removes only that transaction's backups after a completed swap or complete
   rollback is verified. Interrupted/failed recovery keeps its backups and staging.

Only the program files are replaced; settings, the log, the battery history and
portable.txt (portable mode keeps them in the app folder) are never touched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import ntpath
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

log = logging.getLogger("halo_battery")

REPO = "HeyOkay/HaloBattery"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
TAG_URL = f"https://api.github.com/repos/{REPO}/releases/tags/v{{version}}"
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"
CHECK_EVERY = 24 * 3600          # seconds between checks
# settings that remember what the last check found; they belong to one repository
CACHE_KEYS = ("update_latest", "update_url", "update_notified", "update_last")


def invalidate_cache_for_repo(cfg: dict, repo: Optional[str] = None) -> bool:
    """Drop the remembered update offer when it came from another repository.

    The cache is tagged with the repository it was fetched from ("update_repo").
    A settings file written by a build that checked a different repository (a
    community fork, before returning upstream) would otherwise show that repository's
    offer and link until the next check. Returns True when `cfg` was changed."""
    repo = repo or REPO
    if cfg.get("update_repo") == repo:
        return False
    for key in CACHE_KEYS:
        cfg.pop(key, None)
    cfg["update_repo"] = repo
    return True
TIMEOUT = 10
DOWNLOAD_TIMEOUT = 30            # per read of the zip, not the whole download
CHUNK = 64 * 1024
CHECKSUMS_MAX = 64 * 1024         # the release manifest normally has just two lines

EXE_NAME = "HaloBattery.exe"
INTERNAL = "_internal"           # PyInstaller's folder with Python and the libraries
OLD_SUFFIX = ".old"
STAGING_NAME = "HaloBattery-update"
FINISH_FLAG = "--finish-update"
UPDATE_STATE = ".halobattery-update.json"
STAGE_STATE = ".halobattery-stage.json"
# The updater of 1.17.0 and earlier moved exactly these aside, wrote no record and
# left their removal to the next start of the new copy.
LEGACY_BACKUPS = (EXE_NAME + OLD_SUFFIX, INTERNAL + OLD_SUFFIX)
# in the app folder, never copied over or removed: the user's own files
KEEP = {"portable.txt", "config.json", "config.json.bad", "history.json", "status.json",
        "halo_battery.log", "halo_battery.log.1", "diagnostics.txt", "notification_icon.png",
        "iconify", "settings-window.json", UPDATE_STATE, STAGE_STATE}
WAIT_EXIT_S = 30                 # how long the staged copy waits for the old app to end
RETRY_S = 10                     # antivirus scanners can hold a file for a moment


@dataclass
class Release:
    version: str                 # "1.15.0", without the "v"
    url: str                     # the release page
    notes: str = ""              # the release text (the CHANGELOG section, Markdown)
    zip_url: str = ""            # HaloBattery-<ver>.zip, "" when the release has none
    zip_size: int = 0
    sha256: str = ""             # GitHub asset digest; otherwise use checksums_url
    published: str = ""          # "2026-10-05"
    zip_name: str = ""           # exact asset name for SHA256SUMS.txt matching
    checksums_url: str = ""      # SHA256SUMS.txt from this same release


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """'v1.11.0' / '1.11.0' -> (1, 11, 0); anything else -> None."""
    if not isinstance(text, str):          # a hand-edited settings file can hold any JSON
        return None
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)\s*$", text)
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def parse_release(data: dict) -> Release:
    """A release from GitHub's REST API -> Release. Raises ValueError on a tag that is
    not a version."""
    if not isinstance(data, dict):
        raise ValueError(f"unexpected release data {type(data).__name__}")
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        raise ValueError(f"unexpected tag name {tag!r}")
    version = tag.strip().lstrip("vV")
    rel = Release(version=version, url=str(data.get("html_url") or RELEASES_URL),
                  notes=str(data.get("body") or ""),
                  published=str(data.get("published_at") or "")[:10])
    want = f"halobattery-{version}.zip"
    assets = data.get("assets")
    assets = [a for a in assets if isinstance(a, dict)] if isinstance(assets, list) else []
    for asset in assets:
        if str(asset.get("name") or "").lower() != want:
            continue
        rel.zip_name = asset["name"]
        rel.zip_url = str(asset.get("browser_download_url") or "")
        try:
            rel.zip_size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            rel.zip_size = 0
        digest = asset.get("digest")
        if digest:
            match = re.fullmatch(r"sha256:([0-9a-f]{64})", str(digest), re.IGNORECASE)
            if not match:
                raise ValueError("invalid SHA-256 digest for the release zip")
            rel.sha256 = match[1].lower()
        break
    manifests = [a for a in assets if a.get("name") == "SHA256SUMS.txt"]
    if len(manifests) == 1:
        rel.checksums_url = str(manifests[0].get("browser_download_url") or "")
    return rel


def _require_https(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("update downloads require HTTPS")


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Refuse a downgrade before urllib follows it, including intermediate hops.
        _require_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_https(req: urllib.request.Request, timeout: int):
    _require_https(req.full_url)
    return urllib.request.build_opener(_HTTPSRedirectHandler()).open(req, timeout=timeout)


def _check_response_https(resp) -> None:
    # Injectable openers used by tests may not expose a final URL.
    if callable(getattr(resp, "geturl", None)):
        _require_https(resp.geturl())


def _get_json(url: str, current: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"HaloBattery/{current}",
    })
    with _open_https(req, timeout=TIMEOUT) as resp:
        _check_response_https(resp)
        return json.loads(resp.read().decode("utf-8"))


def _expected_sha256(rel: Release, current: str, opener: Callable) -> str:
    if rel.sha256:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", rel.sha256):
            raise ValueError("invalid SHA-256 digest for the release zip")
        return rel.sha256.lower()
    if not rel.checksums_url:
        raise ValueError("release zip has no SHA-256 checksum; download it from the release page")
    _require_https(rel.checksums_url)
    req = urllib.request.Request(rel.checksums_url,
                                 headers={"User-Agent": f"HaloBattery/{current}"})
    with opener(req, timeout=DOWNLOAD_TIMEOUT) as resp:
        _check_response_https(resp)
        raw = resp.read(CHECKSUMS_MAX + 1)
    if len(raw) > CHECKSUMS_MAX:
        raise ValueError("release SHA-256 manifest is too large")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as e:
        raise ValueError("invalid release SHA-256 manifest") from e
    want = rel.zip_name or f"HaloBattery-{rel.version}.zip"
    matches = []
    for line in text.splitlines():
        if not line:
            continue
        # The workflows publish sha256sum's binary format; text mode is valid too.
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        if not match:
            raise ValueError("invalid release SHA-256 manifest")
        if match[2] == want:
            matches.append(match[1].lower())
    if len(matches) != 1:
        raise ValueError("release SHA-256 manifest must name the exact zip asset once")
    return matches[0]


def fetch_latest(current: str) -> Release:
    """The latest release. Raises on network or API errors."""
    return parse_release(_get_json(API_URL, current))


def fetch_release(version: str, current: str) -> Release:
    """The release of one version (its notes for the "What's new" window)."""
    return parse_release(_get_json(TAG_URL.format(version=version), current))


# ------------------------------------------------------------------ installing
def can_install(frozen: bool, app_dir: str) -> Tuple[bool, str]:
    """(True, "") when this copy can update itself, else (False, why). Only the built
    app (HaloBattery.exe next to its _internal folder) in a folder it can write to."""
    if not frozen:
        return False, "running from source"
    if not os.path.isfile(os.path.join(app_dir, EXE_NAME)):
        return False, f"{EXE_NAME} not found in {app_dir}"
    # Do not download over the recovery staging folder, or replace the only
    # previous copy, while startup cleanup/recovery still owns a transaction.
    if os.path.lexists(os.path.join(app_dir, UPDATE_STATE)):
        return False, "previous update cleanup or recovery is pending"
    # swap_files() refuses while a backup name is taken; say so instead of offering
    # an update that exits, fails and restarts the app.
    if any(os.path.lexists(os.path.join(app_dir, name)) for name in LEGACY_BACKUPS):
        return False, "a previous update's backup could not be removed; run setup to repair"
    # On Windows tempfile retries PermissionError when os.access says the directory
    # is writable, even if its ACL denies creating/deleting files (Program Files).
    # One exclusive open gives the real answer without blocking application startup.
    probe = os.path.join(app_dir, f".halobattery-write-{uuid.uuid4().hex}.tmp")
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_TEMPORARY", 0)
    try:
        fd = os.open(probe, flags, 0o600)
        os.close(fd)
        if os.name != "nt":
            os.unlink(probe)
    except OSError:
        return False, f"{app_dir} cannot be written"
    return True, ""


def staging_dir() -> str:
    return os.path.join(tempfile.gettempdir(), STAGING_NAME)


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def download(rel: Release, dest_dir: str, current: str,
             progress: Optional[Callable[[int], None]] = None,
             opener: Optional[Callable] = None) -> str:
    """Download the release zip into dest_dir (emptied first), check it and unpack it.
    -> the unpacked app folder (the one with HaloBattery.exe). progress(percent) is
    called as it goes. Raises on any problem."""
    if not rel.zip_url:
        raise ValueError(f"release v{rel.version} has no HaloBattery-{rel.version}.zip")
    if parse_version(rel.version) is None:
        raise ValueError("invalid release version")
    _require_https(rel.zip_url)
    opener = opener or _open_https
    expected_sha256 = _expected_sha256(rel, current, opener)
    if not isinstance(rel.zip_size, int) or isinstance(rel.zip_size, bool) or rel.zip_size < 0:
        raise ValueError("invalid release zip size")
    _remove(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, f"HaloBattery-{rel.version}.zip")
    sha = hashlib.sha256()
    done = 0
    last = -1
    req = urllib.request.Request(rel.zip_url, headers={"User-Agent": f"HaloBattery/{current}"})
    with opener(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(zip_path, "wb") as f:
        _check_response_https(resp)
        response_size = int(resp.headers.get("Content-Length") or 0)
        if response_size < 0:
            raise ValueError("invalid download size")
        if rel.zip_size and response_size and rel.zip_size != response_size:
            raise ValueError(f"download incomplete: expected {rel.zip_size} bytes, got {response_size}")
        total = rel.zip_size or response_size
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if total and done > total:
                raise ValueError(f"download size exceeds {total} bytes")
            if progress is not None and total:
                pct = min(99, done * 100 // total)
                if pct != last:
                    last = pct
                    progress(pct)
    if total and done != total:
        raise ValueError(f"download incomplete: {done} of {total} bytes")
    if sha.hexdigest() != expected_sha256:
        raise ValueError("download damaged: SHA-256 does not match the release")
    app = unpack(zip_path, os.path.join(dest_dir, "new"))
    _remove(zip_path)
    if progress is not None:
        progress(100)
    return app


def unpack(zip_path: str, dest: str) -> str:
    """Unpack the release zip -> the folder that holds HaloBattery.exe. The zip from the
    workflow has a HaloBattery folder at the top; a zip without it works too."""
    _remove(dest)
    os.makedirs(dest, exist_ok=True)
    root_real = os.path.realpath(dest)
    with zipfile.ZipFile(zip_path) as z:
        names = [n.replace("\\", "/") for n in z.namelist()]
        exe = [n for n in names if n.rsplit("/", 1)[-1].lower() == EXE_NAME.lower()]
        if not exe:
            raise ValueError(f"{EXE_NAME} not found in the download")
        prefix = min(exe, key=len)[:-len(EXE_NAME)]           # "HaloBattery/" or ""
        for info, name in zip(z.infolist(), names):
            if not name.startswith(prefix) or name == prefix:
                continue
            rel = name[len(prefix):]
            target = os.path.realpath(os.path.join(dest, *rel.split("/")))
            if not (target == root_real or target.startswith(root_real + os.sep)):
                raise ValueError(f"unsafe path in the download: {name}")
            if name.endswith("/"):
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
    if not os.path.isfile(os.path.join(dest, EXE_NAME)):
        raise ValueError(f"{EXE_NAME} not found in the download")
    return dest


def prepare_runner(app_dir: str, staging: str) -> str:
    """Copy the running app's program files into staging/runner -> that folder."""
    runner = os.path.join(staging, "runner")
    _remove(runner)
    os.makedirs(runner)
    for e in os.listdir(app_dir):
        if e.lower() in KEEP or e.endswith(OLD_SUFFIX):
            continue
        src = os.path.join(app_dir, e)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(runner, e))
        else:
            shutil.copy2(src, os.path.join(runner, e))
    return runner


def finish_command(runner: str, new_app: str, app_dir: str, pid: int,
                   expected_creation_time: Optional[int] = None) -> List[str]:
    """The command the running app starts before it exits."""
    _validate_creation_time(expected_creation_time)
    cmd = [os.path.join(runner, EXE_NAME), FINISH_FLAG, "--app-dir", app_dir,
           "--new", new_app, "--pid", str(pid)]
    if expected_creation_time is not None:
        if not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= 0xFFFFFFFF:
            raise ValueError("update: process creation time needs a valid pid")
        cmd[-2:-2] = ["--pid-created", str(expected_creation_time)]
    return cmd


def start_detached(cmd: List[str], cwd: Optional[str] = None) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=cwd, creationflags=flags, close_fds=True)


def _validate_creation_time(value: Optional[int]) -> None:
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)
                              or not 0 < value <= 0xFFFFFFFFFFFFFFFF):
        raise ValueError("update: invalid process creation time")


def parse_finish_creation_time(argv: List[str]) -> Optional[int]:
    """Read optional birth metadata without changing the legacy three-value parser."""
    if FINISH_FLAG not in argv:
        return None
    value = None
    for i, arg in enumerate(argv):
        if arg.startswith("--pid-created="):
            raise ValueError("update: use --pid-created followed by a FILETIME")
        if arg != "--pid-created":
            continue
        if value is not None or i + 1 >= len(argv):
            raise ValueError("update: missing or duplicate process creation time")
        raw = argv[i + 1]
        if not re.fullmatch(r"[0-9]{1,20}", raw):
            raise ValueError("update: invalid process creation time")
        value = int(raw)
        _validate_creation_time(value)
    return value


def _process_creation_time(kernel, handle) -> int:
    """Query one held process handle; FILETIME consists of two 32-bit DWORDs.

    https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes
    """
    import ctypes
    class FileTime(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]
    kernel.GetProcessTimes.restype = ctypes.c_int
    kernel.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FileTime)] * 4
    created, exited, system, user = (FileTime() for _ in range(4))
    if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                  ctypes.byref(system), ctypes.byref(user)):
        raise OSError(ctypes.get_last_error(), "update: could not verify process creation time")
    value = (created.high << 32) | created.low
    if not value:
        raise OSError("update: process creation time is empty")
    return value


def current_process_creation_time() -> int:
    """Capture the updater parent's own identity before starting its copied helper."""
    import ctypes
    if sys.platform != "win32":
        raise OSError("update: process creation time requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetCurrentProcess.argtypes = []
    # This is a pseudo-handle, not an owned handle; do not CloseHandle it.
    return _process_creation_time(kernel, kernel.GetCurrentProcess())


def parse_finish_args(argv: List[str]) -> Optional[Tuple[str, str, int]]:
    """argv with --finish-update -> (app folder, new files, old pid); None without it."""
    if FINISH_FLAG not in argv:
        return None
    creation_time = parse_finish_creation_time(argv)
    app_dir, new_app, pid = "", "", 0
    for i, a in enumerate(argv):
        if i + 1 >= len(argv):
            break
        if a == "--app-dir":
            app_dir = argv[i + 1]
        elif a == "--new":
            new_app = argv[i + 1]
        elif a == "--pid":
            try:
                pid = int(argv[i + 1])
            except ValueError:
                pid = 0
    if not app_dir or not new_app:
        raise ValueError("--finish-update needs --app-dir and --new")
    if creation_time is not None and not 0 < pid <= 0xFFFFFFFF:
        raise ValueError("update: process creation time needs a valid pid")
    return app_dir, new_app, pid


def wait_for_exit(pid: int, timeout: float = WAIT_EXIT_S,
                  expected_exe: Optional[str] = None,
                  expected_creation_time: Optional[int] = None) -> None:
    """Confirm the old app ended, or raise before its files can be replaced."""
    _validate_creation_time(expected_creation_time)
    if expected_creation_time is not None and (
            not isinstance(pid, int) or isinstance(pid, bool) or not 0 < pid <= 0xFFFFFFFF):
        raise ValueError("update: process creation time needs a valid pid")
    if sys.platform != "win32" and expected_creation_time is not None:
        raise OSError("update: process creation time requires Windows")
    if not pid or sys.platform != "win32":
        return
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k.WaitForSingleObject.restype = ctypes.c_uint32
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.TerminateProcess.restype = ctypes.c_int
    k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    access = 0x00100000 | 0x0001                         # SYNCHRONIZE | PROCESS_TERMINATE
    if expected_exe is not None or expected_creation_time is not None:
        access |= 0x1000                                # PROCESS_QUERY_LIMITED_INFORMATION
    h = k.OpenProcess(access, 0, pid)
    if not h:
        error = ctypes.get_last_error()
        if error == 87:                                 # ERROR_INVALID_PARAMETER: pid gone
            return
        raise OSError(error, "update: could not open the old app to confirm its exit")
    try:
        if expected_creation_time is not None:
            if _process_creation_time(k, h) != expected_creation_time:
                raise OSError("update: the pid belongs to a different process instance")
        result = k.WaitForSingleObject(h, int(timeout * 1000))
        if result == 0:                                 # WAIT_OBJECT_0
            return
        if result != 258:                               # WAIT_TIMEOUT
            raise OSError(ctypes.get_last_error(), "update: could not confirm the old app exited")
        if expected_exe is not None:
            # The PID may have been reused; check the image on this same handle
            # before terminating anything that still runs.
            k.QueryFullProcessImageNameW.restype = ctypes.c_int
            k.QueryFullProcessImageNameW.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_wchar),
                ctypes.POINTER(ctypes.c_uint32)]
            image = ctypes.create_unicode_buffer(32768)
            length = ctypes.c_uint32(len(image))
            if not k.QueryFullProcessImageNameW(h, 0, image, ctypes.byref(length)):
                raise OSError(ctypes.get_last_error(), "update: could not verify the old app's executable")
            actual = ntpath.normcase(ntpath.abspath(image.value))
            expected = ntpath.normcase(ntpath.abspath(expected_exe))
            if not length.value or length.value >= len(image) or actual != expected:
                raise OSError("update: the process belongs to a different executable")
        log.warning("update: the old app (pid %d) did not exit, ending it", pid)
        ended = k.TerminateProcess(h, 1)
        error = 0 if ended else ctypes.get_last_error()
        # Even a refused termination can race with a natural exit. Only a signalled
        # process handle proves it is safe to swap, never TerminateProcess alone.
        result = k.WaitForSingleObject(h, 5000)
        if result != 0:
            if result != 258:
                error = ctypes.get_last_error()
            raise OSError(error or 1460, "update: the old app's exit was not confirmed")
    finally:
        k.CloseHandle(h)


def _retry(fn, *args, seconds: Optional[float] = None, sleep=time.sleep):
    end = time.monotonic() + (RETRY_S if seconds is None else seconds)
    while True:
        try:
            return fn(*args)
        except OSError:
            if time.monotonic() >= end:
                raise
            sleep(0.5)


def swap_files(new_app: str, app_dir: str, sleep=time.sleep,
               start: Optional[Callable[[], None]] = None) -> None:
    """Replace the program files in app_dir with the ones in new_app. The old ones are
    moved aside as *.old first; on any error they are moved back and the error raised.
    When supplied, start launches the new app before committing the swap, so a launch
    refusal restores the old files too."""
    if os.path.lexists(os.path.join(app_dir, UPDATE_STATE)):
        raise OSError("previous update cleanup or recovery is pending")
    entries = [e for e in os.listdir(new_app) if e.lower() not in KEEP]
    if not _update_names(entries) or EXE_NAME not in entries:
        raise ValueError("invalid update program files")
    backups = [e for e in entries if os.path.lexists(os.path.join(app_dir, e))]
    if any(os.path.lexists(os.path.join(app_dir, e + OLD_SUFFIX)) for e in entries):
        raise OSError("previous update backup must be recovered before replacing files")
    previous_hash = (_exe_hash(app_dir) if EXE_NAME in backups else None)
    new_path = os.path.abspath(new_app)
    parent = os.path.dirname(new_path)
    staging = ([parent, new_path] if os.path.basename(parent).casefold() == STAGING_NAME.casefold()
               else [new_path])
    state = dict(schema=1, state="pending", backups=backups, files=entries,
                 exe_sha256=_exe_hash(new_app), staging=staging, stage_token=uuid.uuid4().hex)
    # Cleanup needs independent evidence in the staged tree, not merely a path
    # read from the app's editable recovery record or settings.
    stage_marker = os.path.join(staging[0], STAGE_STATE)
    try:
        with open(stage_marker, "x", encoding="utf-8") as marker:
            json.dump(dict(schema=1, owner=os.path.normcase(os.path.realpath(app_dir)),
                           root=os.path.normcase(os.path.abspath(staging[0])),
                           token=state["stage_token"], files=entries, backups=backups,
                           installed_exe_sha256=state["exe_sha256"],
                           rollback_exe_sha256=previous_hash), marker)
            marker.flush()
            os.fsync(marker.fileno())
    except FileExistsError:
        raise OSError("previous staging ownership must be resolved before replacing files") from None
    except BaseException:
        _remove(stage_marker)
        raise
    # This must be durable BEFORE the first rename. An interrupted runner leaves
    # pending evidence, never an apparently successful transaction to clean up.
    try:
        _write_update_state(app_dir, state, sleep=sleep)
    except BaseException:
        _remove(stage_marker)
        raise
    moved: List[Tuple[str, str]] = []
    copied: List[str] = []
    try:
        for e in entries:
            dst = os.path.join(app_dir, e)
            if os.path.exists(dst):
                old = dst + OLD_SUFFIX
                _retry(os.replace, dst, old, sleep=sleep)
                moved.append((dst, old))
        for e in entries:
            src, dst = os.path.join(new_app, e), os.path.join(app_dir, e)
            copied.append(dst)
            if os.path.isdir(src):
                _retry(shutil.copytree, src, dst, sleep=sleep)
            else:
                _retry(shutil.copy2, src, dst, sleep=sleep)
        if start is not None:
            start()
    except Exception:
        restored = True
        for dst in copied:
            try:
                _remove_checked(dst)
            except OSError as e:
                restored = False
                log.error("update: could not remove partial replacement %s: %s", dst, e)
        for dst, old in reversed(moved):
            try:
                _retry(os.replace, old, dst, sleep=sleep)
            except OSError as e:
                restored = False
                log.error("update: could not restore %s: %s", dst, e)
        state.update(state="rolled_back" if restored and previous_hash else "failed",
                     files=backups, exe_sha256=previous_hash or state["exe_sha256"])
        try:
            _write_update_state(app_dir, state, sleep=sleep)
        except OSError:
            # The prewritten pending record still protects every backup.
            log.exception("update: recovery result could not be saved; backups retained")
        raise
    state["state"] = "installed"
    try:
        _write_update_state(app_dir, state, sleep=sleep)
    except OSError:
        # The new process may already own the replacement. Do not roll it back
        # merely because saving completion failed: pending evidence keeps backups.
        log.exception("update: completion could not be saved; backups retained")


def finish_update(app_dir: str, pid: int, new_app: str,
                  start: Callable[[List[str]], None] = None, sleep=time.sleep,
                  wait: Optional[Callable[[int], None]] = None,
                  expected_creation_time: Optional[int] = None) -> bool:
    """Run by the runner: swap the files and start the app from app_dir.
    -> True when the new version was installed."""
    _validate_creation_time(expected_creation_time)
    start = start or (lambda cmd: start_detached(cmd, cwd=app_dir))
    exe = os.path.join(app_dir, EXE_NAME)
    try:
        if wait is None:
            wait_for_exit(pid, expected_exe=exe, expected_creation_time=expected_creation_time)
        else:
            wait(pid)
    except OSError as e:
        # The old app may still own its files. Do not swap or launch another copy.
        log.error("update: cannot install while the old app's exit is unconfirmed: %s", e)
        return False
    try:
        swap_files(new_app, app_dir, sleep=sleep, start=lambda: start([exe]))
        log.info("update: installed into %s", app_dir)
        return True
    except Exception as e:
        log.error("update: could not replace or start the files in %s: %s", app_dir, e)
    try:
        start([exe])
    except OSError as e:
        log.error("update: could not start %s: %s", exe, e)
    return False


def _exe_hash(folder: str) -> str:
    digest = hashlib.sha256()
    with open(os.path.join(folder, EXE_NAME), "rb") as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _update_names(names) -> bool:
    return (type(names) is list and len(names) <= 256
            and len({n.casefold() for n in names if isinstance(n, str)}) == len(names)
            and all(isinstance(n, str) and n and n not in (".", "..")
                    and not any(c in n for c in ("/", "\\", "\0", ":"))
                    and n.casefold() not in KEEP and not n.casefold().endswith(OLD_SUFFIX)
                    and not n.startswith(UPDATE_STATE + ".") for n in names))


def _paths_overlap(first: str, second: str) -> bool:
    first, second = (os.path.normcase(os.path.realpath(p)) for p in (first, second))
    try:
        return os.path.commonpath((first, second)) in (first, second)
    except ValueError:  # Different Windows volumes cannot contain each other.
        return False


def _write_update_state(app_dir: str, state: dict, *, sleep=time.sleep) -> None:
    payload = json.dumps(state, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 65536:
        raise ValueError("update recovery record is too large")
    descriptor, temporary = tempfile.mkstemp(prefix=UPDATE_STATE + ".", suffix=".tmp", dir=app_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        _retry(os.replace, temporary, os.path.join(app_dir, UPDATE_STATE), sleep=sleep)
    finally:
        _remove(temporary)


def _read_update_state(app_dir: str):
    try:
        with open(os.path.join(app_dir, UPDATE_STATE), encoding="utf-8") as source:
            raw = source.read(65537)
    except FileNotFoundError:
        return None
    if len(raw) > 65536:
        raise ValueError("oversized update recovery record")
    state = json.loads(raw)
    if (type(state) is not dict or set(state) !=
            {"schema", "state", "backups", "files", "exe_sha256", "staging", "stage_token"}
            or type(state["schema"]) is not int or state["schema"] != 1
            or state["state"] not in ("pending", "installed", "rolled_back", "failed")
            or not _update_names(state["backups"]) or not _update_names(state["files"])
            or not set(state["backups"]).issubset(state["files"])
            or (state["state"] in ("installed", "rolled_back") and EXE_NAME not in state["files"])
            or not isinstance(state["exe_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", state["exe_sha256"]) is None
            or not isinstance(state["stage_token"], str)
            or re.fullmatch(r"[0-9a-f]{32}", state["stage_token"]) is None
            or type(state["staging"]) is not list or not 1 <= len(state["staging"]) <= 2
            or not all(isinstance(p, str) and p and "\0" not in p and os.path.isabs(p)
                       and not _paths_overlap(p, app_dir) for p in state["staging"])):
        raise ValueError("invalid update recovery record")
    return state


def _owns_staging(app_dir: str, state: dict) -> bool:
    root = state["staging"][0]
    if not os.path.lexists(root):
        # A prior cleanup removes backups BEFORE staging. Without the receipt,
        # only an already empty transaction can finish removing its app marker.
        return not any(os.path.lexists(os.path.join(app_dir, n + OLD_SUFFIX))
                       for n in state["backups"])
    marker = os.path.join(root, STAGE_STATE)
    if not os.path.isdir(root) or not os.path.lexists(marker):
        return False
    # On Windows junctions are reparse points even when islink() is false.
    # Refuse every reparse component rather than treating its resolved target
    # as an independently owned cleanup tree.
    for path in (marker, *tuple(Path(root).parents), root):
        if os.path.islink(path) or getattr(os.lstat(path), "st_file_attributes", 0) & 0x400:
            return False
    with open(marker, encoding="utf-8") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        return False
    owner = json.loads(raw)
    if (type(owner) is not dict or set(owner) != {
            "schema", "owner", "root", "token", "files", "backups",
            "installed_exe_sha256", "rollback_exe_sha256"}
            or type(owner["schema"]) is not int or owner["schema"] != 1
            or owner["owner"] != os.path.normcase(os.path.realpath(app_dir))
            or owner["root"] != os.path.normcase(os.path.abspath(root))
            or owner["token"] != state["stage_token"]
            or not _update_names(owner["files"]) or not _update_names(owner["backups"])
            or not set(owner["backups"]).issubset(owner["files"])
            or owner["backups"] != state["backups"]):
        return False
    expected_files = owner["files"] if state["state"] == "installed" else owner["backups"]
    expected_hash = owner[("installed" if state["state"] == "installed" else "rollback") + "_exe_sha256"]
    return state["files"] == expected_files and state["exe_sha256"] == expected_hash


def _remove_checked(path: str) -> None:
    _remove(path)
    if os.path.lexists(path):
        raise OSError("update recovery file is still in use: " + path)


def _clear_owned_staging(root: str) -> None:
    if not os.path.lexists(root):
        return
    # Keep the independent receipt while any downloaded/runner file remains.
    # A locked child must not turn a safely retryable cleanup into an unowned
    # directory. Do not use one rmtree(root), whose traversal can delete the
    # receipt first and then fail on a different child.
    for name in os.listdir(root):
        if name != STAGE_STATE:
            _remove_checked(os.path.join(root, name))
    _remove_checked(os.path.join(root, STAGE_STATE))
    try:
        os.rmdir(root)
    except OSError:
        # A new/locked entry may race the final empty-directory removal. Leave
        # that remainder untouched; program backups were already verified and
        # cleaned. Do not recursively delete an ownerless directory.
        log.info("update: completed staging cleanup left a directory for manual inspection")


def _clear_legacy_backups(app_dir: str) -> bool:
    """Remove the backups of a pre-recovery-record update; False if one remains.

    Only the two names that updater created, only while this copy's own program
    files are present (this start is the one that updater handed over to), and
    never through a link or junction. Every other *.old name stays.
    """
    if not (os.path.isfile(os.path.join(app_dir, EXE_NAME))
            and os.path.isdir(os.path.join(app_dir, INTERNAL))):
        return not any(os.path.lexists(os.path.join(app_dir, n)) for n in LEGACY_BACKUPS)
    for name in LEGACY_BACKUPS:
        path = os.path.join(app_dir, name)
        if not os.path.lexists(path):
            continue
        if os.path.islink(path) or getattr(os.lstat(path), "st_file_attributes", 0) & 0x400:
            log.warning("update: %s is a link; left in place", name)
            return False
        _remove_checked(path)
        log.info("update: removed %s left by the previous updater", name)
    return True


def cleanup(app_dir: str, staging: Optional[str] = None) -> bool:
    """Delete only verified terminal transaction backups; False retains recovery.

    Missing/old-format evidence never authorizes deleting arbitrary *.old files;
    without a record only the two legacy backup names are removed (see
    _clear_legacy_backups), so an update from 1.17.0 or earlier does not block the next.
    Interrupted/malformed/failed records preserve both backups and staging. The
    marker is removed last, making partial cleanup safe to retry after a crash.
    """
    try:
        state = _read_update_state(app_dir)
        if state is None:
            # No owned transaction: legacy staging stays; legacy backups would block
            # every later swap_files(), so remove exactly those.
            return _clear_legacy_backups(app_dir)
        if state["state"] not in ("installed", "rolled_back"):
            return False
        if (not all(os.path.exists(os.path.join(app_dir, name)) for name in state["files"])
                or _exe_hash(app_dir) != state["exe_sha256"]):
            return False
        allowed = {os.path.normcase(os.path.realpath(p)) for p in state["staging"]}
        if staging is not None and (not isinstance(staging, str) or
                os.path.normcase(os.path.realpath(staging)) not in allowed):
            return False
        if not _owns_staging(app_dir, state):
            if any(os.path.lexists(os.path.join(app_dir, name + OLD_SUFFIX))
                   for name in state["backups"]):
                return False
            # Cleanup may have crashed after removing its receipt and backups.
            # Finalize only the app record; no unowned staging path is removed.
            _remove_checked(os.path.join(app_dir, UPDATE_STATE))
            return True
        for name in state["backups"]:
            _remove_checked(os.path.join(app_dir, name + OLD_SUFFIX))
        # Use the persisted path when a prior startup already consumed config,
        # but never accept an unrelated directory from hand-edited settings.
        _clear_owned_staging(state["staging"][0])
        _remove_checked(os.path.join(app_dir, UPDATE_STATE))
        return True
    except (OSError, TypeError, ValueError, RecursionError):
        log.warning("update: recovery evidence could not be verified; backups retained", exc_info=True)
        return False
