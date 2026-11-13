"""Check native settings icon provenance offline, without renderer dependencies."""
import hashlib
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class SettingsSourcesTests(unittest.TestCase):
    def test_official_svg_sources_keep_pinned_hashes_and_geometry(self):
        directory = ROOT / "artwork" / "settings"
        sources = json.loads((directory / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual(sources["package"], "lucide-static")
        self.assertEqual(sources["version"], "1.17.0")
        self.assertTrue((directory / sources["license"]).is_file())
        self.assertTrue(sources["icons"])
        for name, record in sources["icons"].items():
            with self.subTest(icon=name):
                data = (directory / f"lucide-{name}.svg").read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), record["sha256"])
                self.assertEqual(record["source"],
                                 f"https://unpkg.com/lucide-static@1.17.0/icons/{name}.svg")
                svg = ET.fromstring(data)
                self.assertEqual(svg.attrib["viewBox"], "0 0 24 24")
                self.assertEqual(svg.attrib["stroke-width"], "2")


if __name__ == "__main__":
    unittest.main()
