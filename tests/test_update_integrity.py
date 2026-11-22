"""Mandatory updater checksum and HTTPS checks. All downloads use in-memory fakes."""
import hashlib
import io
import os
import shutil
import sys
import unittest
import urllib.request
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from halo import updates  # noqa: E402

ZIP_NAME = "HaloBattery-1.17.0.zip"
ZIP_URL = "https://example.invalid/" + ZIP_NAME
SUMS_URL = "https://example.invalid/SHA256SUMS.txt"


class Response(io.BytesIO):
    def __init__(self, data, url, size=None):
        super().__init__(data)
        self.url = url
        self.headers = {"Content-Length": str(len(data) if size is None else size)}

    def geturl(self):
        return self.url


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = _sandbox.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("HaloBattery/HaloBattery.exe", b"new exe")
            z.writestr("HaloBattery/_internal/python312.dll", b"new dll")
        self.data = buf.getvalue()
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.rel = updates.Release("1.17.0", "https://example.invalid/release",
                                   zip_url=ZIP_URL, zip_size=len(self.data),
                                   zip_name=ZIP_NAME, checksums_url=SUMS_URL)
        self.calls = []
        self.progress = []

    def manifest(self, name=ZIP_NAME, mode="*"):
        return f"{self.digest.upper()} {mode}{name}\r\n".encode("ascii")

    def download(self, manifest=None, data=None, size=None, final_url=ZIP_URL):
        data = self.data if data is None else data

        def opener(req, timeout):
            self.calls.append(req.full_url)
            self.assertEqual(timeout, updates.DOWNLOAD_TIMEOUT)
            self.assertEqual(req.get_header("User-agent"), "HaloBattery/1.16.0")
            if req.full_url == SUMS_URL:
                return Response(manifest, SUMS_URL)
            self.assertEqual(req.full_url, ZIP_URL)
            return Response(data, final_url, size)
        return updates.download(self.rel, os.path.join(self.tmp, "stage"), "1.16.0",
                                progress=self.progress.append, opener=opener)

    def assert_not_unpacked(self):
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "stage", "new")))
        self.assertNotIn(100, self.progress)

    def test_missing_digest_uses_same_release_manifest_binary_and_text_formats(self):
        for mode in ("*", " "):
            with self.subTest(mode=mode):
                self.calls.clear()
                app = self.download(self.manifest(mode=mode))
                self.assertTrue(os.path.isfile(os.path.join(app, updates.EXE_NAME)))
                self.assertEqual(self.calls, [SUMS_URL, ZIP_URL])
                self.assertEqual(self.progress[-1], 100)

    def test_api_digest_is_authoritative_and_does_not_fetch_manifest(self):
        self.rel.sha256 = self.digest.upper()
        self.download()
        self.assertEqual(self.calls, [ZIP_URL])

    def test_api_digest_mismatch_never_falls_back(self):
        self.rel.sha256 = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 does not match"):
            self.download(self.manifest())
        self.assertEqual(self.calls, [ZIP_URL])
        self.assert_not_unpacked()

    def test_no_checksum_refuses_before_network_and_preserves_existing_staging(self):
        self.rel.checksums_url = ""
        stage = os.path.join(self.tmp, "stage")
        os.makedirs(stage)
        sentinel = os.path.join(stage, "existing")
        with open(sentinel, "wb") as f:
            f.write(b"preserve")
        with self.assertRaisesRegex(ValueError, "no SHA-256"):
            self.download()
        self.assertEqual(self.calls, [])
        self.assertTrue(os.path.isfile(sentinel))
        self.assert_not_unpacked()

    def test_malformed_explicit_digest_never_falls_back(self):
        for digest in ("abc", "0" * 63, "g" * 64, "0" * 64 + "\n"):
            with self.subTest(digest=digest):
                self.rel.sha256 = digest
                with self.assertRaisesRegex(ValueError, "invalid SHA-256"):
                    self.download(self.manifest())
                self.assertEqual(self.calls, [])
                self.assert_not_unpacked()

    def test_manifest_requires_exact_one_asset_filename_and_valid_records(self):
        invalid = (
            b"", b"garbage", b"\xff", self.manifest("HaloBattery-1.16.0.zip"),
            self.manifest("HaloBattery-1.17.0-setup.exe"),
            self.manifest("./" + ZIP_NAME), self.manifest(ZIP_NAME.lower()),
            self.manifest() + self.manifest(), self.manifest() + b"invalid\n",
            self.manifest().replace(self.digest.upper().encode(), b"abc"),
        )
        for manifest in invalid:
            with self.subTest(manifest=manifest):
                self.calls.clear()
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    self.download(manifest)
                self.assertEqual(self.calls, [SUMS_URL])
                self.assert_not_unpacked()

    def test_oversized_manifest_is_refused(self):
        with self.assertRaisesRegex(ValueError, "too large"):
            self.download(b"x" * (updates.CHECKSUMS_MAX + 1))
        self.assertEqual(self.calls, [SUMS_URL])
        self.assert_not_unpacked()

    def test_checksum_fallback_detects_damaged_zip(self):
        with self.assertRaisesRegex(ValueError, "SHA-256 does not match"):
            self.download(self.manifest(), data=self.data[:-1] + b"x")
        self.assert_not_unpacked()

    def test_manifest_fetch_errors_never_reach_zip_or_unpack(self):
        with mock.patch.object(updates, "_open_https", side_effect=OSError("offline")) as opened:
            with self.assertRaisesRegex(OSError, "offline"):
                updates.download(self.rel, os.path.join(self.tmp, "stage"), "1.16.0")
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.args[0].full_url, SUMS_URL)
        self.assert_not_unpacked()

    def test_archive_http_url_is_rejected_before_any_download(self):
        self.rel.zip_url = ZIP_URL.replace("https:", "http:")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.download(self.manifest())
        self.assertEqual(self.calls, [])

    def test_manifest_http_url_is_rejected_before_any_download(self):
        self.rel.checksums_url = SUMS_URL.replace("https:", "http:")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.download(self.manifest())
        self.assertEqual(self.calls, [])

    def test_non_https_final_response_is_rejected(self):
        self.rel.sha256 = self.digest
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            self.download(final_url=ZIP_URL.replace("https:", "http:"))
        self.assert_not_unpacked()

    def test_content_length_checked_when_api_size_is_missing(self):
        self.rel.sha256 = self.digest
        self.rel.zip_size = 0
        for size in (len(self.data) - 1, len(self.data) + 1, -1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.download(size=size)
            self.assert_not_unpacked()

    def test_truncated_body_refused_even_if_hash_matches_truncated_data(self):
        data = self.data[:-1]
        self.rel.sha256 = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.download(data=data, size=len(self.data))
        self.assert_not_unpacked()

    def test_invalid_release_sizes_are_rejected(self):
        self.rel.sha256 = self.digest
        for size in (-1, "123", True):
            self.rel.zip_size = size
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "invalid release zip size"):
                self.download()
        self.assertEqual(self.calls, [])

    def test_response_without_content_length_still_uses_api_size(self):
        self.rel.sha256 = self.digest
        response = Response(self.data, ZIP_URL)
        response.headers.clear()
        with mock.patch.object(updates, "_open_https", return_value=response):
            updates.download(self.rel, os.path.join(self.tmp, "stage"), "1.16.0")


