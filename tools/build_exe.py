"""Freeze Python, then include the independently published settings companion.

The native app is already self-contained. Passing its PE files into Python's
Analysis makes PyInstaller analyze them again and copy extra DLLs into the
Python runtime directory. Add the complete publish tree as DATA after Analysis
instead, preserving its files and paths without guessing which DLLs to delete.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def native_payload_entries(directory: Path) -> list[tuple[str, str, str]]:
    directory = directory.resolve()
    if not (directory / "HaloBattery.Settings.exe").is_file():
        raise FileNotFoundError(f"Publish the settings companion first: {directory}")
    return [("settings-ui/" + path.relative_to(directory).as_posix(), str(path), "DATA")
            for path in sorted(directory.rglob("*")) if path.is_file()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-ui", type=Path, default=ROOT / ".build/settings-ui")
    parser.add_argument("--distpath", type=Path, default=ROOT / "dist")
    parser.add_argument("--workpath", type=Path, default=ROOT / ".build/pyinstaller")
    args = parser.parse_args()
    payload = native_payload_entries(args.native_ui)
    for required in (ROOT / "halo.ico", ROOT / "version_info.txt"):
        if not required.is_file():
            parser.error(f"Generate the build asset first: {required}")
    workpath = args.workpath.resolve()
    specpath = workpath / "spec" / "HaloBattery.spec"
    specpath.parent.mkdir(parents=True, exist_ok=True)
    datas = [(str(ROOT / "docs/licenses"), "licenses"),
             (str(ROOT / "docs/credits.md"), "licenses")]
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
    # Build-only dependency; importing this module for offline tests needs no
    # PyInstaller installation and never starts a build.
    os.environ.setdefault("PYINSTALLER_CONFIG_DIR", str(workpath / "cache"))
    import PyInstaller.__main__
    PyInstaller.__main__.run([str(specpath), "--noconfirm", "--clean",
        "--distpath", str(args.distpath.resolve()), "--workpath", str(workpath)])


if __name__ == "__main__":
    main()
