"""Regenerate Lucide mic alpha masks from the pinned local SVG (development only).

Requires Pillow and a local Chrome/Edge installation, never a runtime dependency.
No browser window, user profile, desktop DPI setting or input is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "artwork" / "tray"
SIZES = (16, 20, 24, 32, 40, 48, 64)
PINNED_SHA256 = "05ea78b25f9ec6284612abe51f8a95f07c28659825409ed6a5cb286322efa117"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, help="local Chrome/Edge executable")
    args = parser.parse_args()
    browser = args.browser
    if browser is None:
        browser = next((p for p in (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
    if browser is None:
        parser.error("provide a local Chromium browser with --browser")
    svg_bytes = (ASSETS / "lucide-mic.svg").read_bytes()
    if hashlib.sha256(svg_bytes).hexdigest() != PINNED_SHA256:
        raise ValueError("mic SVG differs from the reviewed lucide-static 1.17.0 source")
    svg = ET.fromstring(svg_bytes)
    if svg.attrib.get("viewBox") != "0 0 24 24" or svg.attrib.get("stroke-width") != "2":
        raise ValueError("unexpected Lucide source geometry")
    work = ROOT / "build" / "mic-artwork"
    work.mkdir(parents=True, exist_ok=True)
    html = work / "mic.html"
    html.write_text('''<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;width:1024px;height:1024px;background:transparent;overflow:hidden;color:white}
svg{position:absolute;left:128px;top:128px;width:768px;height:768px}
</style>''' + svg_bytes.decode("utf-8"), encoding="utf-8")
    screenshot = work / "mic-master-1024.png"
    command = [str(browser), "--headless", "--disable-gpu", "--no-first-run",
               "--no-default-browser-check", "--disable-background-networking",
               "--disable-component-update", "--disable-sync", "--disable-extensions",
               "--disable-default-apps", "--host-resolver-rules=MAP * ~NOTFOUND",
               "--force-device-scale-factor=1", "--default-background-color=00000000",
               "--hide-scrollbars", "--window-size=1024,1024",
               "--user-data-dir=" + str(work / "profile"),
               "--screenshot=" + str(screenshot), html.as_uri()]
    screenshot.unlink(missing_ok=True)
    completed = subprocess.run(command, capture_output=True, timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    (work / "browser-output.log").write_bytes(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError(f"headless renderer failed ({completed.returncode}); see {work}")
    with Image.open(screenshot) as image:
        if image.size != (1024, 1024) or image.mode != "RGBA":
            raise ValueError("headless renderer must produce a 1024-square RGBA image")
        alpha = image.getchannel("A")
        if alpha.getextrema() != (0, 255):
            raise ValueError("headless SVG output must include transparent and opaque pixels")
    manifest_path = ASSETS / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    existing = [m for m in manifest["masks"] if m["name"] != "microphone"]
    with Image.open(ASSETS / manifest["atlas"]) as original:
        if original.mode != "L":
            raise ValueError("expected L-mode source atlas")
        row = max(m["box"][3] for m in existing)
        atlas = Image.new("L", (original.width, row+64))
        atlas.paste(original.crop((0, 0, original.width, row)), (0, 0))
    masks = []
    left = 0
    for size in SIZES:
        mask = alpha.resize((size, size), Image.Resampling.LANCZOS)
        # Match the other glyph masks: eliminate resampling-only alpha-1 halos
        # near the battery track without changing the visible source geometry.
        mask.putdata([mask.getpixel((x, y))
                      if math.hypot(x+.5-size/2, y+.5-size/2) <= 6.6*size/16 else 0
                      for y in range(size) for x in range(size)])
        box = [left, row, left+size, row+size]
        atlas.paste(mask, tuple(box))
        masks.append({"name": "microphone", "size": size, "box": box, "source": "lucide"})
        left += size
    atlas.save(ASSETS / manifest["atlas"])
    manifest["masks"] = existing + masks
    manifest["sources"]["lucide"]["glyphs"]["microphone"] = "mic"
    manifest["svg_generation"] = {
        "microphone": {
            "file": "lucide-mic.svg", "sha256": PINNED_SHA256,
            "url": "https://unpkg.com/lucide-static@1.17.0/icons/mic.svg",
            "tool": "tools/make_microphone_artwork.py",
            "view_box": [0, 0, 24, 24], "stroke_width": 2,
            "logical_scale": 0.5, "logical_visible_ink_bounds": [4, 2.5, 12, 13.5],
            "logical_optical_offset": [0, 0], "logical_alpha_limit_radius": 6.6, "master_size": 1024,
            "svg_viewport": [128, 128, 896, 896],
            "sampling": "Chromium transparent RGBA SVG, then Pillow LANCZOS alpha masks"}}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n",
                             encoding="utf-8", newline="")
    print("Generated seven microphone masks; original 56 masks and color masters preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
