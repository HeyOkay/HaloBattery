"""Embed repo-native tray assets, replacing only icon_artwork.py's _PNG literal.

Usage: python tools/make_icon_artwork.py [--check]
See artwork/tray/README.md for editing masks and provenance.
"""
from __future__ import annotations

import argparse
import ast
import base64
import io
import json
from pathlib import Path
import textwrap

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "artwork" / "tray"
MODULE = ROOT / "halo" / "icon_artwork.py"


def encode_sources() -> dict:
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    if manifest["format"] != 1:
        raise ValueError("unsupported artwork manifest format")
    result = {}
    boxes = []
    with Image.open(ASSETS / manifest["atlas"]) as atlas:
        if atlas.mode != "L":
            raise ValueError("the mask atlas must use mode L")
        for entry in manifest["masks"]:
            name, size, box = entry["name"], entry["size"], tuple(entry["box"])
            x0, y0, x1, y1 = box
            if (size <= 0 or x1-x0 != size or y1-y0 != size
                    or not 0 <= x0 < x1 <= atlas.width or not 0 <= y0 < y1 <= atlas.height):
                raise ValueError(f"invalid mask rectangle: {entry}")
            if any(x0 < b[2] and x1 > b[0] and y0 < b[3] and y1 > b[1] for b in boxes):
                raise ValueError(f"overlapping mask rectangle: {entry}")
            boxes.append(box)
            if (name, size) in result:
                raise ValueError(f"duplicate glyph: {name}/{size}")
            output = io.BytesIO()
            atlas.crop(box).save(output, format="PNG")
            result[name, size] = base64.b64encode(output.getvalue()).decode("ascii")
    for entry in manifest["color_masters"]:
        name, size = entry["name"], entry["size"]
        data = (ASSETS / entry["file"]).read_bytes()
        with Image.open(io.BytesIO(data)) as image:
            if image.mode != "RGBA" or image.size != (size, size):
                raise ValueError(f"invalid color master: {name}")
        if (name, size) in result or entry["optical_offset_already_applied"] is not True:
            raise ValueError(f"duplicate or unpositioned color master: {name}")
        # Already-positioned PNG bytes are embedded directly, never resampled.
        result[name, size] = base64.b64encode(data).decode("ascii")
    return result


def regenerate(source: str, pngs: dict) -> str:
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_PNG" for t in n.targets))
    literal = "_PNG = {\n" + "".join(
        "    " + repr(key) + ": (\n"
        + "".join("        " + repr(line) + "\n" for line in textwrap.wrap(value, 100))
        + "    ),\n" for key, value in pngs.items()) + "}\n"
    lines = source.splitlines(keepends=True)
    return "".join(lines[:node.lineno-1]) + literal + "".join(lines[node.end_lineno:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if regeneration changes the embedded assets")
    args = parser.parse_args()
    source = MODULE.read_text(encoding="utf-8")
    pngs = encode_sources()
    generated = regenerate(source, pngs)
    if generated == source:
        print(f"{len(pngs)} glyph assets: embedded literal already matches repository sources")
        return 0
    if args.check:
        print("Embedded artwork is out of date; run tools/make_icon_artwork.py")
        return 1
    MODULE.write_text(generated, encoding="utf-8", newline="")
    print(f"Regenerated {len(pngs)} glyph assets in {MODULE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
