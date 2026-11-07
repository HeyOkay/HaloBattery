"""Write a PyInstaller version resource file for HaloBattery.exe.

Usage: python tools/make_version.py version_info.txt

The version comes from VERSION in halo_battery.pyw. Windows shows these fields
in the file's Properties > Details tab; an .exe without them looks more
suspicious to antivirus heuristics.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({t}),
    prodvers=({t}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', 'HeyOkay'),
        StringStruct('FileDescription', 'Halo Battery - battery levels of wireless devices in the tray'),
        StringStruct('FileVersion', '{v}'),
        StringStruct('InternalName', 'HaloBattery'),
        StringStruct('LegalCopyright', 'MIT License, https://github.com/HeyOkay/HaloBattery'),
        StringStruct('OriginalFilename', 'HaloBattery.exe'),
        StringStruct('ProductName', 'Halo Battery'),
        StringStruct('ProductVersion', '{v}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def version() -> str:
    with open(os.path.join(ROOT, "halo_battery.pyw"), encoding="utf-8") as f:
        m = re.search(r'^VERSION = "([0-9.]+)"', f.read(), re.M)
    if not m:
        raise SystemExit("VERSION not found in halo_battery.pyw")
    return m.group(1)


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "version_info.txt"
    v = version()
    parts = [int(p) for p in v.split(".")][:4]
    parts += [0] * (4 - len(parts))
    with open(out, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(v=v, t=", ".join(map(str, parts))))
    print(f"wrote {out} ({v})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
