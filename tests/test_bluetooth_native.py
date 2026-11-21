"""The in-process Bluetooth reader (providers/bluetooth_native.py) and NativeBluetoothWatcher.

No Bluetooth stack, no PowerShell, no real time: the watcher runs synchronously against a fake
reader and a fake clock, the provider's one-shot read and the app's fallback chain use mocks.

Run from the repository root:
    python -m unittest discover -s tests
"""
import json
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hide_rename import hb, make_app  # noqa: E402
from providers import bluetooth, bluetooth_native  # noqa: E402

MAC = "AABBCCDDEEFF"
ROOT = "BTHENUM\\DEV_" + MAC + "\\7&123"
T0 = 1000.0


def snapshot(connected, level=75):
    return {"devs": [{"id": ROOT, "name": "Windows Headset", "status": "OK"}],
            "props": [{"id": ROOT, "key": bluetooth.K_BAT, "data": level},
                      {"id": ROOT, "key": bluetooth.K_CONN, "data": connected}],
            "aep": [{"addr": MAC, "conn": connected, "le": False, "err": None, "cls": 0x0404}],
            "aep_error": None, "roots": [{"le": False, "mac": MAC}]}


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class FakeStop:
    """The watcher's stop event: wait(s) moves the fake clock instead of sleeping."""

    def __init__(self, clock, until):
        self.clock, self.until, self.flag = clock, until, False

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, seconds):
        self.clock.t += seconds
        if self.clock.t >= self.until:
            self.flag = True
        return self.flag


class FakeReader:
    """Connected from `connect_at` on (never when None); records what the watcher asks."""

    def __init__(self, clock, connect_at=None, winrt_ok=True, fail_checks=False):
        self.clock, self.connect_at = clock, connect_at
        self.winrt_ok = winrt_ok
        self.winrt_error = None if winrt_ok else "OSError: no WinRT"
        self.fail_checks = fail_checks
        self.checks, self.snaps, self.closed = [], [], False

    def connected(self):
        return self.connect_at is not None and self.clock.t >= self.connect_at

    def connections(self, roots):
        self.checks.append(self.clock.t)
        if self.fail_checks:
            raise OSError("check failed")
        return snapshot(self.connected())["aep"]

    def snapshot(self):
        self.snaps.append(self.clock.t)
        return snapshot(self.connected())

    def close(self):
        self.closed = True


def run_watcher(reader, until, provider=None):
    """Runs the watcher loop on this thread until the fake clock reaches `until`."""
    updates = []
    w = bluetooth.NativeBluetoothWatcher(provider or bluetooth.BluetoothProvider(), updates.append,
                                         reader_factory=lambda: reader, clock=reader.clock)
    w._stop = FakeStop(reader.clock, until)  # type: ignore[assignment]
    with mock.patch.object(bluetooth, "log"):
        w._run()
    return w, updates


class SnapshotHelpersTests(unittest.TestCase):
    def test_roots_are_the_device_nodes_with_upper_case_addresses(self):
        devs = [{"id": "BTHENUM\\DEV_a1b2c3d4e5f6\\7&1"},
                {"id": "BTHLE\\DEV_112233445566\\8&2"},
                {"id": "BTHLEDEVICE\\{00001800-0000-1000-8000-00805F9B34FB}_DEV_VID&01_112233445566\\9&3"},
                {"id": "BTHENUM\\{0000110B-0000-1000-8000-00805F9B34FB}_LOCALMFG&0002\\7&4"},
                {"id": None}, {}]
        self.assertEqual(bluetooth_native.roots_of(devs),
                         [{"le": False, "mac": "A1B2C3D4E5F6"}, {"le": True, "mac": "112233445566"}])

    def test_connection_key_changes_only_with_a_connection_state(self):
        a = [{"addr": MAC, "conn": True}, {"addr": "112233445566", "conn": None}]
        b = [{"addr": MAC, "conn": False}, {"addr": "112233445566", "conn": None}]
        key = bluetooth_native.connection_key
        self.assertEqual(key(a), key([dict(x) for x in a]))
        self.assertNotEqual(key(a), key(b))
        self.assertEqual(key([]), "")


