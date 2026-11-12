"""Edge cases for the settings file: wrong types, out-of-range values, unknown keys,
damaged or empty files. The real %APPDATA% settings are never touched: CONFIG_PATH is
pointed at a temporary folder for every test.

Run from the repository root:

    python -m unittest discover -s tests
"""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tests/ helpers
import _sandbox  # noqa: E402

from test_hide_rename import hb, make_app  # noqa: E402


class ValidSettingTests(unittest.TestCase):
    def test_every_default_is_valid(self):
        for key, value in hb.DEFAULTS.items():
            with self.subTest(key=key):
                self.assertTrue(hb._valid_setting(key, value))

    def test_interval_bounds_and_types(self):
        lo, hi = hb.LIMITS["interval"]
        for value, expected in ((lo, True), (hi, True), (60, True), (lo - 1, False), (hi + 1, False),
                                (0, False), (-60, False), (10 ** 30, False), (60.0, False),
                                ("60", False), (True, False), (False, False), (None, False),
                                ([60], False), (float("nan"), False), (float("inf"), False)):
            with self.subTest(value=value):
                self.assertIs(hb._valid_setting("interval", value), expected)

    def test_low_bounds_and_types(self):
        for value, expected in ((0, True), (20, True), (100, True), (-1, False), (101, False),
                                (20.5, False), ("20", False), (True, False), (None, False)):
            with self.subTest(value=value):
                self.assertIs(hb._valid_setting("low", value), expected)

    def test_bool_settings_accept_only_bools(self):
        bool_keys = [k for k, v in hb.DEFAULTS.items() if isinstance(v, bool)]
        self.assertIn("notify", bool_keys)
        for key in bool_keys:
            for value, expected in ((True, True), (False, True), (1, False), (0, False), ("true", False),
                                    ("", False), (None, False), ([], False)):
                with self.subTest(key=key, value=value):
                    self.assertIs(hb._valid_setting(key, value), expected)

    def test_language_must_be_a_known_code(self):
        for value, expected in (("en", True), ("fr", True), ("zh-TW", True), ("xx", False), ("", False),
                                ("EN", False), ("zh_TW", False), (None, False), (5, False), (["en"], False)):
            with self.subTest(value=value):
                self.assertIs(hb._valid_setting("language", value), expected)

    def test_list_and_string_settings(self):
        for value, expected in (([], True), (["razer"], True), ((), False), ("razer", False),
                                ({}, False), (None, False)):
            with self.subTest(value=value):
                self.assertIs(hb._valid_setting("disabled_providers", value), expected)
        for value, expected in (("auto", True), ("white", True), ("", True), (3, False), (None, False),
                                (["auto"], False)):
            with self.subTest(value=value):
                self.assertIs(hb._valid_setting("icon_theme", value), expected)


class ConfigFileTestCase(unittest.TestCase):
    def setUp(self):
        self.dir = _sandbox.mkdtemp(prefix="halo_cfg_")
        self.path = os.path.join(self.dir, "config.json")
        patch = mock.patch.object(hb, "CONFIG_PATH", self.path)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, text, mode="w", **kwargs):
        with open(self.path, mode, **kwargs) as f:
            f.write(text)

    def damaged(self):
        return os.path.exists(self.path + ".bad")


