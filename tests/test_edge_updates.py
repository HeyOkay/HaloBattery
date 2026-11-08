"""Edge cases for halo/updates.py: version comparison, a malformed GitHub reply, the
update cache. No network: urlopen is replaced.

Run from the repository root:

    python -m unittest discover -s tests
"""
import io
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from halo import updates  # noqa: E402


class VersionTests(unittest.TestCase):
    def test_parse_version_table(self):
        for text, expected in (("1.16.0", (1, 16, 0)), ("v1.16.0", (1, 16, 0)), ("1.16", (1, 16)),
                               ("1", (1,)), (" 1.16.0 ", (1, 16, 0)), ("1.16.0.1", (1, 16, 0, 1)),
                               ("1.016.0", (1, 16, 0)), ("1.100", (1, 100)),
                               ("V1.2", None),                      # only a lowercase v is a prefix
                               ("", None), (None, None), ("abc", None), ("v", None), ("1.", None),
                               (".1", None), ("1..2", None), ("1.x", None), ("1.16.0-rc1", None),
                               ("1.16.0+build", None), ("1,16", None), ("1.16.0 beta", None),
                               ("-1.2", None), ("1.2\n3", None)):
            with self.subTest(text=text):
                self.assertEqual(updates.parse_version(text), expected)

    def test_parse_version_non_strings_are_not_versions(self):
        for value in (1, 1.5, True, b"1.2", ["1.2"], {"v": "1.2"}, object()):
            with self.subTest(value=value):
                self.assertIsNone(updates.parse_version(value))

    def test_is_newer_table(self):
        for latest, current, expected in (
                ("1.16.1", "1.16.0", True), ("1.17.0", "1.16.9", True), ("2.0", "1.99.99", True),
                ("1.16.0", "1.16.0", False), ("1.16.0", "1.16", False), ("1.16", "1.16.0", False),
                ("1.16.0.1", "1.16.0", True), ("1.16.0", "1.16.0.1", False),
                ("v1.16.0", "1.15.9", True), ("1.16.0", "v1.16.0", False), ("v1.16.0", "v1.16.0", False),
                ("1.10", "1.9", True), ("1.9", "1.10", False),             # numeric, not text order
                ("1.2", "1.10", False), ("1.0.0.0.0", "1", False),
                ("1.15.0", "1.16.0", False), ("0.9", "1.0", False)):
            with self.subTest(latest=latest, current=current):
                self.assertIs(updates.is_newer(latest, current), expected)

    def test_is_newer_with_garbage_is_never_true_and_never_raises(self):
        for bad in ("", None, "abc", "1.x", "1.16.0-rc1", "latest", 5, object()):
            with self.subTest(bad=bad):
                self.assertIs(updates.is_newer(bad, "1.16.0"), False)
                self.assertIs(updates.is_newer("1.99.0", bad), False)
                self.assertIs(updates.is_newer(bad, bad), False)