class NativeWatcherCadenceTests(unittest.TestCase):
    """The same timing as WATCH_SCRIPT: a check every 2 s, snapshots at once and +3, +8 and +15 s
    after a connect or disconnect, and a full snapshot at least once a minute."""

    def test_snapshots_after_a_connect_follow_the_powershell_schedule(self):
        reader = FakeReader(Clock(), connect_at=T0 + 12)
        w, updates = run_watcher(reader, until=T0 + 90)
        self.assertEqual(reader.snaps, [T0, T0 + 12, T0 + 16, T0 + 20, T0 + 28, T0 + 88])
        self.assertEqual(reader.checks, [T0 + 2 * i for i in range(1, 45)])
        self.assertEqual(len(updates), 6)
        self.assertEqual(w.snapshots, 6)
        self.assertFalse(w.failed)
        self.assertTrue(reader.closed)

    def test_without_changes_a_full_snapshot_comes_once_a_minute(self):
        reader = FakeReader(Clock())
        run_watcher(reader, until=T0 + 122)
        self.assertEqual(reader.snaps, [T0, T0 + 60, T0 + 120])

    def test_without_winrt_it_reads_full_snapshots_and_never_checks(self):
        reader = FakeReader(Clock(), connect_at=T0 + 12, winrt_ok=False)
        w, _ = run_watcher(reader, until=T0 + 122)
        self.assertEqual(reader.checks, [])
        self.assertEqual(reader.snaps, [T0, T0 + 60, T0 + 120])
        self.assertFalse(w.failed)

    def test_each_snapshot_goes_through_the_provider(self):
        reader = FakeReader(Clock(), connect_at=T0)
        _, updates = run_watcher(reader, until=T0 + 2)
        self.assertEqual(len(updates), 1)
        (status,) = updates[0]
        self.assertEqual((status.key, status.level, status.online), ("bt:" + MAC, 75, True))


class NativeWatcherFailureTests(unittest.TestCase):
    def test_gives_up_after_three_failed_rounds_in_a_row(self):
        reader = FakeReader(Clock(), fail_checks=True)
        w, updates = run_watcher(reader, until=T0 + 600)
        self.assertTrue(w.failed)
        self.assertEqual(len(reader.checks), bluetooth.NativeBluetoothWatcher.FAIL_LIMIT)
        self.assertEqual(len(updates), 1)                 # the first snapshot still arrived
        self.assertTrue(reader.closed)

    def test_a_reader_that_cannot_start_fails_at_once(self):
        def broken():
            raise OSError("cfgmgr32 missing")
        w = bluetooth.NativeBluetoothWatcher(bluetooth.BluetoothProvider(), lambda devices: None,
                                             reader_factory=broken, clock=Clock())
        with mock.patch.object(bluetooth, "log"):
            w._run()
        self.assertTrue(w.failed)

    def test_a_stuck_round_counts_as_failed_after_the_stall_timeout(self):
        clock, release, entered = Clock(), threading.Event(), threading.Event()

        class StuckReader(FakeReader):
            def snapshot(self):
                entered.set()
                release.wait(15)                          # a Windows call that hangs
                return super().snapshot()

        reader = StuckReader(clock)
        w = bluetooth.NativeBluetoothWatcher(bluetooth.BluetoothProvider(), lambda devices: None,
                                             reader_factory=lambda: reader, clock=clock)
        with mock.patch.object(bluetooth, "log"):
            w.start()
            try:
                self.assertTrue(entered.wait(15))
                clock.t = T0 + bluetooth.STALL_TIMEOUT - 1
                self.assertFalse(w.failed)
                clock.t = T0 + bluetooth.STALL_TIMEOUT + 1
                self.assertTrue(w.failed)
                self.assertEqual(w.stalls, 1)
            finally:
                release.set()
                w._thread.join(15)
        self.assertFalse(w._thread.is_alive())            # it stops once the call returns
        self.assertTrue(reader.closed)


