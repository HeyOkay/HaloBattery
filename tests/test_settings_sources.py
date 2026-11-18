"""Check native settings icon provenance offline, without renderer dependencies."""
import hashlib
import json
from pathlib import Path
import re
import unittest
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


class SettingsSourcesTests(unittest.TestCase):
    def test_requested_setting_glyphs_keep_their_official_geometry(self):
        names = {"languages", "circle-arrow-up", "smartphone-nfc", "bluetooth",
                 "circle", "loader-circle", "sun-moon", "battery-full", "bell-off",
                 "keyboard", "headphones", "mic", "speaker", "webcam", "heart-pulse",
                 "house", "watch", "tv-minimal", "radar", "gamepad-2", "eye-closed"}
        source = (ROOT / "ui" / "HaloBattery.Settings" / "Lucide.cs").read_text(encoding="utf-8")
        native = {name: json.loads("[" + paths + "]") for name, paths in
                  re.findall(r'\["([^"]+)"\]\s*=\s*new\[\]\{([^}]*)\}', source)}
        self.assertTrue(names | {"eye", "refresh-cw", "download"} <= native.keys())
        for name in names:
            expected = []
            svg = ET.parse(ROOT / "artwork" / "settings" / f"lucide-{name}.svg").getroot()
            for element in svg:
                tag = element.tag.rsplit("}", 1)[-1]
                attrs = element.attrib
                self.assertNotIn("transform", attrs)
                if tag == "path":
                    expected.append(attrs["d"])
                elif tag == "circle":
                    x, y, r = (float(attrs[key]) for key in ("cx", "cy", "r"))
                    expected.append(f"M{x-r},{y} A{r},{r} 0 1 0 {x+r},{y} A{r},{r} 0 1 0 {x-r},{y}")
                elif tag == "rect":
                    x, y, w, h = (float(attrs.get(key, 0)) for key in ("x", "y", "width", "height"))
                    r = float(attrs.get("rx", 0))
                    expected.append(f"M{x+r},{y} H{x+w-r} A{r},{r} 0 0 1 {x+w},{y+r} "
                                    f"V{y+h-r} A{r},{r} 0 0 1 {x+w-r},{y+h} H{x+r} "
                                    f"A{r},{r} 0 0 1 {x},{y+h-r} V{y+r} A{r},{r} 0 0 1 {x+r},{y} Z")
                elif tag == "line":
                    x1, y1, x2, y2 = (float(attrs[key]) for key in ("x1", "y1", "x2", "y2"))
                    expected.append(f"M{x1},{y1} L{x2},{y2}")
                else:
                    self.fail(f"Unsupported source primitive {tag} in {name}")
            with self.subTest(icon=name):
                self.assertEqual(native[name], expected)

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
