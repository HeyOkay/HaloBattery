"""Tests for the Bluetooth watcher fix, item 7 of the review in #62.

The watcher read its child with `for line in proc.stdout`, which parks for ever on a
PowerShell wedged inside a WinRT call: no snapshot, `failed` still False, `running()`
still True, and the app never falls back to polling. The child is read through a queue
with a watchdog now, killed and restarted; two stalls in a row make the watcher give up.

No hardware and no PowerShell: subprocess.Popen is replaced by a fake child whose stdout
is scripted, and STALL_TIMEOUT is patched down so the walk is quick.

Run from the repository root:

    python -m unittest discover -s tests
"""
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers import bluetooth  # noqa: E402

MAC = "AABBCCDDEEFF"
ROOT = "BTHENUM\\DEV_" + MAC + "\\7&1"


def phone_snapshot(connected):
    return json.dumps({"devs": [{"id": ROOT, "name": "Phone", "status": "OK"}],
                       "props": [{"id": ROOT, "key": bluetooth.K_BAT, "data": 75},
                                 {"id": ROOT, "key": bluetooth.K_CONN, "data": connected}],
                       "aep": [{"addr": MAC, "conn": connected, "le": False,
                                "err": None, "cls": 0x0203}],
                       "aep_error": None, "roots": [{"le": False, "mac": MAC}]})


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t


class ScriptedQueue:
    """Empty gets advance the fake clock, so the watcher's 1 s timeout becomes a tick."""

    def __init__(self, clock, events, until, stop):
        self.clock, self.events, self.until, self.stop = clock, list(events), until, stop

    def put(self, item):
        pass

    def empty(self):
        return False

    def get(self, timeout=None):
        if self.events and self.events[0][0] <= self.clock.t:
            return self.events.pop(0)[1].encode()
        self.clock.t += timeout if timeout is not None else 1.0
        if self.clock.t >= self.until:
            self.stop.set()
        raise bluetooth.queue.Empty


class BlockingStdout:
    """Prints its lines, then keeps the read blocked - like a child that wedged."""

    def __init__(self, lines):
        self.lines = list(lines)
        self._done = threading.Event()

    def __iter__(self):
        for line in self.lines:
            yield line.encode() + b"\n"
        self._done.wait()

    def close(self):
        self._done.set()


class TimedStdout:
    """Prints a line every `interval` seconds, like a healthy child."""

    def __init__(self, interval=0.15, count=30):
        self.interval, self.count = interval, count
        self._done = threading.Event()

    def __iter__(self):
        for i in range(self.count):
            if self._done.wait(self.interval):
                return
            yield b'{"i": %d}\n' % i
        self._done.wait()

    def close(self):
        self._done.set()


class FakeProc:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = None

    def poll(self):
        return self.returncode

    def kill(self):
        self.returncode = -9
        self.stdout.close()

    def terminate(self):
        self.kill()

    def wait(self, *a):
        return self.returncode


class WatcherTest(unittest.TestCase):
    def setUp(self):
        self._saved = (bluetooth.subprocess.Popen, bluetooth.STALL_TIMEOUT, bluetooth.STALL_LIMIT)
        self.procs = []
        test = self

        class Popen(FakeProc):
            def __init__(self, *a, **kw):
                super().__init__(test.make_stdout())
                test.procs.append(self)

        bluetooth.subprocess.Popen = Popen
        bluetooth.STALL_TIMEOUT = 0.6
        bluetooth.STALL_LIMIT = 2
        self.make_stdout = lambda: BlockingStdout(['{"a": 1}'])

    def tearDown(self):
        bluetooth.subprocess.Popen, bluetooth.STALL_TIMEOUT, bluetooth.STALL_LIMIT = self._saved

    def run_watcher(self, until, timeout=20.0):   # an upper bound: it returns once `until` holds
        w = bluetooth.BluetoothWatcher(bluetooth.BluetoothProvider(), lambda *a: None)
        w.start()
        try:
            end = time.time() + timeout
            while time.time() < end and not until(w):
                time.sleep(0.05)
            return w
        finally:
            w.stop()

    def test_a_wedged_child_is_killed_and_counted(self):
        w = self.run_watcher(lambda w: w.stalls >= 1)
        self.assertGreaterEqual(w.stalls, 1)
        self.assertTrue(any(p.returncode == -9 for p in self.procs), "the child was not killed")
        self.assertFalse(w.failed)                    # one stall is not yet the fallback

    def test_a_stall_limit_of_one_gives_up_so_the_app_falls_back(self):
        bluetooth.STALL_LIMIT = 1
        w = self.run_watcher(lambda w: w.failed)
        self.assertTrue(w.failed)
        self.assertFalse(w.running())

    def test_a_healthy_child_is_neither_killed_nor_counted(self):
        self.make_stdout = lambda: TimedStdout(interval=0.05)
        bluetooth.STALL_TIMEOUT = 5.0      # a late scheduler tick must not look like a wedged child
        w = bluetooth.BluetoothWatcher(bluetooth.BluetoothProvider(), lambda *a: None)
        w.start()
        try:
            end = time.time() + 20.0
            while time.time() < end and w.snapshots < 3:
                time.sleep(0.05)
            self.assertGreaterEqual(w.snapshots, 3)
            self.assertEqual(w.stalls, 0)
            self.assertFalse(w.failed)
            # asserted before stop(): stopping a live child terminates it on purpose
            self.assertTrue(all(p.returncode is None for p in self.procs), "a healthy child was killed")
        finally:
            w.stop()

    def test_the_timeout_clears_the_scripts_own_cadence(self):
        # the watch script emits at least once every 60 s; the timeout must be well clear
        self.assertGreater(self._saved[1], 60.0)

    def test_phone_grace_expiry_is_published_between_child_lines(self):
        clock = FakeClock()
        start = clock.t
        updates = []
        self.make_stdout = lambda: BlockingStdout([])   # lines come from the scripted queue
        provider = bluetooth.BluetoothProvider(clock=clock)
        w = bluetooth.BluetoothWatcher(provider,
                                       lambda devices: updates.append((clock.t, devices)),
                                       clock=clock)
        q = ScriptedQueue(clock,
                          [(start, phone_snapshot(True)),
                           (start + 2, phone_snapshot(False))],
                          until=start + 33, stop=w._stop)
        try:
            with mock.patch.object(bluetooth.queue, "Queue", lambda: q), \
                    mock.patch.object(bluetooth, "log"):
                w._run()
        finally:
            w.stop()
            if self.procs:
                self.procs[-1].stdout.close()

        by_time = dict(updates)
        self.assertIn(start, by_time)
        self.assertIn(start + 2, by_time)          # the disconnect line, still inside the grace
        self.assertIn(start + 32, by_time)         # the expiry publication, before the next line
        phone = by_time[start + 32][0]
        self.assertEqual((phone.key, phone.level, phone.online),
                         ("bt:" + MAC, 75, False))
        self.assertFalse(w.failed)


if __name__ == "__main__":
    unittest.main()
