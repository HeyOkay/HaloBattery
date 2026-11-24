"""Completed Settings sessions release jobs and process wrappers without GC."""
from contextlib import contextmanager
import gc
import io
import threading
import unittest
import weakref

from halo import settings_host


@contextmanager
def without_cyclic_gc():
    enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if enabled:
            gc.enable()


class _Controller:
    context = {"data_dir": "data"}
    def create_browse_catalog(self):
        raise AssertionError("An idle catalog worker must not load a catalog")


class _Process:
    def __init__(self):
        self.stdin, self.stdout, self.stderr = (io.StringIO() for _ in range(3))
        self.returncode = None
        self.on_wait = None

    def wait(self, timeout):
        callback, self.on_wait = self.on_wait, None
        if callback is not None:
            callback()
        self.returncode = 0
        return self.returncode

    def poll(self):
        return self.returncode


class SettingsSessionLifetimeTests(unittest.TestCase):
    def retire_session(self, *, reentrant=False, repeat=False):
        manager = settings_host.SettingsHost(_Controller())
        process = _Process()
        session = settings_host._Session("general", None, process=process)
        manager._session = session
        # This creates the real broker and session-capturing callbacks. Worker
        # launch remains lazy, so this lifetime test does not need a subprocess.
        jobs = manager._ensure_icon_jobs(session)
        cleanup_done = jobs._cleanup_done
        streams = (process.stdin, process.stdout, process.stderr)
        collected = {name: threading.Event() for name in ("session", "process", "jobs")}
        references = {
            name: weakref.ref(value, lambda _ref, event=collected[name]: event.set())
            for name, value in (("session", session), ("process", process), ("jobs", jobs))
        }
        if reentrant:
            process.on_wait = lambda: manager._cleanup(session)
        manager._cleanup(session)
        if repeat:
            manager._cleanup(session)
        self.assertIsNone(manager._session)
        self.assertTrue(session.stop.is_set())
        self.assertTrue(all(stream.closed for stream in streams))
        self.assertTrue(cleanup_done.wait(3), "Catalog worker disposal did not finish")
        # Returning drops every deliberate strong reference to retired objects.
        return manager, references, collected

    def assert_promptly_released(self, references, collected):
        for name, event in collected.items():
            self.assertTrue(event.wait(3), f"Retired {name} still needs cyclic GC")
            self.assertIsNone(references[name]())

    def test_cleanup_releases_session_process_and_real_jobs_without_cyclic_gc(self):
        with without_cyclic_gc():
            manager, references, collected = self.retire_session()
            self.assert_promptly_released(references, collected)
            self.assertIsNone(manager._session)

    def test_reentrant_and_repeated_cleanup_release_the_same_session(self):
        with without_cyclic_gc():
            manager, references, collected = self.retire_session(reentrant=True, repeat=True)
            self.assert_promptly_released(references, collected)
            self.assertIsNone(manager._session)
