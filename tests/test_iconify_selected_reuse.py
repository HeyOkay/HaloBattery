"""Selected masters can seed a new browse window without bypassing review."""
import copy
import tempfile
import threading
from types import SimpleNamespace
import unittest
import urllib.parse
from unittest.mock import patch

from PIL import Image, ImageDraw

from halo import iconify_catalog as catalog
from halo import svg_glyph
from halo.settings_model import SettingsController


class SizedGlyph:
    """Exercise the declared RAM budget without allocating large pixel buffers."""
    def __init__(self, glyph, memory_bytes):
        self.glyph = glyph
        self.memory_bytes = memory_bytes

    def __getattr__(self, name):
        return getattr(self.glyph, name)


class SelectedReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name
        self.catalog_type = catalog.IconifyCatalog
        self.data = {"prefix": "test", "width": 24, "height": 24,
                     "icons": {"rocket": {"body": '<path fill="currentColor" d="M4 4h16v16H4z"/>'}}}
        self.info = {"name": "Fixture icons", "author": {"name": "Fixture Artist"},
                     "license": {"title": "MIT", "spdx": "MIT",
                                 "url": "https://example.org/LICENSE"}}
        self.registry = {"schema": 1, "iconify_revision": "a" * 40,
                         "collections": {"test": {
                             **copy.deepcopy(self.info),
                             "license_text": "Copyright Fixture Artist\nFixture MIT notice",
                             "notices": [{"name": "NOTICE", "text": "Keep the fixture notice"}],
                             "provenance": {"upstream_revision": "b" * 40},
                             "current_policy": {"selection": "Reviewed fixture artwork"},
                             "icons": {"rocket": catalog._hash(
                                 catalog.canonical_icon(self.data, "rocket"))}}}}
        self.fetches = []
        self.renders = []
        self.app = SimpleNamespace(lock=threading.Lock(), _custom_assets={},
                                   settings_context=lambda: {"data_dir": self.directory})
        self.controller = SettingsController(self.app)
        constructor = patch.object(catalog, "IconifyCatalog", self.browse_factory)
        constructor.start()
        self.addCleanup(constructor.stop)

    def fetch(self, url):
        self.fetches.append(url)
        route = urllib.parse.urlsplit(url).path
        if route == "/collections":
            return {"test": copy.deepcopy(self.info)}
        if route == "/test.json":
            return copy.deepcopy(self.data)
        self.fail("Unexpected offline fixture request: " + url)

    @staticmethod
    def rasterize(markup, size):
        image = Image.new("L", (size, size), 0)
        ImageDraw.Draw(image).ellipse((size // 4, size // 4, 3 * size // 4, 3 * size // 4),
                                      fill=255)
        return image

    def glyph_factory(self, svg):
        self.renders.append(svg)
        return svg_glyph.prepare_glyph(svg, rasterizer=self.rasterize)

    def make_catalog(self):
        return self.catalog_type(self.directory, registry=copy.deepcopy(self.registry),
                                 fetch=self.fetch, glyph_factory=self.glyph_factory,
                                 transient=True)

    def browse_factory(self, directory, *, transient):
        self.assertEqual(directory, self.directory)
        self.assertTrue(transient)
        return self.make_catalog()

    def select(self):
        initial = self.make_catalog()
        asset = initial.prepare("test:rocket")
        initial.persist_selection(asset)
        initial.pin(asset.id)
        self.app._custom_assets = {asset.id: asset}
        initial.clear_browse_cache({asset.id})
        self.assertFalse((initial.root / "lookups").exists())
        return asset

    def test_fresh_browse_windows_reuse_current_master_after_lookup_cleanup(self):
        selected = self.select()
        selected_map = self.app._custom_assets
        self.fetches.clear()
        for _ in range(4):
            browse = self.controller.create_browse_catalog()
            self.assertIs(browse.cached("test:rocket"), selected)
            self.assertIs(browse.prepare("test:rocket"), selected)
            browse.clear_browse_cache({selected.id})
        self.assertEqual(len(self.renders), 1)
        self.assertEqual(self.fetches, [])
        self.assertIs(self.app._custom_assets, selected_map)
        self.assertIs(selected_map[selected.id], selected)

    def test_changed_reviewed_digest_requires_verified_new_source(self):
        selected = self.select()
        self.data["icons"]["rocket"]["body"] = '<path fill="currentColor" d="M6 2h12v20H6z"/>'
        digest = catalog._hash(catalog.canonical_icon(self.data, "rocket"))
        self.registry["collections"]["test"]["icons"]["rocket"] = digest
        self.fetches.clear()
        browse = self.controller.create_browse_catalog()
        self.assertIsNone(browse.cached("test:rocket"))
        replacement = browse.prepare("test:rocket")
        self.assertIsNot(replacement, selected)
        self.assertNotEqual(replacement.id, selected.id)
        self.assertEqual(replacement.metadata["source_hash"], digest)
        self.assertEqual(len(self.renders), 2)
        self.assertTrue(any("/test.json?" in url for url in self.fetches))
        self.assertIs(self.app._custom_assets[selected.id], selected)

    def test_seeded_art_cannot_hide_source_that_fails_current_review(self):
        selected = self.select()
        self.registry["collections"]["test"]["icons"]["rocket"] = "f" * 64
        browse = self.controller.create_browse_catalog()
        with self.assertRaises(catalog.CatalogError) as error:
            browse.prepare("test:rocket")
        self.assertEqual(error.exception.code, "license_unverified")
        self.assertEqual(len(self.renders), 1)
        self.assertIs(self.app._custom_assets[selected.id], selected)

    def test_changed_selection_evidence_is_rechecked_before_reuse(self):
        selected = self.select()
        original = copy.deepcopy(self.registry)
        changes = {
            "name": "Updated fixture collection",
            "author": {"name": "Updated Fixture Artist"},
            "license": {"title": "MIT", "spdx": "MIT", "url": "https://example.org/updated-license"},
            "license_text": "Copyright Fixture Artist\nUpdated reviewed notice",
            "notices": [{"name": "NOTICE", "text": "Updated upstream notice"}],
            "provenance": {"upstream_revision": "c" * 40},
            "current_policy": {"selection": "Updated review policy"},
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                self.registry = copy.deepcopy(original)
                self.registry["collections"]["test"][field] = value
                entry = self.registry["collections"]["test"]
                self.info = {key: copy.deepcopy(entry[key]) for key in ("name", "author", "license")}
                browse = self.controller.create_browse_catalog()
                self.assertIsNone(browse.cached("test:rocket"))
                before = len(self.renders)
                replacement = browse.prepare("test:rocket")
                self.assertEqual(len(self.renders), before + 1)
                self.assertNotEqual(replacement.id, selected.id)
                self.assertEqual(replacement.metadata["collection" if field == "name" else field], value)
                browse.clear_browse_cache({selected.id})
        self.assertIs(self.app._custom_assets[selected.id], selected)

    def test_selected_map_is_snapshotted_under_the_app_lock(self):
        selected = self.select()
        case = self

        class ProtectedSelections(dict):
            def values(self):
                case.assertTrue(case.app.lock.locked())
                return super().values()

        assets = ProtectedSelections(self.app._custom_assets)
        self.app._custom_assets = assets
        browse = self.controller.create_browse_catalog()
        self.assertIs(browse.prepare("test:rocket"), selected)
        self.assertIs(self.app._custom_assets, assets)

    def test_previous_renderer_still_upgrades_a_seeded_selection(self):
        with patch.object(svg_glyph, "RENDERER_VERSION", svg_glyph.PREVIOUS_RENDERER_VERSION):
            selected = self.select()
        self.assertTrue(self.catalog_type.needs_upgrade(selected))
        browse = self.controller.create_browse_catalog()
        self.assertIs(browse.cached("test:rocket"), selected)
        replacement = browse.prepare("test:rocket")
        self.assertIsNot(replacement, selected)
        self.assertEqual(replacement.metadata["renderer_revision"], svg_glyph.RENDERER_VERSION)
        self.assertFalse(browse.needs_upgrade(replacement))
        self.assertEqual(len(self.renders), 2)
        self.assertIs(self.app._custom_assets[selected.id], selected)

    def test_closing_browse_keeps_app_selection_and_offline_master_usable(self):
        selected = self.select()
        selected_map = self.app._custom_assets
        browse = self.controller.create_browse_catalog()
        browse.warm_name_index()
        browse.clear_browse_cache({selected.id})
        self.assertIsNone(browse._name_blob)
        self.assertIs(self.app._custom_assets, selected_map)
        self.assertIs(selected_map[selected.id], selected)
        image = selected.glyph.render(24, (255, 255, 255))
        self.assertIsNotNone(image.getbbox())
        image.close()
        offline = self.catalog_type(self.directory,
                                    fetch=lambda url: self.fail("Selected load used network"))
        loaded = offline.load(selected.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.glyph.png_bytes, selected.glyph.png_bytes)
        self.assertNotIn("registry", offline.__dict__)

    def selected_copies(self, selected, count, *, memory_bytes=None):
        entry = self.registry["collections"]["test"]
        for index in range(count):
            name = "rocket-" + str(index)
            self.data["icons"][name] = copy.deepcopy(self.data["icons"]["rocket"])
            entry["icons"][name] = selected.metadata["source_hash"]
        verifier = self.make_catalog()
        glyph = selected.glyph if memory_bytes is None else SizedGlyph(selected.glyph, memory_bytes)
        assets = {}
        for index in range(count):
            icon_id = "test:rocket-" + str(index)
            metadata = copy.deepcopy(selected.metadata)
            metadata.update(icon_id=icon_id,
                            source_url="https://icon-sets.iconify.design/test/rocket-" + str(index) + "/",
                            selection_key=verifier._selection_key(icon_id, metadata["renderer_revision"]))
            asset_id = metadata["asset_id"] = catalog._selection_id(metadata)
            assets[asset_id] = catalog.IconifyAsset(asset_id, icon_id, glyph, metadata)
        return assets

    def test_seeding_many_selections_preserves_count_budget_and_app_map(self):
        selected = self.select()
        assets = self.selected_copies(selected, catalog.RAM_CACHE_LIMIT + 5)
        self.app._custom_assets = assets
        browse = self.controller.create_browse_catalog()
        self.assertEqual(len(browse._assets), catalog.RAM_CACHE_LIMIT)
        self.assertEqual(list(browse._assets), list(assets)[-catalog.RAM_CACHE_LIMIT:])
        self.assertEqual(len(assets), catalog.RAM_CACHE_LIMIT + 5)
        self.assertIs(self.app._custom_assets, assets)

    def test_seeding_preserves_byte_budget_without_large_fixture_buffers(self):
        selected = self.select()
        weight = catalog.RAM_CACHE_BYTES // 4
        assets = self.selected_copies(selected, 5, memory_bytes=weight)
        self.app._custom_assets = assets
        browse = self.controller.create_browse_catalog()
        self.assertEqual(len(browse._assets), 4)
        self.assertEqual(sum(asset.glyph.memory_bytes for asset in browse._assets.values()),
                         catalog.RAM_CACHE_BYTES)
        self.assertEqual(list(browse._assets), list(assets)[-4:])
        self.assertEqual(len(assets), 5)
        self.assertIs(self.app._custom_assets, assets)


if __name__ == "__main__":
    unittest.main()
