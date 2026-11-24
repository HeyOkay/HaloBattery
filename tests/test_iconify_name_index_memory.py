"""Verified name warming does not retain or disturb fingerprint caches."""
from concurrent.futures import ThreadPoolExecutor
import gzip
import hashlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from halo import iconify_catalog as catalog


class NameIndexMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "manifests").mkdir()
        self.names = {
            "zeta": ("homework", "x-home-round", "home-icon", "home"),
            "alpha": ("smart-home", "my-home-round", "homepage", "home-round", "home"),
        }
        self.registry = {"schema": 1, "collections": {}}
        for prefix, names in self.names.items():
            self.registry["collections"][prefix] = {
                "name": prefix.title() + " icons", "author": {"name": "Artist"},
                "license": {"title": "MIT", "spdx": "MIT", "url": "https://example.org/LICENSE"},
                "license_text": "Copyright Artist. Permission to use.",
                "icons_manifest": prefix + ".json.gz", "icons_count": len(names),
            }
            self.write_manifest(prefix, dict.fromkeys(names, "a" * 64))

    def write_manifest(self, prefix, value):
        raw = gzip.compress(catalog._json_bytes(value), mtime=0)
        (self.root / "manifests" / (prefix + ".json.gz")).write_bytes(raw)
        self.registry["collections"][prefix]["icons_sha256"] = hashlib.sha256(raw).hexdigest()

    def make(self):
        return catalog.IconifyCatalog(
            self.root / "data", registry=self.registry, registry_dir=self.root,
            fetch=lambda url: self.fail("Name indexing attempted network access"))

    def assert_unpublished(self, service):
        self.assertIsNone(service._name_blob)
        self.assertEqual(service._name_ranges, ())
        self.assertEqual(service._name_starts, ())

    def test_warm_does_not_cache_fingerprints_but_selection_still_verifies_them(self):
        service = self.make()
        service.warm_name_index()
        self.assertIsInstance(service._name_blob, bytes)
        self.assertEqual(service._indices, {})
        self.assertEqual(service._index_names, 0)
        prefix, name, entry = service._entry("alpha:home")
        self.assertEqual((prefix, name), ("alpha", "home"))
        self.assertEqual(entry["icons"][name], "a" * 64)
        self.assertEqual(list(service._indices), ["alpha"])
        self.assertEqual(service._index_names, len(self.names["alpha"]))
        with self.assertRaises(catalog.CatalogError):
            service._entry("alpha:unknown")

    def test_warm_preserves_existing_lru_order_identity_and_accounting(self):
        service = self.make()
        service._entry("zeta:home")
        service._entry("alpha:home")
        before = list(service._indices.items())
        count = service._index_names
        service.warm_name_index()
        self.assertEqual(list(service._indices), [prefix for prefix, _ in before])
        for prefix, index in before:
            self.assertIs(service._indices[prefix], index)
        self.assertEqual(service._index_names, count)
        # Normal fingerprint lookups continue to promote their collection.
        service._entry("zeta:home")
        self.assertEqual(list(service._indices), ["alpha", "zeta"])

    def test_empty_inline_collection_preserves_name_index_boundaries(self):
        entry = self.registry["collections"]["alpha"]
        entry.pop("icons_manifest")
        entry.pop("icons_sha256")
        entry.pop("icons_count")
        entry["icons"] = {}
        service = self.make()
        service.warm_name_index()
        expected = b"\n\n\nhome\nhome-icon\nhomework\nx-home-round\n"
        self.assertEqual(service._name_blob, expected)
        self.assertEqual(service._name_ranges, (("alpha", 0, 2), ("zeta", 2, len(expected))))
        self.assertEqual(service.search("alpha:home")["total"], 0)
        self.assertEqual(service.search("home")["total"], 4)

    def test_invalid_manifests_publish_neither_names_nor_digest_cache(self):
        cases = (
            ("hash", {"home": "a" * 64}),
            ("count", {"home": "a" * 64}),
            ("schema", [["home", "a" * 64]]),
            ("name", {"Home": "a" * 64}),
            ("non-ascii", {"h\u00f6me": "a" * 64}),
            ("digest", {"home": "A" * 64}),
            ("digest-type", {"home": None}),
        )
        for kind, value in cases:
            with self.subTest(kind=kind):
                self.write_manifest("zeta", value)
                entry = self.registry["collections"]["zeta"]
                entry["icons_count"] = len(value)
                if kind == "hash":
                    entry["icons_sha256"] = "0" * 64
                elif kind == "count":
                    entry["icons_count"] += 1
                service = self.make()
                with self.assertRaises(catalog.CatalogError):
                    service.warm_name_index()
                self.assert_unpublished(service)
                self.assertEqual(service._indices, {})
                self.assertEqual(service._index_names, 0)

    def test_cancelled_partial_warm_can_retry_without_retained_fingerprints(self):
        service = self.make()
        calls = 0

        def cancel_second_collection():
            nonlocal calls
            calls += 1
            return calls == 2

        with self.assertRaises(catalog.CatalogCancelled):
            service.warm_name_index(cancel_second_collection)
        self.assert_unpublished(service)
        self.assertEqual(service._indices, {})
        self.assertEqual(service._index_names, 0)
        service.warm_name_index()
        self.assertEqual(service.search("home")["total"], 9)
        with self.assertRaises(catalog.CatalogCancelled):
            service.warm_name_index(lambda: True)
        self.assertIsInstance(service._name_blob, bytes)

    def test_ranked_pages_metadata_next_ids_and_collection_filters_are_stable(self):
        service = self.make()
        expected = [
            "alpha:home", "zeta:home", "zeta:home-icon", "alpha:home-round",
            "alpha:smart-home", "zeta:x-home-round", "alpha:my-home-round",
            "alpha:homepage", "zeta:homework",
        ]

        def item(icon_id):
            prefix, name = icon_id.split(":")
            return {"id": icon_id, "icon_id": icon_id, "name": name,
                    "collection": prefix.title() + " icons", "collection_id": prefix,
                    "author": "Artist", "license": "MIT", "license_spdx": "MIT",
                    "license_url": "https://example.org/LICENSE",
                    "source_url": f"https://icon-sets.iconify.design/{prefix}/{name}/"}

        first = service.search("  HOME  ", limit=3)
        self.assertEqual(first, {
            "items": [item(icon_id) for icon_id in expected[:3]], "next_start": 3,
            "has_more": True, "cached": False, "offline": False, "total": 9,
            "known_count": 9, "total_exact": True, "page": 1, "page_size": 3,
            "page_count": 3, "search_mode": "local", "next_ids": expected[3:6],
        })
        all_ids = []
        for start in (0, 3, 6):
            result = service.search("home", start=start, limit=3)
            self.assertTrue(result["cached"])
            self.assertEqual(result["next_ids"], expected[start + 3:start + 6])
            all_ids.extend(value["id"] for value in result["items"])
        self.assertEqual(all_ids, expected)
        for kwargs in ({"collection": "alpha"}, {"query": "alpha:home"}):
            query = kwargs.pop("query", "home")
            result = service.search(query, **kwargs)
            self.assertEqual([value["id"] for value in result["items"]],
                             [icon_id for icon_id in expected if icon_id.startswith("alpha:")])
        self.assertEqual([value["id"] for value in service.search("home round")["items"]],
                         ["alpha:home-round", "zeta:x-home-round", "alpha:my-home-round"])
        self.assertEqual(service._indices, {})

    def blocked_manifest_open(self):
        """Pause one real file read; no timing-based thread coordination."""
        entered, release = threading.Event(), threading.Event()
        opened = []
        original = Path.open

        def open_file(path, *args, **kwargs):
            if path.parent == self.root / "manifests":
                opened.append(path.name)
                if path.name == "alpha.json.gz":
                    entered.set()
                    if not release.wait(5):
                        raise RuntimeError("Test did not release the manifest read")
            return original(path, *args, **kwargs)

        return entered, release, opened, patch.object(Path, "open", open_file)

    def test_concurrent_warm_builds_one_verified_name_index(self):
        service = self.make()
        entered, release, opened, reader = self.blocked_manifest_open()
        second_started = threading.Event()

        def second_warm():
            second_started.set()
            service.warm_name_index()

        with reader, ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(service.warm_name_index)
            try:
                self.assertTrue(entered.wait(5))
                second = workers.submit(second_warm)
                self.assertTrue(second_started.wait(5))
            finally:
                release.set()
            first.result(timeout=5)
            second.result(timeout=5)
        self.assertEqual(opened, ["alpha.json.gz", "zeta.json.gz"])
        self.assertEqual(service.search("home")["total"], 9)
        self.assertEqual(service._indices, {})

    def test_cleanup_waits_for_warm_and_clears_the_complete_index(self):
        service = self.make()
        entered, release, _, reader = self.blocked_manifest_open()
        cleanup_started = threading.Event()

        def cleanup():
            cleanup_started.set()
            service.clear_browse_cache(set())

        with reader, ThreadPoolExecutor(max_workers=2) as workers:
            warm = workers.submit(service.warm_name_index)
            try:
                self.assertTrue(entered.wait(5))
                cleared = workers.submit(cleanup)
                self.assertTrue(cleanup_started.wait(5))
            finally:
                release.set()
            warm.result(timeout=5)
            cleared.result(timeout=5)
        self.assert_unpublished(service)
        self.assertEqual(service._indices, {})
        self.assertEqual(service._index_names, 0)
        self.assertEqual(service.search("home")["total"], 9)