class LoadConfigTests(ConfigFileTestCase):
    def test_the_real_settings_folder_is_not_used(self):
        real = os.path.join(os.environ.get("APPDATA", ""), "HaloBattery")
        self.assertFalse(os.path.normcase(os.path.abspath(hb.CONFIG_PATH)).startswith(os.path.normcase(real)))

    def test_files_that_are_not_a_json_object_are_kept_aside(self):
        for name, content in (("empty", b""), ("whitespace", b"  \n"), ("array", b"[1, 2]"),
                              ("null", b"null"), ("number", b"5"), ("string", b'"x"'),
                              ("truncated", b'{"interval": 30, "low":'), ("not json", b"hello"),
                              ("invalid utf-8", b'{"low": "\xff\xfe"}'), ("binary", bytes(range(256)))):
            with self.subTest(file=name):
                for leftover in (self.path, self.path + ".bad"):
                    if os.path.exists(leftover):
                        os.remove(leftover)
                self.write(content, "wb")
                self.assertEqual(hb.load_config(), hb.DEFAULTS)
                self.assertTrue(self.damaged())
                self.assertFalse(os.path.exists(self.path))

    def test_utf8_bom_from_a_text_editor_is_still_a_settings_file(self):
        # Windows PowerShell and older Notepad write UTF-8 with a BOM
        self.write(b"\xef\xbb\xbf" + json.dumps({"low": 35}).encode("utf-8"), "wb")
        self.assertEqual(hb.load_config()["low"], 35)
        self.assertFalse(self.damaged())

    def test_damaged_file_that_cannot_be_moved_is_never_overwritten(self):
        self.write("{oops")
        with mock.patch.object(hb.os, "replace", side_effect=PermissionError("locked")):
            self.assertEqual(hb.load_config(initialize_language=True), hb.DEFAULTS)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{oops")                 # and no language was saved over it

    def test_unreadable_path_gives_defaults_without_moving_anything(self):
        os.mkdir(self.path)                                     # a folder where the file should be
        self.assertEqual(hb.load_config(), hb.DEFAULTS)
        self.assertTrue(os.path.isdir(self.path))
        self.assertFalse(self.damaged())

    def test_wrong_values_fall_back_one_key_at_a_time(self):
        data = {"interval": 4, "low": 101, "notify": "yes", "bluetooth": 0, "language": "xx",
                "icon_theme": None, "disabled_providers": "razer", "badges": False, "full_alert": False}
        self.write(json.dumps(data))
        cfg = hb.load_config()
        for key in ("interval", "low", "notify", "bluetooth", "language", "icon_theme", "disabled_providers"):
            self.assertEqual(cfg[key], hb.DEFAULTS[key], key)
        self.assertIs(cfg["badges"], False)                     # the valid ones survive
        self.assertIs(cfg["full_alert"], False)
        self.assertFalse(self.damaged())

    def test_limits_are_inclusive(self):
        lo, hi = hb.LIMITS["interval"]
        for value in (lo, hi):
            self.write(json.dumps({"interval": value}))
            self.assertEqual(hb.load_config()["interval"], value)
        for value in (lo - 1, hi + 1):
            self.write(json.dumps({"interval": value}))
            self.assertEqual(hb.load_config()["interval"], hb.DEFAULTS["interval"])

    def test_json_extensions_python_accepts_are_still_rejected_as_values(self):
        self.write('{"interval": NaN, "low": Infinity, "notify": -Infinity}')
        cfg = hb.load_config()
        self.assertEqual((cfg["interval"], cfg["low"], cfg["notify"]),
                         (hb.DEFAULTS["interval"], hb.DEFAULTS["low"], hb.DEFAULTS["notify"]))

    def test_unknown_keys_are_kept_and_not_validated(self):
        self.write(json.dumps({"last_version": "1.16.0", "names": ["not", "a", "dict"], "future": {"x": 1}}))
        cfg = hb.load_config()
        self.assertEqual(cfg["last_version"], "1.16.0")
        self.assertEqual(cfg["future"], {"x": 1})
        self.assertEqual(cfg["names"], ["not", "a", "dict"])
        # ...and the code that reads them copes (hand-edited file)
        app = make_app(cfg)
        self.assertEqual(app._settings_map("names"), {})

    def test_the_language_is_detected_only_when_asked_and_only_when_absent(self):
        with mock.patch.object(hb.i18n, "detect_language", return_value="fr") as detect:
            self.assertEqual(hb.load_config()["language"], "en")
            detect.assert_not_called()
            self.assertFalse(os.path.exists(self.path))
            self.assertEqual(hb.load_config(initialize_language=True)["language"], "fr")
            with open(self.path, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["language"], "fr")
            detect.reset_mock()
            self.write(json.dumps({"language": "es"}))
            self.assertEqual(hb.load_config(initialize_language=True)["language"], "es")
            self.write(json.dumps({"language": "xx"}))             # present but invalid: not re-detected
            self.assertEqual(hb.load_config(initialize_language=True)["language"], "en")
            detect.assert_not_called()

    def test_language_is_detected_after_a_damaged_file_is_set_aside(self):
        self.write("{oops")
        with mock.patch.object(hb.i18n, "detect_language", return_value="ja"):
            self.assertEqual(hb.load_config(initialize_language=True)["language"], "ja")
        self.assertTrue(self.damaged())
        self.assertEqual(hb.load_config()["language"], "ja")


class SaveConfigTests(ConfigFileTestCase):
    def test_unwritable_folder_does_not_raise(self):
        with mock.patch.object(hb, "CONFIG_PATH", os.path.join(self.dir, "missing", "config.json")):
            hb.save_config(dict(hb.DEFAULTS))                    # no exception, nothing created
            self.assertFalse(os.path.exists(os.path.join(self.dir, "missing")))

    def test_values_json_cannot_hold_keep_the_old_file(self):
        hb.save_config(dict(hb.DEFAULTS, low=33))
        for bad in (object(), {1, 2}, b"bytes"):
            hb.save_config(dict(hb.DEFAULTS, low=bad))
        self.assertEqual(hb.load_config()["low"], 33)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_unicode_and_odd_names_round_trip(self):
        names = {"k1": "Écouteurs \U0001F3A7 \"Bureau\"", "k2": "日本語", "k3": "{x} %s \\ /"}
        hb.save_config(dict(hb.DEFAULTS, names=names))
        self.assertEqual(hb.load_config()["names"], names)

    def test_concurrent_saves_leave_a_valid_file(self):
        threads = [threading.Thread(target=hb.save_config, args=(dict(hb.DEFAULTS, low=i),))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertIn(hb.load_config()["low"], range(20))
        self.assertFalse(os.path.exists(self.path + ".tmp"))


class UpdateLoopSettingsTests(unittest.TestCase):
    """update_loop reads settings a user may have edited by hand."""

    def run_loop(self, cfg):
        app = make_app(dict(cfg, update_repo=hb.updates.REPO))
        app.stop_evt = mock.Mock()
        app.stop_evt.wait.return_value = False
        app.stop_evt.is_set.side_effect = [False, True]
        app.update_wake = mock.Mock()
        app.update_manual = False
        app.install_ok = False
        app.after_update = mock.Mock()
        app.check_update = mock.Mock()
        with mock.patch.object(hb, "save_config"):
            app.update_loop()
        return app

    def test_garbage_update_settings_do_not_kill_the_loop(self):
        for name, cfg in (("text time", {"update_last": "yesterday"}), ("list time", {"update_last": [1]}),
                          ("number latest", {"update_latest": 5}), ("list latest", {"update_latest": ["1.99"]}),
                          ("null latest and url", {"update_latest": None, "update_url": None})):
            with self.subTest(case=name):
                app = self.run_loop(cfg)
                self.assertIsNone(app.update)

    def test_unparsable_last_check_means_check_now(self):
        app = self.run_loop({"update_last": "yesterday"})
        app.check_update.assert_called_once_with(False)

    def test_recent_check_is_not_repeated(self):
        import time
        app = self.run_loop({"update_last": time.time()})
        app.check_update.assert_not_called()


if __name__ == "__main__":
    unittest.main()
