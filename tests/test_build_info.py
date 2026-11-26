"""Build identity is bundled data, never user configuration or process environment."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from halo import build_info

VERSION = "1.18.0"


class BuildInfoTests(unittest.TestCase):
    def bundled_channel(self, data, version=VERSION):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "halo").mkdir()
            if data is not None:
                (root / "halo/build-info.json").write_bytes(data)
            with mock.patch.object(build_info.sys, "frozen", True, create=True), \
                    mock.patch.object(build_info.sys, "_MEIPASS", str(root), create=True):
                return build_info.channel(version)

    def test_matching_bundled_preview_and_stable_identities(self):
        for channel in (build_info.STABLE, build_info.PRE_RELEASE):
            with self.subTest(channel=channel):
                raw = json.dumps(dict(schema=1, version=VERSION, channel=channel)).encode()
                self.assertEqual(self.bundled_channel(raw), channel)

    def test_source_runs_do_not_read_a_preview_marker(self):
        with mock.patch.object(build_info.sys, "frozen", False, create=True), \
                mock.patch.object(build_info, "_read") as read:
            self.assertEqual(build_info.channel(VERSION), build_info.STABLE)
        read.assert_not_called()

    def test_missing_malformed_unknown_or_mismatched_metadata_is_stable(self):
        values = [None, b"", b"{", b"\xff", b"[]", b"null", b"true", b"[" * 1500 + b"]" * 1500,
                  b"x" * (build_info.METADATA_LIMIT + 1)]
        for changed in ({"schema": True}, {"schema": 2}, {"version": "1.17.0"},
                        {"version": [VERSION]}, {"channel": "preview"}, {"channel": True}):
            values.append(json.dumps(dict(dict(schema=1, version=VERSION, channel="pre-release"),
                                          **changed)).encode())
        for raw in values:
            with self.subTest(raw=repr(raw)[:100]):
                self.assertEqual(self.bundled_channel(raw), build_info.STABLE)

    def test_invalid_running_version_cannot_enable_preview(self):
        raw = json.dumps(dict(schema=1, version=VERSION, channel="pre-release")).encode()
        for version in (None, True, [VERSION], "1.18.0-pre", " 1.18.0"):
            with self.subTest(version=version):
                self.assertEqual(self.bundled_channel(raw, version), build_info.STABLE)

    def test_frozen_copy_without_bundle_root_is_stable(self):
        with mock.patch.object(build_info.sys, "frozen", True, create=True), \
                mock.patch.object(build_info.sys, "_MEIPASS", None, create=True):
            self.assertEqual(build_info.channel(VERSION), build_info.STABLE)


class PreviewNotesTests(unittest.TestCase):
    def notes(self, data, frozen):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            if data is not None:
                (root / "CHANGELOG.md").write_bytes(data)
            with mock.patch.object(build_info.sys, "frozen", frozen, create=True), \
                    mock.patch.object(build_info.sys, "_MEIPASS", str(root), create=True), \
                    mock.patch.object(build_info, "__file__", str(root / "halo/build_info.py")):
                return build_info.preview_notes()

    def test_bundled_and_source_notes_are_only_the_unreleased_section(self):
        raw = "# Changelog\r\n\r\n## [Unreleased]\r\n\r\n### Added\r\n- Українська\r\n\r\n## [1.17.0] - 2026-11-12\r\nold\r\n".encode()
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                self.assertEqual(self.notes(raw, frozen), "### Added\r\n- Українська")

    def test_unreleased_at_end_and_empty_unreleased(self):
        self.assertEqual(self.notes(b"## [Unreleased]\n\nNew notes\n", True), "New notes")
        self.assertEqual(self.notes(b"## [Unreleased]\n\n## [1.17.0]\nOld\n", True), "")

    def test_missing_invalid_or_oversized_notes_are_empty(self):
        for raw in (None, b"\xff", b"## [1.17.0]\nOld\n", b"x" * (build_info.NOTES_LIMIT + 1)):
            with self.subTest(raw=repr(raw)[:60]):
                self.assertEqual(self.notes(raw, True), "")


if __name__ == "__main__":
    unittest.main()
