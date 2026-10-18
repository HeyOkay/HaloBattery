"""Updates: is there a newer Halo Battery release on GitHub, and installing it.

Once a day the app asks GitHub for the latest release (the public REST API, no token,
one small request); "Check for updates now" in the menu asks at once. When a newer
release exists, the tray shows a notification once for that version and the menu gets
an "Update to vX.Y.Z" item. The automatic check can be turned off in Preferences
("Check for updates").

Installing (the built app only; from source the item opens the release page):

1. download() fetches the release zip (HaloBattery-<ver>.zip, the HaloBattery folder that
   the GitHub workflow builds) into a staging folder in %TEMP%, checks its size and the
   SHA-256 that GitHub lists for the asset, and unpacks it next to the download.
2. The running app copies its OWN program files into the staging folder (the "runner"),
   starts that copy with --finish-update and exits. The installing is done by the version
   that is already installed and known to work, never by the download, so a release with
   a broken installer cannot break updating. No script, no PowerShell, no admin rights.
3. finish_update(), in the runner, waits for the old process to end, moves the old
   HaloBattery.exe and _internal folder aside (*.old), copies the new ones in, and starts
   the app from its usual folder. If anything fails on the way, the old files are put
   back and the old app is started again.
4. The updated app removes the *.old leftovers and the staging folder (cleanup()) and
   shows the "What's new" window (whatsnew.py).

Only the program files are replaced; settings, the log, the battery history and
portable.txt (portable mode keeps them in the app folder) are never touched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
TIMEOUT = 10
DOWNLOAD_TIMEOUT = 30            # per read of the zip, not the whole download
CHUNK = 64 * 1024

EXE_NAME = "HaloBattery.exe"
INTERNAL = "_internal"           # PyInstaller's folder with Python and the libraries
OLD_SUFFIX = ".old"
STAGING_NAME = "HaloBattery-update"
FINISH_FLAG = "--finish-update"
# in the app folder, never copied over or removed: the user's own files
KEEP = {"portable.txt", "config.json", "config.json.bad", "history.json", "status.json",
        "halo_battery.log", "halo_battery.log.1", "diagnostics.txt", "notification_icon.png"}
WAIT_EXIT_S = 30                 # how long the staged copy waits for the old app to end
RETRY_S = 10                     # antivirus scanners can hold a file for a moment


@dataclass
class Release:
    version: str                 # "1.15.0", without the "v"
    url: str                     # the release page
    notes: str = ""              # the release text (the CHANGELOG section, Markdown)
    zip_url: str = ""            # HaloBattery-<ver>.zip, "" when the release has none
    zip_size: int = 0
    sha256: str = ""             # from GitHub's asset digest, "" when it lists none
    published: str = ""          # "2026-10-05"


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


def parse_release(data: dict) -> Release:
    """A release from GitHub's REST API -> Release. Raises ValueError on a tag that is
    not a version."""
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) is None:
        raise ValueError(f"unexpected tag name {tag!r}")
    version = tag.lstrip("vV").strip()
    rel = Release(version=version, url=str(data.get("html_url") or RELEASES_URL),
                  notes=str(data.get("body") or ""),
                  published=str(data.get("published_at") or "")[:10])
    want = f"halobattery-{version}.zip"
    for asset in data.get("assets") or []:
        if str(asset.get("name") or "").lower() != want:
            continue
        rel.zip_url = str(asset.get("browser_download_url") or "")
        try:
            rel.zip_size = int(asset.get("size") or 0)
        except (TypeError, ValueError):
            rel.zip_size = 0
        digest = str(asset.get("digest") or "")
        if digest.lower().startswith("sha256:"):
            rel.sha256 = digest.split(":", 1)[1].strip().lower()
        break
    return rel


def _get_json(url: str, current: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"HaloBattery/{current}",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


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
             opener: Callable = urllib.request.urlopen) -> str:
    """Download the release zip into dest_dir (emptied first), check it and unpack it.
    -> the unpacked app folder (the one with HaloBattery.exe). progress(percent) is
    called as it goes. Raises on any problem."""
    if not rel.zip_url:
        raise ValueError(f"release v{rel.version} has no HaloBattery-{rel.version}.zip")
    _remove(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    zip_path = os.path.join(dest_dir, f"HaloBattery-{rel.version}.zip")
    sha = hashlib.sha256()
    done = 0
    last = -1
    req = urllib.request.Request(rel.zip_url, headers={"User-Agent": f"HaloBattery/{current}"})
    with opener(req, timeout=DOWNLOAD_TIMEOUT) as resp, open(zip_path, "wb") as f:
        total = rel.zip_size or int(resp.headers.get("Content-Length") or 0)
        while True:
            chunk = resp.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
            sha.update(chunk)
            done += len(chunk)
            if progress is not None and total:
                pct = min(99, done * 100 // total)
                if pct != last:
                    last = pct
                    progress(pct)
    if rel.zip_size and done != rel.zip_size:
        raise ValueError(f"download incomplete: {done} of {rel.zip_size} bytes")
    if rel.sha256 and sha.hexdigest() != rel.sha256:
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


def finish_command(runner: str, new_app: str, app_dir: str, pid: int) -> List[str]:
    """The command the running app starts before it exits."""
    return [os.path.join(runner, EXE_NAME), FINISH_FLAG, "--app-dir", app_dir,
            "--new", new_app, "--pid", str(pid)]


def start_detached(cmd: List[str], cwd: Optional[str] = None) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(cmd, cwd=cwd, creationflags=flags, close_fds=True)


def parse_finish_args(argv: List[str]) -> Optional[Tuple[str, str, int]]:
    """argv with --finish-update -> (app folder, new files, old pid); None without it."""
    if FINISH_FLAG not in argv:
        return None
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
    return app_dir, new_app, pid


def wait_for_exit(pid: int, timeout: float = WAIT_EXIT_S) -> None:
    """Wait for the old app to end; after the timeout it is ended."""
    if not pid or sys.platform != "win32":
        return
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    k.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k.OpenProcess(0x00100000 | 0x0001, 0, pid)        # SYNCHRONIZE | PROCESS_TERMINATE
    if not h:
        return                                             # already gone
    try:
        if k.WaitForSingleObject(h, int(timeout * 1000)) != 0:
            log.warning("update: the old app (pid %d) did not exit, ending it", pid)
            k.TerminateProcess(h, 1)
            k.WaitForSingleObject(h, 5000)
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


def swap_files(new_app: str, app_dir: str, sleep=time.sleep) -> None:
    """Replace the program files in app_dir with the ones in new_app. The old ones are
    moved aside as *.old first; on any error they are moved back and the error raised."""
    entries = [e for e in os.listdir(new_app) if e.lower() not in KEEP]
    moved: List[Tuple[str, str]] = []
    copied: List[str] = []
    try:
        for e in entries:
            dst = os.path.join(app_dir, e)
            if os.path.exists(dst):
                old = dst + OLD_SUFFIX
                _remove(old)
                _retry(os.replace, dst, old, sleep=sleep)
                moved.append((dst, old))
        for e in entries:
            src, dst = os.path.join(new_app, e), os.path.join(app_dir, e)
            copied.append(dst)
            if os.path.isdir(src):
                _retry(shutil.copytree, src, dst, sleep=sleep)
            else:
                _retry(shutil.copy2, src, dst, sleep=sleep)
    except Exception:
        for dst in copied:
            _remove(dst)
        for dst, old in reversed(moved):
            try:
                os.replace(old, dst)
            except OSError as e:
                log.error("update: could not restore %s: %s", dst, e)
        raise


def finish_update(app_dir: str, pid: int, new_app: str,
                  start: Callable[[List[str]], None] = None, sleep=time.sleep,
                  wait: Callable[[int], None] = wait_for_exit) -> bool:
    """Run by the runner: swap the files and start the app from app_dir.
    -> True when the new version was installed."""
    start = start or (lambda cmd: start_detached(cmd, cwd=app_dir))
    exe = os.path.join(app_dir, EXE_NAME)
    wait(pid)
    try:
        swap_files(new_app, app_dir, sleep=sleep)
        ok = True
        log.info("update: installed into %s", app_dir)
    except Exception as e:
        ok = False
        log.error("update: could not replace the files in %s: %s", app_dir, e)
    try:
        start([exe])
    except OSError as e:
        log.error("update: could not start %s: %s", exe, e)
    return ok


def cleanup(app_dir: str, staging: Optional[str] = None) -> None:
    """After an update: remove the *.old program files and the staging folder."""
    try:
        names = os.listdir(app_dir)
    except OSError:
        names = []
    for n in names:
        if n.endswith(OLD_SUFFIX):
            _remove(os.path.join(app_dir, n))
    if staging and os.path.isdir(staging):
        _remove(staging)
