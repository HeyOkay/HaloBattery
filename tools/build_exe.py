"""Freeze Python, then include the independently published settings companion.

The native app is already self-contained. Passing its PE files into Python's
Analysis makes PyInstaller analyze them again and copy extra DLLs into the
Python runtime directory. Add the complete publish tree as DATA after Analysis
instead, preserving its files and paths without guessing which DLLs to delete.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import venv

ROOT = Path(__file__).resolve().parents[1]


def build_process_environment() -> dict[str, str]:
    # Build inputs come from the committed lock and the public index (or an
    # explicitly selected offline wheelhouse), never developer pip settings.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("PIP_")}
    env["PIP_CONFIG_FILE"] = os.devnull
    return env


def install_build_requirements(python: Path, requirements: Path, wheelhouse: Path | None = None) -> None:
    command = [str(python), "-I", "-m", "pip", "install", "--require-hashes",
               "--only-binary=:all:", "--disable-pip-version-check", "--no-input",
               "--no-cache-dir", "-r", str(requirements)]
    if wheelhouse is None:
        command += ["--index-url", "https://pypi.org/simple"]
    else:
        command += ["--no-index", "--find-links", str(wheelhouse.resolve())]
    subprocess.run(command, check=True, env=build_process_environment())


def native_payload_entries(directory: Path) -> list[tuple[str, str, str]]:
    directory = directory.resolve()
    if not (directory / "HaloBattery.Settings.exe").is_file():
        raise FileNotFoundError(f"Publish the settings companion first: {directory}")
    return [("settings-ui/" + path.relative_to(directory).as_posix(), str(path), "DATA")
            for path in sorted(directory.rglob("*")) if path.is_file()]


def write_build_info(workpath: Path, channel: str) -> Path:
    """Always regenerate identity, including stable builds reusing a preview's work directory."""
    if channel not in ("stable", "pre-release"):
        raise ValueError("unsupported build channel")
    text = (ROOT / "halo_battery.pyw").read_text(encoding="utf-8")
    versions = re.findall(r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"\r?$', text, re.MULTILINE)
    if len(versions) != 1:
        raise ValueError("expected one numeric VERSION in halo_battery.pyw")
    workpath.mkdir(parents=True, exist_ok=True)
    metadata = workpath / "build-info.json"
    metadata.write_text(json.dumps({"schema": 1, "channel": channel, "version": versions[0]}) + "\n",
                        encoding="utf-8")
    return metadata


def freeze_python(python: Path, native_ui: Path, distpath: Path, workpath: Path,
                  channel: str = "stable") -> None:
    """Freeze with the caller's freshly created and hash-verified interpreter."""
    payload = native_payload_entries(native_ui)
    for required in (ROOT / "halo.ico", ROOT / "version_info.txt"):
        if not required.is_file():
            raise FileNotFoundError(f"Generate the build asset first: {required}")
    specpath = workpath / "spec" / "HaloBattery.spec"
    specpath.parent.mkdir(parents=True, exist_ok=True)
    metadata = write_build_info(workpath, channel)
    # Runtime legal metadata already contains the full grants/NOTICE texts.
    # Developer audit inputs and duplicate plaintext exports stay in source.
    datas = [(str(metadata), "halo"), (str(ROOT / "CHANGELOG.md"), "."),
             (str(ROOT / "halo/iconify_data/registry.json.gz"), "halo/iconify_data"),
             (str(ROOT / "halo/iconify_data/manifests"), "halo/iconify_data/manifests"),
             (str(ROOT / "docs/licenses"), "licenses"),
             (str(ROOT / "docs/credits.md"), "licenses"),
             (str(ROOT / "assets/app-icon.png"), "assets"),
             (str(ROOT / "assets/app-icon-header-dark.png"), "assets")]
    spec = f"""from PyInstaller.utils.hooks import collect_data_files

a = Analysis([{str(ROOT / 'halo_battery.pyw')!r}],
    pathex=[], binaries=[],
    datas={datas!r} + collect_data_files('libusb_package'),
    hiddenimports=['hid', 'pystray._win32', 'serial', 'serial.tools.list_ports',
                   'usb.backend.libusb1'],
    hookspath=[], hooksconfig={{}}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0)
# This is another self-contained application, not a Python extension.
a.datas += {payload!r}
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='HaloBattery',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
    version={str(ROOT / 'version_info.txt')!r}, icon=[{str(ROOT / 'halo.ico')!r}])
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='HaloBattery')
"""
    specpath.write_text(spec, encoding="utf-8")
    env = build_process_environment()
    env["PYINSTALLER_CONFIG_DIR"] = str(workpath / "cache")
    subprocess.run([str(python), "-I", "-m", "PyInstaller", str(specpath), "--noconfirm", "--clean",
                    "--distpath", str(distpath.resolve()), "--workpath", str(workpath)],
                   check=True, env=env)


def prepare_local_build(python: Path, native_ui: Path, intermediate: Path) -> None:
    env = build_process_environment()
    for script, output in (("make_icon.py", "halo.ico"), ("make_version.py", "version_info.txt")):
        subprocess.run([str(python), "-I", str(ROOT / "tools" / script), str(ROOT / output)],
                       check=True, cwd=ROOT, env=env)
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-File", str(ROOT / "scripts/build_settings.ps1"),
                    "-OutputDirectory", str(native_ui.resolve()),
                    "-IntermediateDirectory", str(intermediate.resolve())],
                   check=True, cwd=ROOT, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-ui", type=Path, default=ROOT / ".build/settings-ui")
    parser.add_argument("--distpath", type=Path, default=ROOT / "dist")
    parser.add_argument("--workpath", type=Path, default=ROOT / ".build/pyinstaller")
    parser.add_argument("--channel", choices=("stable", "pre-release"), default="stable",
                        help="Bundled build identity; only pre-release builds offer an equal-version stable update")
    parser.add_argument("--wheelhouse", type=Path,
                        help="Install the hashed build requirements from this offline wheel directory")
    parser.add_argument("--prepare-local-build", action="store_true",
                        help="Generate assets and publish native settings using the same verified build environment")
    parser.add_argument("--native-intermediate", type=Path,
                        help="Native build cache for --prepare-local-build (default: beside --workpath)")
    args = parser.parse_args()
    if not args.prepare_local_build:
        native_payload_entries(args.native_ui)
        for required in (ROOT / "halo.ico", ROOT / "version_info.txt"):
            if not required.is_file():
                parser.error(f"Generate the build asset first: {required}")
    workpath = args.workpath.resolve()
    workpath.mkdir(parents=True, exist_ok=True)
    # A fresh environment forces every artifact through pip's hash checks.
    # Installing into an existing environment can skip already installed,
    # unverified requirements. Build environments never ship in the app.
    print(f"Build interpreter: {sys.executable} ({sys.version.split()[0]})", flush=True)
    with tempfile.TemporaryDirectory(prefix="build-environment-", dir=workpath) as directory:
        environment = Path(directory)
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        install_build_requirements(python, ROOT / "requirements-build.txt", args.wheelhouse)
        if args.prepare_local_build:
            prepare_local_build(python, args.native_ui,
                                args.native_intermediate or workpath.parent / "settings-ui-obj")
        freeze_python(python, args.native_ui, args.distpath, workpath, args.channel)


if __name__ == "__main__":
    main()
