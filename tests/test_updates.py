"""Updates: the release from GitHub's API, the download, the swap of the program files,
the "What's new" notes and the app's update menu. No network and no Windows needed.

Run from the repository root:

    python -m unittest discover -s tests
"""
import hashlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from halo import updates  # noqa: E402
from halo import whatsnew  # noqa: E402
from test_hide_rename import hb, make_app  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def release_json(version="1.15.0", digest=True, size=None, data=b""):
    asset = {"name": f"HaloBattery-{version}.zip",
             "browser_download_url": f"https://example.invalid/HaloBattery-{version}.zip",
             "size": len(data) if size is None else size}
    if digest:
        asset["digest"] = "sha256:" + hashlib.sha256(data).hexdigest()
    return {"tag_name": f"v{version}", "html_url": f"https://github.com/x/releases/tag/v{version}",
            "body": "Summary.\n\n### Added\n- **A** thing (#1). More words.\n",
            "published_at": "2026-10-20T10:00:00Z",
            "assets": [{"name": "other.txt"}, asset]}


def make_zip(files, top="HaloBattery/") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(top + name, data)
    return buf.getvalue()


APP_FILES = {"HaloBattery.exe": b"new exe", "_internal/python312.dll": b"new dll",
             "_internal/lib/x.pyd": b"new pyd"}


