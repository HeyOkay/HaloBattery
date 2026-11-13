"""SVG-only compensation invariants; no browser, hardware or external artwork."""
import unittest
import xml.etree.ElementTree as ET

from tools.filled_optics import compensate, profile


HOUSE = ("M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0"
         "l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z")
DOOR = "M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"


def fixture(nodes, scale=.5):
    return ET.fromstring(
        '<svg width="20" height="20" viewBox="0 0 16 16">'
        '<circle cx="8" cy="8" r="7.375" fill="none" stroke="white" stroke-width="1"/>'
        '<g transform="translate(8 8.25) scale(' + str(scale) + ') translate(-12 -12)" opacity=".902">'
        '<svg width="24" height="24" viewBox="0 0 24 24" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<defs><mask id="ink">' + nodes + '</mask></defs></svg></g></svg>')


def indexed(svg, index):
    return next(n for n in svg.iter() if n.get("data-source-index") == str(index))


def xml(svg):
    return ET.tostring(svg)


class FilledOpticsTests(unittest.TestCase):
    def test_input_frame_placement_and_dimensions_are_preserved(self):
        source = fixture('<rect x="5" y="2" width="14" height="20" rx="7" '
                         'fill="white" stroke="white" data-source-index="0"/>'
                         '<path d="M12 6v4" stroke="black" data-source-index="1"/>')
        before = xml(source)
        result = compensate(source, "mouse", 20)
        self.assertEqual(xml(source), before)
        self.assertEqual(xml(result[0]), xml(source[0]))
        self.assertEqual(result[1].attrib, source[1].attrib)
        self.assertEqual(result[1][0].attrib, source[1][0].attrib)
        for key, value in source.attrib.items():
            self.assertEqual(result.get(key), value)
        for index in (0, 1):
            original = indexed(source, index)
            changed = indexed(result, index)
            for key in ("d", "x", "y", "width", "height", "rx"):
                self.assertEqual(changed.get(key), original.get(key))

    def test_growth_is_bounded_and_weaker_at_larger_sizes(self):
        growth = [profile(size)["detail_growth_logical"] for size in (16, 20, 32, 64)]
        self.assertEqual(growth[0], .1)
        self.assertTrue(all(a > b > 0 for a, b in zip(growth, growth[1:])))
        self.assertLess(growth[-1], growth[0] / 4)
        self.assertLessEqual(profile(8)["detail_growth_logical"], .1)
        for size in (False, 0, -1, 16.0, "20"):
            with self.subTest(size=size), self.assertRaises(ValueError):
                profile(size)

    def test_body_ends_at_centreline_while_supporting_stems_stay(self):
        source = fixture('<path d="M12 19v3" stroke="white" data-source-index="0"/>'
                         '<path d="M19 10v2a7 7 0 0 1-14 0v-2" stroke="white" data-source-index="1"/>'
                         '<rect x="9" y="2" width="6" height="13" rx="3" fill="white" '
                         'stroke="white" data-source-index="2"/>')
        result = compensate(source, "microphone", 16)
        self.assertEqual(indexed(result, 2).get("stroke"), "none")
        self.assertEqual(indexed(result, 2).get("fill"), "white")
        for index in (0, 1):
            self.assertEqual(indexed(result, index).attrib, indexed(source, index).attrib)

    def test_negative_line_growth_is_in_logical_pixels(self):
        source = fixture('<rect fill="white" stroke="white" data-source-index="0"/>'
                         '<path d="M12 6v4" stroke="black" data-source-index="1"/>')
        a = compensate(source, "mouse", 16)
        b = compensate(source, "mouse", 20)
        width_a = float(indexed(a, 1).get("stroke-width"))
        width_b = float(indexed(b, 1).get("stroke-width"))
        self.assertAlmostEqual((width_a - 2) * .5 / 2, .1)
        self.assertGreater(width_a, width_b)

    def test_hollow_circle_expands_both_boundaries_and_preserves_positive_point(self):
        source = fixture('<rect fill="white" stroke="white" data-source-index="0"/>'
                         '<path d="M12 6h.01" stroke="black" data-source-index="1"/>'
                         '<circle cx="12" cy="14" r="4" stroke="black" data-source-index="2"/>'
                         '<path d="M12 14h.01" stroke="white" data-source-index="3"/>')
        result = compensate(source, "speaker", 16)
        ring = indexed(result, 2)
        radius, width = float(ring.get("r")), float(ring.get("stroke-width"))
        self.assertAlmostEqual((radius + width / 2 - 5) * .5, .1)
        self.assertAlmostEqual((radius - width / 2 - 3) * .5, .065)
        self.assertGreaterEqual(radius - width / 2, 3)
        self.assertEqual((ring.get("cx"), ring.get("cy")), ("12", "14"))
        point = indexed(result, 3)
        self.assertEqual(point.get("stroke"), "white")
        self.assertEqual(point.get("d"), "M12 14h.01")
        self.assertAlmostEqual((float(point.get("stroke-width")) - 2) * .5 / 2, .065)

    def test_house_roof_and_restored_outline_match_with_original_other_geometry(self):
        source = fixture('<path d="' + HOUSE + '" stroke="white" fill="white" data-source-index="1"/>'
                         '<path d="' + DOOR + '" fill="black" stroke="none" data-source-index="0"/>'
                         '<path d="' + HOUSE + '" stroke="white" fill="none" '
                         'data-restore-body-outline="true"/>', scale=.45)
        result = compensate(source, "smart_home", 20)
        body = indexed(result, 1)
        helper = next(n for n in result.iter() if n.get("data-restore-body-outline") == "true")
        expected = HOUSE.replace("l7-6a2 2 0 0 1 2.582 0l7 6", "L12 2 20.291 8.472")
        self.assertEqual(body.get("d"), expected)
        self.assertEqual(helper.get("d"), expected)
        self.assertEqual(body.get("stroke"), "none")
        self.assertEqual(float(helper.get("stroke-width")), 2)
        self.assertEqual(helper.get("stroke"), "white")
        clip = next(n for n in result.iter() if n.tag == "clipPath")
        self.assertEqual(helper.get("clip-path"), "url(#" + clip.get("id") + ")")
        self.assertEqual(clip.get("clipPathUnits"), "userSpaceOnUse")
        self.assertEqual(clip[0].get("d"), body.get("d"))
        self.assertEqual(clip[0].get("stroke"), "none")
        self.assertEqual(clip[0].get("fill"), "white")
        self.assertIsNone(clip[0].get("data-source-index"))
        defs = result[1][0][0]
        self.assertIn(clip, list(defs))
        self.assertNotIn(clip, list(defs[0]))
        self.assertEqual(body.get("stroke-linejoin"), "miter")
        self.assertEqual(body.get("stroke-miterlimit"), "2")
        self.assertEqual(indexed(result, 0).get("d"), DOOR)
        self.assertEqual(indexed(result, 0).get("fill"), "black")
        self.assertEqual(indexed(result, 0).get("stroke"), "black")
        self.assertAlmostEqual(float(indexed(result, 0).get("stroke-width")) * .45 / 2,
                               profile(20)["detail_growth_logical"])
        self.assertEqual(result[1].get("transform"), source[1].get("transform"))

    def test_headphone_band_keeps_source_width_and_cups_keep_previous_filled_outline(self):
        compound = ("M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2"
                    "v-7a9 9 0 0 1 18 0v7a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2"
                    "v-3a2 2 0 0 1 2-2h3")
        cups = ("M3 14h3a2 2 0 0 1 2 2v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z",
                "M21 14h-3a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2z")
        source = fixture('<path d="' + compound + '" fill="none" stroke="white" data-source-index="0"/>'
                         + "".join('<path d="' + d + '" fill="white" stroke="none" '
                                   'data-fill-existing-face="earcup"/>' for d in cups))
        before = xml(source)
        result = compensate(source, "headset", 20)
        band = indexed(result, 0)
        self.assertEqual(band.get("d"), "M3 14v-2a9 9 0 0 1 18 0v2")
        self.assertEqual(band.get("stroke"), "white")
        self.assertEqual(band.get("fill"), "none")
        self.assertIsNone(band.get("stroke-width"))
        self.assertEqual(result[1][0].get("stroke-width"), "2")
        self.assertEqual(result[1][0].get("stroke-linecap"), "round")
        faces = [n for n in result.iter() if n.get("data-fill-existing-face") == "earcup"]
        self.assertEqual([n.get("d") for n in faces], list(cups))
        for face in faces:
            self.assertEqual(face.get("stroke"), "white")
            self.assertAlmostEqual(float(face.get("stroke-width")), 1.6)
            self.assertEqual(face.get("fill"), "white")
        self.assertEqual(xml(source), before)
        self.assertEqual(xml(compensate(result, "headset", 20)), xml(result))

    def test_rect_circle_and_path_faces_use_original_centreline_coordinates(self):
        cases = [
            ("mouse", '<rect x="5" y="2" width="14" height="20" rx="7" '
             'fill="white" stroke="white" data-source-index="0"/>', 0),
            ("webcam", '<circle cx="12" cy="10" r="8" fill="white" '
             'stroke="white" data-source-index="0"/>', 0),
            ("gamepad", '<path d="M2 16C2 10 3 5 7 5H17C21 5 22 10 22 16Z" '
             'fill="white" stroke="white" data-source-index="4"/>', 4),
        ]
        for kind, nodes, index in cases:
            for size in (16, 20, 64):
                with self.subTest(kind=kind, size=size):
                    source = fixture(nodes)
                    result = compensate(source, kind, size)
                    old, new = indexed(source, index), indexed(result, index)
                    self.assertEqual(new.get("stroke"), "none")
                    self.assertEqual(new.get("fill"), "white")
                    self.assertEqual({k: v for k, v in old.attrib.items() if k != "stroke"},
                                     {k: v for k, v in new.attrib.items() if k != "stroke"})
                    if kind == "mouse":
                        # Original left-edge stroke spans x=4..6; fill ends at x=5.
                        self.assertEqual(float(new.get("x")), (4 + 6) / 2)
                        self.assertEqual(float(new.get("x")) + float(new.get("width")), (18 + 20) / 2)
                    if kind == "webcam":
                        # Original circular stroke spans radii 7..9.
                        self.assertEqual(float(new.get("r")), (7 + 9) / 2)

    def test_same_size_is_idempotent_and_unconfigured_kind_is_unchanged(self):
        source = fixture('<rect fill="white" stroke="white" data-source-index="0"/>'
                         '<path d="M12 6v4" stroke="black" data-source-index="1"/>')
        first = compensate(source, "mouse", 16)
        self.assertEqual(xml(compensate(first, "mouse", 16)), xml(first))
        unchanged = compensate(source, "unconfigured-kind", 16)
        self.assertIsNot(unchanged, source)
        self.assertEqual(xml(unchanged), xml(source))


if __name__ == "__main__":
    unittest.main()
