"""Startup migrations use an in-memory Run key and temporary config files only."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from test_hide_rename import hb


class _RunKey:
    def __init__(self, access):
        self.access = access

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeRunRegistry:
    HKEY_CURRENT_USER = object()
    KEY_QUERY_VALUE, KEY_SET_VALUE, REG_SZ = 1, 2, 1

    def __init__(self):
        self.values = {}
        self.mutations = []
        self.deny_open = False
        self.deny_write = False
        self.deny_delete = False

    def OpenKey(self, root, path, reserved=0, access=KEY_QUERY_VALUE):
        if root is not self.HKEY_CURRENT_USER or path != hb.RUN_KEY or reserved != 0:
            raise AssertionError("only the fake HKCU Run key is permitted")
        if self.deny_open:
            raise PermissionError("fake registry access denied")
        return _RunKey(access)

    def QueryValueEx(self, key, name):
        if not key.access & self.KEY_QUERY_VALUE:
            raise AssertionError("query without read access")
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        if not key.access & self.KEY_SET_VALUE or kind != self.REG_SZ or reserved != 0:
            raise AssertionError("invalid Run value write")
        if self.deny_write:
            raise PermissionError("fake Run value write denied")
        self.values[name] = value
        self.mutations.append(("set", name, value))

    def DeleteValue(self, key, name):
        if not key.access & self.KEY_SET_VALUE:
            raise AssertionError("delete without write access")
        if self.deny_delete:
            raise PermissionError("fake Run value deletion denied")
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]
        self.mutations.append(("delete", name))


class AutostartMigrationTests(unittest.TestCase):
    def setUp(self):
        self.registry = FakeRunRegistry()
        patches = (
            mock.patch.dict(sys.modules, {"winreg": self.registry}),
            mock.patch.object(hb.sys, "platform", "win32"),
            mock.patch.object(hb.sys, "frozen", True, create=True),
            mock.patch.object(hb.sys, "executable", r"C:\Tools\Halo Battery\HaloBattery.exe"),
            mock.patch.object(hb, "running_from_temp", return_value=False),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.registry.values["UnrelatedApp"] = '"C:\\Other\\app.exe" --quiet'
        self.unrelated = self.registry.values["UnrelatedApp"]

    def tearDown(self):
        self.assertEqual(self.registry.values["UnrelatedApp"], self.unrelated)

    def test_enable_disable_and_repeated_disable_only_touch_own_value(self):
        self.assertFalse(hb.autostart_enabled())
        self.assertTrue(hb.set_autostart(True))
        self.assertEqual(self.registry.values[hb.APP_NAME], '"C:\\Tools\\Halo Battery\\HaloBattery.exe"')
        self.assertTrue(hb.autostart_enabled())
        self.assertTrue(hb.set_autostart(False))
        self.assertTrue(hb.set_autostart(False))
        self.assertFalse(hb.autostart_enabled())

    def test_disable_permission_denial_propagates_and_retains_run_entry(self):
        current = self.registry.values[hb.APP_NAME] = '"chosen.exe" --saved-option'
        self.registry.deny_delete = True
        with self.assertRaises(PermissionError):
            hb.set_autostart(False)
        self.assertEqual(self.registry.values[hb.APP_NAME], current)
        self.assertFalse(self.registry.mutations)

    def test_temp_copy_rejects_enable_and_both_migrations_without_mutation(self):
        self.registry.values.update({hb.APP_NAME: '"old.exe"', hb.LEGACY_NAME: '"legacy.exe"'})
        before = dict(self.registry.values)
        with mock.patch.object(hb, "running_from_temp", return_value=True):
            with self.assertLogs("halo_battery", level="WARNING"):
                self.assertFalse(hb.set_autostart(True))
            self.assertFalse(hb.migrate_legacy_autostart())
            self.assertFalse(hb.refresh_autostart())
            self.assertEqual(self.registry.values, before)
            self.assertTrue(hb.set_autostart(False))
        self.assertNotIn(hb.APP_NAME, self.registry.values)
        self.assertIn(hb.LEGACY_NAME, self.registry.values)

    def test_non_windows_never_opens_even_fake_registry(self):
        with mock.patch.object(hb.sys, "platform", "linux"), \
                mock.patch.object(self.registry, "OpenKey", side_effect=AssertionError("registry touched")):
            self.assertFalse(hb.autostart_enabled())
            self.assertTrue(hb.set_autostart(True))
            self.assertFalse(hb.migrate_legacy_autostart())
            self.assertFalse(hb.refresh_autostart())

    def test_legacy_migration_writes_new_command_before_removing_old_and_is_once(self):
        self.registry.values[hb.LEGACY_NAME] = '"C:\\Old\\BatteryTray.exe"'
        self.assertTrue(hb.migrate_legacy_autostart())
        self.assertEqual(self.registry.values[hb.APP_NAME], hb._launch_command())
        self.assertNotIn(hb.LEGACY_NAME, self.registry.values)
        self.assertEqual([event[0] for event in self.registry.mutations], ["set", "delete"])
        mutations = list(self.registry.mutations)
        self.assertFalse(hb.migrate_legacy_autostart())
        self.assertEqual(self.registry.mutations, mutations)

    def test_absent_legacy_never_enables_autostart(self):
        self.assertFalse(hb.migrate_legacy_autostart())
        self.assertNotIn(hb.APP_NAME, self.registry.values)
        self.assertFalse(self.registry.mutations)

    def test_current_entry_wins_when_legacy_and_current_both_exist(self):
        current = self.registry.values[hb.APP_NAME] = '"C:\\Chosen\\HaloBattery.exe" --user-option'
        self.registry.values[hb.LEGACY_NAME] = '"legacy.exe"'
        self.assertTrue(hb.migrate_legacy_autostart())
        self.assertEqual(self.registry.values[hb.APP_NAME], current)
        self.assertNotIn(hb.LEGACY_NAME, self.registry.values)
        self.assertEqual(self.registry.mutations, [("delete", hb.LEGACY_NAME)])

    def test_legacy_delete_failure_keeps_successful_new_entry_and_can_retry(self):
        legacy = self.registry.values[hb.LEGACY_NAME] = '"legacy.exe"'
        self.registry.deny_delete = True
        with self.assertLogs("halo_battery", level="WARNING"):
            self.assertFalse(hb.migrate_legacy_autostart())
        self.assertEqual(self.registry.values[hb.APP_NAME], hb._launch_command())
        self.assertEqual(self.registry.values[hb.LEGACY_NAME], legacy)
        self.registry.deny_delete = False
        self.assertTrue(hb.migrate_legacy_autostart())
        self.assertEqual(self.registry.values[hb.APP_NAME], hb._launch_command())
        self.assertNotIn(hb.LEGACY_NAME, self.registry.values)
        self.assertEqual([event[0] for event in self.registry.mutations], ["set", "delete"])

    def test_failed_new_value_write_preserves_legacy_for_retry(self):
        old = self.registry.values[hb.LEGACY_NAME] = '"legacy.exe" --saved-option'
        self.registry.deny_write = True
        with self.assertLogs("halo_battery", level="WARNING"):
            self.assertFalse(hb.migrate_legacy_autostart())
        self.assertEqual(self.registry.values.get(hb.LEGACY_NAME), old)
        self.assertNotIn(hb.APP_NAME, self.registry.values)
        self.assertFalse(self.registry.mutations)

    def test_rejected_enable_does_not_report_success_or_remove_legacy(self):
        self.registry.values[hb.LEGACY_NAME] = '"legacy.exe"'
        with mock.patch.object(hb, "set_autostart", return_value=False):
            self.assertFalse(hb.migrate_legacy_autostart())
        self.assertIn(hb.LEGACY_NAME, self.registry.values)

    def test_registry_access_denial_is_nonfatal_and_preserves_values(self):
        self.registry.values[hb.LEGACY_NAME] = '"legacy.exe"'
        before = dict(self.registry.values)
        self.registry.deny_open = True
        self.assertFalse(hb.autostart_enabled())
        with self.assertLogs("halo_battery", level="WARNING"):
            self.assertFalse(hb.migrate_legacy_autostart())
        self.assertFalse(hb.refresh_autostart())
        self.assertEqual(self.registry.values, before)

    def test_refresh_repoints_only_enabled_frozen_copy_and_is_idempotent(self):
        self.assertFalse(hb.refresh_autostart())
        self.assertFalse(self.registry.mutations)
        self.registry.values[hb.APP_NAME] = '"C:\\Old\\HaloBattery.exe"'
        self.assertTrue(hb.refresh_autostart())
        self.assertEqual(self.registry.values[hb.APP_NAME], hb._launch_command())
        self.assertFalse(hb.refresh_autostart())
        self.registry.values[hb.APP_NAME] = "  " + hb._launch_command().upper() + "  "
        self.assertFalse(hb.refresh_autostart())
        self.assertEqual(len(self.registry.mutations), 1)
        with mock.patch.object(hb.sys, "frozen", False):
            self.assertFalse(hb.refresh_autostart())

    def test_refresh_write_failure_and_false_result_keep_existing_entry(self):
        old = self.registry.values[hb.APP_NAME] = '"old.exe"'
        self.registry.deny_write = True
        with self.assertLogs("halo_battery", level="WARNING"):
            self.assertFalse(hb.refresh_autostart())
        self.assertEqual(self.registry.values[hb.APP_NAME], old)
        with mock.patch.object(hb, "set_autostart", return_value=False):
            self.assertFalse(hb.refresh_autostart())
        self.assertEqual(self.registry.values[hb.APP_NAME], old)

    def test_source_launch_uses_quoted_pythonw_when_available_and_python_otherwise(self):
        with mock.patch.object(hb.sys, "frozen", False), \
                mock.patch.object(hb.sys, "executable", r"C:\Python Install\python.exe"):
            pythonw = str(Path(hb.sys.executable).with_name("pythonw.exe"))
            with mock.patch.object(hb.os.path, "exists", side_effect=lambda path: str(path) == pythonw):
                self.assertEqual(hb._launch_command(), f'"{pythonw}" "{Path(hb.__file__).resolve()}"')
            with mock.patch.object(hb.os.path, "exists", return_value=False):
                self.assertEqual(hb._launch_command(), f'"{hb.sys.executable}" "{Path(hb.__file__).resolve()}"')


class LegacyConfigMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.old = root / hb.LEGACY_NAME / "config.json"
        self.old.parent.mkdir()
        self.current = root / hb.APP_NAME / "config.json"
        self.current.parent.mkdir()
        for patch in (mock.patch.object(hb, "APPDATA_DIR", str(root)),
                      mock.patch.object(hb, "CONFIG_PATH", str(self.current))):
            patch.start()
            self.addCleanup(patch.stop)

    def test_first_copy_preserves_unicode_unknown_keys_and_original_bytes_once(self):
        settings = {"names": {"mouse": "Écouteurs 日本語"}, "interval": 30,
                    "future": {"retained": True}, "language": "fr"}
        original = b"\xef\xbb\xbf" + json.dumps(settings, ensure_ascii=False).encode("utf-8")
        self.old.write_bytes(original)
        self.assertTrue(hb.migrate_legacy_config())
        self.assertEqual(self.current.read_bytes(), original)
        self.assertEqual(self.old.read_bytes(), original)
        loaded = hb.load_config()
        for key, value in settings.items():
            self.assertEqual(loaded[key], value)
        self.assertFalse(hb.migrate_legacy_config())
        self.assertEqual(self.current.read_bytes(), original)

    def test_existing_current_settings_win_over_legacy(self):
        self.old.write_text('{"low": 10}', encoding="utf-8")
        self.current.write_text('{"low": 75}', encoding="utf-8")
        self.assertFalse(hb.migrate_legacy_config())
        self.assertEqual(hb.load_config()["low"], 75)
        self.assertEqual(self.old.read_text(encoding="utf-8"), '{"low": 10}')

    def test_current_created_during_copy_wins_without_losing_source_or_leaving_temp(self):
        original = b'{"names":{"mouse":"Legacy"},"low":15}'
        competing = b'{"names":{"mouse":"Newly saved"},"low":75}'
        self.old.write_bytes(original)
        copyfile = shutil.copyfile
        def copy_then_create_current(source, destination):
            result = copyfile(source, destination)
            self.current.write_bytes(competing)
            return result
        with mock.patch("shutil.copyfile", side_effect=copy_then_create_current) as copied:
            self.assertFalse(hb.migrate_legacy_config())
        copied.assert_called_once()
        self.assertEqual(self.current.read_bytes(), competing)
        self.assertEqual(self.old.read_bytes(), original)
        self.assertEqual(list(self.current.parent.iterdir()), [self.current])

    def test_missing_source_and_copy_access_failure_leave_settings_untouched(self):
        self.assertFalse(hb.migrate_legacy_config())
        self.assertFalse(self.current.exists())
        original = b'{"low": 33}'
        self.old.write_bytes(original)
        with mock.patch("shutil.copyfile", side_effect=PermissionError("fake read-only profile")):
            self.assertFalse(hb.migrate_legacy_config())
        self.assertEqual(self.old.read_bytes(), original)
        self.assertFalse(self.current.exists())

    def test_partial_failed_copy_cannot_leave_a_current_config_that_blocks_retry(self):
        original = b'{"names":{"mouse":"Saved"},"low":33}'
        self.old.write_bytes(original)
        def partial_copy(source, destination):
            Path(destination).write_bytes(b'{"names":')
            raise OSError("simulated full disk during copy")
        with mock.patch("shutil.copyfile", side_effect=partial_copy):
            self.assertFalse(hb.migrate_legacy_config())
        self.assertEqual(self.old.read_bytes(), original)
        self.assertFalse(self.current.exists())
        self.assertEqual(list(self.current.parent.iterdir()), [])
        self.assertTrue(hb.migrate_legacy_config())
        self.assertEqual(self.current.read_bytes(), original)