class OneShotReadTests(unittest.TestCase):
    """BluetoothProvider(native=True) reads in-process first and keeps PowerShell as the fallback."""

    def test_native_snapshot_needs_no_powershell(self):
        provider = bluetooth.BluetoothProvider(native=True)
        with mock.patch.object(bluetooth_native, "one_shot_snapshot", return_value=snapshot(True)), \
                mock.patch.object(bluetooth.subprocess, "run") as run:
            raw = provider._query()
        assert raw is not None
        run.assert_not_called()
        self.assertEqual(json.loads(raw), snapshot(True))
        self.assertTrue(any("native: 1 nodes, 2 properties, 1 device(s)" in line
                            for line in provider.diagnostics()))

    def test_a_failed_native_read_falls_back_to_powershell(self):
        provider = bluetooth.BluetoothProvider(native=True)
        done = mock.Mock(returncode=0, stdout=json.dumps(snapshot(True)).encode(), stderr=b"")
        with mock.patch.object(bluetooth_native, "one_shot_snapshot", side_effect=OSError("no cfgmgr32")), \
                mock.patch.object(bluetooth.subprocess, "run", return_value=done) as run:
            raw = provider._query()
        assert raw is not None
        run.assert_called_once()
        self.assertEqual(json.loads(raw)["devs"], snapshot(True)["devs"])
        self.assertTrue(any("native read failed (OSError: no cfgmgr32), using PowerShell" in line
                            for line in provider.diagnostics()))

    def test_the_default_provider_does_not_read_in_process(self):
        provider = bluetooth.BluetoothProvider()
        done = mock.Mock(returncode=0, stdout=json.dumps(snapshot(True)).encode(), stderr=b"")
        with mock.patch.object(bluetooth_native, "one_shot_snapshot") as native, \
                mock.patch.object(bluetooth.subprocess, "run", return_value=done):
            provider._query()
        native.assert_not_called()


class AppFallbackChainTests(unittest.TestCase):
    def test_a_failed_native_watcher_hands_over_to_the_powershell_watcher(self):
        app = make_app({"bluetooth": True})
        app.stop_evt, app.bt_wake = threading.Event(), threading.Event()
        app.bt_watch, app.bt_watch_failed = None, False
        app.bt = object()
        made = []

        class Native:
            def __init__(self, provider, on_update):
                self.failed = True
                made.append("native")

            def start(self):
                return self

            def stop(self):
                pass

        class PowerShell:
            def __init__(self, provider, on_update):
                self.failed = False
                made.append("powershell")
                app.stop_evt.set()                        # one round is enough
                app.bt_wake.set()

            def start(self):
                return self

            def stop(self):
                pass

        with mock.patch.object(hb, "NativeBluetoothWatcher", Native), \
                mock.patch.object(hb, "BluetoothWatcher", PowerShell), \
                mock.patch.object(hb.sys, "platform", "win32"), mock.patch.object(hb, "log"):
            t = threading.Thread(target=app.bt_loop, daemon=True)
            t.start()
            t.join(15)
        self.assertFalse(t.is_alive())
        self.assertEqual(made, ["native", "powershell"])
        self.assertTrue(app.bt_native_failed)
        self.assertFalse(app.bt_watch_failed)


@unittest.skipUnless(sys.platform == "win32", "Windows DLLs")
class ReaderSetupTests(unittest.TestCase):
    """Loads the Windows DLLs and the WinRT factories; reads no device (no query, no snapshot)."""

    def test_reader_starts_and_closes_on_its_own_thread(self):
        box = {}

        def work():
            reader = bluetooth_native.NativeReader()
            box["ok"], box["error"] = reader.winrt_ok, reader.winrt_error
            self.assertEqual(reader.connections([]), [])
            reader.close()
            reader.close()                                # a second close is harmless

        t = threading.Thread(target=work)
        t.start()
        t.join(30)
        self.assertTrue(box.get("ok"), box.get("error"))


if __name__ == "__main__":
    unittest.main()