class MetadataAndTransportTests(unittest.TestCase):
    def test_manifest_is_taken_only_from_same_release_asset_list(self):
        asset_name = ZIP_NAME.upper()
        rel = updates.parse_release({
            "tag_name": "v1.17.0",
            "assets": [
                {"name": asset_name, "browser_download_url": ZIP_URL, "digest": None},
                {"name": "SHA256SUMS.txt", "browser_download_url": SUMS_URL},
            ],
        })
        self.assertEqual(rel.zip_name, asset_name)
        self.assertEqual(rel.checksums_url, SUMS_URL)
        self.assertEqual(rel.sha256, "")

    def test_no_manifest_url_is_invented_for_old_releases(self):
        rel = updates.parse_release({"tag_name": "v1.17.0", "assets": [
            {"name": ZIP_NAME, "browser_download_url": ZIP_URL},
            {"name": "unrelated-SHA256SUMS.txt", "browser_download_url": SUMS_URL},
        ]})
        self.assertEqual(rel.checksums_url, "")

    def test_ambiguous_manifests_are_not_used(self):
        asset = {"name": "SHA256SUMS.txt", "browser_download_url": SUMS_URL}
        rel = updates.parse_release({"tag_name": "v1.17.0", "assets": [asset, asset]})
        self.assertEqual(rel.checksums_url, "")

    def test_test_build_tags_are_not_stable_update_versions(self):
        for tag in ("prerelease-build", "pre-1.17.0-0123456-12345-1", "pre-1.17.0-0123456-12345-2"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                updates.parse_release({"tag_name": tag})

    def test_downgrade_redirect_is_refused_before_following(self):
        req = urllib.request.Request(ZIP_URL)
        handler = updates._HTTPSRedirectHandler()
        for url in ("http://example.invalid/file", "ftp://example.invalid/file", "file:///file"):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "HTTPS"):
                handler.redirect_request(req, None, 302, "Found", {}, url)
        target = "https://release-assets.githubusercontent.com/signed-download"
        self.assertEqual(handler.redirect_request(req, None, 302, "Found", {}, target).full_url, target)

    def test_default_opener_installs_https_redirect_guard(self):
        req = urllib.request.Request(ZIP_URL)
        with mock.patch.object(updates.urllib.request, "build_opener") as build:
            updates._open_https(req, timeout=10)
        self.assertIsInstance(build.call_args.args[0], updates._HTTPSRedirectHandler)
        build.return_value.open.assert_called_once_with(req, timeout=10)


if __name__ == "__main__":
    unittest.main()
