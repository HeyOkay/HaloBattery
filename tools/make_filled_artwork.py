"""Render the reviewed filled SVGs at native tray sizes and embed 77 masks.

Usage: python tools/make_filled_artwork.py [--browser PATH] [--check]
Requires Pillow and a local Chromium browser; neither SVG nor Chromium is used
at runtime. Check mode compares decoded mask pixels and never rewrites sources.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from PIL import Image

try:
    from .filled_optics import compensate, profile
    from .make_icon_artwork import regenerate
except ImportError:
    from filled_optics import compensate, profile
    from make_icon_artwork import regenerate

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "artwork" / "tray" / "filled"
MODULE = ROOT / "halo" / "icon_artwork_filled.py"
SIZES = (16, 20, 24, 32, 40, 48, 64)
NAMESPACE = "http://www.w3.org/2000/svg"


def prepare_sources():
    """Validate pinned local sources and preserve their reviewed placement."""
    manifest = json.loads((ASSETS / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("sizes") != list(SIZES):
        raise ValueError("unsupported filled artwork manifest format or sizes")
    glyphs = manifest["glyphs"]
    if len(glyphs) != 11:
        raise ValueError("expected eleven filled glyph sources")
    ET.register_namespace("", NAMESPACE)
    cells = []
    for kind, specification in glyphs.items():
        path = (ASSETS / specification["file"]).resolve()
        if not path.is_relative_to(ASSETS.resolve()):
            raise ValueError(f"filled source escapes artwork directory: {kind}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != specification["sha256"]:
            raise ValueError(f"filled SVG hash mismatch: {kind}")
        for size in SIZES:
            content = ET.fromstring(data)
            if content.get("viewBox") != "0 0 24 24":
                raise ValueError(f"unexpected filled SVG viewBox: {kind}")
            content.set("width", "24")
            content.set("height", "24")
            content.set("stroke-width", str(specification["stroke_width"]))
            x, y = specification["translation"]
            scale = specification["scale"]
            wrapper = ET.Element("{" + NAMESPACE + "}svg", {
                "class": "icon", "viewBox": "0 0 16 16", "aria-hidden": "true"})
            group = ET.SubElement(wrapper, "{" + NAMESPACE + "}g", {
                "opacity": "1",
                "transform": f"translate({x} {y}) scale({scale}) translate(-12 -12)"})
            group.append(content)
            wrapper = compensate(wrapper, kind, size)
            wrapper.set("width", str(size))
            wrapper.set("height", str(size))
            wrapper.set("style", "color:white;display:block;overflow:visible")
            # Each native-size cell has independent mask/clip identifiers.
            svg = ET.tostring(wrapper, encoding="unicode")
            svg = svg.replace("fill-" + kind, "fill-" + kind + "-" + str(size))
            cells.append('<div class="cell">' + svg + "</div>")
    html = ('<style>html,body{margin:0;width:512px;height:704px;background:transparent}'
            '.grid{display:grid;grid-template-columns:repeat(7,64px);grid-auto-rows:64px}'
            '.cell{width:64px;height:64px}</style><div class="grid">'
            + "".join(cells) + "</div>")
    return tuple(glyphs), html


def render_masks(browser, kinds, html):
    work = ROOT / "build" / "filled-artwork"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="native-", dir=work) as temporary:
        scratch = Path(temporary)
        source = scratch / "source.html"
        screenshot = scratch / "native-masks.png"
        source.write_text(html, encoding="utf-8")
        screenshot.unlink(missing_ok=True)
        command = [str(browser), "--headless", "--disable-gpu", "--no-first-run",
                   "--no-default-browser-check", "--disable-background-networking",
                   "--disable-component-update", "--disable-sync", "--disable-extensions",
                   "--disable-default-apps", "--host-resolver-rules=MAP * ~NOTFOUND",
                   "--force-device-scale-factor=1", "--default-background-color=00000000",
                   "--hide-scrollbars", "--window-size=512,704",
                   "--user-data-dir=" + str(scratch / "profile"),
                   "--screenshot=" + str(screenshot), source.as_uri()]
        completed = subprocess.run(command, capture_output=True, timeout=60,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        (work / "browser-output.log").write_bytes(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"headless renderer failed ({completed.returncode}); see {work}")
        with Image.open(screenshot) as source_image:
            if source_image.mode != "RGBA" or source_image.size != (512, 704):
                raise ValueError("native renderer must produce a transparent 512 x 704 RGBA atlas")
            atlas = source_image.copy()
    masks = {}
    for row, kind in enumerate(kinds):
        for column, size in enumerate(SIZES):
            alpha = atlas.crop((column * 64, row * 64,
                                column * 64 + size, row * 64 + size)).getchannel("A")
            if not alpha.getbbox() or alpha.getextrema()[1] != 255:
                raise ValueError(f"empty or non-opaque native mask: {kind}/{size}")
            masks[kind, size] = alpha
    return masks


def assigned_node(source, name):
    return next(node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == name for target in node.targets))


def literal(source, name):
    value = assigned_node(source, name).value
    if name == "KINDS" and isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
        return frozenset(ast.literal_eval(value.args[0]))
    return ast.literal_eval(value)


def replace_assignment(source, name, expression):
    node = assigned_node(source, name)
    lines = source.splitlines(keepends=True)
    return "".join(lines[:node.lineno - 1]) + name + " = " + expression + "\n" + "".join(lines[node.end_lineno:])


def check_sources(source, kinds, masks):
    expected = {(kind, size) for kind in kinds for size in SIZES}
    embedded = literal(source, "_PNG")
    if len(expected) != 77 or set(embedded) != expected or set(masks) != expected:
        raise ValueError("filled mask keys differ from the 77 reviewed kind/size pairs")
    if literal(source, "KINDS") != frozenset(kinds):
        raise ValueError("filled KINDS differs from source manifest")
    if literal(source, "OPTICAL_PROFILE") != {size: profile(size) for size in SIZES}:
        raise ValueError("filled OPTICAL_PROFILE differs from generator")
    stale = []
    for key, mask in masks.items():
        with Image.open(io.BytesIO(base64.b64decode(embedded[key], validate=True))) as image:
            if image.mode != "L" or image.size != mask.size or image.tobytes() != mask.tobytes():
                stale.append(f"{key[0]}/{key[1]}")
    if stale:
        raise ValueError("filled mask pixels differ: " + ", ".join(stale))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, help="local Chrome/Edge executable")
    parser.add_argument("--check", action="store_true", help="compare decoded pixels without rewriting tracked sources")
    args = parser.parse_args()
    browser = args.browser
    if browser is None:
        browser = next((path for path in (
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if path.is_file()), None)
    if browser is None or not browser.is_file():
        parser.error("provide a local Chromium browser with --browser")
    kinds, html = prepare_sources()
    masks = render_masks(browser.resolve(), kinds, html)
    source = MODULE.read_text(encoding="utf-8")
    if args.check:
        try:
            check_sources(source, kinds, masks)
        except ValueError as error:
            print(error)
            return 1
        print("77 filled masks: decoded pixels, KINDS and OPTICAL_PROFILE match")
        return 0
    pngs = {}
    for key, mask in masks.items():
        stream = io.BytesIO()
        mask.save(stream, format="PNG")
        pngs[key] = base64.b64encode(stream.getvalue()).decode("ascii")
    generated = regenerate(source, pngs)
    generated = replace_assignment(generated, "KINDS", "frozenset(" + repr(kinds) + ")")
    generated = replace_assignment(generated, "OPTICAL_PROFILE", repr({size: profile(size) for size in SIZES}))
    check_sources(generated, kinds, masks)
    if generated != source:
        MODULE.write_text(generated, encoding="utf-8", newline="")
    print("Generated 77 native-size filled masks in halo/icon_artwork_filled.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
