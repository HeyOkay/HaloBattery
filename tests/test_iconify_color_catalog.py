"""Offline color-cache and legacy upgrade tests; no native renderer or network."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse

from PIL import Image, ImageDraw
from halo import iconify_catalog as C
from halo import svg_glyph as G


def _png(mode, paint):
    image = Image.new(mode, (G.MASTER_SIZE, G.MASTER_SIZE))
    ImageDraw.Draw(image).rectangle((504, 504, 519, 519), fill=paint)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


class IconifyColorCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.icon_id = "test:colored-heart"
        self.data = {"prefix": "test", "width": 24, "height": 24,
                     "icons": {"colored-heart": {"body": '<path fill="#dd2e44" d="M4 4h16v16H4z"/>'}}}
        self.canonical = C.canonical_icon(self.data, "colored-heart")
        self.source_hash = C._hash(self.canonical)
        self.info = {"name": "Test artwork", "author": {"name": "Test artist"},
                     "license": {"title": "MIT", "spdx": "MIT", "url": "https://example.org/LICENSE"}}
        self.entry = {**copy.deepcopy(self.info), "license_text": "Copyright Test artist\nComplete test license notice",
                      "notices": [{"name": "NOTICE", "text": "Retain attribution"}],
                      "provenance": {"upstream_revision": "a" * 40},
                      "icons": {"colored-heart": self.source_hash}}
        self.registry = {"schema": 1, "iconify_revision": "b" * 40,
                         "collections": {"test": self.entry}}
        self.calls = []
        self.renders = []
        self.color_png = _png("RGBA", (221, 46, 68, 255))
        self.legacy_png = _png("L", 255)

    def fetch(self, url):
        self.calls.append(url)
        route = urllib.parse.urlsplit(url).path
        if route == "/collections":
            return {"test": copy.deepcopy(self.info)}
        if route == "/test.json":
            return copy.deepcopy(self.data)
        raise AssertionError("Unexpected fake API request: " + url)

    def factory(self, svg):
        self.renders.append(svg)
        # Deliberately exercise the real RGB cache validator, while avoiding
        # Windows/Direct2D and any dependency on a particular SVG rasterizer.
        return G.CustomGlyph.from_png(self.color_png)

    def catalog(self, *, fetch=None):
        return C.IconifyCatalog(self.root, fetch=fetch or self.fetch,
                               glyph_factory=self.factory, registry=self.registry,
                               clock=lambda: 1234.0)

    def legacy(self, *, save_source=False):
        version = G.PREVIOUS_RENDERER_VERSION
        asset_id = C._hash([C.ASSET_REVISION, self.icon_id, self.source_hash, version])
        folder = self.root / "iconify" / "assets" / asset_id
        folder.mkdir(parents=True)
        metadata = {
            "asset_id": asset_id, "icon_id": self.icon_id, "collection": self.entry["name"],
            "collection_id": self.icon_id.split(":")[0], "author": copy.deepcopy(self.entry["author"]),
            "license": copy.deepcopy(self.entry["license"]), "license_text": self.entry["license_text"],
            "notices": copy.deepcopy(self.entry["notices"]), "provenance": copy.deepcopy(self.entry["provenance"]),
            "source_url": "https://icon-sets.iconify.design/" + self.icon_id.replace(":", "/") + "/",
            "source_hash": self.source_hash, "renderer_revision": version,
            "iconify_revision": self.registry["iconify_revision"], "acquired_utc": "2026-11-14T00:00:00+00:00",
            "modifications": "Recolored to a monochrome tray glyph; scaled and rasterized.",
            "png_sha256": hashlib.sha256(self.legacy_png).hexdigest()}
        (folder / "glyph.png").write_bytes(self.legacy_png)
        (folder / "metadata.json").write_bytes(C._json_bytes(metadata))
        if save_source:
            (folder / "source.json").write_bytes(C._json_bytes(self.canonical))
        return asset_id, folder, metadata

    def no_network(self, url):
        self.fail("An offline-only operation requested the API: " + url)

    def test_explicit_single_hue_is_saved_and_reloaded_as_rgb(self):
        service = self.catalog()
        asset = service.prepare(self.icon_id)
        self.assertTrue(asset.glyph.is_colored)
        self.assertEqual(asset.glyph.color_mode, "color")
        self.assertEqual(asset.metadata["color_mode"], "color")
        self.assertNotIn("monochrome", asset.metadata["modifications"].lower())
        folder = self.root / "iconify" / "assets" / asset.id
        self.assertEqual(json.loads((folder / "source.json").read_bytes()), self.canonical)
        restored = self.catalog(fetch=self.no_network).load(asset.id)
        self.assertIsNotNone(restored)
        self.assertTrue(restored.glyph.is_colored)
        self.assertEqual(restored.metadata["color_mode"], "color")
        self.assertEqual(restored.glyph.digest, asset.glyph.digest)
        rendered = restored.glyph.render(80, (0, 255, 0), foreground_opacity=1)
        pixels = rendered.load()
        red_pixels = [pixels[x, y] for y in range(80) for x in range(80) if pixels[x, y][3] > 32]
        self.assertTrue(red_pixels)
        # Premultiplied area sampling can round semi-transparent edge RGB by
        # one or two units. It must retain the authored red, not become green fg.
        self.assertTrue(all(pixel[0] > 180 and pixel[1] < 90 and pixel[2] < 100 for pixel in red_pixels))

    def test_current_color_cache_needs_no_api_or_repeated_render(self):
        service = self.catalog()
        asset = service.prepare(self.icon_id)
        self.calls.clear()
        service._fetch = self.no_network
        self.assertFalse(service.needs_upgrade(asset))
        self.assertEqual(service.cached(self.icon_id).id, asset.id)
        self.assertEqual(service.prepare(self.icon_id).id, asset.id)
        service.pin(asset.id)
        self.assertEqual(service.attributions([asset.id])[0]["color_mode"], "color")
        self.assertEqual(len(self.renders), 1)

    def test_legacy_mask_remains_available_offline_before_upgrade(self):
        asset_id, _, metadata = self.legacy()
        service = self.catalog(fetch=self.no_network)
        asset = service.cached(self.icon_id)
        self.assertIsNotNone(asset)
        self.assertEqual(asset.id, asset_id)
        self.assertFalse(asset.glyph.is_colored)
        self.assertTrue(service.needs_upgrade(asset))
        self.assertEqual(asset.metadata, metadata)
        self.assertEqual(service.load(asset_id).id, asset_id)
        service.pin(asset_id)
        self.assertEqual(service.attributions([asset_id])[0]["icon_id"], self.icon_id)

    def test_legacy_upgrade_fetches_verified_source_and_keeps_old_pin(self):
        old_id, old_folder, _ = self.legacy()
        service = self.catalog()
        service.pin(old_id)
        upgraded = service.prepare(self.icon_id)
        self.assertNotEqual(upgraded.id, old_id)
        self.assertTrue(upgraded.glyph.is_colored)
        self.assertEqual(upgraded.metadata["source_hash"], self.source_hash)
        self.assertEqual(upgraded.metadata["renderer_revision"], G.RENDERER_VERSION)
        self.assertEqual(upgraded.metadata["color_mode"], "color")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.renders), 1)
        self.assertTrue(old_folder.is_dir())
        service.pin(upgraded.id)
        self.assertIn(old_id, service._pinned)
        self.assertIn(upgraded.id, service._pinned)
        self.assertEqual(service.load(old_id).id, old_id)

    def test_offline_upgrade_falls_back_without_discarding_selection(self):
        old_id, old_folder, metadata = self.legacy()
        def offline(url):
            self.calls.append(url)
            raise OSError("offline")
        service = self.catalog(fetch=offline)
        service.pin(old_id)
        asset = service.prepare(self.icon_id)
        self.assertEqual(asset.id, old_id)
        self.assertEqual(asset.metadata, metadata)
        self.assertTrue(old_folder.is_dir())
        self.assertFalse(self.renders)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(service.cached(self.icon_id).id, old_id)

    def test_verified_cached_source_can_upgrade_without_network(self):
        old_id, _, _ = self.legacy(save_source=True)
        service = self.catalog(fetch=self.no_network)
        upgraded = service.prepare(self.icon_id)
        self.assertNotEqual(upgraded.id, old_id)
        self.assertTrue(upgraded.glyph.is_colored)
        self.assertEqual(len(self.renders), 1)
        self.assertEqual(upgraded.metadata["source_hash"], self.source_hash)

    def test_tampered_cached_source_cannot_bypass_registry_color_hash(self):
        old_id, folder, _ = self.legacy(save_source=True)
        tampered = dict(self.canonical, body='<path fill="#00ff00" d="M4 4h16v16H4z"/>')
        (folder / "source.json").write_bytes(C._json_bytes(tampered))
        def offline(url):
            raise OSError("offline")
        asset = self.catalog(fetch=offline).prepare(self.icon_id)
        self.assertEqual(asset.id, old_id)
        self.assertFalse(self.renders)
        self.assertNotEqual(C._hash(tampered), self.source_hash)

    def test_mixed_theme_masters_round_trip_with_actual_color_metadata(self):
        self.data["icons"]["colored-heart"]["body"] = (
            '<path fill="currentColor" d="M4 4h16v16H4z"/>'
            '<path fill="#dd2e44" d="M8 8h8v8H8z"/>')
        self.canonical = C.canonical_icon(self.data, "colored-heart")
        self.source_hash = C._hash(self.canonical)
        self.entry["icons"]["colored-heart"] = self.source_hash
        pair = Image.new("RGBA", (2 * G.MASTER_SIZE, G.MASTER_SIZE))
        for offset, color in ((0, (0, 0, 0, 255)), (G.MASTER_SIZE, (255, 255, 255, 255))):
            tile = Image.new("RGBA", (G.MASTER_SIZE, G.MASTER_SIZE))
            draw = ImageDraw.Draw(tile)
            draw.rectangle((496, 496, 527, 527), fill=color)
            draw.rectangle((508, 508, 515, 515), fill=(221, 46, 68, 255))
            pair.paste(tile, (offset, 0))
        stream = io.BytesIO()
        pair.save(stream, format="PNG")
        service = self.catalog()
        service._glyph_factory = lambda svg: G.CustomGlyph.from_png(stream.getvalue())
        asset = service.prepare(self.icon_id)
        self.assertEqual(asset.metadata["color_mode"], "mixed")
        self.assertIn("currentColor", asset.metadata["modifications"])
        loaded = self.catalog(fetch=self.no_network).load(asset.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.glyph.color_mode, "mixed")
        self.assertEqual(loaded.glyph.digest, asset.glyph.digest)
        black = loaded.glyph.render(80, (0, 0, 0))
        white = loaded.glyph.render(80, (255, 255, 255))
        self.assertNotEqual(black.tobytes(), white.tobytes())
        self.assertEqual(black.getchannel("A").tobytes(), white.getchannel("A").tobytes())

    def test_known_builtin_profile_needs_no_legacy_color_upgrade(self):
        real = C.IconifyCatalog(self.root, fetch=self.no_network)
        _, name, entry = real._entry("lucide:mouse")
        self.icon_id = "lucide:mouse"
        self.entry = entry
        self.source_hash = entry["icons"][name]
        self.registry = real.registry
        old_id, _, _ = self.legacy()
        service = self.catalog(fetch=self.no_network)
        asset = service.load(old_id)
        self.assertIsNotNone(asset)
        self.assertTrue(asset.glyph.uses_builtin_profile)
        self.assertFalse(service.needs_upgrade(asset))
        self.assertEqual(service.prepare(self.icon_id).id, old_id)
        self.assertFalse(self.renders)

    def test_remote_color_change_is_rejected_before_factory(self):
        self.data["icons"]["colored-heart"]["body"] = '<path fill="#00ff00" d="M4 4h16v16H4z"/>'
        with self.assertRaises(C.CatalogError) as failure:
            self.catalog().prepare(self.icon_id)
        self.assertEqual(failure.exception.code, "license_unverified")
        self.assertFalse(self.renders)


if __name__ == "__main__":
    unittest.main()
