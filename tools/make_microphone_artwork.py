"""Regenerate pinned Lucide SVG alpha masks through a shared offline pipeline.

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


def _place_masks(manifest, original, alpha, kind):
    """Replace a glyph in its existing boxes, or append one new 64px row."""
    slots = {m["size"]: m for m in manifest["masks"] if m["name"] == kind}
    row = min(m["box"][1] for m in slots.values()) if slots else max(m["box"][3] for m in manifest["masks"])
    atlas = Image.new("L", (original.width, max(original.height, row + 64)))
    atlas.paste(original, (0, 0))
    replacements = {}
    left = 0
    for size in SIZES:
        mask = alpha.resize((size, size), Image.Resampling.LANCZOS)
        mask.putdata([mask.getpixel((x, y))
                      if math.hypot(x+.5-size/2, y+.5-size/2) <= 6.6*size/16 else 0
                      for y in range(size) for x in range(size)])
        box = slots[size]["box"] if size in slots else [left, row, left+size, row+size]
        atlas.paste(mask, tuple(box))
        replacements[size] = {"name": kind, "size": size, "box": box, "source": "lucide"}
        left += size
    masks = [replacements[m["size"]] if m["name"] == kind else m for m in manifest["masks"]]
    masks.extend(replacements[size] for size in SIZES if size not in slots)
    return atlas, masks


def main(kind="microphone", svg_name="mic", pinned_sha256=PINNED_SHA256,
         ink_bounds=(4, 2.5, 12, 13.5), center_visible=False, logical_scale=.5) -> int:
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
    svg_bytes = (ASSETS / f"lucide-{svg_name}.svg").read_bytes()
    if hashlib.sha256(svg_bytes).hexdigest() != pinned_sha256:
        raise ValueError(f"{svg_name} SVG differs from the reviewed lucide-static 1.17.0 source")
    svg = ET.fromstring(svg_bytes)
    if svg.attrib.get("viewBox") != "0 0 24 24" or svg.attrib.get("stroke-width") != "2":
        raise ValueError("unexpected Lucide source geometry")
    work = ROOT / "build" / f"{svg_name}-artwork"
    work.mkdir(parents=True, exist_ok=True)
    html = work / f"{svg_name}.html"
    html.write_text('''<!doctype html><meta charset="utf-8"><style>
html,body{margin:0;width:1024px;height:1024px;background:transparent;overflow:hidden;color:white}
svg{position:absolute;left:128px;top:128px;width:768px;height:768px}
</style>''' + svg_bytes.decode("utf-8"), encoding="utf-8")
    viewport = 768
    inset = 128
    if logical_scale != .5:
        # Fit unusually wide corner ink without changing path proportions or
        # the shared one-logical-pixel stroke. The pinned SVG stays untouched.
        viewport = 1536 * logical_scale
        inset = (1024 - viewport) / 2
        markup = html.read_text(encoding="utf-8")
        markup = markup.replace("left:128px;top:128px;width:768px;height:768px",
                                f"left:{inset}px;top:{inset}px;width:{viewport}px;height:{viewport}px")
        markup = markup.replace('stroke-width="2"', f'stroke-width="{1/logical_scale:.12g}"')
        html.write_text(markup, encoding="utf-8")
    screenshot = work / f"{svg_name}-master-1024.png"
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
    centering = None
    if center_visible:
        # Center measured source ink, rather than assuming a centered SVG viewBox.
        # This development-only translation is baked once into the alpha masks.
        bounds = alpha.getbbox()
        dx = round(512 - (bounds[0] + bounds[2]) / 2)
        dy = round(512 - (bounds[1] + bounds[3]) / 2)
        centered = Image.new("L", alpha.size)
        centered.paste(alpha, (dx, dy))
        if centered.histogram() != alpha.histogram():
            raise ValueError("source centering would discard ink")
        alpha = centered
        ink_bounds = [v / 64 for v in alpha.getbbox()]
        centering = {"logical_uncentered_ink_bounds": [v / 64 for v in bounds],
                     "logical_geometric_centering_translation": [dx / 64, dy / 64]}
    manifest_path = ASSETS / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with Image.open(ASSETS / manifest["atlas"]) as original:
        if original.mode != "L":
            raise ValueError("expected L-mode source atlas")
        atlas, masks = _place_masks(manifest, original, alpha, kind)
    atlas.save(ASSETS / manifest["atlas"])
    manifest["masks"] = masks
    manifest["sources"]["lucide"]["glyphs"][kind] = svg_name
    manifest.setdefault("svg_generation", {})[kind] = {
            "file": f"lucide-{svg_name}.svg", "sha256": pinned_sha256,
            "url": f"https://unpkg.com/lucide-static@1.17.0/icons/{svg_name}.svg",
            "tool": f"tools/make_{kind}_artwork.py",
            "view_box": [0, 0, 24, 24], "stroke_width": 2,
            "logical_scale": logical_scale, "logical_visible_ink_bounds": list(ink_bounds),
            "logical_optical_offset": [0, 0], "logical_alpha_limit_radius": 6.6, "master_size": 1024,
            "svg_viewport": [inset, inset, 1024-inset, 1024-inset],
            "sampling": "Chromium transparent RGBA SVG, then Pillow LANCZOS alpha masks"}
    if centering is not None:
        manifest["svg_generation"][kind].update(centering)
    if logical_scale != .5:
        manifest["svg_generation"][kind].update({
            "source_stroke_width": 2, "render_stroke_width": 1/logical_scale,
            "logical_stroke_width": 1,
            "fit_reason": "Fit full rounded TV corner ink inside the 6.6-logical-pixel glyph region"})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n",
                             encoding="utf-8", newline="")
    print(f"Generated seven {kind} masks; other glyph boxes, masks and metadata preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
