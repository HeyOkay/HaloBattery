"""History transactions shared by polling, diagnostics and shutdown. No devices."""
import json
import os
import sys
import threading
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _sandbox  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from halo import history  # noqa: E402


class ObservedRLock:
    """Expose a contender's arrival without relying on a sleep or scheduling race."""
    def __init__(self):
        self.lock = threading.RLock()
        self.contending = threading.Event()

    def __enter__(self):
        if (threading.current_thread().name == "history-contender"
                and not self.contending.is_set()):
            unheld = self.lock.acquire(blocking=False)
            if unheld:
                self.lock.release()
            self.contending.set()
            if unheld:
                raise AssertionError("the in-flight history transaction did not hold its lock")
        self.lock.acquire()
        return self

    def __exit__(self, *args):
        self.lock.release()


class HistoryConcurrencyTests(unittest.TestCase):
    def make_history(self):
        folder = _sandbox.mkdtemp(prefix="history_concurrency_")
        result = history.History(os.path.join(folder, "history.json"))
        result._lock = ObservedRLock()
        result.record("mouse", 80, False, True, 1000.0)
        result.save(force=True)
        result.record("mouse", 79, False, True, 1600.0)
        return result

    def start_call(self, callback, *, contender=False):
        done, errors, results = threading.Event(), [], []
        def run():
            try:
                results.append(callback())
            except BaseException as error:
                errors.append(error)
            finally:
                done.set()
        thread = threading.Thread(target=run, daemon=True,
                                  name="history-contender" if contender else "history-save")
        thread.start()
        self.addCleanup(thread.join, 5)
        return thread, done, errors, results

    def finish_call(self, call):
        thread, done, errors, results = call
        self.assertTrue(done.wait(5), "history operation did not finish")
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        return results[0]

    @contextmanager
    def paused_save(self, value):
        captured, release = threading.Event(), threading.Event()
        real_dump = json.dump
        dumps = []
        def dump(data, stream, **kwargs):
            real_dump(data, stream, **kwargs)
            dumps.append(None)
            captured.set()
            if not release.wait(5):
                raise RuntimeError("test did not release history save")
        with mock.patch.object(history.json, "dump", side_effect=dump):
            call = self.start_call(lambda: value.save(force=True))
            try:
                self.assertTrue(captured.wait(5), "history save did not reach serialization")
                yield call, release, dumps
            finally:
                release.set()
                self.finish_call(call)

    def wait_for_contender(self, value, call):
        self.assertTrue(value._lock.contending.wait(5), "history operation did not reach its lock")
        self.assertFalse(call[1].is_set(), "history operation escaped an in-flight save")

    def test_record_after_save_snapshot_stays_dirty_until_its_own_save(self):
        value = self.make_history()
        with self.paused_save(value) as (saving, release, _):
            recording = self.start_call(lambda: value.record("mouse", 78, False, True, 2200.0),
                                        contender=True)
            self.wait_for_contender(value, recording)
            release.set()
            self.finish_call(saving)
            self.finish_call(recording)
        self.assertTrue(value.dirty)
        with open(value.path, encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["devices"]["mouse"]["samples"][-1][1], 79)
        value.save(force=True)
        self.assertFalse(value.dirty)
        with open(value.path, encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["devices"]["mouse"]["samples"][-1][1], 78)

    def test_concurrent_forced_saves_share_one_complete_transaction(self):
        value = self.make_history()
        with self.paused_save(value) as (first, release, dumps):
            second = self.start_call(lambda: value.save(force=True), contender=True)
            self.wait_for_contender(value, second)
            self.assertEqual(len(dumps), 1)
            release.set()
            self.finish_call(first)
            self.finish_call(second)
            self.assertEqual(len(dumps), 1)
        self.assertFalse(value.dirty)
        self.assertFalse(os.path.exists(value.path + ".tmp"))
        with open(value.path, encoding="utf-8") as stream:
            self.assertEqual(json.load(stream)["devices"], value.devices)

    def test_load_and_estimates_wait_for_a_complete_save(self):
        for method, args in (("load", ()), ("rate", ("mouse",)),
                             ("seconds_left", ("mouse", 79)), ("report", ())):
            with self.subTest(method=method):
                value = self.make_history()
                with self.paused_save(value) as (saving, release, _):
                    reading = self.start_call(lambda: getattr(value, method)(*args), contender=True)
                    self.wait_for_contender(value, reading)
                    release.set()
                    self.finish_call(saving)
                    self.finish_call(reading)

    def test_nested_estimate_operations_are_reentrant(self):
        value = history.History()
        for index in range(4):
            value.record("mouse", 80 - index, False, True, 1000.0 + index * 600)
        call = self.start_call(lambda: (value.seconds_left("mouse", 60), value.report()))
        seconds, report = self.finish_call(call)
        self.assertAlmostEqual(seconds, 36000.0)
        self.assertIn("6.00 %/h", report[0])


if __name__ == "__main__":
    unittest.main()