class FakeResponse(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, path, data=b""):
        full = os.path.join(self.tmp, *path.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        return full

    def read(self, path):
        with open(os.path.join(self.tmp, *path.split("/")), "rb") as f:
            return f.read()


class ReleaseTests(unittest.TestCase):
    def test_release_with_zip_and_digest(self):
        rel = updates.parse_release(release_json(data=b"abc"))
        self.assertEqual(rel.version, "1.15.0")
        self.assertTrue(rel.zip_url.endswith("HaloBattery-1.15.0.zip"))
        self.assertEqual(rel.zip_size, 3)
        self.assertEqual(rel.sha256, hashlib.sha256(b"abc").hexdigest())
        self.assertEqual(rel.published, "2026-10-20")
        self.assertIn("### Added", rel.notes)

    def test_release_without_its_zip_or_digest(self):
        data = release_json(digest=False)
        rel = updates.parse_release(data)
        self.assertEqual(rel.sha256, "")
        data["assets"] = []
        self.assertEqual(updates.parse_release(data).zip_url, "")

    def test_not_a_version(self):
        with self.assertRaises(ValueError):
            updates.parse_release({"tag_name": "nightly"})

    def test_versions(self):
        self.assertTrue(updates.is_newer("1.15.0", "1.14.0"))
        self.assertTrue(updates.is_newer("v1.14.1", "1.14"))
        self.assertFalse(updates.is_newer("1.14.0", "1.14.0"))
        self.assertFalse(updates.is_newer("junk", "1.14.0"))


class DownloadTests(TempDirTestCase):
    def download(self, data, **kw):
        rel = updates.parse_release(release_json(data=data, **kw))
        seen = []
        app = updates.download(rel, os.path.join(self.tmp, "stage"), "1.14.0", progress=seen.append,
                               opener=lambda req, timeout: FakeResponse(data))
        return app, seen

    def test_download_checks_and_unpacks(self):
        app, seen = self.download(make_zip(APP_FILES))
        self.assertTrue(os.path.isfile(os.path.join(app, "HaloBattery.exe")))
        with open(os.path.join(app, "_internal", "lib", "x.pyd"), "rb") as f:
            self.assertEqual(f.read(), b"new pyd")
        self.assertEqual(seen[-1], 100)
        self.assertEqual(seen, sorted(seen))
        self.assertFalse(any(n.endswith(".zip") for n in os.listdir(os.path.join(self.tmp, "stage"))))

    def test_zip_without_the_top_folder(self):
        app, _ = self.download(make_zip(APP_FILES, top=""))
        self.assertTrue(os.path.isfile(os.path.join(app, "HaloBattery.exe")))

    def test_damaged_download_is_refused(self):
        data = make_zip(APP_FILES)
        rel = updates.parse_release(release_json(data=data))
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            updates.download(rel, os.path.join(self.tmp, "s"), "1.14.0",
                             opener=lambda req, timeout: FakeResponse(data[:-1] + b"x"))

    def test_short_download_is_refused(self):
        data = make_zip(APP_FILES)
        rel = updates.parse_release(release_json(data=data, size=len(data) + 10))
        with self.assertRaisesRegex(ValueError, "incomplete"):
            updates.download(rel, os.path.join(self.tmp, "s"), "1.14.0",
                             opener=lambda req, timeout: FakeResponse(data))

    def test_zip_without_the_exe_is_refused(self):
        with self.assertRaisesRegex(ValueError, "HaloBattery.exe"):
            self.download(make_zip({"readme.txt": b"x"}))

    def test_path_outside_the_folder_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.download(make_zip({"HaloBattery.exe": b"x", "../../evil.txt": b"x"}))

    def test_no_zip_in_the_release(self):
        rel = updates.Release("1.15.0", "u")
        with self.assertRaisesRegex(ValueError, "no HaloBattery-1.15.0.zip"):
            updates.download(rel, os.path.join(self.tmp, "s"), "1.14.0")


class SwapTests(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.app = os.path.join(self.tmp, "app")
        for name, data in {"HaloBattery.exe": b"old exe", "_internal/python312.dll": b"old dll",
                           "_internal/gone.pyd": b"old only", "portable.txt": b"",
                           "config.json": b"{\"low\": 15}", "history.json": b"{}"}.items():
            self.write("app/" + name, data)
        self.new = os.path.join(self.tmp, "new")
        for name, data in APP_FILES.items():
            self.write("new/" + name, data)

    def test_program_files_replaced_user_files_kept(self):
        started = []
        ok = updates.finish_update(self.app, 0, self.new, start=started.append,
                                   sleep=lambda s: None, wait=lambda pid: None)
        self.assertTrue(ok)
        self.assertEqual(self.read("app/HaloBattery.exe"), b"new exe")
        self.assertEqual(self.read("app/_internal/python312.dll"), b"new dll")
        self.assertFalse(os.path.exists(os.path.join(self.app, "_internal", "gone.pyd")))
        self.assertEqual(self.read("app/config.json"), b"{\"low\": 15}")
        self.assertTrue(os.path.exists(os.path.join(self.app, "portable.txt")))
        self.assertEqual(started, [[os.path.join(self.app, "HaloBattery.exe")]])
        # the next start removes the old files and the staging folder
        updates.cleanup(self.app, self.new)
        self.assertEqual(sorted(os.listdir(self.app)),
                         ["HaloBattery.exe", "_internal", "config.json", "history.json", "portable.txt"])
        self.assertFalse(os.path.exists(self.new))

    def test_failed_copy_puts_the_old_files_back_and_starts_them(self):
        started = []

        def broken_copytree(src, dst):
            os.makedirs(dst)
            raise OSError("disk full")

        with mock.patch.object(updates.shutil, "copytree", broken_copytree), \
                mock.patch.object(updates, "RETRY_S", 0):
            ok = updates.finish_update(self.app, 0, self.new, start=started.append,
                                       sleep=lambda s: None, wait=lambda pid: None)
        self.assertFalse(ok)
        self.assertEqual(self.read("app/HaloBattery.exe"), b"old exe")
        self.assertEqual(self.read("app/_internal/gone.pyd"), b"old only")
        self.assertFalse(any(n.endswith(".old") for n in os.listdir(self.app)))
        self.assertEqual(started, [[os.path.join(self.app, "HaloBattery.exe")]])

    def test_a_held_file_is_retried(self):
        real, calls = os.replace, []

        def flaky(a, b):
            calls.append(a)
            if len(calls) == 1:
                raise PermissionError("in use")
            real(a, b)

        with mock.patch.object(updates.os, "replace", flaky):
            updates.swap_files(self.new, self.app, sleep=lambda s: None)
        self.assertEqual(self.read("app/HaloBattery.exe"), b"new exe")


class CommandLineTests(TempDirTestCase):
    def test_finish_args(self):
        cmd = updates.finish_command("C:/t/runner", "C:/t/new", "C:/Apps/HaloBattery", 4242)
        self.assertEqual(cmd[0], os.path.join("C:/t/runner", "HaloBattery.exe"))
        self.assertEqual(updates.parse_finish_args(cmd[1:]), ("C:/Apps/HaloBattery", "C:/t/new", 4242))
        self.assertIsNone(updates.parse_finish_args(["--probe"]))
        with self.assertRaises(ValueError):
            updates.parse_finish_args(["--finish-update", "--app-dir", "x"])

    def test_runner_is_a_copy_of_the_program_files_only(self):
        for name in ("app/HaloBattery.exe", "app/_internal/a.dll", "app/config.json",
                     "app/portable.txt", "app/_internal.old/b.dll"):
            self.write(name, b"x")
        runner = updates.prepare_runner(os.path.join(self.tmp, "app"), os.path.join(self.tmp, "stage"))
        self.assertEqual(sorted(os.listdir(runner)), ["HaloBattery.exe", "_internal"])
        self.assertEqual(os.listdir(os.path.join(runner, "_internal")), ["a.dll"])

    def test_who_can_install(self):
        self.assertEqual(updates.can_install(False, self.tmp), (False, "running from source"))
        self.assertFalse(updates.can_install(True, self.tmp)[0])          # no exe there
        self.write("HaloBattery.exe")
        self.assertEqual(updates.can_install(True, self.tmp), (True, ""))
        self.assertEqual(os.listdir(self.tmp), ["HaloBattery.exe"])

    def test_denied_install_folder_does_not_retry_or_block_startup(self):
        self.write("HaloBattery.exe")
        with mock.patch.object(updates.os, "access", return_value=True), \
                mock.patch.object(updates.os, "open", side_effect=PermissionError("access denied")) as opened:
            self.assertEqual(updates.can_install(True, self.tmp),
                             (False, f"{self.tmp} cannot be written"))
        opened.assert_called_once()
        self.assertEqual(os.listdir(self.tmp), ["HaloBattery.exe"])

    def test_staged_copy_keeps_the_data_of_the_app_it_updates(self):
        argv = ["x.exe", "--finish-update", "--app-dir", self.tmp, "--pid", "1"]
        self.assertEqual(hb._data_base_dir(argv), self.tmp)
        self.assertEqual(hb._data_base_dir(["x.exe"]), hb.BASE_DIR)


class NotesTests(unittest.TestCase):
    def changelog_section(self, version):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import release_notes
        with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
            return release_notes.section(f.read(), version)

    def test_real_release_notes(self):
        blocks = whatsnew.parse_notes(self.changelog_section("1.14.0"))
        kinds = [k for k, _ in blocks]
        self.assertEqual(kinds[0], "para")
        self.assertIn(("heading", "Added"), blocks)
        self.assertIn(("heading", "Fixed"), blocks)
        for kind, text in blocks:
            for mark in ("**", "`", "#138", "1038:2244", "]("):
                self.assertNotIn(mark, text)
        nova = next(t for k, t in blocks if k == "item" and t.startswith("SteelSeries Arctis Nova Elite"))
        self.assertTrue(nova.endswith("without SteelSeries GG."), nova)

    def test_points_and_paragraphs(self):
        md = ("Intro line one\nand two.\n\n### Added\n- **Portable mode**: put a file. Then more.\n"
              "  continued here.\n- [Link](https://x) works (#12).\n\n### Fixed\n* Short fix\n")
        self.assertEqual(whatsnew.parse_notes(md), [
            ("para", "Intro line one and two."),
            ("heading", "Added"),
            ("item", "Portable mode: put a file."),
            ("item", "Link works."),
            ("heading", "Fixed"),
            ("item", "Short fix"),
        ])
        self.assertEqual(whatsnew.parse_notes(""), [])

    def test_subtitle(self):
        self.assertEqual(whatsnew.subtitle("1.14.0", "2026-10-20T10:00:00Z"),
                         "Updated from 1.14.0  ·  20 October 2026")
        self.assertEqual(whatsnew.subtitle("", "bad"), "")

    def test_window_leaves_the_summary_out(self):
        blocks = [("para", "Summary."), ("heading", "Added"), ("item", "A.")]
        self.assertEqual(whatsnew.visible_blocks(blocks), [("heading", "Added"), ("item", "A.")])
        self.assertEqual(whatsnew.visible_blocks([("para", "Only text.")]), [("para", "Only text.")])

    def test_summary_is_split_into_sentences(self):
        self.assertEqual(whatsnew.sentences("Portable mode: a file. New devices: A, B. Fixes for C."),
                         ["Portable mode: a file.", "New devices: A, B.", "Fixes for C."])
        self.assertEqual(whatsnew.sentences("v1.2 is out. docs/devices.md moved."),
                         ["v1.2 is out. docs/devices.md moved."])     # lower case: not split

    def test_logo_corners_are_transparent(self):
        c = whatsnew.colours(False)
        key = whatsnew._rgb(c["key"])
        logo = whatsnew.app_logo(44, c["base_rgb"], key)
        self.assertEqual(logo.getpixel((0, 0)), key)          # the corner shows the acrylic
        self.assertNotEqual(logo.getpixel((22, 22)), key)     # the disc does not

    def test_button_states(self):
        imgs = {st: whatsnew.button_image(80, 32, 5, (76, 194, 255), st)
                for st in ("normal", "hover", "pressed")}
        n, p = imgs["normal"], imgs["pressed"]
        self.assertLess(n.getpixel((0, 0))[3], 16)              # rounded corner
        self.assertEqual(n.getpixel((40, 16))[3], 255)
        self.assertGreater(sum(n.getpixel((40, 0))[:3]), sum(n.getpixel((40, 31))[:3]))  # lit rim on top
        self.assertLess(p.getpixel((40, 0))[3], n.getpixel((40, 0))[3] - 150)   # pressed: smaller
        self.assertLess(sum(p.getpixel((40, 16))[:3]), sum(n.getpixel((40, 16))[:3]))   # and darker

    def test_smooth_scroll_eases_out_and_lands(self):
        pos, steps, moves = 0.0, 0, []
        while pos != 300:
            new, _ = whatsnew.scroll_step(pos, 300)
            moves.append(new - pos)
            pos, steps = new, steps + 1
            self.assertLess(steps, 60)
        self.assertEqual(moves[:-1], sorted(moves[:-1], reverse=True))   # slower and slower, then lands
        self.assertEqual(whatsnew.scroll_step(299.8, 300), (300, 300))
        self.assertLess(whatsnew.scroll_step(300, 0)[0], 300)    # and back up

    def test_scroll_speed_does_not_depend_on_the_frame_rate(self):
        def glide(frame_ms, total_ms=200):
            pos = 0.0
            for _ in range(int(total_ms / frame_ms)):
                pos, _t = whatsnew.scroll_step(pos, 300, whatsnew.ease_for(frame_ms))
            return pos
        self.assertAlmostEqual(glide(1000 / 60), glide(1000 / 144), delta=3)
        self.assertAlmostEqual(glide(1000 / 60), glide(1000 / 30), delta=3)
        self.assertAlmostEqual(whatsnew.ease_for(1000 / 60), whatsnew.SCROLL_EASE)

    def test_leave_only_counts_outside_the_window(self):
        win = types.SimpleNamespace(thumb_drag=None, x=100, y=100, w=460, h=600, calls=[])
        win.root = types.SimpleNamespace(winfo_pointerxy=lambda: win.pointer)
        win._set_hover = lambda part: win.calls.append(part)
        win.pointer = (520, 120)                   # over the close button: from catcher to window
        whatsnew.WhatsNewWindow._leave(win)
        self.assertEqual(win.calls, [])
        win.pointer = (1000, 120)                  # really gone
        whatsnew.WhatsNewWindow._leave(win)
        self.assertEqual(win.calls, [None])

    def test_scroll_bar_thin_to_wide(self):
        self.assertEqual(whatsnew.bar_look(0.0, 2, 6), (0.0, 2))
        self.assertEqual(whatsnew.bar_look(1.0, 2, 6), (1.0, 6))
        q, w = whatsnew.bar_look(0.5, 2, 6)
        self.assertEqual((q, w), (0.5, 4))
        self.assertEqual(whatsnew.bar_look(0.51, 2, 6)[0], 0.5)       # a few steps are drawn
        self.assertEqual(whatsnew.smoothstep(0), 0)
        self.assertEqual(whatsnew.smoothstep(2), 1)
        self.assertLess(whatsnew.smoothstep(0.1), 0.1)                 # soft start

    def test_close_button_outline(self):
        fill = (39, 43, 45)
        img = whatsnew.chip_image(32, 32, 5, fill, "hover", light=False)
        px = lambda x, y: img.getpixel((x, y))
        self.assertEqual(px(16, 16)[:3], fill)                       # an even face
        top, side = sum(px(16, 0)[:3]), sum(px(0, 16)[:3])
        self.assertGreater(top, sum(fill))                            # a light outline
        self.assertGreater(side, sum(fill))
        self.assertGreater(top, side)                                 # brighter at the ends
        self.assertGreater(sum(px(16, 31)[:3]), side)
        pressed = whatsnew.chip_image(32, 32, 5, fill, "pressed", light=False)
        self.assertLess(pressed.getpixel((16, 0))[3], img.getpixel((16, 0))[3])   # smaller
        light = whatsnew.chip_image(32, 32, 5, (230, 230, 230), "hover", light=True)
        self.assertLess(sum(light.getpixel((16, 0))[:3]), 3 * 230)    # a dark outline in light

    def test_scroll_thumb(self):
        self.assertEqual(whatsnew.thumb_geometry(300, 300, 900, 0, 28), (0, 100))
        self.assertEqual(whatsnew.thumb_geometry(300, 300, 900, 600, 28), (200, 100))   # at the end
        self.assertEqual(whatsnew.thumb_geometry(300, 300, 30000, 0, 28)[1], 28)        # never tiny
        pill = whatsnew.pill_image(6, 60, (200, 200, 200))
        self.assertLess(pill.getpixel((0, 0))[3], 16)           # round ends
        self.assertEqual(pill.getpixel((3, 30))[3], 255)

    def test_windows_accent_colour(self):
        # Windows' AccentPalette for a purple accent: 8 x RGBA
        purple = [(0xE6, 0xBC, 0xFF), (0xD0, 0x8C, 0xFF), (0xB1, 0x5F, 0xF0), (0x88, 0x17, 0x98),
                  (0x6B, 0x0B, 0x7A), (0x4B, 0x06, 0x57), (0x2A, 0x02, 0x33), (0x88, 0x17, 0x98)]
        data = b"".join(bytes(c) + b"\x00" for c in purple)
        pal = whatsnew.parse_accent_palette(data)
        self.assertEqual(pal, purple)
        dark, light = whatsnew.colours(False, pal), whatsnew.colours(True, pal)
        self.assertEqual(dark["accent"], "#d08cff")            # Light2: button in the dark theme
        self.assertEqual(dark["link"], "#e6bcff")              # Light3: links
        # the text follows the button, not the theme: dark on a light button, white on a
        # dark one; held down it is white
        self.assertEqual(dark["on_accent"], "#000000")         # light purple button
        self.assertEqual(light["accent"], "#6b0b7a")           # Dark1 in the light theme
        self.assertEqual(light["on_accent"], "#ffffff")        # dark purple button
        self.assertEqual(dark["on_accent_down"], "#ffffff")
        self.assertEqual(light["on_accent_down"], "#ffffff")
        # a light accent in the light theme: still dark text
        pale = whatsnew.palette_from_accent((0xFF, 0xE0, 0x80))
        self.assertEqual(whatsnew.colours(True, pale)["on_accent"], "#000000")
        # a dark shadow under the text, at rest as when pressed
        for c in (dark, light):
            self.assertLess(sum(whatsnew._rgb(c["emboss"])), sum(whatsnew._rgb(c["accent"])))
            self.assertLess(sum(whatsnew._rgb(c["emboss_down"])), sum(whatsnew._rgb(c["accent"])))
        self.assertIsNone(whatsnew.parse_accent_palette(b"short"))

    def test_theme_colours_flow_from_light_to_dark(self):
        light, dark = whatsnew.colours(True), whatsnew.colours(False)
        self.assertEqual(whatsnew.blend_colours(light, dark, 0), light)
        self.assertEqual(whatsnew.blend_colours(light, dark, 1), dark)
        mid = whatsnew.blend_colours(light, dark, 0.5)
        for key in ("text", "muted", "accent", "base"):
            m, a, b = (whatsnew._rgb(c[key]) for c in (mid, light, dark))
            for x, y, z in zip(m, a, b):
                self.assertTrue(min(y, z) <= x <= max(y, z), key)
        # the colour key is never mixed: one theme's or the other's
        self.assertEqual(whatsnew.blend_colours(light, dark, 0.49)["key"], light["key"])
        self.assertEqual(whatsnew.blend_colours(light, dark, 0.5)["key"], dark["key"])
        # the acrylic tint is mixed channel by channel (alpha too)
        tint = whatsnew.blend_colours(light, dark, 0.5)["tint_abgr"]
        for shift in (0, 8, 16, 24):
            a, b = (light["tint_abgr"] >> shift) & 0xFF, (dark["tint_abgr"] >> shift) & 0xFF
            self.assertTrue(min(a, b) <= (tint >> shift) & 0xFF <= max(a, b))

    def test_default_blue_when_windows_has_no_accent(self):
        self.assertEqual(whatsnew.colours(False, None)["accent"], "#4cc2ff")
        blue = whatsnew.palette_from_accent((0x00, 0x78, 0xD4))
        self.assertEqual(blue[whatsnew.BASE], (0x00, 0x78, 0xD4))
        self.assertGreater(sum(blue[whatsnew.LIGHT2]), sum(blue[whatsnew.BASE]))
        self.assertLess(sum(blue[whatsnew.DARK1]), sum(blue[whatsnew.BASE]))

    def test_colours_are_opaque_and_the_key_is_unused(self):
        for light in (False, True):
            c = whatsnew.colours(light)
            used = [v for k, v in c.items() if k not in ("key", "tint_abgr", "base_rgb", "base")]
            self.assertNotIn(c["key"], used)
            self.assertTrue(all(len(v) == 7 for v in used))


class AppUpdateTests(unittest.TestCase):
    def app(self, cfg=None, install_ok=True):
        app = make_app(cfg)
        app.update_progress = None
        app.update_manual = False
        app.update_lock = hb.threading.Lock()
        app.install_ok, app.install_why = install_ok, "" if install_ok else "running from source"
        app.notes = []
        app.notify_any = lambda text, title, key="": app.notes.append(text)
        app.refresh_menus = lambda: None
        app.opened = []
        app.open_update = lambda: app.opened.append(True)
        return app

    def test_menu_item_text(self):
        self.assertEqual(hb.update_item_text("1.15.0", True), "Update to v1.15.0")
        self.assertEqual(hb.update_item_text("1.15.0", False), "Download v1.15.0…")
        self.assertEqual(hb.update_item_text("1.15.0", True, 42), "Downloading v1.15.0… 42%")

    def test_manual_check_says_up_to_date(self):
        app = self.app()
        rel = updates.Release(hb.VERSION, "u")
        with mock.patch.object(hb, "save_config", lambda cfg: None), \
                mock.patch.object(hb.updates, "fetch_latest", lambda cur: rel):
            app.check_update(manual=True)
        self.assertEqual(app.notes, [f"You have the latest version (v{hb.VERSION})."])
        self.assertIsNone(app.update)

    def test_manual_check_repeats_the_offer_and_reports_errors(self):
        app = self.app({"update_notified": "99.0.0"})
        rel = updates.Release("99.0.0", "u")
        with mock.patch.object(hb, "save_config", lambda cfg: None), \
                mock.patch.object(hb.updates, "fetch_latest", lambda cur: rel):
            app.check_update(manual=False)
            self.assertEqual(app.notes, [])               # already told about 99.0.0
            app.check_update(manual=True)
        self.assertIn("Update to v99.0.0", app.notes[0])
        self.assertIs(app.update, rel)

        def offline(cur):
            raise OSError("offline")

        with mock.patch.object(hb, "save_config", lambda cfg: None), \
                mock.patch.object(hb.updates, "fetch_latest", offline):
            app.check_update(manual=True)
        self.assertEqual(app.notes[-1], "Could not check for updates: offline")

    def test_from_source_the_release_page_opens(self):
        app = self.app(install_ok=False)
        app.update = updates.Release("99.0.0", "u")
        app.start_update()
        self.assertEqual(app.opened, [True])
        self.assertIsNone(app.update_progress)

    def test_install_starts_the_staged_copy_and_exits(self):
        app = self.app()
        rel = updates.Release("99.0.0", "u", notes="n", zip_url="z")
        app.update = rel
        started, quit_ = [], []
        app.quit = lambda: quit_.append(True)
        with mock.patch.object(hb, "save_config", lambda cfg: None), \
                mock.patch.object(hb.updates, "download",
                                  lambda r, d, cur, progress: (progress(50), "C:/t/new")[1]), \
                mock.patch.object(hb.updates, "prepare_runner", lambda app_dir, st: "C:/t/runner"), \
                mock.patch.object(hb.updates, "start_detached", lambda cmd, cwd=None: started.append(cmd)):
            app.install_update()
        self.assertEqual(started[0][0], os.path.join("C:/t/runner", "HaloBattery.exe"))
        self.assertEqual(started[0][1:3], ["--finish-update", "--app-dir"])
        self.assertIn("C:/t/new", started[0])
        self.assertEqual(started[0][-1], str(os.getpid()))
        self.assertEqual(app.cfg["update_from"], hb.VERSION)
        self.assertEqual(app.cfg["whats_new_notes"]["version"], "99.0.0")
        self.assertEqual(quit_, [True])
        self.assertEqual(app.update_progress, 50)

    def test_failed_install_opens_the_page_and_keeps_running(self):
        app = self.app()
        app.update = updates.Release("99.0.0", "u", zip_url="z")
        app.quit = lambda: self.fail("must not exit")

        def broken(*a, **k):
            raise ValueError("download damaged")

        with mock.patch.object(hb, "save_config", lambda cfg: None), \
                mock.patch.object(hb.updates, "download", broken):
            app.install_update()
        self.assertIn("download damaged", app.notes[-1])
        self.assertEqual(app.opened, [True])
        self.assertIsNone(app.update_progress)

    def run_after_update(self, cfg, existed=True):
        app = self.app(cfg)
        app.config_existed = existed
        shown = []
        app.show_whats_new = lambda frm="": shown.append(frm)
        with mock.patch.object(hb, "save_config", lambda c: None):
            app.after_update()
        return app, shown

    def test_whats_new_after_an_update(self):
        app, shown = self.run_after_update({"last_version": "1.0.0", "update_from": "1.0.0"})
        self.assertEqual(shown, ["1.0.0"])
        self.assertEqual(app.cfg["last_version"], hb.VERSION)
        self.assertNotIn("update_from", app.cfg)
        # the next start shows nothing
        _, shown = self.run_after_update(dict(app.cfg))
        self.assertEqual(shown, [])

    def test_whats_new_after_a_manual_update_and_not_on_a_first_start(self):
        _, shown = self.run_after_update({"last_version": "1.0.0"})
        self.assertEqual(shown, ["1.0.0"])
        _, shown = self.run_after_update({}, existed=True)       # settings from before 1.15.0
        self.assertEqual(shown, [""])
        app, shown = self.run_after_update({}, existed=False)    # a new install
        self.assertEqual(shown, [])
        self.assertEqual(app.cfg["last_version"], hb.VERSION)

    def test_whats_new_can_be_turned_off_and_not_shown_for_a_downgrade(self):
        _, shown = self.run_after_update({"last_version": "1.0.0", "whats_new": False})
        self.assertEqual(shown, [])
        _, shown = self.run_after_update({"last_version": "99.0.0"})
        self.assertEqual(shown, [])


if __name__ == "__main__":
    unittest.main()
