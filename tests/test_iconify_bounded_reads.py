"""Bounded registry reads avoid large requests without weakening verification."""
import gzip
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import zlib
from unittest.mock import patch

from halo import iconify_catalog as catalog


class BoundedReadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "manifests").mkdir()
        self.index = {"mouse": "a" * 64}
        self.entry = dict(name="Fixture", author={"name": "Artist"},
                          license={"spdx": "MIT", "title": "MIT"},
                          license_text="Fixture permission notice", icons_count=1,
                          icons_manifest="test.json.gz")
        self.registry = {"schema": 1, "collections": {"test": self.entry}}
        self.manifest = self.root / "manifests" / "test.json.gz"
        self.write_manifest(gzip.compress(catalog._json_bytes(self.index), mtime=0))

    def write_manifest(self, raw):
        self.manifest.write_bytes(raw)
        self.entry["icons_sha256"] = hashlib.sha256(raw).hexdigest()

    def service(self, *, lazy_registry=False):
        if lazy_registry:
            (self.root / "registry.json.gz").write_bytes(
                gzip.compress(catalog._json_bytes(self.registry), mtime=0))
        return catalog.IconifyCatalog(self.root / "data", registry_dir=self.root,
            registry=None if lazy_registry else self.registry,
            fetch=lambda url: self.fail("Local registry verification used the network"))

    def assert_index_unpublished(self, service):
        self.assertEqual(service._indices, {})
        self.assertEqual(service._index_names, 0)
        self.assertIsNone(service._name_blob)

    def test_gzip_registry_and_manifest_reads_request_at_most_one_mib(self):
        # Both inputs span multiple chunks and retain their exact parsed bytes.
        self.entry["license_text"] += "x" * (2 * 1024 * 1024)
        payload = catalog._json_bytes(self.index) + b" " * (2 * 1024 * 1024)
        self.write_manifest(gzip.compress(payload, mtime=0))
        service = self.service(lazy_registry=True)
        sizes = []
        read = gzip.GzipFile.read

        def record(stream, size=-1):
            sizes.append(size)
            self.assertGreater(size, 0)
            self.assertLessEqual(size, 1024 * 1024)
            return read(stream, size)

        with patch.object(gzip.GzipFile, "read", record):
            self.assertEqual(service.registry, self.registry)
            self.assertEqual(service._entry("test:mouse")[2]["icons"], self.index)
        self.assertGreaterEqual(len(sizes), 6)

    def test_raw_manifest_read_uses_open_file_size_including_growth_probe(self):
        service = self.service()
        original = Path.open
        sizes = []

        class Reader:
            def __init__(self, stream):
                self.stream = stream
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.stream.close()
            def fileno(self):
                return self.stream.fileno()
            def read(self, size):
                sizes.append(size)
                return self.stream.read(size)

        def open_file(path, *args, **kwargs):
            stream = original(path, *args, **kwargs)
            return Reader(stream) if path == self.manifest else stream

        with patch.object(Path, "open", open_file), \
                patch.object(catalog, "MAX_MANIFEST", self.manifest.stat().st_size):
            self.assertEqual(service._entry("test:mouse")[2]["icons"], self.index)
        self.assertEqual(sizes, [self.manifest.stat().st_size + 1])

    def test_oversized_raw_manifest_is_rejected_before_reading(self):
        service = self.service()
        limit = self.manifest.stat().st_size - 1
        case = self
        with self.manifest.open("rb") as stream:
            class NoRead:
                def __enter__(self):
                    return self
                def __exit__(self, *args):
                    pass
                def fileno(self):
                    return stream.fileno()
                def read(self, size):
                    case.fail("An oversized manifest must be rejected before reading")

            with patch.object(Path, "open", return_value=NoRead()), \
                    patch.object(catalog, "MAX_MANIFEST", limit), self.assertRaises(catalog.CatalogError):
                service._entry("test:mouse")
        self.assert_index_unpublished(service)

    def test_file_size_changes_fail_closed_even_when_manifest_hash_would_match(self):
        size = self.manifest.stat().st_size
        for advertised in (size - 1, size + 1):
            service = self.service()
            with self.subTest(advertised=advertised), \
                    patch.object(catalog.os, "fstat", return_value=SimpleNamespace(st_size=advertised)), \
                    self.assertRaises(catalog.CatalogError):
                service._entry("test:mouse")
            self.assert_index_unpublished(service)

    def test_decompressed_limits_accept_exact_boundary_and_reject_one_extra_byte(self):
        for kind in ("registry", "manifest"):
            for extra in (False, True):
                with self.subTest(kind=kind, extra=extra):
                    payload = catalog._json_bytes(self.registry if kind == "registry" else self.index)
                    raw = gzip.compress(payload + (b" " if extra else b""), mtime=0)
                    if kind == "registry":
                        (self.root / "registry.json.gz").write_bytes(raw)
                        service = catalog.IconifyCatalog(self.root / "data", registry_dir=self.root,
                            fetch=lambda url: self.fail("Unexpected network"))
                        invoke = service.collections
                    else:
                        self.write_manifest(raw)
                        service = self.service()
                        invoke = lambda: service._entry("test:mouse")
                    with patch.object(catalog, "MAX_REGISTRY", len(payload)):
                        if extra:
                            with self.assertRaises(catalog.CatalogError):
                                invoke()
                            if kind == "registry":
                                self.assertNotIn("registry", vars(service))
                            self.assert_index_unpublished(service)
                        else:
                            invoke()

    def test_truncated_corrupt_or_invalid_gzip_publishes_no_index(self):
        valid = self.manifest.read_bytes()
        crc_error = bytearray(valid)
        crc_error[-8] ^= 1
        for raw in (valid[:-1], valid[:-8], bytes(crc_error), b"not gzip", gzip.compress(b"not JSON")):
            with self.subTest(raw=raw[:12]):
                self.write_manifest(raw)  # Check the gzip/JSON after a matching digest.
                service = self.service()
                with self.assertRaises(catalog.CatalogError):
                    service._entry("test:mouse")
                self.assert_index_unpublished(service)

    def test_short_reads_and_limit_probe_preserve_bytes_without_overreading(self):
        class ShortReader(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 7))

        for limit in (0, 6, 7, 8, 19, 20, 21):
            with self.subTest(limit=limit):
                stream = ShortReader(b"abcdefghijklmnopqrst")
                self.assertEqual(catalog._read_chunks(stream, limit), b"abcdefghijklmnopqrst"[:limit + 1])
                self.assertEqual(stream.tell(), min(20, limit + 1))

    def test_invalid_deflate_is_reported_as_unverified_for_registry_and_manifest(self):
        # A real invalid DEFLATE block (BTYPE=3) raises zlib.error, not OSError.
        raw = bytes.fromhex("1f8b0800000000000003") + b"\x07" + b"\x00" * 8
        with self.assertRaises(zlib.error):
            gzip.decompress(raw)
        self.assert_corrupt_gzip_unverified(raw)

    def test_truncated_registry_and_bad_crc_are_reported_as_unverified(self):
        raw = gzip.compress(catalog._json_bytes(self.registry), mtime=0)
        corrupt = bytearray(raw)
        corrupt[-8] ^= 1
        for payload in (raw[:-1], bytes(corrupt)):
            with self.subTest(payload=payload[-12:]):
                self.assert_corrupt_gzip_unverified(payload)

    def assert_corrupt_gzip_unverified(self, raw):
        self.write_manifest(raw)
        manifest = self.service()
        with self.assertRaises(catalog.CatalogError) as failure:
            manifest._entry("test:mouse")
        self.assertEqual(failure.exception.code, "license_unverified")
        self.assert_index_unpublished(manifest)
        (self.root / "registry.json.gz").write_bytes(raw)
        registry = catalog.IconifyCatalog(self.root / "data", registry_dir=self.root,
            fetch=lambda url: self.fail("Unexpected network"))
        with self.assertRaises(catalog.CatalogError) as failure:
            registry.collections()
        self.assertEqual(failure.exception.code, "license_unverified")
        self.assertNotIn("registry", vars(registry))
        self.assert_index_unpublished(registry)
