"""Offline catalog verification, cache, license and paging regression tests."""
import copy
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

from PIL import Image, ImageDraw
from halo import iconify_catalog as catalog
from halo.svg_glyph import CustomGlyph


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.time = 1000.0
        self.data = {"prefix": "test", "width": 24, "height": 24,
                     "icons": {f"icon-{i}": {"body": f'<path d="M0 0h{i + 1}v10z"/>'}
                               for i in range(100)}}
        self.info = {"name": "Test icons", "author": {"name": "Test Artist", "url": "https://example.org"},
                     "license": {"title": "MIT", "spdx": "MIT", "url": "https://example.org/LICENSE"}}
        self.entry = {**copy.deepcopy(self.info), "license_text": "Copyright Test Artist\nFULL MIT NOTICE",
                      "notices": [{"name": "NOTICE", "text": "Keep this actual notice"}],
                      "provenance": {"upstream_revision": "a" * 40},
                      "icons": {name: catalog._hash(catalog.canonical_icon(self.data, name))
                                for name in self.data["icons"]}}
        self.registry = {"schema": 1, "iconify_revision": "b" * 40, "collections": {"test": self.entry}}
        self.calls = []
        self.rendered = []
        image = Image.new("L", (1024, 1024))
        ImageDraw.Draw(image).rectangle((504, 504, 519, 519), fill=255)
        buffer = io.BytesIO()
        image.save(buffer, "PNG")
        self.png = buffer.getvalue()

    def fetch(self, url):
        self.calls.append(url)
        parsed = urllib.parse.urlsplit(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "api.iconify.design")
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/collections":
            return {"test": copy.deepcopy(self.info)}
        if parsed.path == "/search":
            limit = int(params["limit"][0])
            return {"icons": ["test:" + name for name in list(self.data["icons"])[:limit]],
                    "collections": {"test": copy.deepcopy(self.info)}, "limit": limit}
        if parsed.path == "/test.json":
            # Same response shape as the API: requested names plus parent aliases.
            return copy.deepcopy(self.data)
        raise AssertionError(url)

    def glyph_factory(self, svg):
        self.rendered.append(svg)
        return CustomGlyph.from_png(self.png)

    def make(self, **kwargs):
        return catalog.IconifyCatalog(self.temp.name, fetch=kwargs.get("fetch", self.fetch),
                                      registry=kwargs.get("registry", self.registry),
                                      glyph_factory=self.glyph_factory, clock=lambda: self.time,
                                      transient=kwargs.get("transient", False))

    def test_registry_expands_audited_sets_and_preserves_actual_notices(self):
        service = catalog.IconifyCatalog(self.temp.name, fetch=lambda url: self.fail("network"))
        self.assertTrue({"lucide", "tabler", "material-symbols", "rivet-icons", "temaki", "codicon"} <= set(service._collections))
        self.assertGreater(len(service.collections()), 100)
        self.assertGreater(sum(item["total"] for item in service.collections()), 300000)
        self.assertIn("Cole Bemis", service._collections["lucide"]["license_text"])
        for entry in service._collections.values():
            self.assertEqual(len(entry["provenance"]["upstream_revision"]), 40)
            self.assertTrue(entry["license_text"].strip())
            self.assertGreater(len(entry["license_text"]) + sum(len(notice["text"]) for notice in entry["notices"]), 500)
        for icon_id in ("lucide:bluetooth", "tabler:brand-github", "fa7-brands:github"):
            self.assertEqual(service._entry(icon_id)[0], icon_id.split(":")[0])
        # This source has no visible artwork, independent of its name.
        with self.assertRaises(catalog.CatalogError):
            service._entry("codicon:blank")

    def test_expansion_records_actual_artwork_scope_and_all_exclusions(self):
        service = catalog.IconifyCatalog(self.temp.name, fetch=lambda url: self.fail("network"))
        data = Path(catalog.__file__).with_name("iconify_data")
        report = json.loads((data / "audit_report.json").read_text(encoding="utf-8"))
        self.assertEqual({prefix for prefix, info in report.items() if info["status"] == "verified"}, set(service._collections))
        self.assertIn("commercial", report["gg"]["reason"])
        self.assertEqual(report["simple-icons"]["status"], "verified")
        self.assertEqual(service._entry("simple-icons:markdown")[2]["license"]["spdx"], "CC0-1.0")
        self.assertEqual(report["vscode-icons"]["status"], "excluded")
        self.assertIn("CC-BY-SA", report["vscode-icons"]["reason"])
        self.assertEqual(report["thesvg"]["status"], "excluded")
        self.assertIn("per-icon", report["thesvg"]["reason"])
        for prefix, original, actual in (("healthicons", "MIT", "CC0-1.0"),
                                          ("fxemoji", "Apache-2.0", "CC-BY-4.0"),
                                          ("la", "Apache-2.0", "MIT")):
            entry = service._collections[prefix]
            self.assertEqual(entry["api_license"]["spdx"], original)
            self.assertEqual(entry["license"]["spdx"], actual)
        self.assertTrue(any("Bowtie" in notice["text"] for notice in service._collections["andrsrxn"]["notices"]))
        for icon_id in ("nrk:nrk-logo-nrk", "nrk:nrk-super-logo"):
            with self.assertRaises(catalog.CatalogError):
                service._entry(icon_id)

    def test_expansion_keeps_original_proof_metadata_and_fingerprints(self):
        service = catalog.IconifyCatalog(self.temp.name, fetch=lambda url: self.fail("network"))
        data = Path(catalog.__file__).with_name("iconify_data")
        config = json.loads((data / "audit_sources.json").read_text(encoding="utf-8"))
        original_hashes = {
            "codicon": "2a30d0052e9dc38ad7d25e4017402b0b031a772837cdbfd7962e7f967cb72b1b",
            "lucide": "3e3fef4ce79454321f058990275831d9c7282989dae7ddb46985fd8e15775ff2",
            "material-symbols": "cdda28024299632f89868f211e56cf87cb374ea9b969b765d00cd6c93dfb4126",
            "rivet-icons": "0080cd8f33783f188985e021e6b31101ef5270066dfce0a5031e8a192b6561b0",
            "tabler": "b4c31b2001be5deb0cb06f7e801a12d5959db8a6d4c33456f2f97d68a96fcb13",
            "temaki": "1ccf7ec7e37e9112f137e94c4727d096bf29f1d2ff8303fc27d3f0bda5d243ad"}
        for prefix, expected in original_hashes.items():
            entry = service._collections[prefix]
            proof = {key: value for key, value in entry.items()
                     if key not in ("icons_manifest", "icons_sha256", "icons_count", "current_policy")}
            self.assertEqual(proof, config["baseline"][prefix])
            # Historic exclusions describe the old subset only. Every old
            # selectable name keeps its exact digest; added names do not remove it.
            old_excluded = set(config["baseline"][prefix]["provenance"]["excluded_names"])
            prior_subset = {name: digest for name, digest in service._icon_index(prefix, entry).items()
                            if name not in old_excluded}
            self.assertEqual(catalog._hash(prior_subset), expected)

    def test_license_only_policy_keeps_brand_names_and_historical_proofs(self):
        import gzip
        service = catalog.IconifyCatalog(self.temp.name, fetch=lambda url: self.fail("network"))
        data = Path(catalog.__file__).with_name("iconify_data")
        history = json.loads(gzip.decompress((data / "historical_metadata.json.gz").read_bytes()))
        for prefix, before in history["collections"].items():
            after = service._collections[prefix]
            ignored = {"icons_manifest", "icons_sha256", "icons_count", "current_policy"}
            self.assertEqual({k: v for k, v in before.items() if k not in ignored},
                             {k: v for k, v in after.items() if k not in ignored})
        for icon_id in ("lucide:bluetooth", "tabler:brand-github", "fa7-brands:github",
                        "fa6-brands:github", "logos:github-icon", "cib:bluetooth"):
            with self.subTest(icon_id=icon_id):
                prefix, name, entry = service._entry(icon_id)
                self.assertEqual(len(entry["icons"][name]), 64)
                self.assertIn("copyright license", entry["current_policy"]["selection"])
        updater = (Path(catalog.__file__).resolve().parents[1] / "tools" / "update_iconify_registry.py").read_text(encoding="utf-8")
        self.assertNotIn("EXCLUDED_PREFIXES", updater)
        self.assertNotIn("EXCLUDED_NAMES", updater)
        self.assertNotIn('"bluetooth" in name', updater)
        self.assertNotIn('data.get("categories"', updater)

    def test_complete_numbered_pages_exceed_public_api_cap_without_svg_downloads(self):
        self.entry["icons"].update({f"icon-{number}": "a" * 64 for number in range(100, 1350)})
        service = self.make(fetch=lambda url: self.fail("Local name search used network"))
        first = service.search("  ICON  ")
        self.assertEqual(len(first["items"]), 50)
        self.assertEqual(first["next_start"], 50)
        self.assertEqual(first["total"], 1350)
        self.assertTrue(first["total_exact"])
        self.assertEqual(first["page_count"], 27)
        self.assertEqual(first["page_size"], 50)
        self.assertEqual(len(first["next_ids"]), 50)
        self.assertEqual(first["items"][0]["author"], "Test Artist")
        ids = []
        for page in range(27):
            result = service.search("icon", start=page * 50)
            self.assertTrue(result["cached"])
            self.assertEqual(result["page"], page + 1)
            self.assertEqual(result["has_more"], page < 26)
            ids.extend(item["id"] for item in result["items"])
        self.assertEqual(len(set(ids)), 1350)
        self.assertEqual(first["next_ids"], ids[50:100])
        last = service.search("icon", start=1300)
        self.assertIsNone(last["next_start"])
        beyond = service.search("icon", start=999999)
        self.assertEqual(beyond["items"], [])
        self.assertFalse(beyond["has_more"])
        self.assertFalse(self.rendered)
        self.assertFalse((Path(self.temp.name) / "iconify").exists())

    def test_name_search_is_complete_on_first_offline_run_and_restart(self):
        def offline(url):
            raise OSError("offline")
        first = self.make(fetch=offline).search("icon")
        result = self.make(fetch=offline).search("icon", start=50)
        self.assertEqual(result["total"], 100)
        self.assertEqual(len(result["items"]), 50)
        self.assertFalse(result["has_more"])
        self.assertEqual(first["next_ids"], [item["id"] for item in result["items"]])
        self.assertEqual(result["search_mode"], "local")
        self.assertFalse(self.calls)

    def test_name_search_ranking_tokens_and_prefix_are_stable(self):
        names = ("home", "home-round", "homepage", "smart-home", "homemaker", "my-home-round")
        self.entry["icons"] = dict.fromkeys(names, "a" * 64)
        service = self.make(fetch=lambda url: self.fail("network"))
        result = service.search("home")
        self.assertEqual([item["name"] for item in result["items"]],
                         ["home", "home-round", "smart-home", "my-home-round", "homepage", "homemaker"])
        self.assertEqual([item["name"] for item in service.search("home round")["items"]],
                         ["home-round", "my-home-round"])
        self.assertEqual(service.search("test:home")["total"], 6)
        self.assertEqual(service.search("unverified:home")["total"], 0)
        self.assertEqual(service.search("home", collection="test")["total"], 6)

    def test_search_uses_approved_names_only_and_full_evidence_remains_required(self):
        service = self.make(fetch=lambda url: self.fail("Local search consulted API"))
        self.assertEqual(service.search("unknown")["items"], [])
        self.assertEqual(service.search("evil:icon")["items"], [])
        result = service.search("icon-0")
        self.assertEqual(result["items"][0]["id"], "test:icon-0")
        invalid = copy.deepcopy(self.registry)
        invalid["collections"]["test"]["license_text"] = ""
        with self.assertRaises(catalog.CatalogError):
            self.make(registry=invalid)

    def test_name_candidate_does_not_bypass_live_metadata_check_on_prepare(self):
        self.info["license"]["spdx"] = "CC-BY-NC-4.0"
        service = self.make()
        self.assertEqual(service.search("icon-0")["items"][0]["id"], "test:icon-0")
        with self.assertRaises(catalog.CatalogError) as error:
            service.prepare("test:icon-0")
        self.assertEqual(error.exception.code, "license_unverified")
        self.assertFalse(self.rendered)

    def test_invalid_request_types(self):
        service = self.make()
        for kwargs in ({"query": []}, {"query": ""}, {"query": "x", "start": True},
                       {"query": "x", "start": -1}, {"query": "x", "limit": False},
                       {"query": "x", "limit": 999}, {"query": "x", "collection": []},
                       {"query": "x", "collection": "unknown"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(catalog.CatalogError) as error:
                service.search(**kwargs)
            self.assertEqual(error.exception.code, "invalid_request")
        self.assertFalse(self.calls)

    def test_prepare_retains_actual_legal_text_and_provenance(self):
        asset = self.make().prepare("test:icon-0")
        self.assertEqual(len(asset.id), 64)
        self.assertEqual(asset.icon_id, "test:icon-0")
        self.assertIn("FULL MIT NOTICE", asset.metadata["license_text"])
        self.assertEqual(asset.metadata["notices"], self.entry["notices"])
        self.assertEqual(asset.metadata["provenance"], self.entry["provenance"])
        self.assertIn("rasterized", asset.metadata["modifications"])
        self.assertTrue(asset.metadata["acquired_utc"].endswith("+00:00"))
        self.assertEqual(len(self.rendered), 1)

    def test_asset_id_stable_offline_load_and_attribution_do_not_fetch(self):
        asset = self.make().prepare("test:icon-0")
        self.time += 100
        service = self.make(fetch=lambda url: self.fail("offline load fetched"))
        loaded = service.load(asset.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.glyph.digest, asset.glyph.digest)
        self.assertEqual(service.prepare("test:icon-0").id, asset.id)
        self.assertEqual(len(service.attributions([asset.id, asset.id])), 1)
        self.assertEqual(service.attributions(["../../oops"]), [])
        self.assertIsNone(service.load("../../oops"))
        self.assertIsNone(service.load("a" * 64))

    def test_tampered_body_is_rejected_before_renderer(self):
        self.data["icons"]["icon-0"]["body"] += '<script>bad()</script>'
        with self.assertRaises(catalog.CatalogError) as error:
            self.make().prepare("test:icon-0")
        self.assertEqual(error.exception.code, "license_unverified")
        self.assertFalse(self.rendered)

    def test_dimensions_and_transform_drift_also_fail_hash(self):
        for change in ({"width": 25}, {"hFlip": True}, {"rotate": 1}):
            with self.subTest(change=change):
                data = copy.deepcopy(self.data)
                data["icons"]["icon-0"].update(change)
                def fetch(url):
                    return data if "/test.json?" in url else self.fetch(url)
                with self.assertRaises(catalog.CatalogError):
                    self.make(fetch=fetch).prepare("test:icon-0")
        self.assertFalse(self.rendered)

    def test_unknown_set_and_name_rejected_without_network(self):
        service = self.make()
        for name in ("evil:icon-0", "test:unknown", "../test:icon-0", [], None):
            with self.subTest(name=name), self.assertRaises(catalog.CatalogError):
                service.prepare(name)
        self.assertFalse(self.calls)

    def test_live_author_or_license_change_rejected(self):
        for field in ("author", "license"):
            original = copy.deepcopy(self.info)
            self.info[field]["name" if field == "author" else "title"] = "Changed"
            with self.subTest(field=field), self.assertRaises(catalog.CatalogError):
                self.make().prepare("test:icon-0")
            self.info = original
        self.assertFalse(self.rendered)

    def test_png_corruption_and_metadata_tampering_fail_safely(self):
        asset = self.make().prepare("test:icon-0")
        folder = Path(self.temp.name) / "iconify" / "assets" / asset.id
        png = folder / "glyph.png"
        png.write_bytes(b"corrupt")
        self.assertIsNone(self.make().load(asset.id))
        png.write_bytes(asset.glyph.png_bytes)
        metadata = copy.deepcopy(asset.metadata)
        metadata["license_text"] = "Fake notice"
        (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        self.assertIsNone(self.make().load(asset.id))

    def test_search_ignores_legacy_capped_disk_cache_and_bounds_ram_queries(self):
        folder = Path(self.temp.name) / "iconify" / "browse"
        folder.mkdir(parents=True)
        (folder / "legacy.json").write_bytes(b"corrupt old 128-result cache")
        service = self.make(fetch=lambda url: self.fail("network"))
        self.assertEqual(service.search("icon")["total"], 100)
        for number in range(12):
            service.search("icon-" + str(number))
        self.assertLessEqual(len(service._searches), 8)
        self.assertEqual(len(list(folder.glob("*.json"))), 1)

    def test_transient_sources_are_ram_only_and_selected_asset_survives_cleanup(self):
        service = self.make(transient=True)
        service.prefetch_sources(["test:icon-0", "test:icon-1"])
        self.assertFalse((service.root / "sources").exists())
        self.assertEqual(service.icon_data("test:icon-0", offline=True), catalog.canonical_icon(self.data, "icon-0"))
        applied = service.prepare("test:icon-0")
        details = service.prepare("test:icon-1")
        self.assertFalse((service.root / "assets").exists())
        service.persist_selection(applied)
        service.pin(applied.id)
        for name in ("browse", "sources", "preview-pages"):
            folder = service.root / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "legacy.json").write_text("{}", encoding="utf-8")
        service.search("icon")
        service.clear_browse_cache({applied.id})
        self.assertIsNone(service._name_blob)
        self.assertFalse(service._raw_icons)
        self.assertFalse(service._searches)
        self.assertFalse(service._preview_glyphs)
        self.assertFalse((service.root / "assets" / details.id).exists())
        self.assertEqual(service._pinned, {applied.id})
        for name in ("browse", "sources", "preview-pages"):
            self.assertFalse((service.root / name).exists())
        restarted = self.make(fetch=lambda url: self.fail("network"))
        self.assertIsNotNone(restarted.load(applied.id))
        self.assertEqual(restarted._pinned, {applied.id})
        for filename in ("source.json", "metadata.json", "glyph.png"):
            self.assertTrue((service.root / "assets" / applied.id / filename).is_file())

    def test_old_session_cleanup_does_not_delete_new_session_details(self):
        old = self.make(transient=True)
        previous = old.prepare("test:icon-0")
        fresh = self.make(transient=True)
        details = fresh.prepare("test:icon-1")
        old.clear_browse_cache(set())
        self.assertFalse((old.root / "assets" / previous.id).exists())
        self.assertFalse((fresh.root / "assets" / details.id).exists())
        self.assertEqual(fresh.cached("test:icon-1").id, details.id)
        fresh.persist_selection(details)
        fresh.pin(details.id)
        old.clear_browse_cache(set())
        restarted = self.make(fetch=lambda url: self.fail("network"))
        self.assertIsNotNone(restarted.load(details.id))
        self.assertIn(details.id, restarted._pinned)

    def test_legacy_asset_migration_preserves_only_current_choices(self):
        service = self.make()
        selected = service.prepare("test:icon-0")
        previous = service.prepare("test:icon-1")
        service.pin(previous.id)
        service.purge_unselected_assets({selected.id})
        self.assertTrue((service.root / "assets" / selected.id).exists())
        self.assertFalse((service.root / "assets" / previous.id).exists())

    def test_cleanup_checks_resolved_path_before_recursive_delete(self):
        service = self.make(transient=True)
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external)
            payload = outside / "important.json"
            payload.write_text("keep", encoding="utf-8")
            from unittest.mock import Mock
            redirected = Mock()
            redirected.resolve.return_value = outside.resolve()
            with patch.object(catalog.shutil, "rmtree") as remove:
                service._remove_cache_folder(redirected)
                remove.assert_not_called()
            self.assertEqual(payload.read_text(encoding="utf-8"), "keep")

    def test_transient_ignores_old_unselected_source_cache(self):
        self.make().prefetch_sources(["test:icon-0"])
        service = self.make(transient=True, fetch=lambda url: self.fail("network"))
        with self.assertRaises(catalog.CatalogError) as error:
            service.icon_data("test:icon-0", offline=True)
        self.assertEqual(error.exception.code, "offline")

    def test_prefetch_parallel_groups_are_verified_and_bounded_to_four_http_requests(self):
        registry = copy.deepcopy(self.registry)
        registry["collections"] = {f"set-{number}": copy.deepcopy(self.entry) for number in range(8)}
        lock = threading.Lock()
        active = maximum = 0
        def fetch(url):
            nonlocal active, maximum
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            path = urllib.parse.urlsplit(url).path
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.015)
            try:
                if path == "/collections":
                    return ({params["prefix"][0]: copy.deepcopy(self.info)} if "prefix" in params else
                            {prefix: copy.deepcopy(self.info) for prefix in registry["collections"]})
                prefix = path[1:-5]
                return dict(copy.deepcopy(self.data), prefix=prefix)
            finally:
                with lock:
                    active -= 1
        service = self.make(registry=registry, fetch=fetch, transient=True)
        ids = [f"set-{number}:icon-0" for number in range(8)]
        service.prefetch_sources(ids)
        self.assertGreaterEqual(maximum, 2)
        self.assertLessEqual(maximum, 4)
        for icon_id in ids:
            self.assertEqual(service.icon_data(icon_id, offline=True), catalog.canonical_icon(self.data, "icon-0"))
        self.assertFalse((service.root / "sources").exists())

    def test_rate_limit_backoff_blocks_repeated_requests(self):
        def fetch(url):
            self.calls.append(url)
            raise urllib.error.HTTPError(url, 429, "limit", {"Retry-After": "60"}, None)
        service = self.make(fetch=fetch)
        with self.assertRaises(catalog.CatalogError):
            service.prepare("test:icon-0")
        with self.assertRaises(catalog.CatalogError) as error:
            service.prepare("test:icon-0")
        self.assertEqual(error.exception.code, "rate_limit")
        self.assertEqual(len(self.calls), 1)
        self.time += 61
        with self.assertRaises(catalog.CatalogError):
            service.prepare("test:icon-0")
        self.assertEqual(len(self.calls), 2)

    def test_cancellation_prevents_network_and_cache_writes(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(catalog.CatalogCancelled):
            self.make().search("mouse", cancel=cancel)
        with self.assertRaises(catalog.CatalogCancelled):
            self.make().prepare("test:icon-0", cancel=cancel.is_set)
        self.assertFalse(self.calls)
        self.assertFalse((Path(self.temp.name) / "iconify").exists())

    def test_prefetch_batches_and_prepare_reuses_data(self):
        service = self.make()
        ids = ["test:icon-" + str(i) for i in range(8)]
        service.prefetch(ids)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("icons=icon-0%2Cicon-1", self.calls[-1])
        for icon_id in ids:
            service.prepare(icon_id)
        self.assertEqual(len(self.calls), 2)

    def test_pinned_selection_survives_cache_eviction_and_restart(self):
        service = self.make()
        pinned = service.prepare("test:icon-0")
        service.pin(pinned.id)
        with patch.object(catalog, "ASSET_CACHE_LIMIT", 1), patch.object(catalog, "RAM_CACHE_LIMIT", 1):
            for number in range(1, 5):
                service.prepare("test:icon-" + str(number))
        folders = list((Path(self.temp.name) / "iconify" / "assets").iterdir())
        self.assertEqual(len(folders), 2)
        self.assertLessEqual(len(service._assets), 1)
        restarted = self.make(fetch=lambda url: self.fail("network"))
        self.assertIn(pinned.id, restarted._pinned)
        self.assertIsNotNone(restarted.load(pinned.id))
        restarted.prefetch(["test:icon-0"])
        self.assertEqual(restarted.prepare("test:icon-0").id, pinned.id)

    def test_cached_is_offline_only_and_handles_missing_invalid_names(self):
        original = self.make().prepare("test:icon-0")
        service = self.make(fetch=lambda url: self.fail("cached() queried network"))
        self.assertEqual(service.cached("test:icon-0").id, original.id)
        for icon_id in ("test:icon-1", "unknown:icon-0", "../icon", None):
            with self.subTest(icon_id=icon_id):
                self.assertIsNone(service.cached(icon_id))

    def test_byte_budget_prunes_unselected_assets_but_preserves_pin(self):
        service = self.make()
        pinned = service.prepare("test:icon-0")
        service.pin(pinned.id)
        with patch.object(catalog, "ASSET_CACHE_BYTES", 1):
            service.prepare("test:icon-1")
        self.assertEqual(len(list((Path(self.temp.name) / "iconify" / "assets").iterdir())), 1)
        self.assertIsNotNone(self.make().load(pinned.id))

    def test_unsupported_svg_has_specific_error_code(self):
        service = self.make()
        service._glyph_factory = lambda svg: (_ for _ in ()).throw(ValueError("unsupported"))
        with self.assertRaises(catalog.CatalogError) as error:
            service.prepare("test:icon-0")
        self.assertEqual(error.exception.code, "unsupported_svg")

    def test_alias_canonicalization_and_viewport_transform(self):
        data = {"width": 24, "height": 16, "icons": {"base": {"body": "<path/>"}},
                "aliases": {"first": {"parent": "base", "hFlip": True, "rotate": 1},
                            "second": {"parent": "first", "hFlip": True, "vFlip": True, "rotate": 3}}}
        icon = catalog.canonical_icon(data, "second")
        self.assertFalse(icon["hFlip"])
        self.assertTrue(icon["vFlip"])
        self.assertEqual(icon["rotate"], 0)
        self.assertEqual(icon["width"], 24)
        icon["rotate"] = 1
        svg = catalog.icon_svg(icon)
        self.assertIn('viewBox="0 0 16 24"', svg)
        self.assertIn('rotate(90)', svg)

    def test_malformed_icon_reply_edge_cases(self):
        for data in ({}, {"icons": {}}, {"icons": {"x": {"body": ""}}},
                     {"icons": {"x": {"body": "x", "width": 0}}},
                     {"icons": {"x": {"body": "x", "width": True}}},
                     {"icons": {"x": {"body": "x", "width": float("nan")}}},
                     {"icons": {"x": {"body": "x", "rotate": "90"}}},
                     {"icons": {"x": {"body": "x", "hFlip": 1}}},
                     {"icons": {}, "aliases": {"x": {"parent": "x"}}}):
            with self.subTest(data=data), self.assertRaises(catalog.CatalogError):
                catalog.canonical_icon(data, "x")

    def test_bounded_bad_api_json(self):
        for bad in (b"[1]", b"{", b"x" * (catalog.MAX_RESPONSE + 1), {"icons": False}):
            with self.subTest(kind=type(bad)), self.assertRaises(catalog.CatalogError):
                self.make(fetch=lambda url: bad).prepare("test:icon-0")

    def test_fixed_host_and_no_redirects(self):
        for url in ("http://api.iconify.design/search", "https://evil.org/search",
                    "https://api.iconify.design.evil.org/search", "https://api.iconify.design:444/search",
                    "https://user@api.iconify.design/search", "https://api.iconify.design/search#frag"):
            with self.subTest(url=url), self.assertRaises(catalog.CatalogError):
                catalog._official_fetch(url)
        with self.assertRaises(catalog.CatalogError):
            catalog._NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://evil.org")


if __name__ == "__main__":
    unittest.main()
