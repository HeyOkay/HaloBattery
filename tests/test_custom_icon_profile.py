"""Known verified Lucide selections reuse the exact existing offline tray pixels."""
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from halo import icon_artwork, icons
from halo.custom_icon_profile import with_builtin_profile, has_builtin_profile, _BUILTIN_PROFILES
from halo.svg_glyph import SvgGlyphError


class BuiltinProfileTests(unittest.TestCase):
    def setUp(self):
        self.glyph = SimpleNamespace(png_bytes=b'verified normalized png', digest='a' * 64)

    def test_mapping_is_exactly_the_reviewed_manifest_lucide_sources(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / 'artwork/tray/manifest.json').read_text(encoding='utf-8'))
        expected = {'lucide:' + name: kind for kind, name in manifest['sources']['lucide']['glyphs'].items()}
        self.assertEqual(_BUILTIN_PROFILES, expected)
        for kind in expected.values():
            self.assertIn((kind, 16), icon_artwork._PNG)

    def test_unknown_names_and_other_collections_preserve_original_object(self):
        for name in ('tabler:mouse', 'material-symbols:mouse', 'lucide:mouse-pointer',
                     'lucide:gamepad', 'lucide:microphone', 'lucide:health', 'Lucide:mouse',
                     'lucide:mouse-filled', '', None, [], {}):
            with self.subTest(name=name):
                self.assertIs(with_builtin_profile(name, self.glyph), self.glyph)

    def test_exact_existing_pixels_for_every_profile_size_theme_and_alpha(self):
        for name, kind in _BUILTIN_PROFILES.items():
            profile = with_builtin_profile(name, self.glyph)
            for size in (16, 20, 24, 32, 40, 48, 64, 80):
                for foreground in ((255, 255, 255), (0, 0, 0)):
                    for alpha in (255, 110, 0):
                        with self.subTest(name=name, size=size, foreground=foreground, alpha=alpha):
                            actual = profile.render(size, foreground, alpha)
                            expected = icon_artwork.glyph(kind, size, foreground, alpha,
                                                          foreground_opacity=230, filled=False)
                            self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_full_mouse_tray_matches_builtin_for_every_battery_state(self):
        profile = with_builtin_profile('lucide:mouse', self.glyph)
        states = ((75, False, True, False), (0, False, True, False),
                  (100, True, True, False), (None, True, True, False),
                  (50, False, False, False), (None, False, True, False),
                  (None, False, False, True))
        for size in (16, 20, 24, 32, 40, 48, 64, 80):
            for light in (True, False):
                for level, charging, online, paused in states:
                    with self.subTest(size=size, light=light, state=(level, charging, online, paused)):
                        options = dict(size=size, light_taskbar=light, force_active=paused)
                        builtin = icons.render(level, charging, online, badge='mouse', **options)
                        custom = icons.render(level, charging, online, custom_glyph=profile, **options)
                        self.assertEqual(custom.tobytes(), builtin.tobytes())

    def test_specific_outline_preserves_existing_optical_placement(self):
        for name, kind in (('lucide:webcam', 'webcam'), ('lucide:heart-pulse', 'health'),
                           ('lucide:house', 'smart_home'), ('lucide:tv-minimal', 'display')):
            profile = with_builtin_profile(name, self.glyph)
            actual = profile.render(32, (255, 255, 255), foreground_opacity=1)
            self.assertEqual(actual.getchannel('A').tobytes(), icon_artwork._image(kind, 32).tobytes())
            self.assertNotEqual(actual.tobytes(), icon_artwork.glyph(kind, 32, (255, 255, 255), 255, filled=True).tobytes())

    def test_wrapper_is_immutable_forwards_cache_bytes_and_has_stable_profile_identity(self):
        mouse = with_builtin_profile('lucide:mouse', self.glyph)
        self.assertIs(mouse.png_bytes, self.glyph.png_bytes)
        self.assertNotEqual(mouse.digest, self.glyph.digest)
        self.assertEqual(mouse.digest, with_builtin_profile('lucide:mouse', self.glyph).digest)
        self.assertNotEqual(mouse.digest, with_builtin_profile('lucide:mic', self.glyph).digest)
        self.assertIs(with_builtin_profile('lucide:mouse', mouse), mouse)
        with self.assertRaises(FrozenInstanceError):
            mouse._kind = 'keyboard'
        first = mouse.render(20, (255, 255, 255))
        expected = first.tobytes()
        first.putalpha(0)
        self.assertEqual(mouse.render(20, (255, 255, 255)).tobytes(), expected)

    def test_profiles_report_monochrome_mode_and_leave_colored_glyphs_unchanged(self):
        profile=with_builtin_profile('lucide:mouse',self.glyph)
        self.assertTrue(profile.uses_builtin_profile)
        self.assertFalse(profile.is_colored)
        self.assertEqual(profile.color_mode,'monochrome')
        self.assertGreaterEqual(profile.memory_bytes,len(self.glyph.png_bytes))
        self.assertTrue(has_builtin_profile('lucide:mouse'))
        self.assertFalse(has_builtin_profile('tabler:mouse'))
        colored=SimpleNamespace(png_bytes=b'color',digest='b'*64,is_colored=True)
        self.assertIs(with_builtin_profile('lucide:mouse',colored),colored)

    def test_bad_render_arguments_follow_custom_glyph_contract(self):
        glyph = with_builtin_profile('lucide:mouse', self.glyph)
        for change in (dict(size=True), dict(size=0), dict(size=1025), dict(fg=(256, 0, 0)),
                       dict(alpha=-1), dict(alpha=True), dict(foreground_opacity=float('nan')),
                       dict(foreground_opacity=230)):
            arguments = dict(size=20, fg=(255, 255, 255)); arguments.update(change)
            with self.subTest(change=change), self.assertRaises(SvgGlyphError):
                glyph.render(**arguments)


if __name__ == '__main__':
    unittest.main()
