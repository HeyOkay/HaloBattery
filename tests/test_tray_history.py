"""Own-path tray history migration, using only a fake registry and temporary files."""
import base64
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from halo import tray_history


class _Key:
    def __init__(self, registry, name=None):
        self.registry, self.name = registry, name

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_READ, KEY_WRITE, KEY_SET_VALUE = 1, 2, 4
    REG_SZ, REG_EXPAND_SZ, REG_BINARY, REG_DWORD, REG_MULTI_SZ = 1, 2, 3, 4, 7

    def __init__(self):
        self.entries = {}
        self.deleted, self.written = [], []
        self.root_present = True
        self.denied = False
        self.before_delete = None
        self.enumerated_values = []

    def add(self, name, path, uid, *, promoted=None, children=()):
        values = {"ExecutablePath": (path, self.REG_SZ), "UID": (uid, self.REG_DWORD)}
        if promoted is not None:
            values["IsPromoted"] = (promoted, self.REG_DWORD)
        self.entries[name] = {"values": values, "children": list(children)}
        return values

    def OpenKey(self, parent, name, reserved=0, access=KEY_READ):
        if self.denied:
            raise PermissionError("registry denied")
        if parent is self.HKEY_CURRENT_USER:
            if name != tray_history.REGISTRY_PATH:
                raise AssertionError("unexpected registry root")
            if not self.root_present:
                raise FileNotFoundError(name)
            return _Key(self)
        if parent.name is not None:
            raise AssertionError("unexpected nested registry read")
        if name not in self.entries:
            raise FileNotFoundError(name)
        return _Key(self, name)

    def QueryInfoKey(self, key):
        if key.name is None:
            return len(self.entries), 0, 0
        entry = self.entries[key.name]
        return len(entry["children"]), len(entry["values"]), 0

    def EnumKey(self, key, index):
        names = sorted(self.entries) if key.name is None else self.entries[key.name]["children"]
        return names[index]

    def EnumValue(self, key, index):
        self.enumerated_values.append(key.name)
        name, (value, kind) = list(self.entries[key.name]["values"].items())[index]
        return name, copy.deepcopy(value), kind

    def QueryValueEx(self, key, name):
        try:
            return copy.deepcopy(self.entries[key.name]["values"][name])
        except KeyError:
            raise FileNotFoundError(name) from None

    def SetValueEx(self, key, name, reserved, kind, value):
        self.entries[key.name]["values"][name] = (value, kind)
        self.written.append((key.name, name, value))

    def DeleteKey(self, root, name):
        if self.before_delete:
            self.before_delete(name)
        if self.entries[name]["children"]:
            raise OSError("key has children")
        del self.entries[name]
        self.deleted.append(name)