class ParseReleaseTests(unittest.TestCase):
    def good(self, **extra):
        data = {"tag_name": "v1.17.0", "html_url": "https://example.invalid/r", "body": "Notes",
                "published_at": "2026-10-20T10:00:00Z",
                "assets": [{"name": "HaloBattery-1.17.0.zip", "browser_download_url": "https://x/z.zip",
                            "size": 123, "digest": "sha256:ABCDEF"}]}
        data.update(extra)
        return data

    def test_good_release(self):
        rel = updates.parse_release(self.good())
        self.assertEqual((rel.version, rel.url, rel.notes, rel.published),
                         ("1.17.0", "https://example.invalid/r", "Notes", "2026-10-20"))
        self.assertEqual((rel.zip_url, rel.zip_size, rel.sha256), ("https://x/z.zip", 123, "abcdef"))

    def test_tag_forms(self):
        for tag, version in (("v1.17.0", "1.17.0"), ("1.17.0", "1.17.0"), ("v1.17", "1.17"),
                             (" v1.17.0 ", "1.17.0")):
            with self.subTest(tag=tag):
                self.assertEqual(updates.parse_release(self.good(tag_name=tag, assets=[])).version, version)

    def test_bad_tag_raises_value_error(self):
        for tag in (None, "", "nightly", "v", "v1.x", "1.17.0-rc1", 0, [], {}):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                updates.parse_release(self.good(tag_name=tag))
        with self.assertRaises(ValueError):
            updates.parse_release({})

    def test_missing_or_null_fields_get_defaults(self):
        rel = updates.parse_release({"tag_name": "v1.17.0"})
        self.assertEqual((rel.url, rel.notes, rel.published, rel.zip_url, rel.zip_size, rel.sha256),
                         (updates.RELEASES_URL, "", "", "", 0, ""))
        rel = updates.parse_release({"tag_name": "v1.17.0", "html_url": None, "body": None,
                                     "published_at": None, "assets": None})
        self.assertEqual((rel.url, rel.notes, rel.published), (updates.RELEASES_URL, "", ""))

    def test_asset_fields_of_the_wrong_type(self):
        for size, expected in (("abc", 0), (None, 0), ("42", 42), (12.9, 12), ([], 0), (-5, -5)):
            with self.subTest(size=size):
                asset = {"name": "halobattery-1.17.0.ZIP", "browser_download_url": "u", "size": size}
                self.assertEqual(updates.parse_release(self.good(assets=[asset])).zip_size, expected)
        for digest, expected in (("sha256:", ""), ("md5:abc", ""), (None, ""), (5, ""),
                                 ("SHA256:  AbC ", "abc")):
            with self.subTest(digest=digest):
                asset = {"name": "HaloBattery-1.17.0.zip", "browser_download_url": "u", "digest": digest}
                self.assertEqual(updates.parse_release(self.good(assets=[asset])).sha256, expected)

    def test_other_assets_and_the_wrong_version_zip_are_not_used(self):
        assets = [{"name": "HaloBattery-1.16.0.zip", "browser_download_url": "old"},
                  {"name": None}, {}, {"name": "notes.txt"}]
        rel = updates.parse_release(self.good(assets=assets))
        self.assertEqual(rel.zip_url, "")

    def test_malformed_assets_container_or_entries_do_not_raise(self):
        # an entry that is not an object, an "assets" that is not a list
        for assets in ([None, 5, "x", ["y"], [[]]], {"name": "x"}, "HaloBattery-1.17.0.zip", 7, True):
            with self.subTest(assets=assets):
                rel = updates.parse_release(self.good(assets=assets))
                self.assertEqual((rel.version, rel.zip_url), ("1.17.0", ""))
        good = {"name": "HaloBattery-1.17.0.zip", "browser_download_url": "z"}
        self.assertEqual(updates.parse_release(self.good(assets=[None, good])).zip_url, "z")

    def test_a_reply_that_is_not_an_object_is_a_value_error(self):
        for data in ([], None, "v1.17.0", 5, [{"tag_name": "v1.17.0"}]):
            with self.subTest(data=data), self.assertRaises(ValueError):
                updates.parse_release(data)

    def test_non_string_text_fields_are_stringified(self):
        rel = updates.parse_release(self.good(body=12, html_url=5, published_at=20261020))
        self.assertEqual((rel.notes, rel.url, rel.published), ("12", "5", "20261020"))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class FetchTests(unittest.TestCase):
    def fetch(self, body):
        with mock.patch.object(updates.urllib.request, "urlopen",
                               return_value=FakeResponse(body)) as opener:
            return updates.fetch_latest("1.16.0"), opener

    def test_good_body(self):
        rel, opener = self.fetch(json.dumps({"tag_name": "v1.17.0"}).encode())
        self.assertEqual(rel.version, "1.17.0")
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, updates.API_URL)
        self.assertEqual(request.get_header("User-agent"), "HaloBattery/1.16.0")
        self.assertEqual(opener.call_args.kwargs["timeout"], updates.TIMEOUT)

    def test_bad_bodies_raise_and_never_return_a_release(self):
        for name, body in (("empty", b""), ("html", b"<html>rate limited</html>"), ("array", b"[]"),
                           ("null", b"null"), ("truncated", b'{"tag_name": "v1.17'),
                           ("not utf-8", b"\xff\xfe{}"), ("no tag", b"{}"),
                           ("error object", b'{"message": "API rate limit exceeded"}')):
            with self.subTest(body=name), self.assertRaises((ValueError, UnicodeDecodeError)):
                self.fetch(body)

    def test_network_errors_propagate(self):
        with mock.patch.object(updates.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                updates.fetch_latest("1.16.0")


class CacheTests(unittest.TestCase):
    CACHE = {"update_latest": "1.17.0", "update_url": "https://x", "update_notified": "1.17.0",
             "update_last": 5.0}

    def test_same_repo_keeps_everything(self):
        cfg = dict(self.CACHE, update_repo=updates.REPO, low=15)
        before = dict(cfg)
        self.assertFalse(updates.invalidate_cache_for_repo(cfg))
        self.assertEqual(cfg, before)

    def test_other_or_missing_repo_drops_only_the_cache_and_tags_it(self):
        for repo in (None, "someone/else", "", 5, ["x"]):
            with self.subTest(repo=repo):
                cfg = dict(self.CACHE, low=15, names={"k": "n"})
                if repo is not None:
                    cfg["update_repo"] = repo
                self.assertTrue(updates.invalidate_cache_for_repo(cfg))
                self.assertEqual(cfg, {"low": 15, "names": {"k": "n"}, "update_repo": updates.REPO})

    def test_second_call_changes_nothing(self):
        cfg = dict(self.CACHE)
        self.assertTrue(updates.invalidate_cache_for_repo(cfg))
        snapshot = dict(cfg)
        self.assertFalse(updates.invalidate_cache_for_repo(cfg))
        self.assertEqual(cfg, snapshot)

    def test_empty_config_is_tagged(self):
        cfg = {}
        self.assertTrue(updates.invalidate_cache_for_repo(cfg))
        self.assertEqual(cfg, {"update_repo": updates.REPO})

    def test_explicit_repo(self):
        cfg = dict(self.CACHE, update_repo="a/b")
        self.assertFalse(updates.invalidate_cache_for_repo(cfg, "a/b"))
        self.assertTrue(updates.invalidate_cache_for_repo(cfg, "c/d"))
        self.assertEqual(cfg["update_repo"], "c/d")
        self.assertNotIn("update_latest", cfg)
        # an empty repo name means "this fork", not "any"
        cfg = dict(self.CACHE, update_repo="a/b")
        self.assertTrue(updates.invalidate_cache_for_repo(cfg, ""))
        self.assertEqual(cfg["update_repo"], updates.REPO)


if __name__ == "__main__":
    unittest.main()
