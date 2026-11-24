"""The tray owns selected pixels, not retired catalogue/Settings caches."""
import gc
from pathlib import Path
import tempfile
import unittest
import weakref
from unittest.mock import patch

from PIL import Image

from halo import iconify_catalog, svg_glyph
from halo.settings_model import SettingsController
from test_hide_rename import HideRenameTestCase, dev, make_app


class SettingsCatalogLifetimeTests(HideRenameTestCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.app = make_app({"custom_icons": {}})
        self.app.hid_results, self.app.bt_cache = [dev()], []
        self.model = SettingsController(self.app)
        self.model.context["data_dir"] = temporary.name
        self.model.context["save_config"] = lambda cfg: True
        self.model._redraw = lambda: None
        source = {"prefix": "test", "icons": {"mouse": {"body": '<path fill="currentColor" d="M1 1h10v10H1z"/>'}}}
        entry = dict(name="Fixture", author={"name": "Fixture artist"},
                     license={"spdx": "MIT", "title": "MIT"}, license_text="Fixture notice",
                     icons={"mouse": iconify_catalog._hash(iconify_catalog.canonical_icon(source, "mouse"))})
        registry = {"schema": 1, "collections": {"test": entry}}
        store = iconify_catalog.IconifyCatalog(temporary.name, registry=registry,
            fetch=lambda url: source if "/test.json" in url else {"test": entry},
            glyph_factory=lambda svg: svg_glyph.prepare_glyph(svg, rasterizer=self.rasterize))
        self.asset = store.prepare("test:mouse")
        store.persist_selection(self.asset)
        self.references = []
        constructor = iconify_catalog.IconifyCatalog

        def record(*args, **kwargs):
            result = constructor(*args, **kwargs)
            self.references.append(weakref.ref(result))
            return result

        self.factory = patch.object(iconify_catalog, "IconifyCatalog", side_effect=record)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        enabled = gc.isenabled()
        gc.disable()
        self.addCleanup(lambda: gc.enable() if enabled else None)

    @staticmethod
    def rasterize(svg, size):
        image = Image.new("L", (size, size))
        image.paste(255, (size // 3, size // 3, 2 * size // 3, 2 * size // 3))
        return image

    def assert_stores_released(self):
        self.assertTrue(self.references)
        self.assertTrue(all(reference() is None for reference in self.references),
                        "A completed asset operation retains its catalogue without cyclic GC")

    def test_apply_restore_cycles_do_not_retain_store_or_previous_selected_glyph(self):
        for _ in range(8):
            self.assertTrue(self.model.apply_custom_icon(dev().key, self.asset))
            self.assertEqual(set(self.app._custom_assets), {self.asset.id})
            self.assert_stores_released()
            self.assertTrue(self.model.apply_custom_icon(dev().key, None))
            self.assertEqual(self.app._custom_assets, {})
        self.assertEqual(len(self.references), 8)

    def test_startup_releases_its_store_but_keeps_the_verified_offline_selection(self):
        self.app.cfg["custom_icons"] = {dev().key: self.asset.id}
        self.model.load_custom_icons()
        self.assert_stores_released()
        selected = self.app._custom_assets[self.asset.id]
        self.assertEqual(selected.glyph.png_bytes, self.asset.glyph.png_bytes)
        self.assertEqual(self.app._custom_icon_errors, [])

    def test_failed_pin_releases_store_and_does_not_change_configuration(self):
        # Corrupt durable pixels after preparation: the normal offline
        # loader must refuse the pin, even though this caller has a glyph.
        path = Path(self.model.context["data_dir"]) / "iconify" / "assets" / self.asset.id / "glyph.png"
        path.write_bytes(b"invalid PNG")
        with self.assertRaises(iconify_catalog.CatalogError):
            self.model.apply_custom_icon(dev().key, self.asset)
        self.assert_stores_released()
        self.assertEqual(self.app.cfg["custom_icons"], {})

    def test_closing_settings_releases_preview_frames_and_next_preview_still_renders(self):
        first = self.model.preview(dev().key)
        frame = weakref.ref(self.model._preview_art[0])
        self.model.release_preview()
        self.assertIsNone(frame())
        self.assertIsNone(self.model._preview_state)
        self.assertIsNone(self.model._preview_background)
        self.assertEqual(self.model.preview(dev().key).tobytes(), first.tobytes())

    def test_upgrade_bindings_detect_restore_and_reapply_of_the_same_icon(self):
        self.model.apply_custom_icon(dev().key, self.asset)
        before = self.model.custom_icon_bindings(self.asset.icon_id)
        self.model.apply_custom_icon(dev().key, None)
        self.assertEqual(self.model.custom_icon_bindings(self.asset.icon_id), {})
        self.model.apply_custom_icon(dev().key, self.asset)
        after = self.model.custom_icon_bindings(self.asset.icon_id)
        self.assertEqual(before[dev().key][0], after[dev().key][0])
        self.assertGreater(after[dev().key][1], before[dev().key][1])


if __name__ == "__main__":
    unittest.main()