class TrayHistoryTests(unittest.TestCase):
    exe = r"C:\Apps\HaloBattery\HaloBattery.exe"
    master_name = "900"

    def setUp(self):
        self.registry = FakeRegistry()
        registry_patch = mock.patch.object(tray_history, "_registry", return_value=self.registry)
        registry_patch.start()
        self.addCleanup(registry_patch.stop)
        self.temporary = tempfile.TemporaryDirectory(prefix="halo_tray_history_")
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)

    def master(self, **kwargs):
        return self.registry.add(self.master_name, self.exe, tray_history.MASTER_UID, **kwargs)

    def reconcile(self, prepared=None):
        return tray_history.reconcile(self.exe, self.data_dir, prepared)

    def backup(self):
        paths = list(self.data_dir.glob("tray-history-backup-*.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text(encoding="utf-8"))

    def test_waits_for_explorer_master_without_creating_or_removing_anything(self):
        self.registry.add("1", self.exe, 0)
        self.assertEqual(self.reconcile().status, "pending")
        self.assertEqual(self.registry.deleted, [])
        self.assertEqual(self.registry.written, [])
        self.assertEqual(list(self.data_dir.iterdir()), [])
        self.registry.root_present = False
        self.assertEqual(self.reconcile().status, "pending")

    def test_removes_all_old_own_uids_preserving_existing_master_exactly(self):
        master = self.master(promoted=0)
        master["InitialTooltip"] = ("Halo Battery", self.registry.REG_SZ)
        before = copy.deepcopy(self.registry.entries[self.master_name])
        for name, uid in (("1", 0), ("2", 1140632680), ("3", 0xFFFFFFFF)):
            self.registry.add(name, self.exe, uid, promoted=1)
        result = self.reconcile()
        self.assertEqual((result.status, result.removed), ("done", 3))
        self.assertEqual(self.registry.entries[self.master_name], before)
        self.assertEqual(self.registry.written, [])
        self.assertEqual({record["key"] for record in self.backup()["batches"][0]["records"]},
                         {"1", "2", "3"})

    def test_exact_path_normalization_does_not_match_other_executables_or_locations(self):
        self.master()
        self.registry.add("1", self.exe.upper().replace("\\", "/"), 1)
        for name, path in (("2", r"D:\Apps\HaloBattery\HaloBattery.exe"),
                           ("3", r"C:\Apps\HaloBattery\HaloBattery-Test.exe"),
                           ("4", r"C:\Apps\HaloBattery-copy\HaloBattery.exe"),
                           ("5", r"%LOCALAPPDATA%\HaloBattery\HaloBattery.exe")):
            self.registry.add(name, path, 1)
        before = copy.deepcopy(self.registry.entries)
        self.assertEqual(self.reconcile().removed, 1)
        for name in ("2", "3", "4", "5", self.master_name):
            self.assertEqual(self.registry.entries[name], before[name])

    def test_identity_filter_does_not_read_other_apps_icon_snapshots(self):
        self.master()
        unrelated = self.registry.add("1", r"D:\Other\TrayApp.exe", 7)
        unrelated["IconSnapshot"] = (b"other-app-image", self.registry.REG_BINARY)
        malformed = self.registry.add("2", self.exe, "7")
        malformed["IconSnapshot"] = (b"invalid-identity-image", self.registry.REG_BINARY)
        self.registry.add("3", self.exe, 7)["UID"] = (7, self.registry.REG_SZ)
        self.registry.add("4", self.exe, 7).pop("UID")
        self.registry.add("5", self.exe, 7).pop("ExecutablePath")
        self.registry.add("6", self.exe, 7)
        prepared = tray_history.prepare(self.exe)
        self.assertEqual([entry.name for entry in prepared.legacy], ["6"])
        self.assertNotIn("1", self.registry.enumerated_values)
        self.assertNotIn("2", self.registry.enumerated_values)
        self.assertNotIn("3", self.registry.enumerated_values)
        self.assertNotIn("4", self.registry.enumerated_values)
        self.assertNotIn("5", self.registry.enumerated_values)
        self.assertIn("6", self.registry.enumerated_values)

    def test_identity_is_rechecked_after_metadata_filter_before_snapshot(self):
        self.master()
        values = self.registry.add("1", self.exe, 7)
        original_read = tray_history._read_entry

        def changed_before_snapshot(registry, root, name):
            if name == "1":
                values["ExecutablePath"] = (r"D:\Other\TrayApp.exe", self.registry.REG_SZ)
            return original_read(registry, root, name)

        with mock.patch.object(tray_history, "_read_entry", side_effect=changed_before_snapshot):
            self.assertEqual(self.reconcile().removed, 0)
        self.assertIn("1", self.registry.entries)
        self.assertEqual(list(self.data_dir.iterdir()), [])

    def test_refuses_shared_python_relative_unc_and_unresolved_paths(self):
        self.master()
        for path in (r"C:\Python\python.exe", r"C:\Python\pythonw.exe",
                     r"C:\Python\python3.12.exe", r"C:halo.exe", "HaloBattery.exe",
                     r"\\server\apps\HaloBattery.exe", r"\\?\C:\Apps\HaloBattery.exe",
                     r"C:\%APP%\HaloBattery.exe", "C:\\Apps\\halo.exe\0", None):
            with self.subTest(path=path):
                self.assertEqual(tray_history.reconcile(path, self.data_dir).status, "skipped")
                self.assertFalse(tray_history.prepare(path).master_absent)
        self.assertEqual(self.registry.deleted, [])

    def test_preserves_malformed_guid_nested_and_unknown_key_records(self):
        self.master()
        self.registry.add("1", self.exe, 1, children=("child",))
        self.registry.add("2", self.exe, 2)["IconGuid"] = ("{guid}", self.registry.REG_SZ)
        self.registry.add("3", self.exe, 3)["UID"] = (3, self.registry.REG_SZ)
        self.registry.add("4", self.exe, True)
        self.registry.add("5", self.exe, -1)
        self.registry.add("6", self.exe, 0x100000000)
        self.registry.add("7", self.exe, 7)["ExecutablePath"] = (self.exe, self.registry.REG_EXPAND_SZ)
        self.registry.add("8", self.exe, 8).pop("UID")
        self.registry.add("9", self.exe, 9).pop("ExecutablePath")
        for name in ("01", "not-numeric", "18446744073709551616"):
            self.registry.add(name, self.exe, 10)
        before = copy.deepcopy(self.registry.entries)
        self.assertEqual(self.reconcile().removed, 0)
        self.assertEqual(self.registry.entries, before)
        self.assertEqual(list(self.data_dir.iterdir()), [])

    def test_ambiguous_duplicate_master_rows_block_cleanup(self):
        self.master()
        self.registry.add("901", self.exe, tray_history.MASTER_UID)
        self.registry.add("1", self.exe, 0)
        self.assertEqual(self.reconcile().status, "skipped")
        self.assertEqual(self.registry.deleted, [])

    def test_backup_captures_all_value_types_and_original_bytes(self):
        self.master()
        values = self.registry.add("1", self.exe, 0, promoted=1)
        values.update({"IconSnapshot": (b"\x00\xffPNG", self.registry.REG_BINARY),
                       "InitialTooltip": ("Мышь 🎮", self.registry.REG_SZ),
                       "OtherStrings": (["one", "два"], self.registry.REG_MULTI_SZ),
                       "UnknownValue": (None, 0)})
        self.assertEqual(self.reconcile().removed, 1)
        record = self.backup()["batches"][0]["records"][0]
        backed = {value["name"]: value for value in record["values"]}
        self.assertEqual(base64.b64decode(backed["IconSnapshot"]["value"]["base64"]), b"\x00\xffPNG")
        self.assertEqual(backed["IconSnapshot"]["type"], self.registry.REG_BINARY)
        self.assertEqual(backed["InitialTooltip"]["value"], "Мышь 🎮")
        self.assertEqual(backed["OtherStrings"]["value"], ["one", "два"])
        self.assertIsNone(backed["UnknownValue"]["value"])

    def test_backup_failure_prevents_every_registry_mutation(self):
        self.registry.add("1", self.exe, 0, promoted=1)
        prepared = tray_history.prepare(self.exe)
        self.master()
        before = copy.deepcopy(self.registry.entries)
        with mock.patch.object(tray_history.os, "replace", side_effect=PermissionError("blocked")):
            result = self.reconcile(prepared)
        self.assertEqual(result.status, "error")
        self.assertEqual(self.registry.entries, before)
        self.assertEqual(list(self.data_dir.iterdir()), [])

    def test_recheck_preserves_changed_disappeared_and_newly_nested_records(self):
        self.master()
        for name in ("1", "2", "3", "4"):
            self.registry.add(name, self.exe, int(name))
        original_backup = tray_history._backup

        def changed_after_backup(*args):
            original_backup(*args)
            self.registry.entries["1"]["values"]["IsPromoted"] = (0, self.registry.REG_DWORD)
            del self.registry.entries["2"]
            self.registry.entries["3"]["children"].append("new")

        with mock.patch.object(tray_history, "_backup", side_effect=changed_after_backup):
            result = self.reconcile()
        self.assertEqual((result.removed, result.skipped), (1, 3))
        self.assertEqual(self.registry.deleted, ["4"])
        self.assertIn("1", self.registry.entries)
        self.assertIn("3", self.registry.entries)

    def test_delete_remains_nonrecursive_when_children_appear_at_last_moment(self):
        self.master()
        self.registry.add("1", self.exe, 0)
        self.registry.before_delete = lambda name: self.registry.entries[name]["children"].append("late")
        self.assertEqual((self.reconcile().removed, self.registry.deleted), (0, []))
        self.assertIn("1", self.registry.entries)

    def test_missing_or_replaced_master_after_backup_preserves_legacy(self):
        for change in ("missing", "path", "uid", "children", "guid"):
            with self.subTest(change=change):
                self.registry.entries.clear()
                self.master()
                self.registry.add("1", self.exe, 0)
                original_backup = tray_history._backup

                def change_master(*args):
                    original_backup(*args)
                    master = self.registry.entries[self.master_name]
                    if change == "missing":
                        del self.registry.entries[self.master_name]
                    elif change == "path":
                        master["values"]["ExecutablePath"] = (r"D:\other.exe", self.registry.REG_SZ)
                    elif change == "uid":
                        master["values"]["UID"] = (42, self.registry.REG_DWORD)
                    elif change == "children":
                        master["children"].append("new")
                    else:
                        master["values"]["IconGuid"] = ("guid", self.registry.REG_SZ)

                with mock.patch.object(tray_history, "_backup", side_effect=change_master):
                    self.assertEqual(self.reconcile().removed, 0)
                self.assertIn("1", self.registry.entries)

    def test_branded_master_migrates_prototype_visibility_and_backs_it_up(self):
        for promoted in (0, 1):
            with self.subTest(promoted=promoted):
                self.registry.entries.clear()
                self.registry.add("7", self.exe, 0x48424D00, promoted=promoted)
                prepared = tray_history.prepare(self.exe)
                self.assertTrue(prepared.master_absent)
                master = self.master()
                result = self.reconcile(prepared)
                self.assertTrue(result.visibility_migrated)
                self.assertEqual(master["IsPromoted"], (promoted, self.registry.REG_DWORD))
                self.assertEqual(set(self.registry.entries), {self.master_name})
                backed = [record for batch in self.backup()["batches"] for record in batch["records"]]
                self.assertTrue(any(record["key"] == "7" for record in backed))

    def test_new_master_inherits_any_promoted_legacy_preference(self):
        self.registry.add("1", self.exe, 0, promoted=0)
        self.registry.add("2", self.exe, 22, promoted=1)
        prepared = tray_history.prepare(self.exe)
        master = self.master()
        result = self.reconcile(prepared)
        self.assertTrue(result.visibility_migrated)
        self.assertEqual(master["IsPromoted"], (1, self.registry.REG_DWORD))
        records = self.backup()["batches"][0]["records"]
        self.assertEqual({record["key"] for record in records}, {"1", "2", self.master_name})
        original_master = next(record for record in records if record["key"] == self.master_name)
        self.assertNotIn("IsPromoted", [value["name"] for value in original_master["values"]])

    def test_new_master_inherits_explicit_unpromoted_legacy_preference(self):
        self.registry.add("1", self.exe, 0, promoted=0)
        prepared = tray_history.prepare(self.exe)
        master = self.master()
        self.assertTrue(self.reconcile(prepared).visibility_migrated)
        self.assertEqual(master["IsPromoted"][0], 0)

    def test_existing_master_with_absent_preference_is_never_changed(self):
        master = self.master()
        self.registry.add("1", self.exe, 0, promoted=1)
        prepared = tray_history.prepare(self.exe)
        self.assertFalse(prepared.master_absent)
        self.assertFalse(self.reconcile(prepared).visibility_migrated)
        self.assertNotIn("IsPromoted", master)
        self.assertEqual(self.registry.written, [])

    def test_new_master_explicit_preference_wins_over_prepared_visibility(self):
        self.registry.add("1", self.exe, 0, promoted=1)
        prepared = tray_history.prepare(self.exe)
        master = self.master(promoted=0)
        self.assertFalse(self.reconcile(prepared).visibility_migrated)
        self.assertEqual(master["IsPromoted"][0], 0)

    def test_missing_preparation_wrong_path_and_changed_legacy_never_seed_visibility(self):
        for scenario in ("missing", "wrong_path", "changed"):
            with self.subTest(scenario=scenario):
                self.registry.entries.clear()
                self.registry.add("1", self.exe, 0, promoted=1)
                prepared = tray_history.prepare(self.exe)
                if scenario == "missing":
                    prepared = None
                elif scenario == "wrong_path":
                    prepared = tray_history.PreparedMigration(r"d:\other.exe", True, 1)
                else:
                    self.registry.entries["1"]["values"]["IsPromoted"] = (0, self.registry.REG_DWORD)
                master = self.master()
                self.assertFalse(self.reconcile(prepared).visibility_migrated)
                self.assertNotIn("IsPromoted", master)

    def test_master_user_change_after_backup_is_preserved(self):
        self.registry.add("1", self.exe, 0, promoted=1)
        prepared = tray_history.prepare(self.exe)
        master = self.master()
        original_backup = tray_history._backup

        def change_master(*args):
            original_backup(*args)
            master["IsPromoted"] = (0, self.registry.REG_DWORD)

        with mock.patch.object(tray_history, "_backup", side_effect=change_master):
            self.assertFalse(self.reconcile(prepared).visibility_migrated)
        self.assertEqual(master["IsPromoted"][0], 0)

    def test_repeated_cleanup_is_idempotent_and_does_not_grow_backup(self):
        self.master()
        self.registry.add("1", self.exe, 0)
        self.assertEqual(self.reconcile().removed, 1)
        path = next(self.data_dir.glob("*.json"))
        before = path.read_bytes()
        for _ in range(3):
            self.assertEqual(self.reconcile().removed, 0)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.backup()["batches"]), 1)

    def test_failed_deletion_reuses_identical_backup_batch(self):
        self.master()
        self.registry.add("1", self.exe, 0)
        with mock.patch.object(self.registry, "DeleteKey", side_effect=PermissionError("busy")):
            self.assertEqual(self.reconcile().skipped, 1)
            self.assertEqual(self.reconcile().skipped, 1)
        self.assertEqual(len(self.backup()["batches"]), 1)
        self.assertEqual(self.reconcile().removed, 1)
        self.assertEqual(len(self.backup()["batches"]), 1)

    def test_bounded_backup_refuses_more_cleanup_without_discarding_old_backups(self):
        self.master()
        self.registry.add("1", self.exe, 0)
        self.reconcile()
        path = next(self.data_dir.glob("*.json"))
        original = path.read_bytes()
        self.registry.add("2", self.exe, 2)
        with mock.patch.object(tray_history, "MAX_BACKUP_BATCHES", 1):
            self.assertEqual(self.reconcile().status, "error")
        self.assertIn("2", self.registry.entries)
        self.assertEqual(path.read_bytes(), original)
        with mock.patch.object(tray_history, "MAX_BACKUP_BYTES", 1):
            self.assertEqual(self.reconcile().status, "error")
        self.assertEqual(path.read_bytes(), original)

    def test_corrupt_backup_is_preserved_and_stops_cleanup(self):
        self.master()
        self.registry.add("1", self.exe, 0)
        self.reconcile()
        path = next(self.data_dir.glob("*.json"))
        for content in (b"{broken", b"[]", b'{"format":2,"batches":[]}'):
            with self.subTest(content=content):
                path.write_bytes(content)
                self.registry.add("2", self.exe, 2)
                self.assertEqual(self.reconcile().status, "error")
                self.assertIn("2", self.registry.entries)
                self.assertEqual(path.read_bytes(), content)

    def test_registry_access_failure_and_unavailable_backend_are_nonfatal(self):
        self.registry.denied = True
        self.assertEqual(self.reconcile().status, "error")
        self.assertFalse(tray_history.prepare(self.exe).master_absent)
        with mock.patch.object(tray_history, "_registry", return_value=None):
            self.assertEqual(self.reconcile().status, "skipped")
            self.assertFalse(tray_history.prepare(self.exe).master_absent)


if __name__ == "__main__":
    unittest.main()
