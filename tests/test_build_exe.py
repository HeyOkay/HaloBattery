"""Verify complete native payload inclusion without PE dependency re-analysis."""
from pathlib import Path
import tempfile
import unittest

from tools.build_exe import native_payload_entries


class NativePayloadTests(unittest.TestCase):
    def test_complete_tree_keeps_paths_and_data_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            names = ["HaloBattery.Settings.exe", "coreclr.dll",
                     "Assets/Fonts/font.ttf", "ja-JP/Microsoft.ui.xaml.dll.mui",
                     "Assets/тест.svg"]
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"MZ" if path.suffix in (".dll", ".exe") else b"data")
            entries = native_payload_entries(root)
            self.assertEqual(len(entries), len(names))
            self.assertEqual({destination for destination, _, _ in entries},
                             {"settings-ui/" + name for name in names})
            for destination, source, kind in entries:
                self.assertEqual(kind, "DATA")
                self.assertEqual(Path(source), root / destination.removeprefix("settings-ui/"))
            self.assertEqual(entries, native_payload_entries(root))

    def test_missing_companion_fails_before_packaging(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(FileNotFoundError, "Publish the settings companion"):
                native_payload_entries(Path(temp))


if __name__ == "__main__":
    unittest.main()
