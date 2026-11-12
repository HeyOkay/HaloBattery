"""The SVG generator check must detect drift without changing repository sources."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from PIL import Image
from tools.make_microphone_artwork import _store_generated


class ArtworkGeneratorCheckTests(unittest.TestCase):
    def test_check_accepts_matching_sources_without_rewriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            atlas = Image.new("L", (16, 16), 128)
            manifest = {"atlas": "masks.png", "label": "source"}
            self.assertEqual(_store_generated(atlas, manifest, path), 0)
            with mock.patch.object(Path, "write_bytes", side_effect=AssertionError("check wrote atlas")), \
                    mock.patch.object(Path, "write_text", side_effect=AssertionError("check wrote manifest")):
                self.assertEqual(_store_generated(atlas, manifest, path, check=True), 0)

    def test_check_reports_image_and_metadata_drift_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            manifest = {"atlas": "masks.png", "label": "original"}
            self.assertEqual(_store_generated(Image.new("L", (16, 16), 128), manifest, path), 0)
            original = {p.name: p.read_bytes() for p in Path(directory).iterdir()}
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = _store_generated(Image.new("L", (16, 16), 255),
                                          dict(manifest, label="changed"), path, check=True)
            self.assertEqual(result, 1)
            self.assertIn("masks.png", output.getvalue())
            self.assertIn("manifest.json", output.getvalue())
            self.assertEqual({p.name: p.read_bytes() for p in Path(directory).iterdir()}, original)

    def test_check_reports_missing_sources_without_creating_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            with contextlib.redirect_stdout(io.StringIO()):
                result = _store_generated(Image.new("L", (16, 16)), {"atlas": "masks.png"}, path, check=True)
            self.assertEqual(result, 1)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
