"""Fast catalog browse path: verified sources and small injected previews only."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.parse
import urllib.error
from unittest.mock import patch

from PIL import Image
from halo import iconify_catalog as C


class _PreviewGlyph:
    """No png_bytes/full-master interface: browse must not depend on either."""
    color_mode = "color"
    is_colored = True

    def __init__(self, svg, raster_size, memory_bytes=None):
        self.digest = hashlib.sha256((svg + str(raster_size)).encode("utf-8")).hexdigest()
        self.memory_bytes = raster_size * raster_size * 4 if memory_bytes is None else memory_bytes

    def render(self, size, fg, alpha=255, foreground_opacity=230 / 255):
        return Image.new("RGBA", (size, size), (40, 100, 220, alpha))


class FastCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = {"prefix": "test", "width": 24, "height": 24,
                     "icons": {f"icon-{i}": {"body": f'<path fill="#dd2e44" d="M0 0h{i + 1}v10z"/>'}
                               for i in range(64)}}
        self.info = {"name": "Test art", "author": {"name": "Test artist"},
                     "license": {"title": "MIT", "spdx": "MIT", "url": "https://example.org/LICENSE"}}
        self.entry = {**copy.deepcopy(self.info), "license_text": "Full actual test license",
                      "notices": [], "provenance": {"upstream_revision": "a" * 40},
                      "icons": {name: C._hash(C.canonical_icon(self.data, name)) for name in self.data["icons"]}}
        self.registry = {"schema": 1, "iconify_revision": "b" * 40, "collections": {"test": self.entry}}
        self.calls = []
        self.rasters = []
        self.preview_memory = None

    def fetch(self, url):
        self.calls.append(url)
        parsed = urllib.parse.urlsplit(url)
        if parsed.path == "/collections":
            return {"test": copy.deepcopy(self.info)}
        if parsed.path == "/test.json":
            return copy.deepcopy(self.data)
        if parsed.path == "/search":
            limit = int(urllib.parse.parse_qs(parsed.query)["limit"][0])
            return {"icons": ["test:" + name for name in list(self.data["icons"])[:limit]],
                    "collections": {"test": copy.deepcopy(self.info)}, "limit": limit}
        raise AssertionError("Unexpected API request " + url)

    def preview_factory(self, svg, *, raster_size):
        self.rasters.append(raster_size)
        return _PreviewGlyph(svg, raster_size, self.preview_memory)

    def full_factory(self, svg):
        self.fail("Browsing attempted to rasterize a full selected master")

    def no_fetch(self, url):
        self.fail("Cached/offline browse attempted an API request: " + url)

    def catalog(self, *, fetch=None):
        return C.IconifyCatalog(self.root, registry=self.registry, fetch=fetch or self.fetch,
                               glyph_factory=self.full_factory, preview_factory=self.preview_factory,
                               clock=lambda: 1234.0)

    def test_32_result_page_uses_one_metadata_and_one_batched_data_request(self):
        service = self.catalog()
        page = service.search("icon", limit=32)
        ids = [item["id"] for item in page["items"]]
        self.assertEqual(len(ids), 32)
        with patch.object(service, "cached", side_effect=AssertionError("Decoded full cached PNG")), \
             patch.object(service, "load", side_effect=AssertionError("Loaded full cached PNG")), \
             patch.object(service, "prepare", side_effect=AssertionError("Prepared selected asset")), \
             patch.object(service, "_prune_assets", side_effect=AssertionError("Pruned selected assets")):
            service.prefetch_sources(ids)
            for icon_id in ids:
                glyph = service.preview_glyph(icon_id, 32)
                self.assertEqual(glyph.render(32, (255, 255, 255)).size, (32, 32))
        self.assertEqual(len(self.calls), 2)
        self.assertIn("/collections?", self.calls[0])
        self.assertIn("/test.json?icons=icon-0%2Cicon-1", self.calls[1])
        self.assertEqual(self.rasters, [128] * 32)
        self.assertFalse((self.root / "iconify" / "assets").exists())
        self.assertEqual(len(list((self.root / "iconify" / "sources").glob("*.json"))), 32)

    def test_repeated_preview_is_ram_only_and_never_loads_full_png(self):
        service = self.catalog()
        service.prefetch_sources(["test:icon-0"])
        first = service.preview_glyph("test:icon-0", 16)
        self.calls.clear()
        with patch.object(service, "cached", side_effect=AssertionError("Decoded full cached PNG")), \
             patch.object(service, "load", side_effect=AssertionError("Loaded full cached PNG")):
            service._fetch = self.no_fetch
            service.prefetch_sources(["test:icon-0"])
            again = service.preview_glyph("test:icon-0", 48)
            source = service.icon_data("test:icon-0", offline=True)
        self.assertIs(again, first)
        self.assertEqual(self.rasters, [128])
        self.assertFalse(self.calls)
        self.assertEqual(C._hash(source), self.entry["icons"]["icon-0"])

    def test_raster_tiers_follow_requested_physical_pixels(self):
        service = self.catalog()
        service.prefetch_sources(["test:icon-0"])
        for size in (8, 64, 65, 128, 129, 192):
            service.preview_glyph("test:icon-0", size)
        self.assertEqual(self.rasters, [128, 256, 512])
        for size in (None, True, 7, 193, "32"):
            with self.subTest(size=size), self.assertRaises(C.CatalogError) as failure:
                service.preview_glyph("test:icon-0", size)
            self.assertEqual(failure.exception.code, "invalid_request")

    def test_verified_raw_cache_survives_restart_offline_without_full_asset(self):
        self.catalog().prefetch_sources(["test:icon-0"])
        self.calls.clear()
        service = self.catalog(fetch=self.no_fetch)
        with patch.object(service, "cached", side_effect=AssertionError("Decoded full cached PNG")), \
             patch.object(service, "load", side_effect=AssertionError("Loaded full cached PNG")):
            source = service.icon_data("test:icon-0", offline=True)
            glyph = service.preview_glyph("test:icon-0", 80, offline=True)
        self.assertEqual(source, C.canonical_icon(self.data, "icon-0"))
        self.assertEqual(glyph.color_mode, "color")
        self.assertEqual(self.rasters, [256])
        self.assertFalse(self.calls)
        self.assertFalse((self.root / "iconify" / "assets").exists())

    def test_tampered_persisted_source_cannot_reach_preview_renderer(self):
        self.catalog().prefetch_sources(["test:icon-0"])
        digest = self.entry["icons"]["icon-0"]
        path = self.root / "iconify" / "sources" / (digest + ".json")
        source = json.loads(path.read_bytes())
        source["body"] = '<path fill="#00ff00" d="M0 0h1v10z"/>'
        path.write_bytes(C._json_bytes(source))
        service = self.catalog(fetch=self.no_fetch)
        with self.assertRaises(C.CatalogError):
            service.preview_glyph("test:icon-0", 32, offline=True)
        self.assertFalse(self.rasters)
        self.assertFalse((self.root / "iconify" / "assets").exists())

    def test_tampered_remote_paint_is_rejected_before_preview_renderer(self):
        self.data["icons"]["icon-0"]["body"] = '<path fill="#00ff00" d="M0 0h1v10z"/>'
        service = self.catalog()
        with self.assertRaises(C.CatalogError) as failure:
            service.preview_glyph("test:icon-0", 32)
        self.assertEqual(failure.exception.code, "license_unverified")
        self.assertFalse(self.rasters)

    def test_failed_fingerprint_is_not_downloaded_again_after_batch(self):
        self.data["icons"]["icon-0"]["body"] = '<path fill="#00ff00" d="M0 0h1v10z"/>'
        service = self.catalog()
        with self.assertRaises(C.CatalogError) as refused:
            service.prefetch_sources(["test:icon-0", "test:icon-1"])
        self.assertEqual(refused.exception.code, "license_unverified")
        self.assertEqual(len(self.calls), 2)
        self.assertIsNotNone(service.icon_data("test:icon-1", offline=True))
        service.preview_glyph("test:icon-1", 32)
        rasters = list(self.rasters)
        for operation in (lambda: service.prefetch_sources(["test:icon-0"]),
                          lambda: service.icon_data("test:icon-0"),
                          lambda: service.preview_glyph("test:icon-0", 32),
                          lambda: service.prepare("test:icon-0")):
            with self.assertRaises(C.CatalogError) as error:
                operation()
            self.assertEqual(error.exception.code, "license_unverified")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.rasters, rasters)

    def test_missing_icon_refusal_keeps_other_batch_sources_and_prevents_retry(self):
        del self.data["icons"]["icon-0"]
        service = self.catalog()
        with self.assertRaises(C.CatalogError) as refused:
            service.prefetch_sources(["test:icon-0", "test:icon-1"])
        self.assertEqual(refused.exception.code, "license_unverified")
        self.assertIsNotNone(service.icon_data("test:icon-1", offline=True))
        before = len(self.calls)
        with self.assertRaises(C.CatalogError):
            service.icon_data("test:icon-0")
        self.assertEqual(len(self.calls), before)

    def test_http_404_source_refusal_is_cached_for_this_session(self):
        def fetch(url):
            if "/test.json?" in url:
                self.calls.append(url)
                raise urllib.error.HTTPError(url, 404, "missing", {}, None)
            return self.fetch(url)
        service = self.catalog(fetch=fetch)
        for _ in range(2):
            with self.assertRaises(C.CatalogError) as refused:
                service.prefetch_sources(["test:icon-0"])
            self.assertEqual(refused.exception.code, "license_unverified")
        self.assertEqual(len(self.calls), 2)

    def test_live_license_refusal_does_not_repeat_metadata_or_source_requests(self):
        self.info["license"]["spdx"] = "CC-BY-NC-4.0"
        service = self.catalog()
        for _ in range(2):
            with self.assertRaises(C.CatalogError) as refused:
                service.prefetch_sources(["test:icon-0"])
            self.assertEqual(refused.exception.code, "license_unverified")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("/collections?", self.calls[0])

    def test_unsupported_svg_is_not_rendered_again_at_another_size_or_as_full_asset(self):
        service = self.catalog()
        attempted = []
        def unsupported(svg, *, raster_size):
            attempted.append(raster_size)
            raise ValueError("Unsupported SVG element")
        service._preview_factory = unsupported
        for size in (32, 80, 32):
            with self.assertRaises(C.CatalogError) as refused:
                service.preview_glyph("test:icon-0", size)
            self.assertEqual(refused.exception.code, "unsupported_svg")
        with self.assertRaises(C.CatalogError) as refused:
            service.prepare("test:icon-0")
        self.assertEqual(refused.exception.code, "unsupported_svg")
        self.assertEqual(attempted, [128])
        self.assertEqual(len(self.calls), 2)

    def test_temporary_offline_and_rate_limit_never_poison_source_cache(self):
        for kind in ("offline", "rate_limit", "api_error"):
            with self.subTest(kind=kind):
                def fail(url):
                    if kind == "offline":
                        raise OSError("offline")
                    raise urllib.error.HTTPError(url, 429 if kind == "rate_limit" else 503,
                                                 "temporary", {"Retry-After": "1"}, None)
                service = self.catalog(fetch=fail)
                service.transient = True
                with self.assertRaises(C.CatalogError) as refused:
                    service.prefetch_sources(["test:icon-0"])
                self.assertEqual(refused.exception.code, kind)
                self.assertFalse(service._source_failures)
                service._clock = lambda: 1236.0
                service._fetch = self.fetch
                service.prefetch_sources(["test:icon-0"])
                self.assertIsNotNone(service.icon_data("test:icon-0", offline=True))

    def test_negative_cache_is_bounded_lru_and_cleared_with_window(self):
        service = self.catalog()
        with patch.object(C, "NEGATIVE_CACHE_LIMIT", 3):
            for number in range(3):
                service._remember_source_failure(f"test:icon-{number}",
                                                C.CatalogError("changed", "license_unverified"))
            self.assertIsNotNone(service._source_failure("test:icon-0"))
            service._remember_source_failure("test:icon-3", C.CatalogError("unsafe", "unsupported_svg"))
        self.assertEqual(len(service._source_failures), 3)
        self.assertIsNone(service._source_failure("test:icon-1"))
        self.assertEqual(service._source_failure("test:icon-3").code, "unsupported_svg")
        service.clear_browse_cache(set())
        self.assertFalse(service._source_failures)

    def test_batch_reports_transient_failure_even_when_another_source_fails_verification(self):
        service = self.catalog()
        service._collections["other"] = copy.deepcopy(self.entry)
        changed = copy.deepcopy(self.data)
        changed["icons"]["icon-0"]["body"] = '<path fill="#00ff00" d="M0 0h1v10z"/>'
        def fetch(url):
            path = urllib.parse.urlsplit(url).path
            if path == "/collections":
                return {"test": copy.deepcopy(self.info), "other": copy.deepcopy(self.info)}
            if path == "/test.json":
                return changed
            if path == "/other.json":
                raise OSError("offline")
            raise AssertionError(url)
        service._fetch = fetch
        with self.assertRaises(C.CatalogError) as error:
            service.prefetch_sources(["test:icon-0", "other:icon-0"])
        self.assertEqual(error.exception.code, "offline")
        self.assertEqual(service._source_failure("test:icon-0").code, "license_unverified")
        self.assertIsNone(service._source_failure("other:icon-0"))

    def test_source_batch_performs_one_directory_pruning_pass(self):
        service = self.catalog()
        original = Path.glob
        scans = []
        def observed(path, pattern, *args, **kwargs):
            if path == self.root / "iconify" / "sources" and pattern == "*.json":
                scans.append(path)
            return original(path, pattern, *args, **kwargs)
        with patch.object(Path, "glob", observed):
            service.prefetch_sources(["test:icon-" + str(i) for i in range(32)])
        self.assertEqual(len(scans), 1)

    def test_builtin_profile_wraps_small_preview_without_png_interface(self):
        self.data = {"prefix": "lucide", "width": 24, "height": 24,
                     "icons": {"mouse": {"body": '<path fill="currentColor" d="M4 4h16v16H4z"/>'}}}
        self.entry["icons"] = {"mouse": C._hash(C.canonical_icon(self.data, "mouse"))}
        self.registry["collections"] = {"lucide": self.entry}
        def fetch(url):
            self.calls.append(url)
            return {"lucide": copy.deepcopy(self.info)} if "/collections?" in url else copy.deepcopy(self.data)
        service = self.catalog(fetch=fetch)
        def monochrome(svg, *, raster_size):
            glyph = self.preview_factory(svg, raster_size=raster_size)
            glyph.is_colored = False
            glyph.color_mode = "monochrome"
            return glyph
        service._preview_factory = monochrome
        service.prefetch_sources(["lucide:mouse"])
        glyph = service.preview_glyph("lucide:mouse", 32)
        self.assertTrue(glyph.uses_builtin_profile)
        self.assertGreater(glyph.memory_bytes, 0)
        self.assertEqual(glyph.render(32, (255, 255, 255)).size, (32, 32))
        self.assertIs(service.preview_glyph("lucide:mouse", 32), glyph)
        self.assertEqual(self.rasters, [128])
        self.assertFalse((self.root / "iconify" / "assets").exists())

    def test_preview_lru_obeys_32_mib_budget(self):
        self.preview_memory = 20 * 1024 * 1024
        service = self.catalog()
        service.prefetch_sources(["test:icon-0", "test:icon-1"])
        service.preview_glyph("test:icon-0", 32)
        service.preview_glyph("test:icon-1", 32)
        self.assertEqual(len(service._preview_glyphs), 1)
        self.assertLessEqual(sum(g.memory_bytes for g in service._preview_glyphs.values()), 32 * 1024 * 1024)
        service.preview_glyph("test:icon-0", 32)
        self.assertEqual(len(self.rasters), 3)
        self.assertEqual(len(self.calls), 2)

    def test_source_disk_cache_obeys_file_and_byte_bounds(self):
        service = self.catalog()
        directory = self.root / "iconify" / "sources"
        directory.mkdir(parents=True)
        for index in range(514):
            (directory / (str(index).zfill(64) + ".json")).write_bytes(b"{}")
        icon = C.canonical_icon(self.data, "icon-0")
        service._persist_sources([(self.entry["icons"]["icon-0"], icon)])
        self.assertLessEqual(len(list(directory.glob("*.json"))), 512)
        # A few large stale cache files also count toward the on-disk budget.
        for index in range(17):
            (directory / ("large-" + str(index) + ".json")).write_bytes(b"x" * (1024 * 1024))
        service._persist_sources([(self.entry["icons"]["icon-0"], icon)])
        files = list(directory.glob("*.json"))
        self.assertLessEqual(len(files), 512)
        self.assertLessEqual(sum(path.stat().st_size for path in files), 16 * 1024 * 1024)

    def test_cancellation_prevents_source_fetch_and_preview_work(self):
        cancel = threading.Event()
        cancel.set()
        service = self.catalog()
        with self.assertRaises(C.CatalogCancelled):
            service.prefetch_sources(["test:icon-0"], cancel=cancel)
        with self.assertRaises(C.CatalogCancelled):
            service.icon_data("test:icon-0", cancel=cancel)
        self.assertFalse(self.calls)
        self.assertFalse(self.rasters)

    def test_canceled_request_cannot_return_cached_preview(self):
        service = self.catalog()
        service.preview_glyph("test:icon-0", 32)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(C.CatalogCancelled):
            service.preview_glyph("test:icon-0", 32, cancel=cancel)


if __name__ == "__main__":
    unittest.main()
