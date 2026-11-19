"""Real temporary-file swaps/rollback/startup cleanup; no network or processes."""
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import _sandbox
from halo import updates
from test_hide_rename import hb, make_app


class FileRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="update-recovery-", dir=_sandbox.root())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.app, self.new = self.root / "app", self.root / "new"
        for folder, exe, library in ((self.app, b"old executable", b"old library"),
                                     (self.new, b"new executable", b"new library")):
            (folder / updates.INTERNAL).mkdir(parents=True)
            (folder / updates.EXE_NAME).write_bytes(exe)
            (folder / updates.INTERNAL / "library.dll").write_bytes(library)
        for name in ("portable.txt", "config.json", "history.json", "settings-window.json", "notes.old"):
            (self.app / name).write_bytes(b"user data: " + name.encode())
        (self.app / "iconify").mkdir()
        (self.app / "iconify" / "chosen.png").write_bytes(b"saved artwork")
        self.starts = []

    def finish(self, start=None):
        return updates.finish_update(str(self.app), 123, str(self.new),
                                     start=start or self.starts.append,
                                     wait=lambda pid: None, sleep=lambda delay: None)

    def state(self):
        return json.loads((self.app / updates.UPDATE_STATE).read_text(encoding="utf-8"))

    def failed_restore(self, name=updates.EXE_NAME):
        original_replace = os.replace

        def replace(source, target):
            if Path(source).name == name + updates.OLD_SUFFIX:
                raise PermissionError("backup cannot be restored")
            return original_replace(source, target)

        def broken_copy(source, target):
            Path(target).mkdir()
            raise OSError("replacement copy interrupted")

        with mock.patch.object(updates.os, "replace", side_effect=replace), \
                mock.patch.object(updates.shutil, "copytree", side_effect=broken_copy), \
                mock.patch.object(updates, "RETRY_S", 0):
            self.assertFalse(self.finish())
        self.assertEqual(self.state()["state"], "failed")

    def startup(self, *, busy=False):
        app = make_app({"update_staging": str(self.new), "update_check": False})
        app.after_update = mock.Mock()
        app.update_lock = threading.Lock()
        app.update_manual = False
        app.stop_evt = threading.Event()
        app.update_wake = SimpleNamespace(clear=lambda: None,
                                          wait=lambda timeout: app.stop_evt.set())
        if busy:
            app.update_lock.acquire()
            self.addCleanup(app.update_lock.release)
        with mock.patch.object(app.stop_evt, "wait", return_value=False), \
                mock.patch.object(hb.sys, "frozen", True, create=True), \
                mock.patch.object(hb, "BASE_DIR", str(self.app)), \
                mock.patch.object(hb, "save_config"):
            app.update_loop()
        return app

    def test_failed_executable_restore_survives_real_startup_cleanup(self):
        self.failed_restore()
        old = self.app / (updates.EXE_NAME + updates.OLD_SUFFIX)
        self.assertFalse((self.app / updates.EXE_NAME).exists())
        self.assertEqual(old.read_bytes(), b"old executable")
        app = self.startup()
        self.assertEqual(old.read_bytes(), b"old executable")
        self.assertEqual(app.cfg["update_staging"], str(self.new))
        self.assertTrue(self.new.is_dir())
        self.assertEqual(self.state()["state"], "failed")

    def test_partial_library_restore_cannot_authorize_cleanup_from_a_working_old_exe(self):
        self.failed_restore(updates.INTERNAL)
        self.assertEqual((self.app / updates.EXE_NAME).read_bytes(), b"old executable")
        self.assertFalse((self.app / updates.INTERNAL).exists())
        self.assertFalse(updates.cleanup(str(self.app), str(self.new)))
        self.assertEqual((self.app / (updates.INTERNAL + ".old") / "library.dll").read_bytes(), b"old library")
        self.assertFalse(updates.can_install(True, str(self.app))[0])

    def test_crash_mid_copy_leaves_durable_pending_protection(self):
        with mock.patch.object(updates.shutil, "copytree", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                updates.swap_files(str(self.new), str(self.app))
        self.assertEqual(self.state()["state"], "pending")
        for _ in range(2):
            self.assertFalse(updates.cleanup(str(self.app), str(self.new)))
            self.assertEqual((self.app / (updates.EXE_NAME + ".old")).read_bytes(), b"old executable")
            self.assertTrue(self.new.exists())

    def test_record_failure_before_first_rename_leaves_original_program_untouched(self):
        with mock.patch.object(updates.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                updates.swap_files(str(self.new), str(self.app))
        self.assertEqual((self.app / updates.EXE_NAME).read_bytes(), b"old executable")
        self.assertFalse((self.app / (updates.EXE_NAME + ".old")).exists())
        self.assertFalse((self.app / updates.UPDATE_STATE).exists())

    def test_completion_record_failure_does_not_roll_back_a_process_already_started(self):
        original = updates._write_update_state
        calls = []

        def write(folder, state, **kwargs):
            calls.append(state["state"])
            if state["state"] == "installed":
                raise OSError("completion cannot be saved")
            return original(folder, state, **kwargs)

        with mock.patch.object(updates, "_write_update_state", side_effect=write):
            self.assertTrue(self.finish())
        self.assertEqual(calls, ["pending", "installed"])
        self.assertEqual((self.app / updates.EXE_NAME).read_bytes(), b"new executable")
        self.assertEqual(self.state()["state"], "pending")
        self.assertFalse(updates.cleanup(str(self.app)))
        self.assertEqual((self.app / (updates.EXE_NAME + ".old")).read_bytes(), b"old executable")
        self.assertEqual(len(self.starts), 1)

    def test_success_cleanup_is_owned_idempotent_and_keeps_portable_data(self):
        (self.new / "config.json").write_bytes(b"must not replace user config")
        (self.new / "iconify").mkdir()
        (self.new / "iconify" / "chosen.png").write_bytes(b"must not replace artwork")
        self.assertTrue(self.finish())
        self.assertEqual(self.state()["state"], "installed")
        app = self.startup()
        self.assertNotIn("update_staging", app.cfg)
        self.assertTrue(app.install_ok)
        self.assertFalse(self.new.exists())
        self.assertFalse((self.app / updates.UPDATE_STATE).exists())
        self.assertTrue(updates.cleanup(str(self.app)))
        for name in ("portable.txt", "config.json", "history.json", "settings-window.json", "notes.old"):
            self.assertEqual((self.app / name).read_bytes(), b"user data: " + name.encode())
        self.assertEqual((self.app / "iconify" / "chosen.png").read_bytes(), b"saved artwork")

    def test_complete_rollback_can_clean_staging_after_the_old_app_starts(self):
        with mock.patch.object(updates.shutil, "copytree", side_effect=OSError("copy failed")), \
                mock.patch.object(updates, "RETRY_S", 0):
            self.assertFalse(self.finish())
        self.assertEqual(self.state()["state"], "rolled_back")
        self.assertEqual((self.app / updates.EXE_NAME).read_bytes(), b"old executable")
        self.assertTrue(updates.cleanup(str(self.app)))
        self.assertFalse(self.new.exists())
        self.assertFalse((self.app / updates.UPDATE_STATE).exists())

    def test_cleanup_retries_after_a_backup_remove_failure(self):
        self.assertTrue(self.finish())
        original = updates._remove
        path = str(self.app / (updates.EXE_NAME + ".old"))
        with mock.patch.object(updates, "_remove", side_effect=lambda p: None if p == path else original(p)):
            self.assertFalse(updates.cleanup(str(self.app)))
        self.assertTrue((self.app / updates.UPDATE_STATE).exists())
        self.assertTrue(self.new.exists())
        self.assertTrue(updates.cleanup(str(self.app)))
        self.assertFalse((self.app / updates.UPDATE_STATE).exists())

    def test_unverified_or_missing_installed_files_keep_backups(self):
        self.assertTrue(self.finish())
        (self.app / updates.EXE_NAME).write_bytes(b"another executable")
        self.assertFalse(updates.cleanup(str(self.app)))
        self.assertEqual((self.app / (updates.EXE_NAME + ".old")).read_bytes(), b"old executable")
        (self.app / updates.EXE_NAME).write_bytes(b"new executable")
        (self.app / updates.INTERNAL / "library.dll").unlink()
        (self.app / updates.INTERNAL).rmdir()
        self.assertFalse(updates.cleanup(str(self.app)))

    def test_legacy_unowned_backups_and_staging_are_not_deleted(self):
        (self.app / (updates.EXE_NAME + ".old")).write_bytes(b"only legacy previous copy")
        self.assertTrue(updates.cleanup(str(self.app), str(self.new)))
        self.assertEqual((self.app / (updates.EXE_NAME + ".old")).read_bytes(), b"only legacy previous copy")
        self.assertTrue(self.new.exists())
        with self.assertRaisesRegex(OSError, "backup"):
            updates.swap_files(str(self.new), str(self.app))

    def test_new_attempt_cannot_overwrite_an_unresolved_transaction(self):
        self.failed_restore(updates.INTERNAL)
        before = (self.app / updates.UPDATE_STATE).read_bytes()
        with self.assertRaisesRegex(OSError, "pending"):
            updates.swap_files(str(self.new), str(self.app))
        self.assertEqual((self.app / updates.UPDATE_STATE).read_bytes(), before)
        self.assertEqual((self.app / (updates.INTERNAL + ".old") / "library.dll").read_bytes(), b"old library")

    def test_wrong_marker_types_and_path_escape_keep_recovery_files(self):
        self.assertTrue(self.finish())
        valid = self.state()
        invalid = [None, True, [], {}, dict(valid, schema=True), dict(valid, state=[]),
                   dict(valid, state="unknown"), dict(valid, backups=["../outside"]),
                   dict(valid, backups=["config.json"]), dict(valid, files=[]),
                   dict(valid, exe_sha256=True), dict(valid, staging=[str(self.app)]),
                   dict(valid, staging=True)]
        marker = self.app / updates.UPDATE_STATE
        for value in invalid:
            with self.subTest(value=value):
                marker.write_text(json.dumps(value), encoding="utf-8")
                self.assertFalse(updates.cleanup(str(self.app), str(self.new)))
                self.assertTrue((self.app / (updates.EXE_NAME + ".old")).exists())
                self.assertTrue(self.new.exists())
        for raw in ("{", "x" * 65537, "[" * 2000 + "]" * 2000):
            marker.write_text(raw, encoding="utf-8")
            self.assertFalse(updates.cleanup(str(self.app)))
        marker.write_text(json.dumps(valid), encoding="utf-8")
        for staging in (True, [], {}, 42, "", str(self.root), str(self.app)):
            with self.subTest(staging=staging):
                self.assertFalse(updates.cleanup(str(self.app), staging))
                self.assertTrue((self.app / (updates.EXE_NAME + ".old")).exists())

    def test_startup_cleanup_does_not_race_an_active_install(self):
        self.assertTrue(self.finish())
        app = self.startup(busy=True)
        self.assertEqual(app.cfg["update_staging"], str(self.new))
        self.assertEqual(self.state()["state"], "installed")
        self.assertTrue((self.app / (updates.EXE_NAME + ".old")).exists())

    def test_unrelated_persisted_staging_path_cannot_authorize_any_cleanup(self):
        self.assertTrue(self.finish())
        unrelated = self.root / "unrelated-user-folder"
        unrelated.mkdir()
        saved = unrelated / "saved.txt"
        saved.write_bytes(b"must retain unrelated data")
        state = self.state()
        state["staging"] = [str(unrelated)]
        (self.app / updates.UPDATE_STATE).write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(updates.cleanup(str(self.app)))
        self.assertEqual(saved.read_bytes(), b"must retain unrelated data")
        self.assertTrue((self.app / (updates.EXE_NAME + ".old")).exists())
        self.assertTrue(self.new.exists())
        # A similarly named marker from another owner or transaction is no proof.
        for owner, token in ((str(self.app), "0" * 32), (str(unrelated), state["stage_token"])):
            (unrelated / updates.STAGE_STATE).write_text(
                json.dumps(dict(schema=1, owner=owner, token=token)), encoding="utf-8")
            self.assertFalse(updates.cleanup(str(self.app)))
            self.assertEqual(saved.read_bytes(), b"must retain unrelated data")

    def test_backups_must_be_recorded_transaction_files(self):
        self.assertTrue(self.finish())
        state = self.state()
        state["backups"] = ["notes"]
        (self.app / updates.UPDATE_STATE).write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(updates.cleanup(str(self.app)))
        self.assertEqual((self.app / "notes.old").read_bytes(), b"user data: notes.old")
        self.assertTrue((self.app / (updates.EXE_NAME + ".old")).exists())
        self.assertTrue(self.new.exists())

    def test_edited_app_file_inventory_must_match_independent_stage_receipt(self):
        self.assertTrue(self.finish())
        (self.app / "notes").write_bytes(b"unrelated current notes")
        valid = self.state()
        for files, backups in ((valid["files"] + ["notes"], valid["backups"] + ["notes"]),
                               ([updates.EXE_NAME], [updates.EXE_NAME])):
            state = dict(valid, files=files, backups=backups)
            (self.app / updates.UPDATE_STATE).write_text(json.dumps(state), encoding="utf-8")
            self.assertFalse(updates.cleanup(str(self.app)))
            self.assertEqual((self.app / "notes.old").read_bytes(), b"user data: notes.old")
            self.assertTrue((self.app / (updates.EXE_NAME + ".old")).exists())
        state = dict(valid, exe_sha256=updates.hashlib.sha256(b"unrelated replacement").hexdigest())
        (self.app / updates.EXE_NAME).write_bytes(b"unrelated replacement")
        (self.app / updates.UPDATE_STATE).write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(updates.cleanup(str(self.app)))
        self.assertTrue((self.app / (updates.EXE_NAME + ".old")).exists())

    def test_real_updater_root_new_layout_cleans_the_owned_root(self):
        root = self.root / updates.STAGING_NAME
        root.mkdir()
        target = root / "new"
        self.new.rename(target)
        self.new = target
        (root / "runner").mkdir()
        (root / "runner" / "retained.exe").write_bytes(b"runner fixture")
        self.assertTrue(self.finish())
        self.assertEqual(self.state()["staging"], [str(root), str(target)])
        self.assertTrue(updates.cleanup(str(self.app), str(root)))
        self.assertFalse(root.exists())

    def test_staging_removed_before_marker_cleanup_is_safe_to_retry(self):
        self.assertTrue(self.finish())
        marker = str(self.app / updates.UPDATE_STATE)
        remove = updates._remove
        with mock.patch.object(updates, "_remove", side_effect=lambda p: None if p == marker else remove(p)):
            self.assertFalse(updates.cleanup(str(self.app)))
        self.assertFalse(self.new.exists())
        self.assertTrue((self.app / updates.UPDATE_STATE).exists())
        self.assertTrue(updates.cleanup(str(self.app)))

    def test_partial_staging_cleanup_retains_receipt_and_retries(self):
        self.assertTrue(self.finish())
        (self.new / "locked.tmp").write_bytes(b"staged temporary data")
        remove = updates._remove
        locked = str(self.new / "locked.tmp")
        with mock.patch.object(updates, "_remove", side_effect=lambda p: None if p == locked else remove(p)):
            self.assertFalse(updates.cleanup(str(self.app)))
        self.assertTrue((self.new / updates.STAGE_STATE).exists())
        self.assertTrue((self.app / updates.UPDATE_STATE).exists())
        self.assertTrue(updates.cleanup(str(self.app)))
        self.assertFalse(self.new.exists())
        self.assertFalse((self.app / updates.UPDATE_STATE).exists())

    def test_lost_receipt_after_backups_removed_finalizes_without_deleting_remainder(self):
        self.assertTrue(self.finish())
        state = self.state()
        for name in state["backups"]:
            updates._remove(str(self.app / (name + ".old")))
        (self.new / updates.STAGE_STATE).unlink()
        retained = self.new / "unowned-remainder.txt"
        retained.write_bytes(b"must not delete without receipt")
        self.assertTrue(updates.cleanup(str(self.app)))
        self.assertEqual(retained.read_bytes(), b"must not delete without receipt")
        self.assertFalse((self.app / updates.UPDATE_STATE).exists())
        self.assertTrue(updates.can_install(True, str(self.app))[0])


if __name__ == "__main__":
    unittest.main()
